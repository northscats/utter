# -*- coding: utf-8 -*-
"""Read-only provenance passport for accepted annual source data.

The annual KPI calculation deliberately owns all metric and rating behaviour.
This module only describes which accepted versions were used, when those
versions were uploaded/confirmed/activated, and what historical state exists
around them.  In particular, an inactive version is history, not an active
year conflict, and a repeated file hash is evidence about uploads rather than
a reason to change a metric.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from database import HistoricalImport, HistoricalYearVersion


NO_ACCEPTED_DATA_REASON = "Нет принятых активных годовых данных"


def _iso(value):
    """Serialize a database timestamp without changing its timezone semantics."""
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "tzinfo"):
        return value.isoformat()
    if value.tzinfo is None:
        return value.isoformat()
    return value.isoformat()


def _period_label(year_from, year_to):
    if year_from is None or year_to is None:
        return None
    return str(year_from) if year_from == year_to else f"{year_from}–{year_to}"


def _factual_period_end(year):
    """Annual source facts are point-in-time facts at the calendar year end."""
    return date(int(year), 12, 31).isoformat()


def _version_record(version, *, accepted=False, history_role=None):
    """Return a stable, UI-facing record for one version.

    ``uploaded_at`` is the import creation time.  It is intentionally not
    substituted with ``confirmed_at`` or ``activated_at``: those timestamps
    answer different audit questions.
    """
    historical_import = version.historical_import
    record = {
        "id": version.id,
        "year": version.year,
        "version": str(version.version_no),
        "version_no": version.version_no,
        "import_id": historical_import.id,
        "filename": historical_import.original_filename,
        "file_hash": historical_import.file_hash,
        "import_status": historical_import.status,
        "is_active": bool(version.is_active),
        "accepted": bool(accepted),
        "uploaded_at": _iso(historical_import.created_at),
        "confirmed_at": _iso(historical_import.confirmed_at),
        "activated_at": _iso(version.activated_at),
        # This is a factual boundary, not a publication or upload date.
        "factual_period_end": _factual_period_end(version.year),
    }
    if history_role is not None:
        record["history_role"] = history_role
    return record


def _import_record(historical_import, versions_by_import):
    """Describe an upload, including imports that produced no version."""
    versions = versions_by_import.get(historical_import.id, ())
    return {
        "id": historical_import.id,
        "filename": historical_import.original_filename,
        "file_hash": historical_import.file_hash,
        "status": historical_import.status,
        "uploaded_at": _iso(historical_import.created_at),
        "confirmed_at": _iso(historical_import.confirmed_at),
        "version_ids": [version.id for version in versions],
        "years": sorted({version.year for version in versions}),
    }


def _duplicate_uploads(imports, versions_by_import):
    """Group repeated non-empty hashes, without treating them as conflicts."""
    by_hash = defaultdict(list)
    for historical_import in imports:
        file_hash = (historical_import.file_hash or "").strip()
        if file_hash:
            by_hash[file_hash].append(historical_import)

    duplicates = []
    for file_hash, matching in sorted(by_hash.items()):
        if len(matching) < 2:
            continue
        records = [
            _import_record(historical_import, versions_by_import)
            for historical_import in sorted(
                matching,
                key=lambda item: (
                    item.created_at is None,
                    _iso(item.created_at) or "",
                    item.id,
                ),
            )
        ]
        duplicates.append({
            "file_hash": file_hash,
            "count": len(records),
            "imports": records,
            "import_ids": [record["id"] for record in records],
            "years": sorted({
                year for record in records for year in record["years"]
            }),
        })
    return duplicates


def _active_conflicts(history_versions):
    """Find genuinely simultaneous active versions for one company/year.

    Normal replacement history has one active row and one or more inactive
    rows.  It is intentionally not reported here as a conflict.  The database
    has a partial unique index for this invariant, but keeping the diagnostic
    makes a legacy or manually repaired database explicit rather than silently
    choosing a row.
    """
    by_year = defaultdict(list)
    for version in history_versions:
        if version.is_active:
            by_year[version.year].append(version)

    conflicts = []
    for year, versions in sorted(by_year.items()):
        if len(versions) < 2:
            continue
        records = [
            _version_record(
                version,
                accepted=version.historical_import.status == "active",
                history_role="active_conflict",
            )
            for version in sorted(
                versions, key=lambda item: (item.version_no, item.id))
        ]
        conflicts.append({
            "year": year,
            "count": len(records),
            "versions": records,
        })
    return conflicts


def _history_records(history_versions, accepted_ids):
    """Return inactive versions as history, never as alternative facts."""
    records = []
    for version in history_versions:
        if version.id in accepted_ids:
            continue
        records.append(_version_record(
            version,
            accepted=False,
            history_role=(
                "inactive_version_history"
                if not version.is_active else "not_accepted_active_version"
            ),
        ))
    return sorted(
        records,
        key=lambda item: (item["year"], item["version_no"], item["id"]),
    )


def build_annual_source_passport(
    company,
    *,
    versions=None,
    year_from=None,
    year_to=None,
    requested_year=None,
    cards=None,
):
    """Build the annual source passport without mutating the database.

    ``versions`` may be supplied by the KPI builder's already tenant-scoped
    accepted-version query.  The history query is intentionally separate and
    repeats the company predicate on both sides of the join, so provenance
    cannot disclose another tenant's uploads even if a legacy row has
    inconsistent foreign keys.

    ``cards`` is optional to keep this module independent from metric
    calculations.  When supplied, the passport contains a reason summary for
    each card without altering the card objects.
    """
    if versions is None:
        versions = HistoricalYearVersion.query.join(HistoricalImport).filter(
            HistoricalYearVersion.company_id == company.id,
            HistoricalYearVersion.is_active.is_(True),
            HistoricalImport.company_id == company.id,
            HistoricalImport.status == "active",
        ).order_by(
            HistoricalYearVersion.year,
            HistoricalYearVersion.version_no,
        ).all()
    else:
        versions = list(versions)

    # This is deliberately not inferred from the accepted list: the passport
    # must retain legitimate inactive version history and failed/staged uploads.
    imports = HistoricalImport.query.filter(
        HistoricalImport.company_id == company.id,
    ).order_by(
        HistoricalImport.created_at,
        HistoricalImport.id,
    ).all()
    history_versions = HistoricalYearVersion.query.join(
        HistoricalImport,
    ).filter(
        HistoricalYearVersion.company_id == company.id,
        HistoricalImport.company_id == company.id,
    ).order_by(
        HistoricalYearVersion.year,
        HistoricalYearVersion.version_no,
        HistoricalYearVersion.id,
    ).all()

    # If a caller supplies versions, retain the same accepted set but defend
    # against an accidental cross-tenant object before serialising it.
    accepted_ids = {
        version.id for version in versions
        if version.company_id == company.id
        and version.historical_import.company_id == company.id
        and version.is_active
        and version.historical_import.status == "active"
    }
    accepted_versions = [
        version for version in history_versions if version.id in accepted_ids
    ]
    accepted_versions.sort(key=lambda item: (item.year, item.version_no, item.id))
    accepted_years = [version.year for version in accepted_versions]
    accepted_year_set = set(accepted_years)

    if year_from is not None and year_to is not None:
        selected_years = list(range(year_from, year_to + 1))
    elif requested_year is not None:
        selected_years = [requested_year]
    elif accepted_years:
        selected_years = [accepted_years[-1]]
    else:
        selected_years = []
    missing_years = [
        year for year in selected_years if year not in accepted_year_set
    ]
    selected_complete = bool(selected_years) and not missing_years
    has_accepted_data = bool(accepted_versions)

    versions_by_import = defaultdict(list)
    for version in history_versions:
        versions_by_import[version.import_id].append(version)
    duplicate_uploads = _duplicate_uploads(imports, versions_by_import)

    accepted_records = [
        _version_record(version, accepted=True)
        for version in accepted_versions
    ]
    inactive_version_history = _history_records(
        history_versions, accepted_ids)

    card_reason_summary = []
    card_missing_reasons = {}
    for card in cards or ():
        reason = card.get("missing_reason")
        item = {
            "id": card.get("id"),
            "title": card.get("title"),
            "available": card.get("value") is not None,
            "missing_reason": reason,
        }
        card_reason_summary.append(item)
        card_missing_reasons[card.get("id")] = reason

    selected_range = {
        "year_from": year_from if year_from is not None else (
            selected_years[0] if selected_years else None),
        "year_to": year_to if year_to is not None else (
            selected_years[-1] if selected_years else None),
        "period_label": _period_label(
            year_from if year_from is not None else (
                selected_years[0] if selected_years else None),
            year_to if year_to is not None else (
                selected_years[-1] if selected_years else None),
        ),
        "requested_year": requested_year,
        "years": selected_years,
        "missing_years": missing_years,
        "complete": selected_complete,
    }

    latest_upload = max(
        (record["uploaded_at"] for record in accepted_records
         if record["uploaded_at"] is not None),
        default=None,
    )
    latest_confirmation = max(
        (record["confirmed_at"] for record in accepted_records
         if record["confirmed_at"] is not None),
        default=None,
    )
    latest_activation = max(
        (record["activated_at"] for record in accepted_records
         if record["activated_at"] is not None),
        default=None,
    )
    factual_as_of = (
        _factual_period_end(selected_range["year_to"])
        if selected_range["year_to"] in accepted_year_set else None
    )

    warnings = []
    if not has_accepted_data:
        warnings.append(NO_ACCEPTED_DATA_REASON)
    if missing_years:
        warnings.append(
            "В выбранном диапазоне нет принятых данных за годы: "
            + ", ".join(str(year) for year in missing_years)
        )
    conflicts = _active_conflicts(history_versions)
    if conflicts:
        warnings.append("Обнаружен конфликт активных версий одного года.")
    if duplicate_uploads:
        warnings.append("Обнаружены повторные загрузки с одинаковым хешем.")

    return {
        "status": "accepted" if has_accepted_data else "no_accepted_data",
        "has_accepted_data": has_accepted_data,
        "no_accepted_data": not has_accepted_data,
        "reason": None if has_accepted_data else NO_ACCEPTED_DATA_REASON,
        "accepted_years": accepted_years,
        "selected_range": selected_range,
        "factual_as_of": factual_as_of,
        "date_semantics": {
            "uploaded_at": "Время создания записи загрузки файла",
            "confirmed_at": "Время подтверждения импорта",
            "activated_at": "Время активации версии года",
            "factual_period_end": (
                "Фактическая граница годового среза: 31 декабря указанного года"
            ),
        },
        "latest_accepted": {
            "uploaded_at": latest_upload,
            "confirmed_at": latest_confirmation,
            "activated_at": latest_activation,
        },
        "accepted_versions": accepted_records,
        "versions": accepted_records,
        "inactive_version_history": inactive_version_history,
        "duplicate_uploads": duplicate_uploads,
        "active_conflicts": conflicts,
        "card_reason_summary": card_reason_summary,
        "card_missing_reasons": card_missing_reasons,
        "warnings": warnings,
    }


__all__ = [
    "NO_ACCEPTED_DATA_REASON",
    "build_annual_source_passport",
]