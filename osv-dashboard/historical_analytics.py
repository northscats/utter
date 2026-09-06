# -*- coding: utf-8 -*-
"""Expert presentation model for approved annual historical analytics."""
from __future__ import annotations

import json
import math
import re
from collections import OrderedDict

from database import HistoricalYearVersion, Upload
from historical_parser import SECTION_ALIASES, canonical_metric_key


SECTION_LABELS = OrderedDict([
    ("assets", "Активы"),
    ("liabilities", "Пассивы"),
    ("net_assets", "Чистые активы"),
    ("financial_stability", "Финансовая устойчивость"),
    ("working_capital", "Собственные оборотные средства"),
    ("liquidity", "Ликвидность"),
    ("asset_liability", "Активы и обязательства"),
    ("financial_results", "Финансовые результаты"),
    ("profitability", "Рентабельность"),
    ("turnover", "Оборачиваемость"),
    ("rating", "Исторический рейтинг"),
    ("insolvent_structure", "Неудовлетворительная структура"),
    ("creditworthiness", "Кредитоспособность"),
    ("bankruptcy_forecast", "Прогноз банкротства"),
    ("dynamics", "Динамика"),
])

SECTION_GROUPS = {
    "assets": "position", "liabilities": "position",
    "net_assets": "position", "financial_stability": "position",
    "working_capital": "position", "liquidity": "position",
    "asset_liability": "position",
    "financial_results": "performance", "profitability": "performance",
    "turnover": "performance", "dynamics": "performance",
    "rating": "risks", "insolvent_structure": "risks",
    "creditworthiness": "risks", "bankruptcy_forecast": "risks",
}

GROUPS = [
    {"key": "position", "label": "Финансовое положение"},
    {"key": "performance", "label": "Результаты и эффективность"},
    {"key": "risks", "label": "Риски"},
]

UNIT_LABELS = {
    "thousand_rubles": "тыс. руб.",
    "percent": "%",
    "ratio": "—",
}


def _unit_label(unit):
    # Parser storage may use ``text`` to distinguish non-numeric values, but it
    # is not a unit of measurement and must never leak into the expert table.
    return UNIT_LABELS.get(unit, "—")


def _source_display_value(raw_text, unit):
    """Separate a unit suffix from source text without changing storage."""
    text = str(raw_text or "").strip()
    if unit == "percent":
        text = re.sub(r"\s*%\s*$", "", text).rstrip()
    elif unit == "thousand_rubles":
        text = re.sub(
            r"\s*(?:тыс\.?\s*руб\.?|thousand_rubles)\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).rstrip()
    text = re.sub(
        r"\bнеудовлетворительное\b",
        "неудовл.",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\bудовлетворительное\b",
        "удовл.",
        text,
        flags=re.IGNORECASE,
    )


def _number_display_value(value):
    if not _finite(value):
        return str(value or "")
    return "{:.12g}".format(value).replace(".", ",")


def _key(label):
    return canonical_metric_key(label)


# Каталог намеренно содержит только подтверждённые методикой определения.
# Остальные импортированные строки всё равно отображаются, но без нормы/формулы.
CATALOG = {
    ("financial_stability", _key("Коэффициент автономии")): {
        "direction": "higher", "norm": 0.5,
        "norm_label": "≥ 0,5",
        "formula": "Собственный капитал / Валюта баланса",
    },
    ("financial_stability", _key("Коэффициент финансового левериджа")): {
        "direction": "lower", "norm": 1.0,
        "norm_label": "≤ 1,0",
        "formula": "Заёмный капитал / Собственный капитал",
    },
    ("financial_stability", _key("Коэффициент обеспеченности СОС")): {
        "direction": "higher", "norm": 0.1,
        "norm_label": "≥ 0,1",
        "formula": "Собственные оборотные средства / Оборотные активы",
    },
    ("liquidity", _key("Коэффициент текущей (общей) ликвидности")): {
        "direction": "higher", "norm": 1.7,
        "norm_label": "≥ 1,7",
        "formula": "Оборотные активы / Краткосрочные обязательства",
    },
    ("insolvent_structure", _key("Коэффициент текущей ликвидности")): {
        "direction": "higher", "norm": 2.0,
        "norm_label": "≥ 2,0",
        "formula": "Оборотные активы / Краткосрочные обязательства",
    },
    ("liquidity", _key("Коэффициент быстрой (промежуточной) ликвидности")): {
        "direction": "higher", "norm": 1.0,
        "norm_label": "≥ 1,0",
        "formula": "(Денежные средства + краткосрочные вложения + ДЗ) / "
                   "Краткосрочные обязательства",
    },
    ("liquidity", _key("Коэффициент абсолютной ликвидности")): {
        "direction": "higher", "norm": 0.2,
        "norm_label": "≥ 0,2",
        "formula": "(Денежные средства + краткосрочные вложения) / "
                   "Краткосрочные обязательства",
    },
    ("profitability", _key(
        "Рентабельность продаж (прибыль от продаж / выручка)")): {
        "direction": "higher", "norm": 4.0,
        "norm_label": "≥ 4%",
        "formula": "Прибыль от продаж / Выручка × 100%",
    },
    ("profitability", _key("Коэффициент покрытия процентов (ICR)")): {
        "direction": "higher", "norm": 1.5,
        "norm_label": "≥ 1,5",
        "formula": "EBIT / Проценты к уплате",
    },
    ("profitability", _key(
        "Рентабельность собственного капитала (ROE)")): {
        "direction": "higher", "norm": 0.0,
        "norm_label": "> 0%",
        "formula": "Чистая прибыль / Средний собственный капитал × 100%",
    },
    ("profitability", _key("Рентабельность активов (ROA)")): {
        "direction": "higher", "norm": 0.0,
        "norm_label": "> 0%",
        "formula": "Чистая прибыль / Средние активы × 100%",
    },
    ("turnover", _key("Оборачиваемость дебиторской задолженности")): {
        "direction": "lower", "norm": 90.0,
        "norm_label": "≤ 90 дней",
        "formula": "Средняя дебиторская задолженность / Выручка × 365",
    },
    ("profitability", "control_adm_pct"): {
        "direction": "lower", "norm": 12.0,
        "norm_label": "≤ 12%",
        "formula": "Управленческие расходы / Выручка × 100%",
        "osv_key": "adm_pct",
        "osv_formula": (
            "Управленческие расходы по ОСВ / Выручка нетто по ОСВ × 100%"),
    },
}

PROFILES = OrderedDict([
    ("methodical", {
        "key": "methodical",
        "name": "Единый методический профиль",
        "description": "Основной источник — утверждённая годовая отчётность.",
        "preferred_source": "historical",
        "absolute_tolerance": {
            "ratio": 0.05, "percent": 0.5, "thousand_rubles": 10.0,
        },
        "norm_overrides": {},
    }),
    ("osv_control", {
        "key": "osv_control",
        "name": "Контроль по ОСВ",
        "description": "При наличии сопоставимого расчёта основным считается ОСВ.",
        "preferred_source": "osv",
        "absolute_tolerance": {
            "ratio": 0.02, "percent": 0.25, "thousand_rubles": 1.0,
        },
        "norm_overrides": {
            ("liquidity",
             _key("Коэффициент текущей (общей) ликвидности")): 1.5,
        },
    }),
])


def profile_payloads():
    return [
        {key: value for key, value in profile.items()
         if key not in ("absolute_tolerance", "norm_overrides")}
        for profile in PROFILES.values()
    ]


def validate_profile_key(value):
    return value if value in PROFILES else "methodical"


def _period_year(upload):
    matches = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)",
                         upload.period_label or "")
    return int(matches[-1]) if matches else upload.uploaded_at.year


def _is_full_year_upload(upload):
    """Only annual OSV snapshots can be compared with annual statements."""
    label = (upload.period_label or "").lower().replace("ё", "е")
    compact = re.sub(r"[\s.\-–—_/]+", "", label)
    return bool(
        ("январ" in compact and "декабр" in compact)
        or re.search(r"(?:^|не)(?:за)?(?:19|20)\d{2}(?:г|год)?$", compact)
        or re.search(
            r"01(?:01)?(?:19|20)\d{2}.*31(?:12)?(?:19|20)\d{2}", compact)
    )


def _osv_snapshots(company_id):
    """Latest immutable OSV metric snapshot for every identifiable year."""
    snapshots = {}
    uploads = Upload.query.filter_by(company_id=company_id).order_by(
        Upload.uploaded_at.asc(), Upload.id.asc()).all()
    for upload in uploads:
        if not _is_full_year_upload(upload):
            continue
        metrics = upload.metrics
        if not metrics:
            continue
        year = _period_year(upload)
        snapshots[year] = {
            "upload_id": upload.id,
            "period": upload.period_label or str(year),
            "uploaded_at": upload.uploaded_at.isoformat(),
            "metrics": metrics,
        }
    return snapshots


def _record_for(records, section_key, metric_key):
    return next((
        record for record in records.values()
        if record["section_key"] == section_key
        and record["key"] == metric_key
    ), None)


def _add_comparable_controls(records, versions):
    """Create controls only when both sources implement the same formula."""
    revenue = _record_for(records, "financial_results", _key("Выручка"))
    admin = _record_for(
        records, "financial_results", _key("Управленческие расходы"))
    if not revenue or not admin:
        return
    version_by_year = {version.year: version for version in versions}
    control = {
        "key": "control_adm_pct",
        "series_key": "value",
        "label": "Контроль: управленческие расходы / выручка",
        "section_key": "profitability",
        "section_label": SECTION_LABELS["profitability"],
        "unit": "percent",
        "values_by_year": {},
    }
    for year, version in version_by_year.items():
        rev = revenue["values_by_year"].get(year, {}).get("historical")
        adm = admin["values_by_year"].get(year, {}).get("historical")
        rev_value = rev.get("value") if rev else None
        adm_value = adm.get("value") if adm else None
        if not (_finite(rev_value) and _finite(adm_value) and rev_value):
            continue
        rev_prov = rev["provenance"]
        adm_prov = adm["provenance"]
        value = abs(adm_value) * 100.0 / abs(rev_value)
        control["values_by_year"][year] = {
            "historical": {
                "value": value,
                "raw": "{} / {} × 100%".format(adm["raw"], rev["raw"]),
                "display_value": _number_display_value(value),
                "unit_label": _unit_label("percent"),
                "upper_bound": False,
                "missing": False,
                "formula": CATALOG[
                    ("profitability", "control_adm_pct")]["formula"],
                "provenance": {
                    "source": "Расчёт по загруженной отчётности",
                    "filename": rev_prov["filename"],
                    "import_id": version.import_id,
                    "version": rev_prov["version"],
                    "sheet": rev_prov["sheet"],
                    "row": "{}, {}".format(
                        adm_prov.get("row", "—"), rev_prov.get("row", "—")),
                    "cell": "{}, {}".format(
                        adm_prov.get("cell", "—"), rev_prov.get("cell", "—")),
                },
            },
        }
    records[("profitability", "control_adm_pct", "value")] = control


def _finite(value):
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value))


def _normalized_value(metric):
    if metric.is_missing:
        return None
    if metric.value_numeric is not None:
        return metric.value_numeric
    return metric.value_text


def _value_payload(metric):
    raw = metric.raw_value
    cell_ref = raw.cell_ref if raw else ""
    row_match = re.search(r"(\d+)$", cell_ref or "")
    return {
        "value": _normalized_value(metric),
        "raw": raw.raw_text if raw else "",
        "display_value": _source_display_value(
            raw.raw_text if raw else "", metric.unit),
        "unit_label": _unit_label(metric.unit),
        "upper_bound": metric.is_upper_bound,
        "missing": metric.is_missing,
        "provenance": {
            "source": "Загруженная отчётность",
            "filename": metric.year_version.historical_import.original_filename,
            "import_id": metric.year_version.import_id,
            "version": "v{}".format(metric.year_version.version_no),
            "sheet": raw.sheet_name if raw else "",
            "row": int(row_match.group(1)) if row_match else None,
            "cell": cell_ref,
        },
    }


def _zone(value, direction, norm):
    if not _finite(value) or direction not in ("higher", "lower") or norm is None:
        return "neutral"
    if direction == "higher":
        return "normal" if value >= norm else "outside"
    return "normal" if value <= norm else "outside"


def _source_choice(historical, osv, preferred):
    if preferred == "osv" and osv is not None:
        return "osv"
    if historical is not None:
        return "historical"
    if osv is not None:
        return "osv"
    return None


def _discrepancy(historical, osv, unit, profile):
    hv = historical.get("value") if historical else None
    ov = osv.get("value") if osv else None
    if not (_finite(hv) and _finite(ov)):
        return None
    absolute = profile["absolute_tolerance"].get(unit, 0.05)
    threshold = max(abs(hv) * 0.01, absolute)
    delta = ov - hv
    return {
        "significant": abs(delta) > threshold,
        "delta": delta,
        "absolute_delta": abs(delta),
        "relative": (abs(delta) / abs(hv)) if hv else None,
        "threshold": threshold,
        "relative_tolerance": 0.01,
        "absolute_tolerance": absolute,
    }


def build_expert_analytics(company_id, profile_key="methodical"):
    profile_key = validate_profile_key(profile_key)
    profile = PROFILES[profile_key]
    versions = HistoricalYearVersion.query.filter_by(
        company_id=company_id, is_active=True).order_by(
            HistoricalYearVersion.year).all()
    available_years = [version.year for version in versions]
    years = (
        list(range(min(available_years), max(available_years) + 1))
        if available_years else []
    )
    osv_by_year = _osv_snapshots(company_id)
    records = OrderedDict()

    for version in versions:
        for metric in sorted(
                version.metrics,
                key=lambda item: (item.section_key, item.id)):
            identity = (metric.section_key, metric.metric_key, metric.series_key)
            record = records.setdefault(identity, {
                "key": metric.metric_key,
                "series_key": metric.series_key,
                "label": metric.metric_label,
                "section_key": metric.section_key,
                "section_label": metric.section_label,
                "unit": metric.unit,
                "values_by_year": {},
            })
            record["values_by_year"][version.year] = {
                "historical": _value_payload(metric),
            }

    _add_comparable_controls(records, versions)

    for record in records.values():
        catalog_key = (record["section_key"], record["key"])
        meta = CATALOG.get(catalog_key, {})
        direction = meta.get("direction", "neutral")
        norm = profile["norm_overrides"].get(catalog_key, meta.get("norm"))
        norm_label = meta.get("norm_label", "Норма не задана")
        if catalog_key in profile["norm_overrides"]:
            symbol = "≥" if direction == "higher" else "≤"
            norm_label = "{} {}".format(symbol, norm)
        record.update({
            "direction": direction,
            "norm": norm,
            "norm_label": norm_label,
            "formula": meta.get("formula"),
            "formula_confirmed": bool(meta.get("formula")),
            "group": SECTION_GROUPS.get(record["section_key"], "position"),
            "is_historical_rating": record["section_key"] == "rating",
        })
        for year in years:
            cell = record["values_by_year"].setdefault(year, {})
            historical = cell.get("historical")
            osv = None
            snapshot = osv_by_year.get(year)
            osv_key = meta.get("osv_key")
            if snapshot and osv_key and _finite(snapshot["metrics"].get(osv_key)):
                osv = {
                    "value": snapshot["metrics"][osv_key],
                    "raw": str(snapshot["metrics"][osv_key]),
                    "display_value": _number_display_value(
                        snapshot["metrics"][osv_key]),
                    "unit_label": _unit_label(record["unit"]),
                    "upper_bound": False,
                    "missing": False,
                    "formula": meta.get("osv_formula"),
                    "provenance": {
                        "source": "Расчёт по ОСВ",
                        "filename": "",
                        "import_id": None,
                        "version": "снимок загрузки #{}".format(
                            snapshot["upload_id"]),
                        "sheet": snapshot["period"],
                        "cell": "серверный расчёт",
                    },
                }
            primary_source = _source_choice(
                historical, osv, profile["preferred_source"])
            primary = (
                historical if primary_source == "historical"
                else osv if primary_source == "osv" else None
            )
            secondary = (
                osv if primary_source == "historical"
                else historical if primary_source == "osv" else None
            )
            cell.update({
                "osv": osv,
                "primary_source": primary_source,
                "primary": primary,
                "secondary": secondary,
                "discrepancy": _discrepancy(
                    historical, osv, record["unit"], profile),
                "zone": _zone(
                    primary.get("value") if primary else None,
                    direction, norm),
                "missing": primary is None or primary.get("missing", False),
                "historical_year_available": year in available_years,
            })

    sections = []
    for section_key in SECTION_ALIASES:
        metrics = [
            record for record in records.values()
            if record["section_key"] == section_key
        ]
        sections.append({
            "key": section_key,
            "label": SECTION_LABELS.get(section_key, section_key),
            "group": SECTION_GROUPS.get(section_key, "position"),
            "metrics": metrics,
        })
    unknown_sections = sorted({
        record["section_key"] for record in records.values()
        if record["section_key"] not in SECTION_ALIASES
    })
    for section_key in unknown_sections:
        metrics = [
            record for record in records.values()
            if record["section_key"] == section_key
        ]
        sections.append({
            "key": section_key,
            "label": metrics[0]["section_label"],
            "group": "position",
            "metrics": metrics,
        })

    bankruptcy = []
    bankruptcy_section = next(
        (section for section in sections
         if section["key"] == "bankruptcy_forecast"), None)
    for key, label, needle in (
        ("altman", "Альтман", "альтман"),
        ("taffler", "Таффлер", "таффлер"),
        ("saifullin", "Сайфуллин–Кадыков", "сайфуллин"),
    ):
        model_metrics = [
            metric for metric in (
                bankruptcy_section["metrics"] if bankruptcy_section else [])
            if needle in metric["label"].lower()
        ]
        bankruptcy.append({
            "key": key, "label": label, "metrics": model_metrics,
        })

    rating_section = next(
        (section for section in sections if section["key"] == "rating"), None)
    return {
        "years": years,
        "available_years": available_years,
        "missing_years": [year for year in years if year not in available_years],
        "groups": GROUPS,
        "sections": sections,
        "metrics": list(records.values()),
        "profile": {
            "key": profile_key,
            "name": profile["name"],
            "description": profile["description"],
            "preferred_source": profile["preferred_source"],
        },
        "historical_rating": (
            rating_section["metrics"] if rating_section else []),
        "bankruptcy_models": bankruptcy,
        "platform_rating_included": False,
    }


__all__ = [
    "CATALOG", "PROFILES", "build_expert_analytics", "profile_payloads",
    "validate_profile_key",
]