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
    "Для диапазона лет потоки суммируются только при наличии каждого года; пропуски не заменяются нулём.",
    "Сравнение выполняется с эквивалентным непосредственно предшествующим диапазоном.",
    "Остатки берутся на последнюю границу диапазона, а отношения пересчитываются только из подтверждённых компонентов.",
    "Границы вида «меньше» сохранены; для них изменение не рассчитывается.",
]


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.isoformat()


def _period_label(kind, year_from=None, year_to=None):
    """Return the card label without hiding a selected multi-year period."""
    if year_from is None or year_to is None:
        return {
            "flow": "за год",
            "balance": "на 31.12",
            "ratio": "по итогам года",
        }[kind]
    if year_from == year_to:
        return {
            "flow": "за год",
            "balance": "на 31.12",
            "ratio": "по итогам года",
        }[kind]
    period = f"{year_from}–{year_to}"
    if kind == "flow":
        return f"за {period} годы"
    if kind == "balance":
        return f"на 31.12.{year_to}"
    return f"по итогам {period} годов"


def _range_label(year_from, year_to):
    if year_from is None or year_to is None:
        return None
    return str(year_from) if year_from == year_to else f"{year_from}–{year_to}"


def _empty_fact(reason="Показатель отсутствует в принятой годовой версии"):
    return {
        "value": None, "upper_bound": False, "missing_reason": reason,
        "provenance": None, "provenance_by_year": {},
        "provenance_components": [],
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
            fact["provenance_by_year"] = {
                row.year_version.year: fact["provenance"],
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
    provenance_by_year = {row.year_version.year: provenance}
    if row.is_missing:
        return {
            **_empty_fact("В источнике явно указан пропуск"),
            "upper_bound": bool(row.is_upper_bound), "provenance": provenance,
            "provenance_by_year": provenance_by_year,
        }
    if row.value_numeric is None or not math.isfinite(float(row.value_numeric)):
        return {
            **_empty_fact("Значение источника не является числом"),
            "upper_bound": bool(row.is_upper_bound), "provenance": provenance,
            "provenance_by_year": provenance_by_year,
        }
    value = float(row.value_numeric)
    if spec["unit"] == "RUB":
        value *= 1000.0
    return {
        "value": value, "upper_bound": bool(row.is_upper_bound),
        "missing_reason": None, "provenance": provenance,
        "provenance_by_year": provenance_by_year,
    }


def _comparison(current, previous, previous_reason=None):
    if previous is None:
        return None, None, previous_reason or (
            "Нет данных за непосредственно предшествующий календарный год")
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


def _component_spec(section, keys, source_unit, unit="RUB", kind="flow"):
    """Create an internal source specification for ratio recalculation."""
    return {
        "section": section,
        "keys": tuple(_key(key) for key in keys),
        "source_unit": source_unit,
        "unit": unit,
        "kind": kind,
    }


_RATIO_COMPONENTS = {
    # A margin over a range is a ratio of the corresponding interval totals.
    "profitability": (
        _component_spec(
            "financial_results", ("Прибыль (убыток) от продаж",),
            "thousand_rubles", "RUB"),
        _component_spec(
            "financial_results", ("Выручка",),
            "thousand_rubles", "RUB"),
    ),
    # Liquidity is a point-in-time ratio, therefore its components come from
    # the last boundary rather than being summed or averaged.
    "current_liquidity": (
        _component_spec(
            "assets", ("Оборотные активы, всего",),
            "thousand_rubles", "RUB", "balance"),
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance"),
    ),
}


def _provenance_for(facts):
    items = []
    seen = set()
    for fact in facts:
        provenance = fact.get("provenance")
        values = provenance if isinstance(provenance, list) else (provenance,)
        for value in values:
            if not isinstance(value, dict):
                continue
            marker = tuple(sorted(value.items()))
            if marker not in seen:
                seen.add(marker)
                items.append(value)
    if not items:
        return None
    return items[0] if len(items) == 1 else items


def _provenance_by_year(facts):
    result = {}
    for year, fact in facts:
        provenance = fact.get("provenance")
        if provenance:
            result[year] = provenance
    return result


def _ratio_provenance(facts):
    """Keep both ratio components, even when they share a calendar year."""
    records = []
    for component, fact in facts:
        by_year = fact.get("provenance_by_year") or {}
        if isinstance(by_year, dict):
            items = by_year.items()
        else:
            # Future-compatible with a list-shaped component provenance.
            items = (
                (item.get("year"), item.get("provenance"))
                for item in by_year
                if isinstance(item, dict)
            )
        for year, provenance in items:
            if provenance:
                records.append({
                    "year": year,
                    "component": component,
                    "provenance": provenance,
                })
    return sorted(records, key=lambda item: (
        item.get("year") or 0, item.get("component") or ""))


def _missing_years_reason(label, facts, year_from, year_to):
    missing = [
        (year, fact.get("missing_reason") or "нет подтверждённого значения")
        for year, fact in facts if fact.get("value") is None
    ]
    years = ", ".join(str(year) for year, _ in missing)
    details = "; ".join(f"{year}: {reason}" for year, reason in missing)
    return (
        f"Невозможно рассчитать {label} за весь диапазон "
        f"{_range_label(year_from, year_to)}: отсутствуют подтверждённые "
        f"значения за {years} ({details})."
    )


def _aggregate_component(facts, spec, year_from, year_to):
    """Aggregate one numerator/denominator using its accounting kind."""
    selected = [(year, facts.get(year, _empty_fact(
        "За этот календарный год нет принятой версии")))
                for year in range(year_from, year_to + 1)]
    if spec["kind"] == "balance":
        boundary_year, boundary_fact = selected[-1]
        if boundary_fact.get("value") is None:
            return {
                **boundary_fact,
                "missing_reason": (
                    f"На границе 31.12.{boundary_year} нет подтверждённого "
                    f"компонента: {boundary_fact.get('missing_reason') or 'пропуск'}."),
            }
        return boundary_fact
    if any(fact.get("value") is None for _, fact in selected):
        values = [fact for _, fact in selected]
        return {
            **_empty_fact(
                _missing_years_reason(
                    "компонент", selected, year_from, year_to)),
            "upper_bound": any(fact.get("upper_bound") for fact in values),
            "provenance": _provenance_for(values),
            "provenance_by_year": _provenance_by_year(selected),
        }
    values = [fact for _, fact in selected]
    return {
        "value": sum(fact["value"] for fact in values),
        "upper_bound": any(fact.get("upper_bound") for fact in values),
        "missing_reason": None,
        "provenance": _provenance_for(values),
        "provenance_by_year": _provenance_by_year(selected),
    }


def _aggregate_fact(facts, spec, year_from, year_to):
    """Apply the contract for flows and balances over an interval."""
    selected = [(year, facts.get(year, _empty_fact(
        "За этот календарный год нет принятой версии")))
                for year in range(year_from, year_to + 1)]
    if year_from == year_to:
        return selected[0][1]
    if spec["kind"] == "balance":
        boundary_year, boundary_fact = selected[-1]
        if boundary_fact.get("value") is None:
            return {
                **boundary_fact,
                "missing_reason": (
                    f"На границе 31.12.{boundary_year} нет подтверждённого "
                    f"значения: {boundary_fact.get('missing_reason') or 'пропуск'}."),
            }
        return boundary_fact
    if any(fact.get("value") is None for _, fact in selected):
        values = [fact for _, fact in selected]
        return {
            **_empty_fact(
                _missing_years_reason(
                    f"показатель «{spec['title']}»", selected,
                    year_from, year_to)),
            "upper_bound": any(fact.get("upper_bound") for fact in values),
            "provenance": _provenance_for(values),
            "provenance_by_year": _provenance_by_year(selected),
        }
    values = [fact for _, fact in selected]
    return {
        "value": sum(fact["value"] for fact in values),
        # Retaining the bound marker prevents a comparison from presenting a
        # sum of bounds as an exact change.
        "upper_bound": any(fact.get("upper_bound") for fact in values),
        "missing_reason": None,
        "provenance": _provenance_for(values),
        "provenance_by_year": _provenance_by_year(selected),
    }


def _aggregate_ratio(spec, facts_by_spec, year_from, year_to):
    """Recalculate a supported ratio without averaging annual ratios."""
    components = _RATIO_COMPONENTS.get(spec["id"])
    if not components:
        return _empty_fact(
            f"Показатель «{spec['title']}» нельзя достоверно пересчитать "
            "за многолетний диапазон: формула не поддержана.")
    numerator_spec, denominator_spec = components
    numerator = _aggregate_component(
        facts_by_spec[numerator_spec["keys"]], numerator_spec,
        year_from, year_to)
    denominator = _aggregate_component(
        facts_by_spec[denominator_spec["keys"]], denominator_spec,
        year_from, year_to)
    component_facts = (
        ("numerator", numerator), ("denominator", denominator))
    provenance_records = _ratio_provenance(component_facts)
    # Multi-year ratios always expose the stable component-aware list.  A
    # plain single-year ratio continues to use the legacy provenance object.
    provenance = provenance_records
    provenance_by_year = provenance_records
    if numerator.get("value") is None or denominator.get("value") is None:
        return {
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан за диапазон "
                f"{_range_label(year_from, year_to)}: отсутствует полный "
                "набор поддержанных числителя и знаменателя; исходные "
                "годовые отношения не суммируются и не усредняются."),
            "provenance": provenance,
            "provenance_by_year": provenance_by_year,
            "provenance_components": provenance_records,
        }
    if numerator.get("upper_bound") or denominator.get("upper_bound"):
        return {
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан: компонент "
                "содержит верхнюю границу."),
            "upper_bound": True,
            "provenance": provenance,
            "provenance_by_year": provenance_by_year,
            "provenance_components": provenance_records,
        }
    denominator_value = denominator["value"]
    if denominator_value == 0:
        return {
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан: знаменатель "
                "равен нулю."),
            "provenance": provenance,
            "provenance_by_year": provenance_by_year,
            "provenance_components": provenance_records,
        }
    if spec["id"] == "profitability":
        value = numerator["value"] / denominator_value * 100.0
    else:
        value = numerator["value"] / denominator_value
    return {
        "value": value, "upper_bound": False, "missing_reason": None,
        "provenance": provenance, "provenance_by_year": provenance_by_year,
        "provenance_components": provenance_records,
    }


def _normalise_range(requested_year, year_from, year_to):
    """Validate the public range arguments and return an explicit range flag."""
    has_from = year_from is not None
    has_to = year_to is not None
    if has_from != has_to:
        raise ValueError("Параметры year_from и year_to должны передаваться вместе")
    if has_from:
        if requested_year is not None and requested_year != year_to:
            raise ValueError(
                "Параметр year должен совпадать с концом выбранного диапазона")
        if year_from > year_to:
            raise ValueError("Начальный год не может быть больше конечного")
        return year_from, year_to, True
    if requested_year is not None:
        return requested_year, requested_year, False
    return None, None, False


def build_annual_key_indicators(
    company, requested_year=None, year_from=None, year_to=None
):
    """Build the API response from active historical versions in one tenant.

    ``requested_year`` remains the original single-year API.  The explicit
    ``year_from``/``year_to`` pair selects an inclusive interval; a missing
    year makes a flow unavailable rather than silently producing a partial
    total.
    """
    versions = HistoricalYearVersion.query.join(HistoricalImport).filter(
        HistoricalYearVersion.company_id == company.id,
        HistoricalYearVersion.is_active.is_(True),
        HistoricalImport.status == "active",
    ).order_by(HistoricalYearVersion.year).all()
    years = [version.year for version in versions]
    selected_from, selected_to, explicit_range = _normalise_range(
        requested_year, year_from, year_to)
    if (requested_year is not None and not explicit_range
            and requested_year not in years):
        raise ValueError("Запрошенный год отсутствует среди принятых данных")
    if selected_from is None:
        selected_year = years[-1] if years else None
        selected_from = selected_to = selected_year
    else:
        selected_year = selected_to
    timeline = list(range(years[0], years[-1] + 1)) if years else []
    interval_length = (
        selected_to - selected_from + 1
        if selected_from is not None and selected_to is not None else 0)
    previous_from = (
        selected_from - interval_length
        if selected_from is not None else None)
    previous_to = (
        selected_from - 1
        if selected_from is not None else None)
    available_years = set(years)
    previous_complete = (
        previous_from is not None
        and all(year in available_years
                for year in range(previous_from, previous_to + 1)))
    previous_year = previous_to if previous_complete else None

    rows_by_year = {}
    for version in versions:
        # Querying by the active version id prevents inactive, unapproved, and
        # cross-company values from entering the result.
        rows_by_year[version.year] = HistoricalMetricValue.query.filter_by(
            year_version_id=version.id).all()

    fact_years = set(timeline)
    if selected_from is not None:
        fact_years.update(range(selected_from, selected_to + 1))
    if previous_from is not None:
        fact_years.update(range(previous_from, previous_to + 1))
    fact_years = sorted(fact_years)
    facts = {}
    for spec in CARD_SPECS:
        facts[spec["id"]] = {
            year: _fact(rows_by_year.get(year, ()), spec)
            if year in rows_by_year else _empty_fact(
                "За этот календарный год нет принятой версии")
            for year in fact_years
        }

    # Ratio components are read from the same selected active versions, but
    # remain separate from the public card facts to avoid alias substitution.
    component_specs = {}
    for components in _RATIO_COMPONENTS.values():
        for component in components:
            component_specs.setdefault(component["keys"], component)
    component_facts = {
        keys: {
            year: _fact(rows_by_year.get(year, ()), component)
            if year in rows_by_year else _empty_fact(
                "За этот календарный год нет принятой версии")
            for year in fact_years
        }
        for keys, component in component_specs.items()
    }
    range_facts = {}
    previous_facts = {}
    for spec in CARD_SPECS:
        if selected_from is None:
            range_facts[spec["id"]] = _empty_fact(
                "Нет принятых годовых данных")
            previous_facts[spec["id"]] = None
            continue
        if spec["kind"] == "ratio" and selected_from != selected_to:
            range_facts[spec["id"]] = _aggregate_ratio(
                spec, component_facts, selected_from, selected_to)
        else:
            range_facts[spec["id"]] = _aggregate_fact(
                facts[spec["id"]], spec, selected_from, selected_to)
        # A sparse equivalent period must not hide a usable point-in-time
        # balance at its final boundary.  Flow aggregation deliberately sees
        # every missing year and returns an unavailable fact itself.
        if previous_from is not None:
            if spec["kind"] == "ratio" and previous_from != previous_to:
                previous_facts[spec["id"]] = _aggregate_ratio(
                    spec, component_facts, previous_from, previous_to)
            else:
                previous_facts[spec["id"]] = _aggregate_fact(
                    facts[spec["id"]], spec, previous_from, previous_to)
        else:
            previous_facts[spec["id"]] = None

    cards = []
    for spec in CARD_SPECS:
        current = range_facts[spec["id"]]
        previous = previous_facts[spec["id"]]
        previous_reason = None
        if selected_from is not None and not previous_complete:
            previous_reason = (
                f"Сравнение недоступно: нет полного сопоставимого периода "
                f"{_range_label(previous_from, previous_to)}.")
        delta, delta_percent, note = _comparison(
            current, previous, previous_reason)
        series = [{
            "year": year, "value": facts[spec["id"]][year]["value"],
            "upper_bound": facts[spec["id"]][year]["upper_bound"],
            "missing_reason": facts[spec["id"]][year]["missing_reason"],
        } for year in timeline]
        cards.append({
            "id": spec["id"], "title": spec["title"],
            "value": current["value"], "unit": spec["unit"],
            "kind": spec["kind"], "period_label": _period_label(
                spec["kind"], selected_from, selected_to),
            "missing_reason": current["missing_reason"],
            "upper_bound": current["upper_bound"],
            "previous_value": previous["value"] if previous else None,
            "previous_upper_bound": (
                previous["upper_bound"] if previous else False),
            "delta": delta, "delta_percent": delta_percent,
            "comparison_note": note,
            "series": series,
            "provenance": current["provenance"],
            "provenance_by_year": current.get("provenance_by_year", {}),
            "provenance_components": current.get("provenance_components", []),
            "previous_period_label": _range_label(previous_from, previous_to),
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
    if (selected_from is not None and selected_to is not None
            and any(year not in available_years
                    for year in range(selected_from, selected_to + 1))):
        warnings.append(
            f"Выбранный диапазон {_range_label(selected_from, selected_to)} "
            "неполный; потоки не суммируются по частичным данным.")
    current_period_label = _range_label(selected_from, selected_to)
    previous_range = None
    if selected_from is not None:
        previous_range = {
            "year_from": previous_from,
            "year_to": previous_to,
            "period_label": _range_label(previous_from, previous_to),
            "complete": previous_complete,
        }
    return {
        "mode": "annual",
        "company": {"id": company.id, "name": company.name},
        "years": years, "timeline_years": timeline, "year": selected_year,
        "year_from": selected_from, "year_to": selected_to,
        "period_label": current_period_label,
        "previous_year": previous_year,
        "previous_year_from": previous_from,
        "previous_year_to": previous_to,
        "previous_period_label": _range_label(previous_from, previous_to),
        "previous_range": previous_range,
        "updated_at": _iso(latest_activation),
        "sources": sources, "cards": cards, "warnings": warnings,
        "methodology": list(METHODOLOGY),
    }


__all__ = ["CARD_SPECS", "build_annual_key_indicators"]