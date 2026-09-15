"""Read-only provenance information for the key-indicator source.

The operational indicators intentionally consume a compact source snapshot.
That snapshot is enough for calculations, but it is not a good explanation of
where a number came from.  This module builds that explanation without reading
the legacy HTML dashboard and without treating ``Upload.accounts_loaded`` as
evidence that a file was actually parsed.

The public contract has two layers:

``build_source_passport(source, uploads)``
    Company-wide provenance.  ``source`` is the JSON-safe snapshot returned by
    :func:`key_indicators.build_indicator_source`; the service may also attach
    a private ``_source_manifest`` containing parsed-file metadata.

``build_period_passport(source, payload)``
    A period-specific view suitable for attaching to the key-indicator JSON
    response.  It uses the company-wide passport and the selected period in
    ``payload['period']``; it never recalculates a KPI.

``enrich_source_passport(source, uploads)``
    Attach the company-wide passport to a source snapshot and return it.  The
    function mutates only the in-memory snapshot that is about to be served.
    No database row or source file is changed.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from key_indicators import _period_bounds


_CARD_ACCOUNTS = {
    "cash": ("50", "51", "52", "55"),
    "revenue": ("90",),
    "collection": ("62",),
    "ar": ("62",),
    "ap": ("60",),
    "cogs": ("90",),
    "profit": ("90",),
    "credits": ("66", "67"),
    "salary": ("70",),
    "capex": ("08",),
}
_MAX_FINDING_FILES = 8


def _source_identity(relative_path, digest):
    """Return the deterministic identity used by accepted source manifests."""
    return hashlib.sha256(
        f"{relative_path}\0{digest}".encode("utf-8")).hexdigest()


def _valid_relative_path(value):
    """Validate the portable path stored by the acceptance-side manifest."""
    text = _text(value).replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        return False
    parts = [part for part in text.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _manifest_records(upload):
    value = upload.get("source_manifest")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return "" if value is None else str(value)


def _iso_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _iso_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_text(value):
    parsed = _iso_date(value)
    return parsed.isoformat() if parsed else None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _period_from_values(value, start=None, end=None):
    parsed_start = _iso_date(start)
    parsed_end = _iso_date(end)
    if parsed_start and parsed_end:
        return min(parsed_start, parsed_end), max(parsed_start, parsed_end)
    if isinstance(value, dict):
        parsed_start = _iso_date(value.get("start"))
        parsed_end = _iso_date(value.get("end"))
        if parsed_start and parsed_end:
            return min(parsed_start, parsed_end), max(parsed_start, parsed_end)
        value = value.get("period") or value.get("label")
    return _period_bounds(value)


def _merge_intervals(intervals):
    valid = sorted(
        (start, end) for start, end in intervals
        if start and end and start <= end)
    result = []
    for start, end in valid:
        if not result or start > result[-1][1] + timedelta(days=1):
            result.append([start, end])
        elif end > result[-1][1]:
            result[-1][1] = end
    return [(start, end) for start, end in result]


def _coverage_gaps(start, end, intervals):
    """Return only portions of a requested window outside report coverage."""

    if not start or not end or start > end:
        return []
    covered = _merge_intervals([
        (max(start, point_start), min(end, point_end))
        for point_start, point_end in intervals
        if point_start and point_end and point_start <= end and point_end >= start
    ])
    gaps = []
    cursor = start
    for point_start, point_end in covered:
        if cursor < point_start:
            gaps.append((cursor, point_start - timedelta(days=1)))
        cursor = max(cursor, point_end + timedelta(days=1))
    if cursor <= end:
        gaps.append((cursor, end))
    return [
        {
            "start": point_start.isoformat(),
            "end": point_end.isoformat(),
            "days": (point_end - point_start).days + 1,
        }
        for point_start, point_end in gaps
    ]


def _between_coverage_gaps(intervals):
    """Return gaps between declared report spans, excluding outer unknowns."""

    merged = _merge_intervals(intervals)
    gaps = []
    for (_, previous_end), (next_start, _) in zip(merged, merged[1:]):
        if previous_end + timedelta(days=1) <= next_start - timedelta(days=1):
            gaps.append((previous_end + timedelta(days=1),
                         next_start - timedelta(days=1)))
    return [
        {
            "start": point_start.isoformat(),
            "end": point_end.isoformat(),
            "days": (point_end - point_start).days + 1,
        }
        for point_start, point_end in gaps
    ]


def _account_coverage_weight(coverage, start=None, end=None):
    """Return continuous account span and declared account-days."""

    intervals = []
    for item in _mapping(coverage).get("declared_intervals") or []:
        item = _mapping(item)
        point_start = _iso_date(item.get("start"))
        point_end = _iso_date(item.get("end"))
        if not point_start or not point_end:
            continue
        if start:
            point_start = max(point_start, start)
        if end:
            point_end = min(point_end, end)
        if point_start <= point_end:
            intervals.append((point_start, point_end))
    intervals = _merge_intervals(intervals)
    if not intervals:
        return 0, 0
    declared_days = sum(
        (point_end - point_start).days + 1
        for point_start, point_end in intervals)
    span_days = (intervals[-1][1] - intervals[0][0]).days + 1
    return span_days, declared_days


def _serialise_accounts_loaded(row):
    raw = row.get("accounts_loaded") if isinstance(row, dict) else getattr(
        row, "accounts_loaded", None)
    if isinstance(raw, (list, tuple, set)):
        return sorted({_text(item) for item in raw if _text(item)})
    if raw is None:
        return [], True
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [], False
    if not isinstance(value, list):
        return [], False
    return sorted({_text(item) for item in value if _text(item)}), True


def _upload_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _upload_record(row):
    accounts, accounts_valid = _serialise_accounts_loaded(row)
    uploaded_at = _iso_datetime(_upload_value(row, "uploaded_at"))
    metrics_raw = _upload_value(row, "metrics_json", "")
    manifest_marker = object()
    manifest = _upload_value(row, "source_manifest", manifest_marker)
    raw_manifest_json = _upload_value(
        row, "source_manifest_json", manifest_marker)
    manifest_parse_invalid = False
    if manifest is manifest_marker and raw_manifest_json is not manifest_marker:
        if raw_manifest_json in (None, ""):
            manifest = None
        else:
            try:
                manifest = json.loads(raw_manifest_json)
            except (TypeError, ValueError):
                manifest = None
                manifest_parse_invalid = True
            if manifest is not None and not isinstance(manifest, list):
                manifest = None
                manifest_parse_invalid = True
    if manifest is manifest_marker:
        manifest = None
    status = _upload_value(row, "source_manifest_status")
    if status is None:
        status = (
            "invalid" if manifest_parse_invalid
            else "absent" if manifest is None
            else "valid" if isinstance(manifest, list) and all(
                isinstance(item, dict) for item in manifest)
            else "invalid"
        )
    if not isinstance(manifest, list):
        manifest = None
    return {
        "id": _upload_value(row, "id"),
        "uploaded_at": uploaded_at.isoformat() if uploaded_at else None,
        "period_label": _text(_upload_value(row, "period_label", "")),
        "accounts_loaded": accounts,
        "accounts_loaded_valid": accounts_valid,
        "files_count": _upload_value(row, "files_count"),
        "source": _text(_upload_value(row, "source", "")),
        "dashboard_file": _text(_upload_value(row, "dashboard_file", "")),
        "source_manifest": copy.deepcopy(manifest),
        "source_manifest_status": _text(status) or "absent",
        "source_manifest_valid": status == "valid",
        "_metrics_json": _text(metrics_raw),
        "_source_manifest_json": (
            raw_manifest_json if raw_manifest_json is not manifest_marker
            else json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            if manifest is not None else None
        ),
    }


def _batch_info(path):
    """Get the accepted-upload batch marker from a source path."""

    if not path:
        return None, None
    raw = _text(path)
    if re.match(r"^\d{8}_\d{6}$", raw):
        folder = raw
    else:
        parts = re.split(r"[\\/]", raw)
        folder = parts[-2] if len(parts) >= 2 else ""
    match = re.match(r"^(\d{8}_\d{6})$", folder)
    if not match:
        return folder or None, None
    try:
        value = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return folder, None
    return folder, value.replace(tzinfo=timezone.utc)


def _manifest(source):
    value = source.get("_source_manifest") if isinstance(source, dict) else None
    if not isinstance(value, list):
        value = source.get("source_manifest") if isinstance(source, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _period_record(item, fallback_file=""):
    start, end = _period_from_values(
        item.get("period"), item.get("report_start"), item.get("report_end"))
    return {
        "period": _text(item.get("period")),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "source_file": _text(item.get("source_file") or fallback_file),
    }


def _source_file_record(item):
    """Strip local paths while keeping enough evidence for the UI."""

    period = _period_record(item, item.get("source_file"))
    daily_dates = sorted({
        value.isoformat()
        for value in (
            _iso_date(item.get("date") if isinstance(item, dict) else item)
            for item in item.get("daily_dates", [])
        )
        if value
    })
    record = {
        "source_file": _text(item.get("source_file") or item.get("file")),
        "kind": _text(item.get("kind") or "osv"),
        "status": _text(item.get("status") or "parsed"),
        "account": _text(item.get("account")) or None,
        "period": period["period"],
        "period_start": period["start"],
        "period_end": period["end"],
        "daily_dates": daily_dates,
        "source_updated_at": _text(item.get("source_updated_at")) or None,
        "source_size": item.get("source_size"),
        "batch": _text(item.get("batch")) or None,
        "relative_path": _text(item.get("relative_path")) or None,
        "sha256": _text(item.get("sha256")) or None,
        "source_id": _text(item.get("source_id")) or None,
        "upload_id": item.get("upload_id"),
        "association_expected_upload_id": item.get(
            "association_expected_upload_id"),
        "association_status": _text(item.get("association_status")) or None,
        "association_candidates": item.get("association_candidates"),
        "association_mismatches": item.get("association_mismatches"),
        "check_ok": item.get("check_ok"),
        "warnings": [_text(value) for value in item.get("warnings", [])],
    }
    return {key: value for key, value in record.items() if value is not None}


def _associate_files(files, uploads):
    """Attach files only through the persisted source manifest.

    Upload timestamps and the ``YYYYmmdd_HHMMSS`` directory name are useful
    display metadata, but neither proves ownership of a file.  A file is
    considered linked only when its source id, relative path and SHA-256 all
    agree with one persisted manifest record.  A path/hash/account/period
    disagreement remains visible as ``mismatch`` and is never downgraded to
    a guessed association.
    """

    manifest_entries = []
    by_source_id = {}
    by_path = {}
    duplicate_ids = set()
    manifest_declared = any(
        isinstance(upload.get("source_manifest"), list)
        for upload in uploads
    )
    for upload in uploads:
        upload_id = upload.get("id")
        for manifest in _manifest_records(upload):
            entry = copy.deepcopy(manifest)
            entry["_upload_id"] = upload_id
            entry["_manifest_valid"] = True
            relative_path = _text(entry.get("relative_path"))
            digest = _text(entry.get("sha256")).lower()
            source_id = _text(entry.get("source_id"))
            if not _valid_relative_path(relative_path):
                entry["_manifest_valid"] = False
                entry.setdefault("_manifest_errors", []).append("relative_path")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                entry["_manifest_valid"] = False
                entry.setdefault("_manifest_errors", []).append("sha256")
            elif source_id != _source_identity(relative_path, digest):
                entry["_manifest_valid"] = False
                entry.setdefault("_manifest_errors", []).append("source_id")
            if not _text(entry.get("account")):
                entry["_manifest_valid"] = False
                entry.setdefault("_manifest_errors", []).append("account")
            if "period" not in entry:
                entry["_manifest_valid"] = False
                entry.setdefault("_manifest_errors", []).append("period")
            if not source_id:
                entry["_manifest_valid"] = False
            if not entry["_manifest_valid"]:
                upload.setdefault("_manifest_invalid_records", []).append(entry)
            manifest_entries.append(entry)
            if source_id:
                if source_id in by_source_id:
                    duplicate_ids.add(source_id)
                by_source_id.setdefault(source_id, []).append(entry)
            if relative_path:
                by_path.setdefault(relative_path, []).append(entry)

    for item in files:
        item.setdefault("association_mismatches", [])
        if item.get("kind") != "osv":
            # The persisted monthly manifest intentionally excludes chart-of-
            # accounts and annual analytical workbooks.
            item["upload_id"] = None
            item["association_status"] = "not_applicable"
            continue
        if item.get("is_manifest_placeholder"):
            item["upload_id"] = item.get("_expected_upload_id")
            item["association_status"] = "manifest_missing"
            continue
        if not manifest_entries:
            item["upload_id"] = None
            item["association_status"] = (
                "unmatched" if manifest_declared else (
                    "legacy_unlinked" if uploads else "unlinked"))
            continue

        source_id = _text(item.get("source_id"))
        relative_path = _text(item.get("relative_path"))
        digest = _text(item.get("sha256")).lower()
        exact = [
            entry for entry in by_source_id.get(source_id, [])
            if entry.get("_manifest_valid")
            and entry.get("relative_path") == relative_path
            and _text(entry.get("sha256")).lower() == digest
        ]
        related = exact or by_path.get(relative_path, []) or (
            by_source_id.get(source_id, []) if source_id else [])
        if len(exact) == 1:
            entry = exact[0]
            expected_account = _text(entry.get("account"))
            actual_account = _text(item.get("account"))
            expected_period = _period_record(entry)
            actual_period = _period_record(item)
            mismatches = []
            if expected_account and expected_account != actual_account:
                mismatches.append({
                    "field": "account",
                    "expected": expected_account,
                    "actual": actual_account or None,
                })
            if expected_period["period"] and (
                    expected_period["period"] != actual_period["period"]):
                mismatches.append({
                    "field": "period",
                    "expected": expected_period["period"],
                    "actual": actual_period["period"],
                })
            for field in ("start", "end"):
                expected = expected_period[field]
                actual = actual_period[field]
                if expected and expected != actual:
                    mismatches.append({
                        "field": "report_" + field,
                        "expected": expected,
                        "actual": actual,
                    })
            if mismatches:
                item["upload_id"] = None
                item["association_expected_upload_id"] = entry.get("_upload_id")
                item["association_candidates"] = [entry.get("_upload_id")]
                item["association_status"] = "mismatch"
                item["association_mismatches"] = mismatches
            else:
                item["upload_id"] = entry.get("_upload_id")
                item["association_status"] = "explicit"
            entry["_matched"] = True
            continue

        item["upload_id"] = None
        if len(related) > 1:
            item["association_status"] = "ambiguous"
            item["association_candidates"] = [
                entry.get("_upload_id") for entry in related]
        elif related:
            entry = related[0]
            item["upload_id"] = None
            item["association_expected_upload_id"] = entry.get("_upload_id")
            item["association_candidates"] = [entry.get("_upload_id")]
            item["association_status"] = "mismatch"
            mismatches = []
            if entry.get("relative_path") != relative_path:
                mismatches.append({
                    "field": "relative_path",
                    "expected": entry.get("relative_path"),
                    "actual": relative_path or None,
                })
            if _text(entry.get("sha256")).lower() != digest:
                mismatches.append({
                    "field": "sha256",
                    "expected": entry.get("sha256"),
                    "actual": digest or None,
                })
            if _text(entry.get("source_id")) != source_id:
                mismatches.append({
                    "field": "source_id",
                    "expected": entry.get("source_id"),
                    "actual": source_id or None,
                })
            item["association_mismatches"] = mismatches or [{
                "field": "manifest",
                "expected": "exact source identity",
                "actual": "different source identity",
            }]
            entry["_matched"] = True
        else:
            item["association_status"] = "unmatched"

    # Keep this internal index available to discrepancy generation without
    # exposing implementation details in the JSON response.
    for upload in uploads:
        upload["_manifest_entries"] = [
            entry for entry in manifest_entries
            if entry.get("_upload_id") == upload.get("id")
        ]
        upload["_manifest_duplicate_ids"] = sorted(duplicate_ids)


def _balance_entry(date_value, debit, credit, source_file, kind):
    entry = {
        "date": _date_text(date_value),
        "debit": _number(debit),
        "credit": _number(credit),
        "source_file": _text(source_file) or None,
        "kind": kind,
    }
    return {key: value for key, value in entry.items() if value is not None}


def _account_passport(account, raw, account_files):
    raw = _mapping(raw)
    periods = []
    for item in account_files:
        if item.get("kind") != "osv" or item.get("status") != "parsed":
            continue
        periods.append(_period_record(item, item.get("source_file")))
    for item in raw.get("coverage") or []:
        item = _mapping(item)
        periods.append(_period_record(item, item.get("source_file")))
    periods = sorted({
        (item["period"], item["start"], item["end"], item["source_file"]): item
        for item in periods
    }.values(), key=lambda item: (
        item["start"] or "9999-99-99", item["end"] or "", item["source_file"]))

    intervals = [
        (_iso_date(item["start"]), _iso_date(item["end"]))
        for item in periods
        if _iso_date(item["start"]) and _iso_date(item["end"])
    ]
    daily = [_mapping(item) for item in raw.get("daily") or []]
    daily_dates = sorted({
        parsed for parsed in (_iso_date(item.get("date")) for item in daily)
        if parsed
    })
    balance_observations = []
    for item in raw.get("balance_observations") or []:
        item = _mapping(item)
        parsed = _iso_date(item.get("date"))
        if not parsed:
            continue
        balance_observations.append({
            "date": parsed.isoformat(),
            "debit": _number(item.get("skd")),
            "credit": _number(item.get("skk")),
            "opening_debit": _number(item.get("snd")),
            "opening_credit": _number(item.get("snk")),
            "source_file": _text(item.get("source_file")) or None,
            "kind": "daily_observation",
        })

    balance_reports = []
    for item in raw.get("balance_reports") or []:
        item = _mapping(item)
        report_start, report_end = _period_from_values(
            item.get("period"), item.get("report_start"), item.get("report_end"))
        top = _mapping(item.get("top"))
        if not report_end or not top:
            continue
        balance_reports.append({
            "source_file": _text(item.get("source_file")) or None,
            "period": _text(item.get("period")),
            "start": report_start.isoformat() if report_start else None,
            "end": report_end.isoformat(),
            "opening": _balance_entry(
                report_start, top.get("snd"), top.get("snk"),
                item.get("source_file"), "report_opening"),
            "closing": _balance_entry(
                report_end, top.get("skd"), top.get("skk"),
                item.get("source_file"), "report_closing"),
        })
    # A hand-built source (and some older snapshots) may retain the account
    # top and period boundary but not the richer balance_reports list.  The
    # boundary is still exact evidence in that case; retain it rather than
    # reporting an unexplained absence of balances.
    if not balance_reports and isinstance(raw.get("top"), dict):
        top = raw["top"]
        report_start, report_end = _period_from_values(
            raw.get("period"), raw.get("report_start"), raw.get("report_end"))
        if report_end:
            balance_reports.append({
                "source_file": _text(raw.get("source_file")) or None,
                "period": _text(raw.get("period")),
                "start": report_start.isoformat() if report_start else None,
                "end": report_end.isoformat(),
                "opening": _balance_entry(
                    report_start, top.get("snd"), top.get("snk"),
                    raw.get("source_file"), "report_opening"),
                "closing": _balance_entry(
                    report_end, top.get("skd"), top.get("skk"),
                    raw.get("source_file"), "report_closing"),
            })

    exact = []
    for report in balance_reports:
        exact.extend([report["opening"], report["closing"]])
    exact.extend(balance_observations)
    exact = [item for item in exact if item.get("date")]

    selected_opening = None
    selected_closing = None
    # The service orders reports by actual source precedence (newest last).
    # Pick the newest observation at each exact outer boundary, never by name.
    if balance_reports:
        first_date = min(
            (item["start"] for item in balance_reports if item["start"]),
            default=None)
        last_date = max(item["end"] for item in balance_reports)
        selected_opening = next(
            (item["opening"] for item in reversed(balance_reports)
             if first_date and item["start"] == first_date), None)
        selected_closing = next(
            item["closing"] for item in reversed(balance_reports)
            if item["end"] == last_date)

    all_intervals = _merge_intervals(intervals)
    # A report period is evidence that the source covers that calendar
    # interval.  A missing dated movement row is not evidence of a missing
    # transaction: zero-transaction days are valid and _turnover_basis()
    # deliberately leaves them empty.  Dated rows remain a separate
    # observability measure.
    gaps = _between_coverage_gaps(all_intervals)
    expected_days = sum((end - start).days + 1 for start, end in all_intervals)
    covered_days = expected_days
    if all_intervals:
        coverage_status = "partial" if gaps else "complete"
        coverage_basis = "report_period"
    elif daily_dates:
        coverage_status = "observed_without_declared_period"
        coverage_basis = "dated_turnover_rows"
    else:
        coverage_status = "no_turnover_dates"
        coverage_basis = "none"

    report_start = min(
        (start for start, _ in all_intervals), default=None)
    report_end = max((end for _, end in all_intervals), default=None)
    observed_start = min(daily_dates, default=None)
    observed_end = max(daily_dates, default=None)
    actual_start = min(value for value in (report_start, observed_start) if value) \
        if report_start or observed_start else None
    actual_end = max(value for value in (report_end, observed_end) if value) \
        if report_end or observed_end else None

    failures = []
    if not periods and not daily_dates:
        failures.append("Не найден фактический период и датированные обороты.")
    if raw.get("check_ok") is False:
        failures.append("Контрольное равенство СН+Об=СК не сходится.")
    failures.extend(_text(value) for value in raw.get("warnings") or [])

    return {
        "account": _text(account),
        "available": True,
        "actual_period": {
            "start": actual_start.isoformat() if actual_start else None,
            "end": actual_end.isoformat() if actual_end else None,
        },
        "periods": periods,
        "exact_balances": {
            "opening": selected_opening,
            "closing": selected_closing,
            "all": exact,
        },
        "turnover_coverage": {
            "declared_intervals": [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in all_intervals
            ],
            "observed_dates": [value.isoformat() for value in daily_dates],
            "observed_start": observed_start.isoformat() if observed_start else None,
            "observed_end": observed_end.isoformat() if observed_end else None,
            "expected_days": expected_days,
            "covered_days": covered_days,
            "observed_days": len(daily_dates),
            "coverage_basis": coverage_basis,
            "coverage_ratio": (
                round(covered_days / expected_days, 6)
                if expected_days else None),
            "status": coverage_status,
            "gaps": gaps,
        },
        "files": [
            _source_file_record(item)
            for item in account_files
            if item.get("kind") == "osv" and item.get("status") == "parsed"
        ],
        "duplicates": [],
        "overlaps": [],
        "failures": list(dict.fromkeys(failures)),
    }


def _metadata_discrepancies(uploads, files):
    discrepancies = []
    for item in files:
        association_status = item.get("association_status")
        if association_status == "ambiguous":
            discrepancies.append({
                "type": "file_upload_association_ambiguous",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "candidates": item.get("association_candidates") or [],
                "message": (
                    "Файл нельзя надёжно связать с одной записью загрузки: "
                    "несколько записей содержат подходящий источник."),
            })
        elif association_status == "unmatched" and uploads:
            discrepancies.append({
                "type": "file_upload_association_unmatched",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "message": (
                    "Файл разобран, но в сохранённом манифесте источников нет "
                    "точной связи с записью загрузки; проверка её метаданных "
                    "не выполняется."),
            })
        elif association_status == "unlinked":
            discrepancies.append({
                "type": "file_upload_link_missing",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "message": (
                    "Для файла нет сохранённого манифеста источников и записи "
                    "загрузки; принадлежность не установлена."),
            })
        elif association_status == "legacy_unlinked":
            discrepancies.append({
                "type": "file_upload_link_missing",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "message": (
                    "Для старой записи нет сохранённого манифеста источников; "
                    "принадлежность файла загрузке не установлена. "
                    "Близость дат не использовалась."),
            })
        elif association_status == "mismatch":
            discrepancies.append({
                "type": "source_manifest_mismatch",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "upload_id": item.get(
                    "association_expected_upload_id", item.get("upload_id")),
                "fields": copy.deepcopy(item.get("association_mismatches") or []),
                "message": (
                    "Файл найден, но сохранённый манифест источников не "
                    "подтверждает его "
                    "идентичность: проверка пути, хеша, счёта или периода "
                    "не прошла."),
            })
        elif association_status == "manifest_missing":
            discrepancies.append({
                "type": "source_manifest_file_missing",
                "source_file": _text(item.get("source_file") or item.get("file")),
                "upload_id": item.get("upload_id"),
                "relative_path": item.get("relative_path"),
                "message": (
                    "Сохранённый манифест источников ссылается на принятый файл, "
                    "но текущий файл не найден по сохранённому пути и хешу."),
            })
    for upload in uploads:
        upload_id = upload.get("id")
        manifest_status = upload.get("source_manifest_status", "absent")
        manifest = _manifest_records(upload)
        if manifest_status == "invalid":
            discrepancies.append({
                "type": "invalid_source_manifest",
                "upload_id": upload_id,
                "message": (
                    "Сохранённый манифест источников имеет неверный формат; "
                    "связь с файлами не подтверждена."),
            })
        seen_ids = {}
        for entry in manifest:
            source_id = _text(entry.get("source_id"))
            seen_ids.setdefault(source_id, []).append(entry)
            errors = []
            relative_path = _text(entry.get("relative_path"))
            digest = _text(entry.get("sha256")).lower()
            if not _valid_relative_path(relative_path):
                errors.append("relative_path")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                errors.append("sha256")
            elif source_id != _source_identity(relative_path, digest):
                errors.append("source_id")
            if not _text(entry.get("account")):
                errors.append("account")
            if "period" not in entry:
                errors.append("period")
            if errors:
                discrepancies.append({
                    "type": "invalid_source_manifest_record",
                    "upload_id": upload_id,
                    "source_id": source_id or None,
                    "fields": errors,
                    "message": (
                        "Запись в манифесте источников не прошла проверку "
                        "идентичности."),
                })
        for source_id, records in seen_ids.items():
            if source_id and len(records) > 1:
                discrepancies.append({
                    "type": "duplicate_source_id",
                    "upload_id": upload_id,
                    "source_id": source_id,
                    "message": (
                        "В манифесте источников повторяется идентификатор "
                        "файла."),
                })
        linked = [
            item for item in files
            if upload_id is not None and item.get("upload_id") == upload_id
            and item.get("association_status") == "explicit"
        ]
        actual_accounts = sorted({
            _text(item.get("account"))
            for item in linked
            if item.get("kind") == "osv" and item.get("status") == "parsed"
            and item.get("account")
        })
        declared_accounts = sorted(set(upload.get("accounts_loaded") or []))
        if not upload.get("accounts_loaded_valid", True):
            discrepancies.append({
                "type": "invalid_accounts_loaded",
                "upload_id": upload_id,
                "message": "Метаданные загруженных счетов имеют неверный формат.",
            })
        if not linked and not manifest and manifest_status == "absent":
            discrepancies.append({
                "type": "upload_without_reliably_matched_files",
                "upload_id": upload_id,
                "message": (
                    "Для записи загрузки нет сохранённого манифеста источников; "
                    "метаданные не считаются доказательством разобранной ОСВ."),
            })
        elif manifest:
            matched_entries = {
                (
                    _text(item.get("source_id")),
                    _text(item.get("relative_path")),
                    _text(item.get("sha256")).lower(),
                )
                for item in files
                if (
                    item.get("upload_id") == upload_id
                    or item.get("association_expected_upload_id") == upload_id
                )
                and item.get("association_status") in (
                    "explicit", "mismatch", "manifest_missing")
            }
            for entry in manifest:
                identity = (
                    _text(entry.get("source_id")),
                    _text(entry.get("relative_path")),
                    _text(entry.get("sha256")).lower(),
                )
                if identity not in matched_entries:
                    discrepancies.append({
                        "type": "source_manifest_file_missing",
                        "upload_id": upload_id,
                        "source_id": entry.get("source_id"),
                        "relative_path": entry.get("relative_path"),
                        "message": (
                            "Принятый файл из манифеста источников не найден среди "
                            "прочитанных файлов."),
                    })
        if not linked:
            # No trustworthy account/period evidence exists for this Upload.
            # In particular, do not compare its metadata to a guessed source.
            continue
        missing = sorted(set(declared_accounts) - set(actual_accounts))
        additional = sorted(set(actual_accounts) - set(declared_accounts))
        if missing:
            discrepancies.append({
                "type": "metadata_accounts_missing_in_source",
                "upload_id": upload_id,
                "accounts": missing,
                "message": (
                    "Счета заявлены в метаданных загрузки, но не найдены в "
                    "разобранной ОСВ."),
            })
        if additional:
            discrepancies.append({
                "type": "source_accounts_missing_in_metadata",
                "upload_id": upload_id,
                "accounts": additional,
                "message": (
                    "В разобранной ОСВ есть счета, которых нет в метаданных "
                    "загрузки."),
            })

        files_count = upload.get("files_count")
        if files_count is not None:
            try:
                expected = int(files_count)
            except (TypeError, ValueError):
                expected = None
            if expected is not None and expected != len(linked):
                discrepancies.append({
                    "type": "metadata_files_count_mismatch",
                    "upload_id": upload_id,
                    "declared": expected,
                    "parsed": len(linked),
                    "message": (
                        "Количество файлов не совпадает с количеством принятых "
                        "файлов ОСВ; "
                        "ZIP и план счетов могут быть записаны как отдельные файлы."),
                })

        label_start, label_end = _period_bounds(upload.get("period_label"))
        actual_intervals = [
            (
                _iso_date(item.get("period_start") or item.get("report_start")),
                _iso_date(item.get("period_end") or item.get("report_end")),
            )
            for item in linked
            if item.get("kind") == "osv" and item.get("status") == "parsed"
        ]
        actual_intervals = [(start, end) for start, end in actual_intervals
                            if start and end]
        if upload.get("period_label") and not (label_start and label_end):
            discrepancies.append({
                "type": "metadata_period_unparseable",
                "upload_id": upload_id,
                "period_label": upload.get("period_label"),
                "message": "Период загрузки нельзя надёжно разобрать.",
            })
        elif label_start and label_end and actual_intervals:
            actual_start = min(start for start, _ in actual_intervals)
            actual_end = max(end for _, end in actual_intervals)
            if (label_start, label_end) != (actual_start, actual_end):
                discrepancies.append({
                    "type": "metadata_period_mismatch",
                    "upload_id": upload_id,
                    "declared": {
                        "start": label_start.isoformat(),
                        "end": label_end.isoformat(),
                    },
                    "actual": {
                        "start": actual_start.isoformat(),
                        "end": actual_end.isoformat(),
                    },
                    "message": (
                        "Метка периода загрузки не совпадает с фактическими "
                        "границами периода разобранной ОСВ."),
                })
    return discrepancies


def _finding_file_name(item):
    return _text(item.get("source_file") or item.get("file"))


def _bounded_file_names(items):
    names = sorted({
        _finding_file_name(item) for item in items
        if _finding_file_name(item)
    })
    return names[:_MAX_FINDING_FILES], max(0, len(names) - _MAX_FINDING_FILES)


def _account_overlap_findings(account, account_files):
    """Aggregate duplicate/overlap evidence in O(n log n) per account.

    Historical imports commonly retain several cumulative copies of the same
    account period.  Pairwise evidence would make both CPU and JSON size grow
    quadratically.  Exact ranges are grouped first; distinct ranges are then
    swept by start date with a min-heap of active ends.  The response contains
    bounded file samples and aggregate pair counts, while preserving enough
    dates to explain the first overlap.
    """

    groups = {}
    for item in account_files:
        if item.get("kind") != "osv" or item.get("status") != "parsed":
            continue
        start, end = _period_from_values(
            item.get("period"), item.get("period_start"), item.get("period_end"))
        if not (start and end):
            continue
        groups.setdefault((start, end), []).append(item)

    duplicate_groups = []
    for (start, end), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        duplicate_groups.append((start, end, items))
    duplicates = []
    if duplicate_groups:
        duplicate_items = [
            item for _, _, items in duplicate_groups for item in items]
        sample, additional = _bounded_file_names(duplicate_items)
        periods = [
            {"start": start.isoformat(), "end": end.isoformat()}
            for start, end, _ in duplicate_groups[:_MAX_FINDING_FILES]
        ]
        duplicates.append({
            "type": "duplicate_period",
            "account": _text(account),
            "start": duplicate_groups[0][0].isoformat(),
            "end": duplicate_groups[0][1].isoformat(),
            "files": sample,
            "file_count": len(duplicate_items),
            "additional_files": additional,
            "period_count": len(duplicate_groups),
            "periods": periods,
            "additional_periods": max(
                0, len(duplicate_groups) - len(periods)),
            "truncated": bool(additional or len(duplicate_groups) > len(periods)),
        })

    # Each unique interval contributes at most one heap entry regardless of
    # how many cumulative files share it.
    unique = sorted(
        (start, end, items)
        for (start, end), items in groups.items())
    active = []
    active_count = 0
    overlap_pairs = 0
    sample_overlap = None
    for start, end, items in unique:
        while active and active[0][0] < start:
            _, _, count = heapq.heappop(active)
            active_count -= count
        if active_count:
            overlap_pairs += len(items) * active_count
            if sample_overlap is None:
                active_end, active_start, _ = active[0]
                sample_overlap = {
                    "start": max(start, active_start).isoformat(),
                    "end": min(end, active_end).isoformat(),
                    "files": _bounded_file_names(
                        [items[0], groups[(active_start, active_end)][0]])[0],
                }
        heapq.heappush(active, (end, start, len(items)))
        active_count += len(items)

    overlaps = []
    if overlap_pairs and sample_overlap:
        all_items = [item for values in groups.values() for item in values]
        sample, additional = _bounded_file_names(all_items)
        overlaps.append({
            "type": "overlapping_period",
            "account": _text(account),
            "start": sample_overlap["start"],
            "end": sample_overlap["end"],
            "files": sample_overlap["files"],
            "file_count": len(all_items),
            "additional_files": additional,
            "pair_count": overlap_pairs,
            "truncated": bool(additional),
        })
    return duplicates, overlaps


def build_source_passport(source, uploads=()):
    """Build a JSON-safe company source passport.

    ``uploads`` may contain ORM ``Upload`` instances or plain dictionaries,
    which keeps this function isolated and straightforward to unit test.
    """

    if not isinstance(source, dict):
        raise ValueError("source must be a dictionary")
    upload_records = [_upload_record(item) for item in uploads or []]
    files = []
    for item in _manifest(source):
        record = _source_file_record(item)
        batch, batch_at = _batch_info(item.get("path") or item.get("batch"))
        record["batch"] = record.get("batch") or batch
        item_copy = dict(item)
        item_copy["batch_at"] = batch_at
        item_copy["batch"] = record.get("batch")
        item_copy["source_file"] = record.get("source_file")
        files.append(item_copy)

    # A persisted/source fixture may not carry the service manifest.  The
    # parsed balance report list still identifies each accepted ОСВ file, so
    # reconstruct a minimal manifest instead of silently losing duplicate
    # evidence.  The service manifest wins whenever it is available.
    if not files:
        for account, raw in (source.get("accounts") or {}).items():
            raw = _mapping(raw)
            for report in raw.get("balance_reports") or []:
                report = _mapping(report)
                source_file = _text(report.get("source_file"))
                if not source_file:
                    continue
                files.append({
                    "source_file": source_file,
                    "kind": "osv",
                    "status": "parsed",
                    "account": _text(account),
                    "period": _text(report.get("period")),
                    "period_start": report.get("report_start"),
                    "period_end": report.get("report_end"),
                    "check_ok": raw.get("check_ok"),
                })

    # A persisted manifest is also evidence that a particular accepted file
    # was expected.  If the current source walk cannot find that exact
    # identity, retain a non-calculating row so the UI can explain the
    # missing file rather than silently dropping it.
    actual_identities = {
        (
            _text(item.get("source_id")),
            _text(item.get("relative_path")),
            _text(item.get("sha256")).lower(),
        )
        for item in files
    }
    for upload in upload_records:
        for manifest in _manifest_records(upload):
            relative_path = _text(manifest.get("relative_path"))
            digest = _text(manifest.get("sha256")).lower()
            identity = (
                _text(manifest.get("source_id")),
                relative_path,
                digest,
            )
            if identity in actual_identities:
                continue
            period = _period_record(manifest)
            files.append({
                "source_file": os.path.basename(relative_path) or relative_path,
                "relative_path": relative_path or None,
                "sha256": digest or None,
                "source_id": _text(manifest.get("source_id")) or None,
                "kind": "osv",
                "status": "missing",
                "account": _text(manifest.get("account")) or None,
                "period": period["period"],
                "report_start": period["start"],
                "report_end": period["end"],
                "upload_id": upload.get("id"),
                "_expected_upload_id": upload.get("id"),
                "is_manifest_placeholder": True,
            })

    # If a caller supplies only a manually built source snapshot, the account
    # list is still useful.  File-level duplicate evidence is available when
    # the service's manifest is present.
    _associate_files(files, upload_records)

    accounts_raw = source.get("accounts") or {}
    accounts = {}
    for account in sorted({_text(value) for value in accounts_raw}):
        account_files = [
            item for item in files
            if _text(item.get("account")) == account
        ]
        accounts[account] = _account_passport(
            account, accounts_raw.get(account), account_files)

    duplicates = []
    overlaps = []
    files_by_account = {}
    for item in files:
        if item.get("kind") == "osv" and item.get("account"):
            if item.get("status") != "parsed":
                continue
            files_by_account.setdefault(_text(item["account"]), []).append(item)
    for account, account_files in sorted(files_by_account.items()):
        account_duplicates, account_overlaps = _account_overlap_findings(
            account, account_files)
        duplicates.extend(account_duplicates)
        overlaps.extend(account_overlaps)
        target = accounts.get(account)
        if target is not None:
            target["duplicates"].extend(copy.deepcopy(account_duplicates))
            target["overlaps"].extend(copy.deepcopy(account_overlaps))

    actual_intervals = []
    declared_turnover_intervals = []
    observed_turnover_dates = set()
    for item in accounts.values():
        period = item["actual_period"]
        start, end = _iso_date(period.get("start")), _iso_date(period.get("end"))
        if start and end:
            actual_intervals.append((start, end))
        declared_turnover_intervals.extend(
            (
                _iso_date(interval.get("start")),
                _iso_date(interval.get("end")),
            )
            for interval in item.get("turnover_coverage", {}).get(
                "declared_intervals", [])
            if _iso_date(interval.get("start")) and
            _iso_date(interval.get("end"))
        )
        observed_turnover_dates.update(
            _iso_date(value)
            for value in item.get("turnover_coverage", {}).get(
                "observed_dates", [])
            if _iso_date(value)
        )
    merged_actual = _merge_intervals(actual_intervals)
    merged_turnover = _merge_intervals(declared_turnover_intervals)
    turnover_gaps = _between_coverage_gaps(merged_turnover)
    weighted_expected_days = 0
    weighted_covered_days = 0
    account_coverage_incomplete = False
    for account in accounts.values():
        coverage = account.get("turnover_coverage") or {}
        span_days, declared_days = _account_coverage_weight(coverage)
        weighted_expected_days += span_days
        weighted_covered_days += declared_days
        if coverage.get("status") != "complete":
            account_coverage_incomplete = True
    expected_turnover_days = weighted_expected_days
    covered_turnover_days = weighted_covered_days
    source_start = min((start for start, _ in merged_actual), default=None)
    source_end = max((end for _, end in merged_actual), default=None)
    if source_start is None:
        observed = [
            _iso_date(item.get("date"))
            for account in accounts_raw.values()
            for item in (_mapping(row) for row in _mapping(account).get("daily") or [])
        ]
        observed = [value for value in observed if value]
        source_start, source_end = min(observed, default=None), max(observed, default=None)

    annual_versions = [
        _source_file_record(item) for item in files
        if item.get("kind") in ("annual_analytics", "annual")
        and item.get("status") not in ("failure", "unreadable")
    ]
    failures = []
    for account, value in accounts.items():
        failures.extend({
            "type": "account_source_warning",
            "account": account,
            "message": message,
        } for message in value.get("failures") or [])

    metadata_discrepancies = _metadata_discrepancies(upload_records, files)
    warnings = [
        "Дата загрузки и фактические границы периода показываются раздельно.",
    ]
    warnings.extend(_text(value) for value in source.get("warnings") or [])
    if annual_versions:
        warnings.append(
            "Годовые книги распознаны отдельно и не включены в ОСВ по счетам.")
    if duplicates:
        warnings.append("Для одного счёта найдены дублирующие периоды; выбран последний файл.")
    if overlaps:
        warnings.append("Для одного счёта найдены пересекающиеся периоды; обороты имеют порядок источников.")

    public_uploads = []
    for upload in upload_records:
        public_uploads.append({
            key: value for key, value in upload.items()
            if not key.startswith("_")
        })

    upload_times = [
        (_iso_datetime(item.get("uploaded_at")), item.get("uploaded_at"))
        for item in public_uploads if _iso_datetime(item.get("uploaded_at"))
    ]
    daily_times = source.get("source_daily_uploaded_at")
    if not isinstance(daily_times, (list, tuple)):
        daily_times = [daily_times] if daily_times else []
    upload_times.extend(
        (_iso_datetime(value), value)
        for value in daily_times if _iso_datetime(value))
    latest_upload_at = None
    if upload_times:
        latest_upload_at = max(upload_times, key=lambda value: value[0])[0].isoformat()
    has_dated_only_account = any(
        item.get("turnover_coverage", {}).get("coverage_basis")
        == "dated_turnover_rows"
        for item in accounts.values())
    if failures:
        passport_status = "failed"
    elif not accounts:
        passport_status = "empty"
    elif (metadata_discrepancies or duplicates or overlaps or turnover_gaps
          or has_dated_only_account or account_coverage_incomplete):
        passport_status = "partial"
    else:
        passport_status = "complete"
    return {
        "version": 1,
        "status": passport_status,
        "actual_period": {
            "start": source_start.isoformat() if source_start else None,
            "end": source_end.isoformat() if source_end else None,
        },
        "latest_upload_at": latest_upload_at,
        "uploads": public_uploads,
        "available_accounts": sorted(accounts),
        "accounts": accounts,
        "turnover_coverage": {
            "declared_intervals": [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in merged_turnover
            ],
            "observed_dates": [
                value.isoformat() for value in sorted(observed_turnover_dates)
            ],
            "covered_days": covered_turnover_days,
            "expected_days": expected_turnover_days,
            "coverage_ratio": (
                round(covered_turnover_days / expected_turnover_days, 6)
                if expected_turnover_days else None),
            "observed_days": len(observed_turnover_dates),
            "coverage_basis": (
                "account_day_weighted" if expected_turnover_days else (
                    "dated_turnover_rows" if observed_turnover_dates else "none")),
            "gaps": turnover_gaps,
            "status": (
                "complete"
                if merged_turnover and not turnover_gaps
                and not account_coverage_incomplete
                else "partial" if merged_turnover else "no_declared_period"
            ),
        },
        "files": [_source_file_record(item) for item in files],
        "annual_versions": annual_versions,
        "duplicates": duplicates,
        "overlaps": overlaps,
        "metadata_discrepancies": metadata_discrepancies,
        "failures": failures,
        "warnings": list(dict.fromkeys(warnings)),
    }


def enrich_source_passport(source, uploads=()):
    """Attach and return a source snapshot with its read-only passport."""

    if not isinstance(source, dict):
        raise ValueError("source must be a dictionary")
    source["source_passport"] = build_source_passport(source, uploads)
    return source


def _period_coverage(account, start, end):
    coverage = _mapping(account.get("turnover_coverage"))
    dates = [
        parsed for parsed in (_iso_date(item) for item in coverage.get("observed_dates") or [])
        if parsed and (start is None or parsed >= start)
        and (end is None or parsed <= end)
    ]
    intervals = []
    for item in coverage.get("declared_intervals") or []:
        item = _mapping(item)
        point_start, point_end = _iso_date(item.get("start")), _iso_date(item.get("end"))
        if not point_start or not point_end:
            continue
        point_start = max(point_start, start) if start else point_start
        point_end = min(point_end, end) if end else point_end
        if point_start <= point_end:
            intervals.append((point_start, point_end))
    intervals = _merge_intervals(intervals)
    if start and end:
        selected_start, selected_end = start, end
        expected = (end - start).days + 1
        covered_intervals = _merge_intervals([
            (max(start, point_start), min(end, point_end))
            for point_start, point_end in intervals
            if point_start <= end and point_end >= start
        ])
        covered = sum(
            (point_end - point_start).days + 1
            for point_start, point_end in covered_intervals)
        gaps = _coverage_gaps(selected_start, selected_end, intervals)
    else:
        expected = sum((point_end - point_start).days + 1
                       for point_start, point_end in intervals)
        covered = expected
        gaps = _between_coverage_gaps(intervals)
    if intervals:
        status = "complete" if not gaps else "partial"
        basis = "report_period"
    elif dates:
        status = "observed_without_declared_period"
        basis = "dated_turnover_rows"
    else:
        status = "no_declared_period"
        basis = "none"
    return {
        "declared_intervals": [
            {"start": point_start.isoformat(), "end": point_end.isoformat()}
            for point_start, point_end in intervals
        ],
        "observed_dates": [item.isoformat() for item in sorted(set(dates))],
        "covered_days": covered,
        "expected_days": expected,
        "observed_days": len(set(dates)),
        "coverage_basis": basis,
        "coverage_ratio": round(covered / expected, 6) if expected else None,
        "status": status,
        "gaps": gaps,
    }


def _period_balances(account, start, end, endpoint):
    exact = _mapping(account.get("exact_balances"))
    all_balances = [
        _mapping(item) for item in exact.get("all") or []
        if _mapping(item).get("date")
    ]
    selected = [
        item for item in all_balances
        if _iso_date(item.get("date"))
        and (start is None or _iso_date(item.get("date")) >= start)
        and (end is None or _iso_date(item.get("date")) <= end)
    ]
    closing = [
        item for item in selected
        if item.get("date") == (endpoint.isoformat() if endpoint else None)
        and item.get("kind") in ("report_closing", "daily_observation")
    ]
    opening = [
        item for item in selected
        if item.get("date") == (start.isoformat() if start else None)
        and item.get("kind") in ("report_opening", "daily_observation")
    ]
    def normalise(item, role):
        if not item:
            return None
        result = copy.deepcopy(item)
        result.pop("opening_debit", None)
        result.pop("opening_credit", None)
        if item.get("kind") == "daily_observation":
            if role == "opening":
                debit = item.get("opening_debit")
                credit = item.get("opening_credit")
                if debit is None and credit is None:
                    return None
                result.pop("debit", None)
                result.pop("credit", None)
                result["opening_debit"] = debit
                result["opening_credit"] = credit
                result["kind"] = "daily_opening"
            else:
                if item.get("debit") is None and item.get("credit") is None:
                    return None
                result["debit"] = item.get("debit")
                result["credit"] = item.get("credit")
                result["kind"] = "daily_closing"
        elif role == "opening":
            result["kind"] = "report_opening"
        else:
            result["kind"] = "report_closing"
        return result
    opening_value = None
    for item in reversed(opening):
        opening_value = normalise(item, "opening")
        if opening_value:
            break
    closing_value = None
    for item in reversed(closing):
        closing_value = normalise(item, "closing")
        if closing_value:
            break
    return {
        "opening": opening_value,
        "closing": closing_value,
        "observations": selected,
        "available": bool(opening_value or closing_value),
        "reason": None if (opening_value or closing_value)
        else "Нет точного сальдо на границе выбранного периода.",
    }


def build_period_passport(source, payload):
    """Build the UI-facing provenance view for a selected KPI period.

    It is safe to call this after ``build_key_indicators``.  ``payload`` is
    read only; values and formulas are copied only as status explanations.
    """

    if not isinstance(source, dict):
        raise ValueError("source must be a dictionary")
    passport = source.get("source_passport")
    if not isinstance(passport, dict):
        passport = build_source_passport(source)
    period = _mapping(payload.get("period") if isinstance(payload, dict) else None)
    start, end = _period_from_values(
        period, period.get("start"), period.get("end"))
    if end is None:
        end = start

    account_views = {}
    for account, item in (passport.get("accounts") or {}).items():
        account_views[account] = {
            "account": account,
            "available": True,
            "actual_period": copy.deepcopy(item.get("actual_period") or {}),
            "balances": _period_balances(item, start, end, end),
            "turnover_coverage": _period_coverage(item, start, end),
        }

    cards = []
    source_cards = payload.get("cards") if isinstance(payload, dict) else []
    for card in source_cards or []:
        card = _mapping(card)
        card_id = _text(card.get("id"))
        required = list(_CARD_ACCOUNTS.get(card_id, ()))
        missing = [account for account in required
                   if account not in account_views]
        reason = card.get("reason")
        if not reason and card.get("value") is None:
            reason = (
                "Счета не найдены в разобранной ОСВ: " + ", ".join(missing)
                if missing else "Для карточки нет достаточных фактов на выбранный период.")
        cards.append({
            "id": card_id,
            "title": _text(card.get("title")),
            "status": _text(card.get("status")) or (
                "unavailable" if card.get("value") is None else "neutral"),
            "available": card.get("value") is not None,
            "required_accounts": required,
            "missing_accounts": missing,
            "reason": _text(reason) or None,
        })

    period_start = start.isoformat() if start else None
    period_end = end.isoformat() if end else None
    files = []
    for item in passport.get("files") or []:
        item_start = _iso_date(item.get("period_start"))
        item_end = _iso_date(item.get("period_end"))
        if (not start or not item_end or item_end >= start) and (
                not end or not item_start or item_start <= end):
            files.append(copy.deepcopy(item))

    selected_intervals = []
    selected_observed = set()
    for item in account_views.values():
        coverage = item["turnover_coverage"]
        selected_intervals.extend(
            (_iso_date(interval.get("start")), _iso_date(interval.get("end")))
            for interval in coverage.get("declared_intervals") or []
            if _iso_date(interval.get("start")) and
            _iso_date(interval.get("end"))
        )
        selected_observed.update(
            _iso_date(value) for value in coverage.get("observed_dates") or []
            if _iso_date(value)
        )
    selected_intervals = _merge_intervals(selected_intervals)
    selected_account_incomplete = any(
        item["turnover_coverage"].get("status") != "complete"
        for item in account_views.values())
    if start and end:
        selected_expected = sum(
            item["turnover_coverage"].get("expected_days") or 0
            for item in account_views.values())
        selected_covered = sum(
            item["turnover_coverage"].get("covered_days") or 0
            for item in account_views.values())
        if not account_views:
            selected_expected = (end - start).days + 1
        selected_gaps = _coverage_gaps(start, end, selected_intervals)
    else:
        weighted = [
            _account_coverage_weight(item["turnover_coverage"])
            for item in account_views.values()]
        selected_expected = sum(item[0] for item in weighted)
        selected_covered = sum(item[1] for item in weighted)
        selected_gaps = _between_coverage_gaps(selected_intervals)
    selected_basis = (
        "account_day_weighted" if account_views and selected_expected else (
            "report_period" if selected_intervals else (
                "dated_turnover_rows" if selected_observed else "none")))
    period_status = (
        "failed" if passport.get("status") == "failed"
        else "empty" if passport.get("status") == "empty"
        else "partial" if selected_gaps or selected_account_incomplete
        else "complete")

    return {
        "version": 1,
        # Keep company-level freshness/provenance beside the period detail.
        # `period_status` is the selected-window assessment; `status` remains
        # the status of the complete source passport.
        "status": passport.get("status"),
        "period_status": period_status,
        "actual_period": copy.deepcopy(passport.get("actual_period") or {}),
        "latest_upload_at": passport.get("latest_upload_at"),
        "uploads": copy.deepcopy(passport.get("uploads") or []),
        "period": {
            "start": period_start,
            "end": period_end,
            "label": period.get("label"),
        },
        "available_accounts": list(passport.get("available_accounts") or []),
        "accounts": account_views,
        "turnover_coverage": {
            "declared_intervals": [
                {"start": point_start.isoformat(), "end": point_end.isoformat()}
                for point_start, point_end in selected_intervals
            ],
            "observed_dates": [
                value.isoformat() for value in sorted(selected_observed)
            ],
            "covered_days": selected_covered,
            "expected_days": selected_expected,
            "coverage_ratio": (
                round(selected_covered / selected_expected, 6)
                if selected_expected else None),
            "observed_days": len(selected_observed),
            "coverage_basis": selected_basis,
            "status": (
                "complete" if selected_intervals and not selected_gaps
                and not selected_account_incomplete
                else "partial" if selected_gaps or selected_account_incomplete
                else "no_declared_period"),
            "gaps": selected_gaps,
        },
        "cards": cards,
        "files": files,
        "duplicates": copy.deepcopy(passport.get("duplicates") or []),
        "overlaps": copy.deepcopy(passport.get("overlaps") or []),
        "metadata_discrepancies": copy.deepcopy(
            passport.get("metadata_discrepancies") or []),
        "failures": copy.deepcopy(passport.get("failures") or []),
        "annual_versions": copy.deepcopy(passport.get("annual_versions") or []),
        "warnings": copy.deepcopy(passport.get("warnings") or []),
    }


__all__ = [
    "build_period_passport",
    "build_source_passport",
    "enrich_source_passport",
]