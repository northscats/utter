# -*- coding: utf-8 -*-
"""Annual key indicators sourced exclusively from accepted historical rows."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal
import math
import re

from database import HistoricalImport, HistoricalMetricValue, HistoricalYearVersion
from annual_source_passport import build_annual_source_passport
from historical_parser import (
    SOURCE_PRECISION_METADATA_PREFIX, _unit, _value, canonical_metric_key,
    normalize_name,
)


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
    # The following cards are deliberately appended after the original ten:
    # consumers of this endpoint have historically treated the first ten
    # cards (and their order) as the legacy contract.
    {
        "id": "autonomy", "title": "Коэффициент автономии",
        "unit": "ratio", "kind": "balance", "derived": "autonomy",
        "formula": "Собственный капитал / Валюта баланса",
        "calculation_note": (
            "Рассчитан по компонентам баланса на последнюю границу периода; "
            "опубликованный коэффициент автономии не подставляется."),
    },
    {
        "id": "net_working_capital", "title": "Чистый оборотный капитал (NWC)",
        "unit": "RUB", "kind": "balance", "derived": "net_working_capital",
        "formula": "Оборотные активы, всего - Краткосрочные обязательства, всего",
        "calculation_note": (
            "Рассчитан как разность подтверждённых балансовых компонентов "
            "на 31 декабря; пропуск не заменяется нулём."),
    },
    {
        "id": "quick_liquidity", "title": "Коэффициент быстрой ликвидности",
        "unit": "ratio", "kind": "balance", "derived": "quick_liquidity",
        "formula": (
            "(Денежные средства и краткосрочные финансовые вложения + "
            "Дебиторская задолженность) / Краткосрочные обязательства, всего"),
        "calculation_note": (
            "Рассчитан по денежным средствам, дебиторской задолженности и "
            "краткосрочным обязательствам на 31 декабря; знаменатель должен "
            "быть положительным."),
    },
    {
        "id": "absolute_liquidity", "title": "Коэффициент абсолютной ликвидности",
        "unit": "ratio", "kind": "balance", "derived": "absolute_liquidity",
        "formula": (
            "Денежные средства и краткосрочные финансовые вложения / "
            "Краткосрочные обязательства, всего"),
        "calculation_note": (
            "Рассчитан по подтверждённым денежным средствам и краткосрочным "
            "обязательствам на 31 декабря; знаменатель должен быть положительным."),
    },
    {
        "id": "short_term_liabilities",
        "title": "Краткосрочные обязательства",
        "unit": "RUB", "kind": "balance", "derived": "short_term_liabilities",
        "section": "liabilities",
        "keys": (_key("Краткосрочные обязательства, всего"),),
        "source_unit": "thousand_rubles",
        "formula": "Краткосрочные обязательства, всего",
        "calculation_note": (
            "Подтверждённый остаток пассива на 31 декабря; значение источника "
            "не суммируется по годам."),
    },
    {
        "id": "long_term_liabilities",
        "title": "Долгосрочные обязательства",
        "unit": "RUB", "kind": "balance", "derived": "long_term_liabilities",
        "section": "liabilities",
        "keys": (_key("Долгосрочные обязательства, всего"),),
        "source_unit": "thousand_rubles",
        "formula": "Долгосрочные обязательства, всего",
        "calculation_note": (
            "Подтверждённый остаток пассива на 31 декабря; значение источника "
            "не суммируется по годам."),
    },
    {
        "id": "short_term_share", "title": "Доля краткосрочных обязательств",
        "unit": "percent", "kind": "balance", "derived": "short_term_share",
        "formula": (
            "Краткосрочные обязательства, всего / (Краткосрочные обязательства, "
            "всего + Долгосрочные обязательства, всего) × 100"),
        "calculation_note": (
            "Рассчитана по двум видам обязательств на 31 декабря; общий "
            "знаменатель должен быть положительным."),
    },
    {
        "id": "long_term_share", "title": "Доля долгосрочных обязательств",
        "unit": "percent", "kind": "balance", "derived": "long_term_share",
        "formula": (
            "Долгосрочные обязательства, всего / (Краткосрочные обязательства, "
            "всего + Долгосрочные обязательства, всего) × 100"),
        "calculation_note": (
            "Рассчитана по двум видам обязательств на 31 декабря; общий "
            "знаменатель должен быть положительным."),
    },
    {
        "id": "interest_coverage", "title": "Покрытие процентов (ICR)",
        "unit": "ratio", "kind": "ratio", "derived": "interest_coverage",
        "formula": "EBIT (прибыль до % и налогов) / Проценты к уплате",
        "calculation_note": (
            "Рассчитано как отношение сумм потоков EBIT и процентов к уплате "
            "за весь выбранный период; усреднение годовых коэффициентов не "
            "выполняется, знаменатель должен быть положительным."),
    },
)

METHODOLOGY = [
    "Денежные показатели показаны в рублях; исходные тысячи рублей умножены на 1 000.",
    "Для диапазона лет потоки суммируются только при наличии каждого года; пропуски не заменяются нулём.",
    "Сравнение выполняется с эквивалентным непосредственно предшествующим диапазоном.",
    "Остатки берутся на последнюю границу диапазона, а отношения пересчитываются только из подтверждённых компонентов.",
    "Границы вида «меньше» сохранены; для них изменение не рассчитывается.",
    "Новые карточки финансовой устойчивости, NWC, ликвидности и структуры обязательств "
    "не используют опубликованные отношения: они единообразно пересчитываются из "
    "подтверждённых компонентов по формулам карточек.",
    "Для балансовых новых карточек (в том числе ликвидности, автономии и долей) "
    "используется граница 31 декабря; для ICR суммируются потоки всех лет диапазона.",
    "Пропуски, верхние границы и неоднозначные источники делают расчёт недоступным. "
    "Знаменатель каждого нового отношения должен быть строго положительным; "
    "подстановка нуля и усреднение годовых отношений запрещены.",
    "Сверка с опубликованным коэффициентом справочная: исходное значение книги, "
    "расчёт и рейтинг не подменяются; принимаются только точные раздел, метка, "
    "год и единица источника.",
    "Разница в сверке равна независимый расчёт минус опубликованное значение; "
    "признак возможного округления определяется по исходной точности ячейки.",
    "Для балансовых отношений в диапазоне сравнивается граница последнего года; "
    "потоковый ICR за диапазон не сравнивается с коэффициентом одного года.",
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


_EBIT_LABEL = "EBIT (прибыль до % и налогов)"


def _legacy_ebit_fact(row, spec):
    """Read one persisted EBIT row corrected only from its raw evidence.

    Older imports classified the percent sign in the confirmed EBIT label as
    the unit, even when the annual cell was a money value.  This is an
    in-memory compatibility correction for the annual component extractor:
    the persisted row is never mutated and no other mislabelled metric is
    accepted.  Both the raw label/period and raw cell text must make the
    parser's corrected ``thousand_rubles`` result unambiguous.
    """
    if (
        row.unit != "percent"
        or row.is_missing
        or row.section_key != "financial_results"
        or row.metric_key != _key(_EBIT_LABEL)
        or normalize_name(row.metric_label) != normalize_name(_EBIT_LABEL)
        or spec.get("source_unit") != "thousand_rubles"
    ):
        return None
    raw = row.raw_value
    if raw is None:
        return None
    raw_text = raw.raw_text or ""
    period = row.period_label or ""
    if not str(period).strip() or str(row.year_version.year) not in str(period):
        return None
    corrected_unit, factor = _unit(
        row.metric_label, period, row.section_key, raw_text)
    parsed = _value(
        raw_text, corrected_unit, factor, row.year_version.year,
        period, raw.cell_ref)
    # Explicit percent evidence in the value/header (including an upper bound)
    # remains percent and is deliberately rejected by the normal unit gate.
    if (
        corrected_unit != "thousand_rubles"
        or parsed.get("unit") != "thousand_rubles"
        or parsed.get("value") is None
        or not math.isfinite(float(parsed["value"]))
    ):
        return None
    provenance = {
        "filename": row.year_version.historical_import.original_filename,
        "sheet": raw.sheet_name if raw else "",
        "cell": raw.cell_ref if raw else "",
    }
    value = float(parsed["value"])
    if spec.get("unit") == "RUB":
        value *= 1000.0
    return {
        "value": value,
        "upper_bound": bool(parsed.get("is_upper_bound") or row.is_upper_bound),
        "missing_reason": None,
        "provenance": provenance,
        "provenance_by_year": {row.year_version.year: provenance},
        "provenance_components": [],
        "correction_note": (
            "Применена только для годового ICR совместимость с ранее "
            "сохранённой строкой EBIT: знак «%» в точной метке "
            "«EBIT (прибыль до % и налогов)» не является единицей; "
            "единица подтверждена parser по периоду и исходному тексту ячейки."
        ),
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
    corrected_rows = [
        corrected for row in candidates
        if (corrected := _legacy_ebit_fact(row, spec)) is not None
    ]
    unit_rows_count = len(unit_rows) + len(corrected_rows)
    if not unit_rows_count:
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
    if unit_rows_count != 1:
        return _empty_fact("Неоднозначный источник: найдено несколько значений")
    if corrected_rows:
        return corrected_rows[0]
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


def _component_spec(
    section, keys, source_unit, unit="RUB", kind="flow", label=None
):
    """Create an internal source specification for ratio recalculation."""
    return {
        "section": section,
        "keys": tuple(_key(key) for key in keys),
        "source_label": keys[0] if keys else None,
        "source_unit": source_unit,
        "unit": unit,
        "kind": kind,
        "label": label,
    }


def _component_identity(spec):
    """Return a collision-safe identity for a source component.

    A metric key is only unique inside a parser section.  In particular,
    ``ВАЛЮТА БАЛАНСА`` is valid in both ``assets`` and ``liabilities``.
    Keeping the section and unit in this identity prevents a newly added
    derived card from accidentally reusing a component from another section.
    """
    return (
        spec["section"], spec["keys"], spec["source_unit"],
        spec.get("unit"), spec.get("kind"),
    )


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


# New cards intentionally have their own component catalogue instead of
# reading published ratios.  The parser labels here are exact, confirmed
# labels; aliases from another section are never substituted.
_DERIVED_COMPONENTS = {
    "autonomy": (
        _component_spec(
            "liabilities", ("Собственный капитал",),
            "thousand_rubles", "RUB", "balance", "equity"),
        _component_spec(
            "assets", ("ВАЛЮТА БАЛАНСА",),
            "thousand_rubles", "RUB", "balance", "assets"),
    ),
    "net_working_capital": (
        _component_spec(
            "assets", ("Оборотные активы, всего",),
            "thousand_rubles", "RUB", "balance", "current_assets"),
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
    ),
    "quick_liquidity": (
        _component_spec(
            "assets",
            ("денежные средства и краткосрочные фин. вложения",),
            "thousand_rubles", "RUB", "balance", "cash"),
        _component_spec(
            "assets", ("дебиторская задолженность",),
            "thousand_rubles", "RUB", "balance", "accounts_receivable"),
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
    ),
    "absolute_liquidity": (
        _component_spec(
            "assets",
            ("денежные средства и краткосрочные фин. вложения",),
            "thousand_rubles", "RUB", "balance", "cash"),
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
    ),
    "short_term_liabilities": (
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
    ),
    "long_term_liabilities": (
        _component_spec(
            "liabilities", ("Долгосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "long_term_liabilities"),
    ),
    "short_term_share": (
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
        _component_spec(
            "liabilities", ("Долгосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "long_term_liabilities"),
    ),
    "long_term_share": (
        _component_spec(
            "liabilities", ("Краткосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "short_term_liabilities"),
        _component_spec(
            "liabilities", ("Долгосрочные обязательства, всего",),
            "thousand_rubles", "RUB", "balance", "long_term_liabilities"),
    ),
    "interest_coverage": (
        _component_spec(
            "financial_results", ("EBIT (прибыль до % и налогов)",),
            "thousand_rubles", "RUB", "flow", "ebit"),
        _component_spec(
            "financial_results", ("Проценты к уплате",),
            "thousand_rubles", "RUB", "flow", "interest_expense"),
    ),
}


# A report comparison is intentionally narrower than the annual card
# catalogue.  The workbook has several similarly named coefficients whose
# definitions are not interchangeable (for example, quick liquidity and
# current liquidity live in the same sheet).  Keep the published metric's
# section, exact label and unit next to the independently calculated formula
# instead of searching the workbook by a loose title.
_REPORT_COMPARISON_SPECS = {
    "profitability": {
        "section": "profitability",
        "label": "Рентабельность продаж (прибыль от продаж / выручка)",
        "unit": "percent",
        "period_kind": "flow",
        "formula_kind": "ratio",
    },
    "current_liquidity": {
        "section": "liquidity",
        "label": "Коэффициент текущей (общей) ликвидности",
        "unit": "ratio",
        "period_kind": "point",
        "formula_kind": "ratio",
    },
    "autonomy": {
        "section": "financial_stability",
        "label": "Коэффициент автономии",
        "unit": "ratio",
        "period_kind": "point",
        "formula_kind": "derived",
        "definition_conflicts": ("Коэффициент финансового левериджа",),
    },
    "quick_liquidity": {
        "section": "liquidity",
        "label": "Коэффициент быстрой (промежуточной) ликвидности",
        "unit": "ratio",
        "period_kind": "point",
        "formula_kind": "derived",
        "definition_conflicts": (
            "Коэффициент текущей (общей) ликвидности",
            "Коэффициент абсолютной ликвидности",
        ),
    },
    "absolute_liquidity": {
        "section": "liquidity",
        "label": "Коэффициент абсолютной ликвидности",
        "unit": "ratio",
        "period_kind": "point",
        "formula_kind": "derived",
        "definition_conflicts": (
            "Коэффициент текущей (общей) ликвидности",
            "Коэффициент быстрой (промежуточной) ликвидности",
        ),
    },
    "interest_coverage": {
        "section": "profitability",
        "label": "Коэффициент покрытия процентов (ICR)",
        "unit": "ratio",
        "period_kind": "flow",
        "formula_kind": "derived",
    },
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
        if isinstance(component, dict):
            component_name = component.get("label") or "component"
            source_label = (
                component.get("source_label")
                or (component.get("keys") or ("",))[0]
            )
        else:
            # Legacy ratio cards intentionally retain their old public
            # ``numerator``/``denominator`` provenance labels.
            component_name = component
            source_label = None
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
                record = {
                    "year": year,
                    "component": component_name,
                    "provenance": provenance,
                }
                if source_label:
                    record["source_label"] = source_label
                records.append(record)
    return sorted(records, key=lambda item: (
        item.get("year") or 0, item.get("component") or ""))


def _report_comparison_template(
    *, status, year, unit, calculated_value=None, reported_value=None,
    delta=None, message="", reported_provenance=None,
    calculated_provenance=None, reported_unit=None, reported_year=None,
):
    """Return the stable, read-only contract for one report comparison.

    ``reported_unit`` and ``reported_year`` are deliberately retained as
    diagnostic fields.  The UI suppresses a reported value when either one is
    incompatible, so a wrong source can never be rendered with the selected
    card's unit or year.
    """
    comparison = {
        "status": status,
        "year": year,
        "unit": unit,
        "calculated_value": calculated_value,
        "reported_value": reported_value,
        "delta": delta,
        "message": message,
        "reported_provenance": reported_provenance,
        "calculated_provenance": list(calculated_provenance or ()),
    }
    if reported_unit is not None:
        comparison["reported_unit"] = reported_unit
    if reported_year is not None:
        comparison["reported_year"] = reported_year
    return comparison


def _raw_precision(raw_text, precision_metadata=None):
    """Read precision only when import recorded explicit source evidence.

    Before source-format metadata existed, imports persisted ``str(value)``.
    That string is ambiguous for numeric Excel cells (``0.4`` could have been
    displayed as 0.4, 0.40, or 0.400), so it must never be treated as proof of
    precision.  New parser imports store a marker in ``value_text`` for either
    a genuinely textual number or an explicit Excel number format.
    """
    metadata = str(precision_metadata or "")
    prefix = SOURCE_PRECISION_METADATA_PREFIX
    if not metadata.startswith(prefix):
        return None
    payload = metadata[len(prefix):]
    source, separator, places = payload.rpartition(":")
    if not separator or source not in {"text", "excel_number_format"}:
        return None
    try:
        precision = int(places)
    except (TypeError, ValueError):
        return None
    return precision if precision >= 0 else None


_ANNUAL_PERIOD_RE = re.compile(
    r"^(?:за\s+)?(?P<year>(?:19|20)\d{2})"
    r"(?:\s*(?:г\.?|год))?$",
    re.IGNORECASE,
)
_YEAR_END_PERIOD_RE = re.compile(
    r"^(?:(?:на|по\s+состоянию\s+на)\s+)?"
    r"(?:(?:31[./-]12[./-](?P<day_first>(?:19|20)\d{2}))|"
    r"(?P<year_first>(?:19|20)\d{2})[./-]12[./-]31|"
    r"(?:31\s+декабря\s+(?P<month_word>(?:19|20)\d{2})))"
    r"(?:\s*(?:г\.?|год))?$",
    re.IGNORECASE,
)


def _report_year_in_period(row, year, period_kind="flow"):
    """Require explicit annual/year-end evidence for the report coefficient.

    A version's year is not enough: a persisted row can still carry an
    interim or multi-year column label.  Both flow and point ratios accept a
    bare annual year (the workbook's normal convention) or an explicit
    31 December label.  No other date, month interval or year interval is
    treated as comparable.
    """
    if period_kind not in {"flow", "point"}:
        return False
    period = str(row.period_label or "").strip()
    period = re.sub(r"\s+", " ", period)
    period = period.replace("–", "-").replace("—", "-").replace("−", "-")
    annual = _ANNUAL_PERIOD_RE.fullmatch(period)
    if annual is not None:
        return int(annual.group("year")) == int(year)
    year_end = _YEAR_END_PERIOD_RE.fullmatch(period)
    if year_end is None:
        return False
    reported_year = (
        year_end.group("day_first")
        or year_end.group("year_first")
        or year_end.group("month_word")
    )
    return reported_year is not None and int(reported_year) == int(year)


def _report_period_years(row):
    period = str(row.period_label or "").strip()
    return re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", period)


def _published_report_fact(rows, report_spec, year):
    """Read one exact published coefficient without fuzzy substitution.

    The ordinary card extractor intentionally accepts canonical parser aliases
    for legacy cards.  A report comparison is stricter: a same-key alias with
    a different visible label is not proof that the published definition is
    the one being recalculated.
    """
    expected_label = report_spec["label"]
    expected_norm = normalize_name(expected_label)
    expected_key = _key(expected_label)
    exact = [
        row for row in rows
        if row.section_key == report_spec["section"]
        and row.metric_key == expected_key
        and normalize_name(row.metric_label) == expected_norm
    ]
    if not exact:
        conflict_norms = {
            normalize_name(label)
            for label in report_spec.get("definition_conflicts", ())
        }
        if any(
            row.section_key == report_spec["section"]
            and row.metric_key == expected_key
            and normalize_name(row.metric_label) != expected_norm
            for row in rows
        ) or any(
            row.section_key == report_spec["section"]
            and normalize_name(row.metric_label) in conflict_norms
            for row in rows
        ):
            return {
                "status": "incompatible_definition",
                "fact": _empty_fact(),
                "reported_unit": None,
                "reported_year": year,
            }
        return {
            "status": "missing_source",
            "fact": _empty_fact(),
            "reported_unit": None,
            "reported_year": year,
        }

    expected_rows = [
        row for row in exact if row.unit == report_spec["unit"]
    ]
    if not expected_rows:
        actual_units = sorted({row.unit for row in exact if row.unit})
        return {
            "status": "incompatible_unit",
            "fact": _empty_fact(),
            "reported_unit": (
                actual_units[0] if len(actual_units) == 1 else "mixed"
            ),
            "reported_year": year,
        }
    # Reuse the established ambiguity, missing-value and provenance rules.
    source_spec = {
        "section": report_spec["section"],
        "keys": (expected_key,),
        "source_unit": report_spec["unit"],
        "unit": report_spec["unit"],
    }
    fact = _fact(exact, source_spec)
    if len(expected_rows) != 1:
        return {
            "status": "missing_source",
            "fact": fact,
            "reported_unit": report_spec["unit"],
            "reported_year": year,
        }
    row = expected_rows[0]
    fact["raw_text"] = row.raw_value.raw_text if row.raw_value else ""
    fact["precision_metadata"] = row.value_text
    fact["reported_unit"] = row.unit
    fact["reported_year"] = year
    if not _report_year_in_period(
        row, year, report_spec.get("period_kind")
    ):
        period_years = _report_period_years(row)
        return {
            "status": "incompatible_year",
            "fact": _empty_fact(),
            "reported_unit": row.unit,
            "reported_year": (
                int(period_years[-1]) if period_years else None
            ),
        }
    if fact.get("value") is None:
        return {
            "status": "missing_source",
            "fact": fact,
            "reported_unit": row.unit,
            "reported_year": year,
        }
    if fact.get("upper_bound"):
        return {
            "status": "reported_upper_bound",
            "fact": fact,
            "reported_unit": row.unit,
            "reported_year": year,
        }
    return {
        "status": "reported",
        "fact": fact,
        "reported_unit": row.unit,
        "reported_year": year,
    }


def _independent_ratio_fact(spec, component_facts, year_from, year_to):
    """Calculate the ratio used only by the report comparison."""
    if year_from is None or year_to is None:
        return _empty_fact("Нет выбранного календарного года")
    if spec.get("derived"):
        return _aggregate_derived(spec, component_facts, year_from, year_to)
    if spec["kind"] == "ratio":
        return _aggregate_ratio(spec, component_facts, year_from, year_to)
    return _empty_fact("Показатель не является отношением")


def _build_report_comparison(
    spec, current_fact, component_facts, rows_by_year,
    selected_from, selected_to,
):
    """Compare a published annual ratio with an independent calculation.

    This helper is intentionally called after the regular cards are built and
    never feeds a value back into those cards.  It is an audit explanation,
    not a source-priority rule.
    """
    report_spec = _REPORT_COMPARISON_SPECS.get(spec["id"])
    if report_spec is None:
        return None

    calculated_from = selected_from
    calculated_to = selected_to
    if selected_from is None or selected_to is None:
        calculated_fact = _empty_fact("Нет выбранного календарного года")
        report_year = None
    elif (
        report_spec["period_kind"] == "point"
        and selected_from != selected_to
    ):
        # A point-in-time balance ratio is comparable at the selected range's
        # endpoint.  Earlier boundaries are not silently averaged.
        calculated_from = calculated_to = selected_to
        calculated_fact = _independent_ratio_fact(
            spec, component_facts, calculated_from, calculated_to)
        report_year = selected_to
    else:
        report_year = selected_to
        calculated_fact = _independent_ratio_fact(
            spec, component_facts, calculated_from, calculated_to)

    calculated_value = calculated_fact.get("value")
    calculated_provenance = calculated_fact.get("provenance_components") or []
    # ICR (and every other flow ratio) over an interval is not comparable to a
    # single annual workbook coefficient.  Keep the independent result and its
    # evidence, but never expose the annual report value or a delta.
    if (
        report_year is not None
        and selected_from is not None
        and selected_to is not None
        and selected_from != selected_to
        and report_spec["period_kind"] == "flow"
    ):
        return _report_comparison_template(
            status="incompatible_period",
            year=report_year,
            unit=report_spec["unit"],
            calculated_value=calculated_value,
            message=(
                "Сверка недоступна: независимый расчёт выполнен за "
                f"период {_range_label(selected_from, selected_to)}, "
                "а опубликованный коэффициент относится к одному году."
            ),
            calculated_provenance=calculated_provenance,
            reported_year=report_year,
        )

    if report_year is None:
        return _report_comparison_template(
            status="missing_source",
            year=None,
            unit=report_spec["unit"],
            calculated_value=calculated_value,
            message="Сверка недоступна: календарный год не выбран.",
            calculated_provenance=calculated_provenance,
        )

    if report_year not in rows_by_year:
        return _report_comparison_template(
            status="missing_year",
            year=report_year,
            unit=report_spec["unit"],
            calculated_value=calculated_value,
            message=(
                "Сверка недоступна: в принятых данных нет версии за "
                f"{report_year} год."
            ),
            calculated_provenance=calculated_provenance,
            reported_year=report_year,
        )

    published = _published_report_fact(
        rows_by_year.get(report_year, ()), report_spec, report_year)
    published_fact = published["fact"]
    published_status = published["status"]
    reported_value = published_fact.get("value")
    reported_provenance = published_fact.get("provenance")
    reported_unit = published.get("reported_unit")
    reported_year = published.get("reported_year")

    if published_status != "reported":
        messages = {
            "missing_source": (
                "Сверка недоступна: в принятой версии нет совместимого "
                "опубликованного коэффициента."
            ),
            "incompatible_definition": (
                "Сверка недоступна: определение опубликованного коэффициента "
                "не совпадает с формулой независимого расчёта."
            ),
            "incompatible_unit": (
                "Сверка недоступна: единица опубликованного коэффициента "
                "не соответствует ожидаемой."
            ),
            "incompatible_year": (
                "Сверка недоступна: период опубликованного коэффициента "
                f"не подтверждает {report_year} год."
            ),
            "reported_upper_bound": (
                "Сверка недоступна: опубликованный коэффициент указан "
                "верхней границей."
            ),
        }
        # A wrong unit/year must not leave a numeric report value next to the
        # selected card unit/year.  Preserve those diagnostics separately.
        if published_status in {
            "incompatible_unit", "incompatible_year", "reported_upper_bound",
        }:
            reported_value = None
            reported_provenance = None
        return _report_comparison_template(
            status=published_status,
            year=report_year,
            unit=report_spec["unit"],
            calculated_value=calculated_value,
            reported_value=reported_value,
            message=messages.get(
                published_status,
                "Сверка недоступна: нет совместимого источника.",
            ),
            reported_provenance=reported_provenance,
            calculated_provenance=calculated_provenance,
            reported_unit=reported_unit,
            reported_year=reported_year,
        )

    if calculated_value is None or not math.isfinite(float(calculated_value)):
        return _report_comparison_template(
            status="missing_calculation",
            year=report_year,
            unit=report_spec["unit"],
            reported_value=reported_value,
            message=(
                "Сверка недоступна: не подтверждён полный набор компонентов "
                "независимого расчёта."
            ),
            reported_provenance=reported_provenance,
            calculated_provenance=calculated_provenance,
            reported_unit=reported_unit,
            reported_year=reported_year,
        )

    calculated_number = float(calculated_value)
    reported_number = float(reported_value)
    delta_decimal = (
        Decimal(str(calculated_number)) - Decimal(str(reported_number))
    )
    delta = float(delta_decimal)
    if not math.isfinite(delta):
        return _report_comparison_template(
            status="missing_calculation",
            year=report_year,
            unit=report_spec["unit"],
            reported_value=reported_value,
            message="Сверка недоступна: разница не является конечным числом.",
            reported_provenance=reported_provenance,
            calculated_provenance=calculated_provenance,
            reported_unit=reported_unit,
            reported_year=reported_year,
        )

    if delta == 0:
        status = "match"
        message = "Значения совпадают."
    else:
        precision = _raw_precision(
            published_fact.get("raw_text"),
            published_fact.get("precision_metadata"),
        )
        rounding_tolerance = (
            float(Decimal(1).scaleb(-precision) / Decimal(2))
            if precision is not None else None
        )
        if (
            rounding_tolerance is not None
            and abs(delta_decimal) <= Decimal(str(rounding_tolerance))
        ):
            status = "rounding"
            message = (
                "Расхождение возможно из-за округления исходной публикации."
            )
        else:
            status = "comparable"
            message = (
                "Сверка выполнена; разница показана как расчёт минус "
                "опубликованное значение."
            )
    return _report_comparison_template(
        status=status,
        year=report_year,
        unit=report_spec["unit"],
        calculated_value=float(calculated_value),
        reported_value=float(reported_value),
        delta=delta,
        message=message,
        reported_provenance=reported_provenance,
        calculated_provenance=calculated_provenance,
        reported_unit=reported_unit,
        reported_year=reported_year,
    )


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


def _correction_notes(facts):
    """Collect compatibility notes without duplicating an annual source note."""
    notes = []
    for fact in facts:
        values = fact.get("correction_notes") or []
        if fact.get("correction_note"):
            values = [*values, fact["correction_note"]]
        for note in values:
            if note and note not in notes:
                notes.append(note)
    return notes


def _attach_correction_notes(target, facts):
    notes = _correction_notes(facts)
    if notes:
        target["correction_notes"] = notes
    return target


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
        return _attach_correction_notes({
            **_empty_fact(
                _missing_years_reason(
                    "компонент", selected, year_from, year_to)),
            "upper_bound": any(fact.get("upper_bound") for fact in values),
            "provenance": _provenance_for(values),
            "provenance_by_year": _provenance_by_year(selected),
        }, values)
    values = [fact for _, fact in selected]
    return _attach_correction_notes({
        "value": sum(fact["value"] for fact in values),
        "upper_bound": any(fact.get("upper_bound") for fact in values),
        "missing_reason": None,
        "provenance": _provenance_for(values),
        "provenance_by_year": _provenance_by_year(selected),
    }, values)


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
        facts_by_spec[_component_identity(numerator_spec)], numerator_spec,
        year_from, year_to)
    denominator = _aggregate_component(
        facts_by_spec[_component_identity(denominator_spec)], denominator_spec,
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


def _derived_provenance(components, component_facts):
    """Return audit records with a stable label for every derived component."""
    records = []
    for component in components:
        fact = component_facts[_component_identity(component)]
        records.extend(_ratio_provenance([(component, fact)]))
    return sorted(records, key=lambda item: (
        item.get("year") or 0, item.get("component") or ""))


def _derived_missing_fact(spec, components, component_facts, message):
    records = _derived_provenance(components, component_facts)
    facts = [
        component_facts[_component_identity(component)]
        for component in components
    ]
    return _attach_correction_notes({
        **_empty_fact(message),
        "upper_bound": any(fact.get("upper_bound") for fact in facts),
        "provenance": records,
        "provenance_by_year": records,
        "provenance_components": records,
    }, facts)


def _aggregate_derived(spec, component_facts, year_from, year_to):
    """Calculate one new card from its exact, typed accounting components."""
    derived_id = spec["derived"]
    components = _DERIVED_COMPONENTS[derived_id]
    aggregate_facts = [
        _aggregate_component(
            component_facts[_component_identity(component)], component,
            year_from, year_to)
        for component in components
    ]
    by_identity = {
        _component_identity(component): fact
        for component, fact in zip(components, aggregate_facts)
    }
    records = _derived_provenance(components, by_identity)
    provenance = records
    provenance_by_year = (
        aggregate_facts[0].get("provenance_by_year", {})
        if len(components) == 1 else provenance
    )
    for component, fact in zip(components, aggregate_facts):
        if fact.get("value") is None:
            label = component["label"]
            source_label = component.get("source_label") or label
            reason = fact.get("missing_reason") or "нет подтверждённого значения"
            return _attach_correction_notes({
                **_empty_fact(
                    f"Показатель «{spec['title']}» не рассчитан: отсутствует "
                    f"компонент «{label} ({source_label})» ({reason})."),
                "upper_bound": any(
                    item.get("upper_bound") for item in aggregate_facts),
                "provenance": provenance,
                "provenance_by_year": provenance_by_year,
                "provenance_components": records,
            }, aggregate_facts)
    if any(fact.get("upper_bound") for fact in aggregate_facts):
        bounded = ", ".join(
            f"{component['label']} ({component.get('source_label') or component['label']})"
            for component, fact
            in zip(components, aggregate_facts) if fact.get("upper_bound"))
        return _attach_correction_notes({
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан: компонент(ы) "
                f"«{bounded}» содержат верхнюю границу."),
            "upper_bound": True,
            "provenance": provenance,
            "provenance_by_year": provenance_by_year,
            "provenance_components": records,
        }, aggregate_facts)

    values = {
        component["label"]: fact["value"]
        for component, fact in zip(components, aggregate_facts)
    }
    denominator = None
    if derived_id == "autonomy":
        denominator = values["assets"]
        value = values["equity"] / denominator if denominator > 0 else None
    elif derived_id == "net_working_capital":
        value = values["current_assets"] - values["short_term_liabilities"]
    elif derived_id == "quick_liquidity":
        denominator = values["short_term_liabilities"]
        value = (
            (values["cash"] + values["accounts_receivable"]) / denominator
            if denominator > 0 else None
        )
    elif derived_id == "absolute_liquidity":
        denominator = values["short_term_liabilities"]
        value = values["cash"] / denominator if denominator > 0 else None
    elif derived_id == "short_term_liabilities":
        value = values["short_term_liabilities"]
    elif derived_id == "long_term_liabilities":
        value = values["long_term_liabilities"]
    elif derived_id in {"short_term_share", "long_term_share"}:
        denominator = (
            values["short_term_liabilities"]
            + values["long_term_liabilities"])
        numerator_label = (
            "short_term_liabilities"
            if derived_id == "short_term_share"
            else "long_term_liabilities"
        )
        value = (
            values[numerator_label] / denominator * 100.0
            if denominator > 0 else None
        )
    elif derived_id == "interest_coverage":
        denominator = values["interest_expense"]
        value = values["ebit"] / denominator if denominator > 0 else None
    else:
        return _derived_missing_fact(
            spec, components, by_identity,
            f"Показатель «{spec['title']}» не поддерживает расчёт.")

    if denominator is not None and denominator <= 0:
        denominator_labels = {
            "autonomy": "assets / Валюта баланса",
            "quick_liquidity": (
                "short_term_liabilities / Краткосрочные обязательства, всего"),
            "absolute_liquidity": (
                "short_term_liabilities / Краткосрочные обязательства, всего"),
            "short_term_share": (
                "short_term_liabilities + long_term_liabilities / "
                "Краткосрочные и Долгосрочные обязательства, всего"),
            "long_term_share": (
                "short_term_liabilities + long_term_liabilities / "
                "Краткосрочные и Долгосрочные обязательства, всего"),
            "interest_coverage": "interest_expense / Проценты к уплате",
        }
        denominator_label = denominator_labels[derived_id]
        return _attach_correction_notes({
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан: знаменатель "
                f"«{denominator_label}» должен быть положительным "
                f"(получено {denominator})."),
            "provenance": provenance,
            "provenance_by_year": provenance_by_year,
            "provenance_components": records,
        }, aggregate_facts)
    if value is None or not math.isfinite(float(value)):
        return _attach_correction_notes({
            **_empty_fact(
                f"Показатель «{spec['title']}» не рассчитан: результат "
                "не является конечным числом."),
            "provenance": provenance,
            "provenance_by_year": provenance,
            "provenance_components": records,
        }, aggregate_facts)
    return _attach_correction_notes({
        "value": float(value), "upper_bound": False, "missing_reason": None,
        "provenance": provenance, "provenance_by_year": provenance_by_year,
        "provenance_components": records,
    }, aggregate_facts)


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
        HistoricalImport.company_id == company.id,
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
        # Derived cards are materialised after the collision-safe component
        # catalogue below has been loaded.
        if spec.get("derived"):
            continue
        facts[spec["id"]] = {
            year: _fact(rows_by_year.get(year, ()), spec)
            if year in rows_by_year else _empty_fact(
                "За этот календарный год нет принятой версии")
            for year in fact_years
        }

    # Ratio components are read from the same selected active versions, but
    # remain separate from the public card facts to avoid alias substitution.
    component_specs = {}
    for components in (
        tuple(_RATIO_COMPONENTS.values())
        + tuple(_DERIVED_COMPONENTS.values())
    ):
        for component in components:
            component_specs.setdefault(
                _component_identity(component), component)
    component_facts = {
        identity: {
            year: _fact(rows_by_year.get(year, ()), component)
            if year in rows_by_year else _empty_fact(
                "За этот календарный год нет принятой версии")
            for year in fact_years
        }
        for identity, component in component_specs.items()
    }
    for spec in CARD_SPECS:
        if spec.get("derived"):
            facts[spec["id"]] = {
                year: _aggregate_derived(
                    spec, component_facts, year, year)
                for year in fact_years
            }
    range_facts = {}
    previous_facts = {}
    for spec in CARD_SPECS:
        if selected_from is None:
            range_facts[spec["id"]] = _empty_fact(
                "Нет принятых годовых данных")
            previous_facts[spec["id"]] = None
            continue
        if spec.get("derived"):
            range_facts[spec["id"]] = _aggregate_derived(
                spec, component_facts, selected_from, selected_to)
        elif spec["kind"] == "ratio" and selected_from != selected_to:
            range_facts[spec["id"]] = _aggregate_ratio(
                spec, component_facts, selected_from, selected_to)
        else:
            range_facts[spec["id"]] = _aggregate_fact(
                facts[spec["id"]], spec, selected_from, selected_to)
        # A sparse equivalent period must not hide a usable point-in-time
        # balance at its final boundary.  Flow aggregation deliberately sees
        # every missing year and returns an unavailable fact itself.
        if previous_from is not None:
            if spec.get("derived"):
                previous_facts[spec["id"]] = _aggregate_derived(
                    spec, component_facts, previous_from, previous_to)
            elif spec["kind"] == "ratio" and previous_from != previous_to:
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
        card = {
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
        }
        report_comparison = _build_report_comparison(
            spec, current, component_facts, rows_by_year,
            selected_from, selected_to,
        )
        if report_comparison is not None:
            card["report_comparison"] = report_comparison
        # Formula metadata is part of the contract for the stability
        # extension.  Leave the legacy cards untouched so their established
        # shape and rating treatment remain backwards compatible.
        if spec.get("formula"):
            card["formula"] = spec["formula"]
            card["calculation_note"] = spec.get("calculation_note")
        correction_notes = current.get("correction_notes")
        if current.get("correction_note"):
            correction_notes = [current["correction_note"]]
        if correction_notes:
            card["correction_note"] = correction_notes
        cards.append(card)

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
    source_passport = build_annual_source_passport(
        company,
        versions=versions,
        year_from=selected_from,
        year_to=selected_to,
        requested_year=requested_year,
        cards=cards,
    )
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
        "source_passport": source_passport,
        "validation_notes": [
            "Расчёт новых карточек выполняется только из подтверждённых "
            "компонентов с точным разделом, меткой и единицей источника.",
            "Пропуски и верхние границы не заменяются нулём; положительность "
            "знаменателей проверяется до деления.",
            "Для балансовых карточек используется последний год диапазона, "
            "для ICR суммируются EBIT и проценты за каждый год диапазона.",
            "report_comparison является справочной read-only сверкой: "
            "значения карточки, рейтинг и принятые версии не изменяются.",
            "delta появляется только при совместимых определении, годе и "
            "единице; потоковый ICR за многолетний диапазон с годовой книгой "
            "не сопоставляется.",
        ],
        "methodology": list(METHODOLOGY),
    }


__all__ = ["CARD_SPECS", "build_annual_key_indicators"]