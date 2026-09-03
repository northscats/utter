# -*- coding: utf-8 -*-
"""Portfolio-level analytics over approved annual company data."""
from __future__ import annotations

import math
import statistics

from historical_analytics import build_expert_analytics
from historical_executive import ZONE_LABELS
from historical_parser import canonical_metric_key


KPI_SPECS = (
    ("revenue", "Выручка", "financial_results", "Выручка", None, None),
    ("sales_profit", "Прибыль от продаж", "financial_results",
     "Прибыль (убыток) от продаж", "profitability",
     "Рентабельность продаж (прибыль от продаж / выручка)"),
    ("ebit", "EBIT / покрытие процентов", "financial_results",
     "EBIT (прибыль до % и налогов)", "profitability",
     "Коэффициент покрытия процентов (ICR)"),
    ("liquidity", "Текущая ликвидность", "liquidity",
     "Коэффициент текущей (общей) ликвидности", None, None),
    ("nwc", "NWC / обеспеченность СОС", "working_capital",
     "СОС2 (с учетом долгосрочных пассивов, NWC)", "financial_stability",
     "Коэффициент обеспеченности СОС"),
    ("receivables", "Дебиторская задолженность", "assets",
     "дебиторская задолженность", "turnover",
     "Оборачиваемость дебиторской задолженности"),
    ("autonomy", "Доля собственного капитала", "financial_stability",
     "Коэффициент автономии", None, None),
    ("rating", "Рейтинг и риски", "rating", None, None, None),
)
ZONE_RANK = {"neutral": 0, "normal": 1, "attention": 2, "risk": 3, "critical": 4}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _metric(analytics, section, label):
    candidates = [
        item for item in analytics.get("metrics", [])
        if item["section_key"] == section
    ]
    if label is None:
        preferred = ("итоговый_рейтинг", "итоговая_рейтинговая_оценка_балл")
        return next((m for key in preferred for m in candidates if m["key"] == key),
                    candidates[0] if candidates else None)
    key = canonical_metric_key(label)
    return next((item for item in candidates if item["key"] == key), None)


def _value(metric, year):
    cell = metric.get("values_by_year", {}).get(year) if metric else None
    primary = cell.get("primary") if cell and not cell.get("missing") else None
    value = primary.get("value") if primary and not primary.get("missing") else None
    return value if _finite(value) else None


def _format(value, unit):
    if value is None:
        return "—"
    if unit == "thousand_rubles":
        amount = abs(value)
        if amount >= 1_000_000:
            return ("{:.1f} млрд ₽".format(value / 1_000_000)).replace(".", ",")
        if amount >= 1_000:
            return ("{:.1f} млн ₽".format(value / 1_000)).replace(".", ",")
        return "{:,.0f} тыс. ₽".format(value).replace(",", " ")
    if unit == "percent":
        return ("{:.1f}%".format(value)).replace(".", ",")
    return ("{:.2f}".format(value)).replace(".", ",").rstrip("0").rstrip(",")


def _norm_zone(value, direction, norm):
    if value is None or direction not in ("higher", "lower") or not _finite(norm):
        return None
    if (direction == "higher" and value >= norm) or (direction == "lower" and value <= norm):
        return "normal"
    if direction == "higher":
        ratio = value / norm if norm > 0 else (1 if value >= norm else 0)
    else:
        ratio = norm / value if value > 0 else (1 if value <= norm else 0)
    return "attention" if ratio >= .85 else "risk" if ratio >= .65 else "critical"


def _percentile_zone(value, values, direction):
    if value is None or direction not in ("higher", "lower") or len(values) < 2:
        return "neutral"
    ordered = sorted(values, reverse=direction == "higher")
    rank = ordered.index(value) / max(len(ordered) - 1, 1)
    return "normal" if rank < .25 else "attention" if rank < .5 else "risk" if rank < .75 else "critical"


def choose_portfolio_year(company_years, threshold=.8):
    """Newest year meeting coverage; newest available year is explicit fallback."""
    total = len(company_years)
    years = sorted({year for values in company_years.values() for year in values}, reverse=True)
    coverage = {
        year: sum(year in values for values in company_years.values()) / total
        for year in years
    } if total else {}
    selected = next((year for year in years if coverage[year] >= threshold),
                    years[0] if years else None)
    return selected, coverage, bool(selected is not None and coverage[selected] >= threshold)


def build_portfolio(companies, selected_year=None, profile_key="methodical"):
    companies = [company for company in companies if company.is_rso]
    analytics_by_company = {
        company.id: build_expert_analytics(company.id, profile_key)
        for company in companies
    }
    years_by_company = {
        company.id: set(analytics_by_company[company.id]["available_years"])
        for company in companies
    }
    default_year, coverage_by_year, current = choose_portfolio_year(years_by_company)
    all_years = sorted(coverage_by_year, reverse=True)
    year = selected_year if selected_year in coverage_by_year else default_year
    metric_rows = {}
    for company in companies:
        analytics = analytics_by_company[company.id]
        cells = {}
        for key, label, p_section, p_label, s_section, s_label in KPI_SPECS:
            primary = _metric(analytics, p_section, p_label)
            status = _metric(analytics, s_section, s_label) if s_section else primary
            value, status_value = _value(primary, year), _value(status, year)
            primary_cell = (
                primary.get("values_by_year", {}).get(year, {}) if primary else {})
            cell = status.get("values_by_year", {}).get(year, {}) if status else {}
            previous_year = max((y for y in analytics["available_years"] if y < year), default=None) if year else None
            previous = _value(status, previous_year)
            direction = status.get("direction", "neutral") if status else "neutral"
            worsening = (
                status_value is not None and previous is not None
                and ((direction == "higher" and status_value < previous)
                     or (direction == "lower" and status_value > previous))
            )
            primary_conflict = bool(
                (primary_cell.get("discrepancy") or {}).get("significant"))
            status_conflict = bool(
                (cell.get("discrepancy") or {}).get("significant"))
            conflict = primary_conflict or status_conflict
            status_required = status is not primary
            cells[key] = {
                "label": label, "value": value, "status_value": status_value,
                "display": _format(value, primary.get("unit") if primary else ""),
                "status_display": (
                    _format(status_value, status.get("unit") if status else "")
                    if status_required else ""),
                "unit": primary.get("unit") if primary else "",
                "direction": direction, "norm": status.get("norm") if status else None,
                "norm_label": status.get("norm_label") if status else "Норма не задана",
                "quality": (
                    "Нет данных" if value is None else "Конфликт" if conflict
                    else "Неполно" if status_required and status_value is None
                    else "Подтверждено"),
                "conflict": conflict, "primary_conflict": primary_conflict,
                "status_conflict": status_conflict, "worsening": worsening,
            }
        metric_rows[company.id] = cells

    medians = {}
    for key, *_ in KPI_SPECS:
        values = [metric_rows[c.id][key]["value"] for c in companies
                  if metric_rows[c.id][key]["value"] is not None
                  and not metric_rows[c.id][key]["primary_conflict"]]
        medians[key] = statistics.median(values) if values else None
        status_values = [metric_rows[c.id][key]["status_value"] for c in companies
                         if metric_rows[c.id][key]["status_value"] is not None
                         and not metric_rows[c.id][key]["status_conflict"]]
        for company in companies:
            cell = metric_rows[company.id][key]
            zone = _norm_zone(cell["status_value"], cell["direction"], cell["norm"])
            cell["zone"] = zone or _percentile_zone(
                cell["status_value"], status_values, cell["direction"])
            median = medians[key]
            cell["median_delta"] = (
                (cell["value"] - median) / abs(median) * 100
                if cell["value"] is not None and median not in (None, 0) else None)

    rows = []
    for company in companies:
        cells = metric_rows[company.id]
        zones = [cell["zone"] for cell in cells.values()]
        has_data = year in years_by_company[company.id] if year else False
        rows.append({
            "id": company.id, "name": company.name, "inn": company.inn or "",
            "has_data": has_data,
            "actuality": "Актуально" if has_data else "Нет данных",
            "critical": any(zone == "critical" for zone in zones),
            "worsening": any(cell["worsening"] for cell in cells.values()),
            "conflicts": sum(cell["conflict"] for cell in cells.values()),
            "quality": "Нет данных" if not has_data else (
                "Конфликт" if any(cell["conflict"] for cell in cells.values())
                else "Неполно" if any(
                    cell["quality"] != "Подтверждено" for cell in cells.values())
                else "Подтверждено"),
            "cells": cells,
            "_severity": max((ZONE_RANK[zone] for zone in zones), default=0),
        })
    rows.sort(key=lambda row: (
        not row["critical"], not row["worsening"], -row["_severity"],
        not row["has_data"], row["name"].lower()))
    for row in rows:
        row.pop("_severity")
    with_data = sum(row["has_data"] for row in rows)
    return {
        "year": year, "years": all_years, "threshold_met": current if year == default_year else (
            coverage_by_year.get(year, 0) >= .8),
        "coverage": coverage_by_year.get(year, 0), "total": len(companies),
        "with_data": with_data, "without_data": len(companies) - with_data,
        "critical": sum(row["critical"] for row in rows),
        "worsening": sum(row["worsening"] for row in rows),
        "conflicts": sum(row["conflicts"] for row in rows),
        "rows": rows, "kpis": [{"key": item[0], "label": item[1]} for item in KPI_SPECS],
        "medians": medians,
    }


__all__ = ["KPI_SPECS", "build_portfolio", "choose_portfolio_year"]