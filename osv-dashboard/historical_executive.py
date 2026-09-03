# -*- coding: utf-8 -*-
"""Deterministic executive summary over approved historical analytics."""
from __future__ import annotations

import html
import math
import statistics

from historical_parser import canonical_metric_key


ZONE_LABELS = {
    "normal": "Норма",
    "attention": "Внимание",
    "risk": "Риск",
    "critical": "Критично",
    "neutral": "Без оценки",
}
ZONE_PRIORITY = {
    "critical": 4,
    "risk": 3,
    "attention": 2,
    "normal": 1,
    "neutral": 0,
}


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _find(analytics, section_key, label):
    key = canonical_metric_key(label)
    return next((
        metric for metric in analytics.get("metrics", [])
        if metric["section_key"] == section_key and metric["key"] == key
    ), None)


def _cell(metric, year):
    if not metric:
        return None
    cell = metric.get("values_by_year", {}).get(year)
    if not cell or cell.get("missing") or not cell.get("primary"):
        return None
    if cell["primary"].get("missing"):
        return None
    return cell


def _numeric(metric, year):
    cell = _cell(metric, year)
    value = cell["primary"].get("value") if cell else None
    return value if _finite(value) else None


def _display(metric, year):
    cell = _cell(metric, year)
    if not cell:
        return "Нет данных"
    primary = cell["primary"]
    value = primary.get("value")
    if _finite(value):
        if metric.get("unit") == "thousand_rubles":
            amount = abs(value)
            if amount >= 1_000_000:
                text = "{:.1f} млрд ₽".format(value / 1_000_000)
            elif amount >= 1_000:
                text = "{:.1f} млн ₽".format(value / 1_000)
            else:
                text = "{:,.0f} тыс. ₽".format(value).replace(",", " ")
            return text.replace(".", ",")
        if metric.get("unit") == "percent":
            return ("{:.1f}%".format(value)).replace(".", ",")
        return ("{:.2f}".format(value)).replace(".", ",").rstrip("0").rstrip(",")
    text = primary.get("display_value")
    unit = primary.get("unit_label")
    return "{}{}".format(text or "Нет данных", " " + unit if unit and unit != "—" else "")


def _four_zone(metric, year):
    value = _numeric(metric, year)
    direction = metric.get("direction") if metric else None
    norm = metric.get("norm") if metric else None
    if value is None or direction not in ("higher", "lower") or not _finite(norm):
        return "neutral"
    if direction == "higher":
        if value >= norm:
            return "normal"
        if norm <= 0:
            return "risk"
        ratio = value / norm
    else:
        if value <= norm:
            return "normal"
        if norm == 0:
            return "risk"
        ratio = norm / value if value else 0
    if ratio >= 0.85:
        return "attention"
    if ratio >= 0.65:
        return "risk"
    return "critical"


def _previous_year(metric, selected_year, years):
    return next((
        year for year in sorted(years, reverse=True)
        if year < selected_year and _numeric(metric, year) is not None
    ), None)


def _trend(metric, selected_year, years):
    value = _numeric(metric, selected_year)
    previous_year = _previous_year(metric, selected_year, years)
    previous = _numeric(metric, previous_year) if previous_year else None
    if value is None or previous is None:
        return {
            "text": "Нет сопоставимого предыдущего года",
            "previous_year": previous_year,
            "delta": None,
            "worsening": False,
        }
    delta = value - previous
    if previous:
        text = "{:+.1f}% к {}".format(delta / abs(previous) * 100, previous_year)
    else:
        text = "{:+.2f} к {}".format(delta, previous_year)
    direction = metric.get("direction", "neutral")
    worsening = (
        (direction == "higher" and delta < 0)
        or (direction == "lower" and delta > 0)
    )
    return {
        "text": text.replace(".", ","),
        "previous_year": previous_year,
        "delta": delta,
        "worsening": worsening,
    }


def _median_delta(metric, selected_year, years):
    value = _numeric(metric, selected_year)
    values = [
        _numeric(metric, year) for year in years
        if year <= selected_year and _numeric(metric, year) is not None
    ]
    if value is None or not values:
        return "нет данных"
    median = statistics.median(values)
    if median:
        return ("{:+.1f}%".format((value - median) / abs(median) * 100)).replace(".", ",")
    return ("{:+.2f}".format(value - median)).replace(".", ",")


def _sparkline(metric, selected_year, years):
    points = [
        (year, _numeric(metric, year))
        for year in years if year <= selected_year
    ]
    finite = [(year, value) for year, value in points if value is not None]
    if len(finite) < 2:
        return ""
    values = [value for _, value in finite]
    low, high = min(values), max(values)
    span = high - low or 1.0
    width, height, pad = 220, 36, 3
    coords = []
    for index, (_, value) in enumerate(finite):
        x = pad + index * (width - 2 * pad) / max(len(finite) - 1, 1)
        y = pad + (high - value) * (height - 2 * pad) / span
        coords.append((x, y))
    polyline = " ".join("{:.1f},{:.1f}".format(x, y) for x, y in coords)
    circles = "".join(
        '<circle cx="{:.1f}" cy="{:.1f}" r="2.2"></circle>'.format(x, y)
        for x, y in coords
    )
    label = ", ".join("{}: {}".format(year, value) for year, value in finite)
    return (
        '<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
        'aria-label="{label}"><polyline points="{points}" fill="none" '
        'stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke">'
        '</polyline><g fill="currentColor">{circles}</g></svg>'
    ).format(
        w=width, h=height, label=html.escape(label),
        points=polyline, circles=circles,
    )


def _source(metric, year):
    cell = _cell(metric, year)
    if not cell:
        return "утверждённые данные отсутствуют"
    primary = cell["primary"]
    provenance = primary.get("provenance", {})
    parts = [
        provenance.get("source"),
        provenance.get("version"),
        provenance.get("sheet"),
    ]
    return " · ".join(str(part) for part in parts if part)


def _quality(metric, year):
    cell = _cell(metric, year)
    if not cell:
        return "Нет данных"
    discrepancy = cell.get("discrepancy")
    if discrepancy and discrepancy.get("significant"):
        return "Конфликт"
    if metric.get("unit") in ("ratio", "percent") and not metric.get("formula_confirmed"):
        return "Ограничено"
    return "Подтверждено"


def _norm(metric):
    if not metric or metric.get("direction") not in ("higher", "lower"):
        return "Норма не задана"
    return metric.get("norm_label") or "Норма не задана"


def _card(title, primary_metric, year, years, secondary_metric=None,
          status_metric=None, note=""):
    status_metric = status_metric or primary_metric
    trend = _trend(primary_metric, year, years)
    secondary = ""
    if secondary_metric:
        secondary = "{}: {}".format(
            secondary_metric["label"].split(" (")[0],
            _display(secondary_metric, year),
        )
    qualities = [_quality(primary_metric, year)]
    if secondary_metric:
        qualities.append(_quality(secondary_metric, year))
    if "Конфликт" in qualities:
        quality = "Конфликт"
    elif all(item == "Подтверждено" for item in qualities):
        quality = "Подтверждено"
    elif any(item != "Нет данных" for item in qualities):
        quality = "Частично"
    else:
        quality = "Нет данных"
    return {
        "title": title,
        "primary": _display(primary_metric, year),
        "secondary": secondary,
        "zone": _four_zone(status_metric, year),
        "zone_label": ZONE_LABELS[_four_zone(status_metric, year)],
        "trend": trend["text"],
        "worsening": trend["worsening"],
        "norm_label": _norm(status_metric),
        "median_delta": _median_delta(primary_metric, year, years),
        "source": _source(primary_metric, year),
        "sparkline": _sparkline(primary_metric, year, years),
        "quality": quality,
        "note": note,
        "_metric": status_metric,
        "_primary_metric": primary_metric,
    }


def _rating_metric(analytics):
    ratings = analytics.get("historical_rating", [])
    preferences = (
        "ИТОГОВЫЙ РЕЙТИНГ",
        "Итоговая рейтинговая оценка (балл)",
        "Качественная оценка фин. положения",
    )
    for label in preferences:
        key = canonical_metric_key(label)
        match = next((metric for metric in ratings if metric["key"] == key), None)
        if match:
            return match
    return ratings[0] if ratings else None


def _signals(analytics, year):
    candidates = []
    for order, metric in enumerate(analytics.get("metrics", [])):
        cell = _cell(metric, year)
        if not cell:
            continue
        zone = _four_zone(metric, year)
        trend = _trend(metric, year, analytics.get("years", []))
        quality = _quality(metric, year)
        reliability = 3 if quality == "Подтверждено" else 2 if quality == "Ограничено" else 1
        if zone in ("attention", "risk", "critical"):
            value = _display(metric, year)
            candidates.append({
                "zone": zone,
                "label": ZONE_LABELS[zone],
                "message": "{}: {}".format(metric["label"], value),
                "reason": "Норматив {}. {}".format(
                    _norm(metric), trend["text"]),
                "_sort": (
                    ZONE_PRIORITY[zone], int(trend["worsening"]),
                    reliability, -order,
                ),
            })
        discrepancy = cell.get("discrepancy")
        if discrepancy and discrepancy.get("significant"):
            candidates.append({
                "zone": "critical",
                "label": "Конфликт источников",
                "message": metric["label"],
                "reason": (
                    "Расхождение между утверждённой отчётностью и ОСВ "
                    "превышает допуск выбранного профиля."
                ),
                "_sort": (5, int(trend["worsening"]), 1, -order),
            })
    candidates.sort(key=lambda item: item["_sort"], reverse=True)
    result = []
    seen = set()
    for item in candidates:
        identity = (item["label"], item["message"])
        if identity in seen:
            continue
        seen.add(identity)
        item.pop("_sort", None)
        result.append(item)
        if len(result) == 5:
            break
    return result


def build_executive_summary(analytics, selected_year=None):
    """Return eight cards and a deterministic management conclusion."""
    years = analytics.get("years", [])
    available_years = analytics.get("available_years", [])
    if not years:
        return {
            "year": None, "years": [], "cards": [], "signals": [],
            "conclusion": {
                "allowed": False,
                "text": "Нет утверждённых исторических данных.",
                "caveats": [],
            },
            "quality": {"label": "Недостаточно", "detail": "Нет действующих годовых версий."},
            "profile": analytics.get("profile", {}),
        }
    year = selected_year if selected_year in years else max(available_years or years)

    revenue = _find(analytics, "financial_results", "Выручка")
    sales_profit = _find(
        analytics, "financial_results", "Прибыль (убыток) от продаж")
    sales_margin = _find(
        analytics, "profitability",
        "Рентабельность продаж (прибыль от продаж / выручка)")
    ebit = _find(
        analytics, "financial_results", "EBIT (прибыль до % и налогов)")
    icr = _find(
        analytics, "profitability", "Коэффициент покрытия процентов (ICR)")
    liquidity = _find(
        analytics, "liquidity",
        "Коэффициент текущей (общей) ликвидности")
    nwc = _find(
        analytics, "working_capital",
        "СОС2 (с учетом долгосрочных пассивов, NWC)")
    sos = _find(
        analytics, "financial_stability", "Коэффициент обеспеченности СОС")
    receivables = _find(analytics, "assets", "дебиторская задолженность")
    receivables_days = _find(
        analytics, "turnover", "Оборачиваемость дебиторской задолженности")
    autonomy = _find(
        analytics, "financial_stability", "Коэффициент автономии")
    rating = _rating_metric(analytics)

    cards = [
        _card("Выручка", revenue, year, years),
        _card(
            "Прибыль от продаж", sales_profit, year, years,
            secondary_metric=sales_margin, status_metric=sales_margin,
            note="Нормативная зона определяется по рентабельности продаж.",
        ),
        _card(
            "EBIT / покрытие процентов", ebit, year, years,
            secondary_metric=icr, status_metric=icr,
            note="ICR показывает запас покрытия процентов операционной прибылью.",
        ),
        _card("Текущая ликвидность", liquidity, year, years),
        _card(
            "NWC / обеспеченность СОС", nwc, year, years,
            secondary_metric=sos, status_metric=sos,
            note="NWC — объём собственных оборотных средств; зона — по коэффициенту СОС.",
        ),
        _card(
            "Дебиторская задолженность", receivables, year, years,
            secondary_metric=receivables_days, status_metric=receivables_days,
            note="Зона определяется по сроку оборота дебиторской задолженности.",
        ),
        _card("Доля собственного капитала", autonomy, year, years),
        _card(
            "Рейтинг и риски", rating, year, years,
            note=(
                "Исторический рейтинг показан отдельно от рейтинга платформы; "
                "модели банкротства не усредняются."
            ),
        ),
    ]

    signals = _signals(analytics, year)
    available = [
        card for card in cards
        if _cell(card["_primary_metric"], year)
    ]
    conflicts = [card for card in available if card["quality"] == "Конфликт"]
    normalized = [
        card for card in available if card["zone"] != "neutral"
    ]
    year_available = year in available_years
    conclusion_allowed = (
        year_available and len(available) >= 5 and len(normalized) >= 3
        and len(conflicts) <= 1
    )
    caveats = []
    if analytics.get("missing_years"):
        caveats.append(
            "В ряду есть пропуски: {}. Значения не интерполируются.".format(
                ", ".join(str(item) for item in analytics["missing_years"])))
    if conflicts:
        caveats.append(
            "Обнаружены конфликты источников; приоритет задан методическим профилем.")
    limited = [card for card in available if card["quality"] == "Частично"]
    if limited:
        caveats.append(
            "Часть составных показателей неполна или имеет неподтверждённую формулу.")

    if conclusion_allowed:
        sales_value = _numeric(sales_profit, year)
        revenue_trend = _trend(revenue, year, years)
        severe = sum(
            1 for signal in signals if signal["zone"] in ("risk", "critical"))
        parts = []
        if sales_value is not None:
            parts.append(
                "Основная деятельность завершила год {}.".format(
                    "с прибылью от продаж" if sales_value >= 0
                    else "с убытком от продаж"))
        if revenue_trend["delta"] is not None:
            parts.append("Выручка: {}.".format(revenue_trend["text"]))
        if severe:
            parts.append(
                "Выделено {} приоритетных {} уровня «Риск» или «Критично».".format(
                    severe, "сигнала" if severe == 1 else "сигнала"))
        elif signals:
            parts.append("Критичных нормативных отклонений среди приоритетов нет.")
        else:
            parts.append("Проверяемых нормативных сигналов за год не выявлено.")
        conclusion_text = " ".join(parts)
    else:
        conclusion_text = (
            "Управленческий вывод не сформирован: недостаточно надёжных "
            "и нормируемых данных за выбранный год."
        )

    for card in cards:
        card.pop("_metric", None)
        card.pop("_primary_metric", None)

    quality_ratio = len(available) / len(cards)
    if not year_available or quality_ratio < 0.5:
        quality_label = "Недостаточно"
    elif conflicts or quality_ratio < 0.875:
        quality_label = "С оговорками"
    else:
        quality_label = "Достаточно"
    return {
        "year": year,
        "years": years,
        "cards": cards,
        "signals": signals,
        "conclusion": {
            "allowed": conclusion_allowed,
            "text": conclusion_text,
            "caveats": caveats,
        },
        "quality": {
            "label": quality_label,
            "detail": (
                "{} из 8 карточек содержат утверждённые значения; "
                "{} конфликтов источников."
            ).format(len(available), len(conflicts)),
        },
        "profile": analytics.get("profile", {}),
    }


__all__ = ["ZONE_LABELS", "build_executive_summary"]