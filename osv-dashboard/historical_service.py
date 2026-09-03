# -*- coding: utf-8 -*-
"""Persistence and activation service for annual historical analytics."""
import hashlib
import json
import os
import re
import uuid
from collections import Counter

from sqlalchemy import func, text

from database import (
    HistoricalImport,
    HistoricalMapping,
    HistoricalMetricValue,
    HistoricalRawValue,
    HistoricalYearVersion,
    db,
    utcnow,
)
from historical_parser import (
    HistoricalParserError,
    SECTION_ALIASES,
    canonical_metric_key,
    normalize_name,
    parse_historical_xlsx,
)


EXPECTED_SECTIONS = set(SECTION_ALIASES)


class HistoricalImportError(ValueError):
    """A staged import cannot safely be confirmed."""


def mapping_aliases(company_id, additions=None):
    """Build parser aliases from confirmed mappings plus this request."""
    aliases = {"sections": {}, "metrics": {}}
    for row in HistoricalMapping.query.filter_by(company_id=company_id).all():
        if row.kind == "section":
            aliases["sections"][row.source_label] = row.canonical_key
        else:
            aliases["metrics"].setdefault("*", {})[
                row.source_label] = row.canonical_key
    for item in additions or []:
        kind = item["kind"]
        if kind == "section":
            aliases["sections"][item["source_label"]] = item["canonical_key"]
        else:
            aliases["metrics"].setdefault("*", {})[
                item["source_label"]] = item["canonical_key"]
    return aliases


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_upload(company_id, user_id, uploaded_file, uploads_root):
    """Save and parse a workbook without touching active yearly versions."""
    original = os.path.basename(uploaded_file.filename or "").strip()
    if not original.lower().endswith(".xlsx"):
        raise HistoricalImportError(
            "Для исторической аналитики требуется файл XLSX.")
    directory = os.path.join(uploads_root, str(company_id), "historical")
    os.makedirs(directory, exist_ok=True)
    stored = os.path.join(directory, f"{uuid.uuid4().hex}.xlsx")
    uploaded_file.save(stored)
    attempt = HistoricalImport(
        company_id=company_id,
        uploaded_by=user_id,
        original_filename=original,
        stored_path=stored,
        file_hash=_sha256(stored),
        status="preview",
    )
    db.session.add(attempt)
    db.session.flush()
    try:
        parsed = parse_historical_xlsx(
            stored, mapping_aliases(company_id))
        attempt.preview_json = json.dumps(parsed, ensure_ascii=False)
        attempt.validation_json = json.dumps(
            parsed["validation"], ensure_ascii=False)
        attempt.is_partial = (
            {section["key"] for section in parsed["sections"]}
            != EXPECTED_SECTIONS
        )
    except HistoricalParserError as exc:
        attempt.status = "failed"
        attempt.error_text = str(exc)
        attempt.preview_json = json.dumps(
            {"warnings": [str(exc)], "preview": {}}, ensure_ascii=False)
    db.session.commit()
    return attempt


def parse_mapping_form(form):
    """Extract explicit unknown-name confirmations from a submitted preview."""
    result = []
    count = int(form.get("mapping_count", "0") or 0)
    for index in range(count):
        source = form.get(f"mapping_source_{index}", "").strip()
        kind = form.get(f"mapping_kind_{index}", "").strip()
        canonical = form.get(f"mapping_canonical_{index}", "").strip()
        label = form.get(f"mapping_label_{index}", "").strip() or source
        if not source or kind not in ("section", "metric") or not canonical:
            raise HistoricalImportError(
                "Для каждого неизвестного названия подтвердите сопоставление.")
        if kind == "section" and canonical not in EXPECTED_SECTIONS:
            raise HistoricalImportError(
                f"Неизвестный канонический раздел: {canonical}.")
        if not re.fullmatch(r"[a-zA-Z0-9_а-яА-ЯёЁ.-]{1,240}", canonical):
            raise HistoricalImportError(
                "Ключ сопоставления содержит недопустимые символы.")
        result.append({
            "kind": kind,
            "source_label": source,
            "canonical_key": canonical,
            "canonical_label": label,
        })
    return result


def _save_mappings(company_id, user_id, additions):
    for item in additions:
        normalized = normalize_name(item["source_label"])
        row = HistoricalMapping.query.filter_by(
            company_id=company_id,
            kind=item["kind"],
            source_normalized=normalized,
        ).first()
        if row is None:
            row = HistoricalMapping(
                company_id=company_id,
                kind=item["kind"],
                source_label=item["source_label"],
                source_normalized=normalized,
            )
            db.session.add(row)
        row.source_label = item["source_label"]
        row.canonical_key = item["canonical_key"]
        row.canonical_label = item["canonical_label"]
        row.confirmed_by = user_id
        row.confirmed_at = utcnow()


def _metric_series_keys(row):
    """Return stable unique series keys for parallel value/% columns."""
    bases = [
        ("percent" if value.get("unit") == "percent" else "value")
        for value in row["values"]
    ]
    totals = Counter(
        (value.get("year"), base)
        for value, base in zip(row["values"], bases))
    seen = Counter()
    keys = []
    for value, base in zip(row["values"], bases):
        counter_key = (value.get("year"), base)
        seen[counter_key] += 1
        keys.append(
            f"{base}_{seen[counter_key]}"
            if totals[counter_key] > 1 else base)
    return keys


def _persist_version_values(version, parsed, year):
    for section in parsed["sections"]:
        for row in section["rows"]:
            if row.get("kind") != "metric":
                continue
            series_keys = _metric_series_keys(row)
            for value, series_key in zip(row["values"], series_keys):
                if value.get("year") != year:
                    continue
                normalized = value.get("value")
                metric = HistoricalMetricValue(
                    year_version=version,
                    section_key=section["key"],
                    section_label=section["title"],
                    metric_key=row["key"],
                    metric_label=row["raw_label"],
                    series_key=series_key,
                    period_label=value.get("period") or "",
                    value_numeric=(
                        float(normalized)
                        if isinstance(normalized, (int, float)) else None),
                    value_text=(
                        normalized if isinstance(normalized, str) else None),
                    unit=value.get("unit") or "ratio",
                    is_missing=bool(value.get("is_missing")),
                    is_upper_bound=bool(value.get("is_upper_bound")),
                )
                metric.raw_value = HistoricalRawValue(
                    raw_text=value.get("raw", ""),
                    sheet_name=section["sheet_name"],
                    cell_ref=value.get("cell", ""),
                )
                db.session.add(metric)


def _lock_year(company_id, year):
    """Serialize version allocation and activation for one company/year."""
    if db.engine.dialect.name == "postgresql":
        # One transaction-scoped advisory lock; released automatically.
        lock_key = int(company_id) * 10_000 + int(year)
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def confirm_and_activate(attempt, user_id, additions, confirm_partial=False):
    """Validate, persist and atomically activate all years in a staged import."""
    # Atomic claim is stronger than checking the already-loaded ORM object.
    # PostgreSQL re-evaluates this predicate after waiting for a concurrent
    # updater; SQLite serializes the conditional write. Only one request wins.
    claimed = HistoricalImport.query.filter(
        HistoricalImport.id == attempt.id,
        HistoricalImport.company_id == attempt.company_id,
        HistoricalImport.status == "preview",
    ).update({"status": "confirming"}, synchronize_session=False)
    if claimed != 1:
        raise HistoricalImportError(
            "Эта загрузка уже обработана или недоступна для подтверждения.")
    db.session.flush()
    db.session.refresh(attempt)
    parsed = parse_historical_xlsx(
        attempt.stored_path,
        mapping_aliases(attempt.company_id, additions),
    )
    if parsed["unknown_sections"] or parsed["unknown_metrics"]:
        raise HistoricalImportError(
            "Остались неподтверждённые названия разделов или показателей.")
    if not parsed["validation"]["ok"]:
        raise HistoricalImportError(
            "Импорт невозможен: нужны баланс, финансовые результаты и ликвидность.")
    is_partial = (
        {section["key"] for section in parsed["sections"]}
        != EXPECTED_SECTIONS
    )
    if is_partial and not confirm_partial:
        raise HistoricalImportError(
            "Для неполной книги требуется явное подтверждение частичного импорта.")

    _save_mappings(attempt.company_id, user_id, additions)
    created = []
    for year in parsed["preview"]["years"]:
        _lock_year(attempt.company_id, year)
        next_no = (db.session.query(func.max(
            HistoricalYearVersion.version_no)).filter_by(
                company_id=attempt.company_id, year=year).scalar() or 0) + 1
        HistoricalYearVersion.query.filter_by(
            company_id=attempt.company_id,
            year=year,
            is_active=True,
        ).update({"is_active": False}, synchronize_session=False)
        version = HistoricalYearVersion(
            historical_import=attempt,
            company_id=attempt.company_id,
            year=year,
            version_no=next_no,
            is_active=True,
            activated_at=utcnow(),
            activated_by=user_id,
        )
        db.session.add(version)
        _persist_version_values(version, parsed, year)
        created.append(version)
    if not created:
        raise HistoricalImportError("В книге не найдено ни одного календарного года.")
    attempt.status = "active"
    attempt.is_partial = is_partial
    attempt.partial_confirmed = bool(is_partial and confirm_partial)
    attempt.preview_json = json.dumps(parsed, ensure_ascii=False)
    attempt.validation_json = json.dumps(
        parsed["validation"], ensure_ascii=False)
    attempt.confirmed_at = utcnow()
    db.session.commit()
    return created


def cancel_attempt(attempt):
    if attempt.status != "preview":
        raise HistoricalImportError("Отменить можно только ожидающий предпросмотр.")
    attempt.status = "cancelled"
    attempt.confirmed_at = utcnow()
    db.session.commit()


def activate_version(version, user_id):
    """Activate an existing version without deleting later history."""
    _lock_year(version.company_id, version.year)
    db.session.refresh(version)
    if version.is_active:
        return version
    HistoricalYearVersion.query.filter_by(
        company_id=version.company_id,
        year=version.year,
        is_active=True,
    ).update({"is_active": False}, synchronize_session=False)
    version.is_active = True
    version.activated_at = utcnow()
    version.activated_by = user_id
    db.session.commit()
    return version