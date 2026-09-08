# -*- coding: utf-8 -*-
"""Annual key indicators sourced exclusively from accepted historical rows."""
from __future__ import annotations

from datetime import timezone
import math

from database import HistoricalImport, HistoricalMetricValue, HistoricalYearVersion
from historical_parser import canonical_metric_key


def _key(label):
    return canonical_metric_key(label)


# Section is part of every mapping on purpose: similarly named ratios in other
# workbook sections are methodologically different and must not be substituted.
CARD_SPECS = (
    {
        "id": "revenue", "title": "Выручка", "unit": "RUB", "kind": "flow",
        "section": "financial_results", "keys": (_key("Выручка"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "cogs", "title": "Себестоимость продаж", "unit": "RUB",
        "kind": "flow", "section": "financial_results",
        "keys": (_key("Себестоимость продаж"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "net_profit", "title": "Чистая прибыль (убыток)", "unit": "RUB",
        "kind": "flow", "section": "financial_results",
        "keys": (_key("Чистая прибыль (убыток)"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "profitability", "title": "Рентабельность продаж",
        "unit": "percent", "kind": "ratio", "section": "profitability",
        "keys": (_key("Рентабельность продаж (прибыль от продаж / выручка)"),),
        "source_unit": "percent",
    },
    {
        "id": "cash",
        "title": "Денежные средства и краткосрочные финансовые вложения",
        "unit": "RUB", "kind": "balance", "section": "assets",
        "keys": (_key("денежные средства и краткосрочные фин. вложения"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "ar", "title": "Дебиторская задолженность", "unit": "RUB",
        "kind": "balance", "section": "assets",
        "keys": (_key("дебиторская задолженность"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "ap", "title": "Кредиторская задолженность", "unit": "RUB",
        "kind": "balance", "section": "liabilities",
        "keys": (_key("кредиторская задолженность"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "equity", "title": "Собственный капитал", "unit": "RUB",
        "kind": "balance", "section": "liabilities",
        "keys": (_key("Собственный капитал"),),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "assets", "title": "Активы", "unit": "RUB", "kind": "balance",
        "section": "assets",
        "keys": (_key("ВАЛЮТА БАЛАНСА"), _key("ИТОГО")),
        "source_unit": "thousand_rubles",
    },
    {
        "id": "current_liquidity", "title": "Текущая ликвидность",
        "unit": "ratio", "kind": "ratio", "section": "liquidity",
        "keys": (_key("Коэффициент текущей (общей) ликвидности"),),
        "source_unit": "ratio",
    },
)

METHODOLOGY = [
    "Денежные показатели показаны в рублях; исходные тысячи рублей умножены на 1 000.",
    "Сравнение выполняется только с непосредственно предшествующим календарным годом.",
    "Границы вида «меньше» сохранены; для них изменение не рассчитывается.",
]


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.isoformat()


def _period_label(kind):
    return {
        "flow": "за год",
        "balance": "на 31.12",
        "ratio": "по итогам года",
    }[kind]


def _empty_fact(reason="Показатель отсутствует в принятой годовой версии"):
    return {
        "value": None, "upper_bound": False, "missing_reason": reason,
        "provenance": None,
    }


def _fact(rows, spec):
    candidates = [
        row for row in rows
        if row.section_key == spec["section"]
        and row.metric_key in spec["keys"]
    ]
    if not candidates:
        return _empty_fact()
    unit_rows = [row for row in candidates if row.unit == spec["source_unit"]]
    if not unit_rows:
        units = ", ".join(sorted({row.unit for row in candidates}))
        fact = _empty_fact(
            "Единица источника не соответствует показателю"
            + (f": {units}" if units else ""))
        # A single rejected cell is still useful audit evidence. With several
        # candidates, attaching one of them would falsely imply precedence.
        if len(candidates) == 1:
            row = candidates[0]
            raw = row.raw_value
            fact["provenance"] = {
                "filename": row.year_version.historical_import.original_filename,
                "sheet": raw.sheet_name if raw else "",
                "cell": raw.cell_ref if raw else "",
            }
        return fact
    # Two aliases (or parallel columns of the same unit) are not silently
    # prioritised: doing so would make the result dependent on row order.
    if len(unit_rows) != 1:
        return _empty_fact("Неоднозначный источник: найдено несколько значений")
    row = unit_rows[0]
    raw = row.raw_value
    provenance = {
        "filename": row.year_version.historical_import.original_filename,
        "sheet": raw.sheet_name if raw else "",
        "cell": raw.cell_ref if raw else "",
    }
    if row.is_missing:
        return {
            **_empty_fact("В источнике явно указан пропуск"),
            "upper_bound": bool(row.is_upper_bound), "provenance": provenance,
        }
    if row.value_numeric is None or not math.isfinite(float(row.value_numeric)):
        return {
            **_empty_fact("Значение источника не является числом"),
            "upper_bound": bool(row.is_upper_bound), "provenance": provenance,
        }
    value = float(row.value_numeric)
    if spec["unit"] == "RUB":
        value *= 1000.0
    return {
        "value": value, "upper_bound": bool(row.is_upper_bound),
        "missing_reason": None, "provenance": provenance,
    }


def _comparison(current, previous):
    if previous is None:
        return None, None, "Нет данных за непосредственно предшествующий календарный год"
    if current["value"] is None or previous["value"] is None:
        return None, None, "Сравнение недоступно из-за пропуска значения"
    if current["upper_bound"] or previous["upper_bound"]:
        return None, None, "Изменение не рассчитано для значения с верхней границей"
    delta = current["value"] - previous["value"]
    if previous["value"] == 0:
        return delta, None, "Относительное изменение не рассчитано: предыдущее значение равно нулю"
    if previous["value"] < 0:
        return delta, None, "Относительное изменение не рассчитано для отрицательной базы"
    return delta, delta / previous["value"] * 100.0, None


def build_annual_key_indicators(company, requested_year=None):
    """Build the API response from active historical versions in one tenant."""
    versions = HistoricalYearVersion.query.join(HistoricalImport).filter(
        HistoricalYearVersion.company_id == company.id,
        HistoricalYearVersion.is_active.is_(True),
        HistoricalImport.status == "active",
    ).order_by(HistoricalYearVersion.year).all()
    years = [version.year for version in versions]
    if requested_year is not None and requested_year not in years:
        raise ValueError("Запрошенный год отсутствует среди принятых данных")
    selected_year = requested_year if requested_year is not None else (
        years[-1] if years else None)
    timeline = list(range(years[0], years[-1] + 1)) if years else []
    previous_year = (
        selected_year - 1
        if selected_year is not None and selected_year - 1 in years else None)

    rows_by_year = {}
    for version in versions:
        # Querying by the active version id prevents inactive, unapproved, and
        # cross-company values from entering the result.
        rows_by_year[version.year] = HistoricalMetricValue.query.filter_by(
            year_version_id=version.id).all()

    facts = {
        spec["id"]: {
            year: _fact(rows_by_year.get(year, ()), spec) if year in rows_by_year
            else _empty_fact("За этот календарный год нет принятой версии")
            for year in timeline
        }
        for spec in CARD_SPECS
    }
    cards = []
    for spec in CARD_SPECS:
        current = (
            facts[spec["id"]][selected_year]
            if selected_year is not None else _empty_fact("Нет принятых годовых данных"))
        previous = (
            facts[spec["id"]][previous_year]
            if previous_year is not None else None)
        delta, delta_percent, note = _comparison(current, previous)
        cards.append({
            "id": spec["id"], "title": spec["title"],
            "value": current["value"], "unit": spec["unit"],
            "kind": spec["kind"], "period_label": _period_label(spec["kind"]),
            "missing_reason": current["missing_reason"],
            "upper_bound": current["upper_bound"],
            "previous_value": previous["value"] if previous else None,
            "previous_upper_bound": (
                previous["upper_bound"] if previous else False),
            "delta": delta, "delta_percent": delta_percent,
            "comparison_note": note,
            "series": [{
                "year": year, "value": facts[spec["id"]][year]["value"],
                "upper_bound": facts[spec["id"]][year]["upper_bound"],
            } for year in timeline],
            "provenance": current["provenance"],
        })

    activation_values = [
        version.activated_at for version in versions
        if version.activated_at is not None]
    # ISO UTC timestamps are also safe when a SQLite fixture returns naive
    # datetimes while another driver returns aware datetimes.
    latest_activation = max(
        activation_values, key=lambda value: _iso(value),
        default=None)
    sources = [{
        "year": version.year,
        "filename": version.historical_import.original_filename,
        "uploaded_at": _iso(version.historical_import.created_at),
        "version": str(version.version_no),
    } for version in versions]
    warnings = []
    if years and len(timeline) != len(years):
        warnings.append("В ряду лет есть пропуски; они не заменены данными соседних лет.")
    if not years:
        warnings.append("Нет принятых активных годовых данных.")
    return {
        "mode": "annual",
        "company": {"id": company.id, "name": company.name},
        "years": years, "timeline_years": timeline, "year": selected_year,
        "previous_year": previous_year, "updated_at": _iso(latest_activation),
        "sources": sources, "cards": cards, "warnings": warnings,
        "methodology": list(METHODOLOGY),
    }


__all__ = ["CARD_SPECS", "build_annual_key_indicators"]