# -*- coding: utf-8 -*-
"""Сопоставление утверждённых годовых показателей нескольких предприятий."""
from __future__ import annotations

import math
import statistics
from urllib.parse import quote

from historical_analytics import GROUPS, build_expert_analytics, validate_profile_key

MODES = {
    "normalized": "Отклонение от нормы / перцентиль",
    "absolute": "Абсолютные значения",
    "median": "Отклонение от медианы",
    "index": "Индекс 100 к базовому году",
}


def metric_ref(metric):
    return "|".join((metric["section_key"], metric["key"], metric["series_key"]))


def _comparison_metric_catalog(analytics):
    """Build a numeric catalog with unambiguous labels for parallel series."""
    catalog = {}
    for payload in analytics.values():
        for metric in payload["metrics"]:
            if metric.get("unit") == "text":
                continue
            ref = metric_ref(metric)
            catalog.setdefault(ref, {
                "ref": ref, "label": metric["label"],
                "section_label": metric["section_label"], "unit": metric["unit"],
                "series_key": metric["series_key"],
                "group": metric.get("group", "balance_capital"),
                "norm": metric.get("norm"),
                "norm_label": metric.get("norm_label", "Норма не задана"),
            })

    groups = {}
    for item in catalog.values():
        key = (item["section_label"].strip().casefold(),
               item["label"].strip().casefold())
        groups.setdefault(key, []).append(item)
    unit_labels = {
        "thousand_rubles": "сумма, тыс. руб.",
        "percent": "доля, %",
        "ratio": "коэффициент",
    }
    for items in groups.values():
        for index, item in enumerate(sorted(
                items, key=lambda value: value["series_key"])):
            suffix = ""
            if len(items) > 1:
                same_unit = sum(
                    other["unit"] == item["unit"] for other in items) > 1
                suffix = unit_labels.get(item["unit"], item["unit"])
                if same_unit:
                    suffix = "{} {}".format(suffix, index + 1)
            item["option_label"] = (
                "{} ({})".format(item["label"], suffix)
                if suffix else item["label"])
    return catalog


PRESET_SPECS = [
    ("key", "Ключевые показатели", [
        "Коэффициент автономии",
        "Коэффициент текущей (общей) ликвидности",
        "Рентабельность активов (ROA)",
        "Рентабельность собственного капитала (ROE)",
        "Рентабельность продаж (прибыль от продаж / выручка)",
        "Коэффициент покрытия процентов (ICR)",
    ]),
    ("stability", "Финансовая устойчивость", [
        "Коэффициент автономии",
        "Коэффициент финансового левериджа",
        "Коэффициент обеспеченности СОС",
        "Коэффициент текущей (общей) ликвидности",
        "Коэффициент покрытия процентов (ICR)",
        "Чистые активы",
    ]),
    ("income", "Доходы и прибыль", [
        "Выручка",
        "Валовая прибыль (убыток)",
        "Прибыль (убыток) от продаж",
        "EBIT (прибыль до % и налогов)",
        "Прибыль до налогообложения",
        "Чистая прибыль (убыток)",
    ]),
    ("risks", "Риски", [
        "Итоговая рейтинговая оценка",
        "Коэффициент текущей ликвидности",
        "Коэффициент обеспеченности собственными средствами",
        "Z-счет Альтмана",
        "Z-счет Таффлера",
        "Итого (R)",
    ]),
]


def _decorate_catalog(catalog, analytics, companies, years):
    lookup = {
        company_id: {metric_ref(metric): metric for metric in payload["metrics"]}
        for company_id, payload in analytics.items()
    }
    for ref, item in catalog.items():
        company_ids = []
        for company in companies:
            metric = lookup.get(company.id, {}).get(ref)
            if not metric:
                continue
            available = any(
                (metric["values_by_year"].get(year, {}).get(
                    "historical_year_available")
                 and _finite((metric["values_by_year"].get(
                     year, {}).get("primary") or {}).get("value")))
                for year in years
            )
            if available:
                company_ids.append(company.id)
        item["company_ids"] = company_ids
        item["coverage"] = len(company_ids)
        item["unit_label"] = {
            "thousand_rubles": "тыс. руб.",
            "percent": "%",
            "ratio": "коэффициент",
        }.get(item["unit"], item["unit"] or "—")
    unit_order = {"thousand_rubles": 0, "ratio": 1, "percent": 2}
    metrics = sorted(catalog.values(), key=lambda item: (
        item["group"], item["section_label"], item["label"],
        unit_order.get(item["unit"], 9), item["series_key"]))
    groups = []
    for group in GROUPS:
        count = sum(item["group"] == group["key"] for item in metrics)
        if count:
            groups.append({
                "key": group["key"], "label": group["label"], "count": count})
    presets = []
    for key, label, labels in PRESET_SPECS:
        refs = []
        for wanted in labels:
            candidates = [
                item for item in metrics
                if item["label"].casefold() == wanted.casefold()]
            if not candidates:
                candidates = [
                    item for item in metrics
                    if wanted.casefold() in item["label"].casefold()]
            if candidates:
                preferred = next((
                    item for item in candidates
                    if item["unit"] != "percent"
                    or "рентабельност" in wanted.casefold()), candidates[0])
                if preferred["ref"] not in refs:
                    refs.append(preferred["ref"])
        presets.append({"key": key, "label": label, "refs": refs[:6]})
    return metrics, groups, presets


def build_metric_catalog(companies, years=None, profile_key="methodical"):
    """Каталог выбора без построения сравнительных рядов и таблицы."""
    profile_key = validate_profile_key(profile_key)
    analytics = {
        company.id: build_expert_analytics(company.id, profile_key)
        for company in companies
    }
    all_years = sorted({
        year for payload in analytics.values() for year in payload["years"]
    })
    available_years = all_years[-4:]
    metrics, groups, presets = _decorate_catalog(
        _comparison_metric_catalog(analytics), analytics, companies,
        available_years)
    return {
        "metrics": metrics,
        "groups": groups,
        "presets": presets,
        "available_years": available_years,
    }


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _pct_delta(value, base):
    if not (_finite(value) and _finite(base)) or base == 0:
        return None
    return (value - base) * 100.0 / abs(base)


def _percentile(value, population):
    if not _finite(value) or len(population) < 2:
        return None
    below = sum(1 for item in population if item < value)
    equal = sum(1 for item in population if item == value)
    return 100.0 * (below + max(0, equal - 1) / 2.0) / max(1, len(population) - 1)


def _display(value, unit, mode):
    if not _finite(value):
        return "—"
    if mode == "index":
        return "{:.1f}".format(value)
    if mode in ("normalized", "median", "change"):
        return "{:+.1f}%".format(value)
    suffix = {"percent": "%", "thousand_rubles": " тыс. руб.",
              "ratio": ""}.get(unit, " " + (unit or ""))
    return "{:,.2f}{}".format(value, suffix).replace(",", " ").replace(".", ",")


def build_comparison(companies, metric_refs=None, start_year=None, end_year=None,
                     mode="normalized", profile_key="methodical",
                     selected_years=None):
    """Строит проверяемую модель без интерполяции пропущенных годов."""
    if not 2 <= len(companies) <= 5:
        raise ValueError("Выберите от двух до пяти предприятий")
    mode = mode if mode in MODES else "normalized"
    profile_key = validate_profile_key(profile_key)
    analytics = {
        company.id: build_expert_analytics(company.id, profile_key)
        for company in companies
    }
    all_years = sorted({
        year for payload in analytics.values() for year in payload["years"]
    })
    if not all_years:
        return {
            "companies": companies, "years": [], "available_years": [],
            "metrics": [], "selected_refs": [], "series": [], "table": [],
            "groups": [], "presets": [], "selected_years": [],
            "mode": mode, "modes": MODES, "profile_key": profile_key,
        }
    available_years = all_years[-4:]
    default_year = 2025 if 2025 in available_years else max(available_years)
    if selected_years is not None:
        years = sorted({
            year for year in selected_years if year in available_years})
        if not years:
            years = [default_year]
    else:
        start_year = start_year if start_year in all_years else default_year
        end_year = end_year if end_year in all_years else default_year
        if start_year > end_year:
            start_year, end_year = end_year, start_year
        years = list(range(start_year, end_year + 1))
    start_year, end_year = min(years), max(years)

    catalog = _comparison_metric_catalog(analytics)
    metrics, groups, presets = _decorate_catalog(
        catalog, analytics, companies, available_years)
    selected = [ref for ref in (metric_refs or []) if ref in catalog]
    if not selected:
        selected = next((
            preset["refs"] for preset in presets
            if preset["key"] == "key" and preset["refs"]), list(catalog)[:1])
    selected = selected[:6]

    lookup = {
        company_id: {metric_ref(metric): metric for metric in payload["metrics"]}
        for company_id, payload in analytics.items()
    }
    raw = {}
    peer_eligible = {}
    for company in companies:
        for ref in selected:
            metric = lookup[company.id].get(ref)
            for year in years:
                cell = metric["values_by_year"].get(year, {}) if metric else {}
                primary = cell.get("primary")
                # Сравнение годовой отчётности не заполняет разрыв одним только
                # снимком ОСВ. ОСВ допустима как выбранный источник профиля лишь
                # когда утверждённая версия этого календарного года существует.
                approved = bool(cell.get("historical_year_available"))
                value = (
                    primary.get("value") if primary and not primary.get("missing")
                    and approved else None)
                raw[(company.id, ref, year)] = value
                conflict = bool(
                    (cell.get("discrepancy") or {}).get("significant"))
                peer_eligible[(company.id, ref, year)] = (
                    _finite(value) and not conflict)

    medians = {}
    populations = {}
    for ref in selected:
        for year in years:
            values = [
                raw[(company.id, ref, year)] for company in companies
                if peer_eligible[(company.id, ref, year)]
            ]
            values = [value for value in values if _finite(value)]
            populations[(ref, year)] = values
            medians[(ref, year)] = (
                statistics.median(values) if len(values) >= 2 else None)

    series = []
    table = []
    for ref in selected:
        meta = catalog[ref]
        chart = {"ref": ref, "label": meta["label"], "unit": meta["unit"],
                 "lines": []}
        for company in companies:
            metric = lookup[company.id].get(ref)
            first = next((
                raw[(company.id, ref, year)] for year in years
                if peer_eligible[(company.id, ref, year)]), None)
            points = []
            for year in years:
                value = raw[(company.id, ref, year)]
                eligible = peer_eligible[(company.id, ref, year)]
                computed = value if eligible else None
                if mode == "median" and eligible:
                    computed = _pct_delta(value, medians[(ref, year)])
                elif mode == "change" and eligible:
                    computed = _pct_delta(value, first)
                elif mode == "index" and eligible:
                    computed = (value * 100.0 / first
                                if _finite(value) and _finite(first) and first else None)
                elif mode == "normalized":
                    norm = metric.get("norm") if metric else None
                    direction = metric.get("direction") if metric else "neutral"
                    if not peer_eligible[(company.id, ref, year)]:
                        computed = None
                    elif _finite(norm) and direction in ("higher", "lower"):
                        # Для порога 0 (например ROE/ROA > 0%) относительное
                        # отклонение не определено: используем процентные пункты.
                        computed = (
                            value - norm if norm == 0
                            else _pct_delta(value, norm))
                        if computed is not None and direction == "lower":
                            computed *= -1
                    else:
                        computed = _percentile(
                            value, populations[(ref, year)])
                point_cell = (
                    metric["values_by_year"].get(year, {}) if metric else {})
                point_primary = point_cell.get("primary") or {}
                point_display = _display(computed, meta["unit"], mode)
                if (mode == "normalized" and metric
                        and metric.get("norm") == 0
                        and meta["unit"] == "percent"
                        and _finite(computed)):
                    point_display = "{:+.1f} п.п.".format(computed)
                points.append({
                    "year": year, "value": computed,
                    "display": point_display,
                    "raw": value,
                    "source": point_primary.get(
                        "provenance", {}).get("source", "—"),
                    "quality": (
                        "Конфликт источников" if value is not None and not eligible
                        else "Верхняя граница"
                        if point_primary.get("upper_bound")
                        else "Контроль по ОСВ"
                        if point_cell.get("primary_source") == "osv"
                        else "Проверено" if value is not None
                        else "Нет утверждённых данных"),
                })
            chart["lines"].append({
                "company_id": company.id, "name": company.name, "points": points,
            })

            last_year = next((year for year in reversed(years)
                              if _finite(raw[(company.id, ref, year)])), None)
            value = raw[(company.id, ref, last_year)] if last_year else None
            cell = (metric["values_by_year"].get(last_year, {})
                    if metric and last_year else {})
            primary = cell.get("primary") or {}
            source = primary.get("provenance", {}).get("source", "—")
            conflict = bool((cell.get("discrepancy") or {}).get("significant"))
            quality = ("Нет утверждённых данных" if last_year is None else
                       "Конфликт источников" if conflict else
                       "Верхняя граница" if primary.get("upper_bound") else
                        "Верхняя граница" if point_primary.get("upper_bound")
                        else "Контроль по ОСВ"
                       if cell.get("primary_source") == "osv" else
                       "Проверено")
            zone = cell.get("zone", "neutral")
            table.append({
                "metric_ref": ref, "metric": meta["label"],
                "company_id": company.id, "company": company.name,
                "year": last_year, "value": value,
                "value_display": _display(value, meta["unit"], "absolute"),
                "change": _pct_delta(value, first),
                "change_display": _display(_pct_delta(value, first),
                                           meta["unit"], "change"),
                "norm": metric.get("norm_label", "Норма не задана")
                if metric else "Норма не задана",
                "median": medians.get((ref, last_year)),
                "median_display": _display(
                    medians.get((ref, last_year)), meta["unit"], "absolute"),
                "zone": zone, "quality": quality, "source": source,
                "comparable": bool(metric and (
                    metric.get("direction") in ("higher", "lower")
                    or metric.get("norm") is None)),
            })
        series.append(chart)

    return {
        "companies": companies, "years": years,
        "available_years": available_years, "selected_years": years,
        "metrics": metrics, "groups": groups, "presets": presets,
        "selected_refs": selected, "series": series, "table": table,
        "mode": mode, "modes": MODES, "profile_key": profile_key,
        "start_year": start_year, "end_year": end_year,
        "query_refs": [quote(ref, safe="") for ref in selected],
    }
