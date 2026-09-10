#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Изолированные synthetic probes методики себестоимости.

Runner intentionally imports only the pure ``key_indicators`` module.  It
builds small source snapshots in memory, evaluates the current Python helpers,
and extracts only the COGS expressions needed from the current frontend source
for execution in a tiny Node VM.  It does not import Flask, the application,
the service layer, a database driver, or a database connection.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OSV = ROOT / "osv-dashboard"
PY_SOURCE = OSV / "key_indicators.py"
JS_SOURCE = OSV / "static" / "key_indicators.js"
RUNNER_SOURCE = Path(__file__)
OUTPUT = Path(__file__).with_suffix(".json")

sys.path.insert(0, str(OSV))
from key_indicators import (  # noqa: E402
    build_indicator_source,
    build_key_indicators,
    _pl_facts,
)


DAY = "2026-01-31"
PERIOD = "за январь 2026 г."
UNSET = object()
CASES: list[dict[str, Any]] = []
JS_EXTRACTIONS: dict[str, dict[str, Any]] = {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _top(
    *,
    snd: float = 0,
    snk: float = 0,
    obd: float = 0,
    obk: float = 0,
    skd: float = 0,
    skk: float = 0,
) -> dict[str, float]:
    return {
        "snd": snd,
        "snk": snk,
        "obd": obd,
        "obk": obk,
        "skd": skd,
        "skk": skk,
    }


def _day(
    dt: str = DAY,
    *,
    code: str = "",
    analytic: str = "",
    obd: Any = 0,
    obk: Any = 0,
    **balance: Any,
) -> dict[str, Any]:
    row = {
        "date": dt,
        "month": dt[:7],
        "path1": analytic,
        "path2": "",
        "path3": code,
        "obd": obd,
        "obk": obk,
    }
    row.update(balance)
    return row


def _account(
    account: str,
    *,
    daily: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    period: str = PERIOD,
    top: dict[str, Any] | object = UNSET,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "acc": account,
        "daily": list(daily),
        "rows": list(rows),
        "period": period,
        "warnings": [],
        "check_ok": True,
    }
    if top is not UNSET:
        result["top"] = top
    return result


def _build(
    parsed: dict[str, Any],
    *,
    period: str = "month",
    selected_date: str | None = DAY,
    date_from: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = build_indicator_source(parsed)
    result = build_key_indicators(
        source,
        period=period,
        date=selected_date,
        date_from=date_from,
    )
    return source, result


def _cards(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {card["id"]: card for card in result["cards"]}


def _extract_const_after(name: str, anchor: str) -> dict[str, Any]:
    """Extract one current JS expression without reproducing its formula."""
    text = JS_SOURCE.read_text(encoding="utf-8")
    anchor_position = text.index(anchor)
    marker = f"const {name} ="
    position = text.index(marker, anchor_position)
    expression_start = position + len(marker)
    semicolon = text.index(";", expression_start)
    first_line = text.count("\n", 0, position) + 1
    last_line = text.count("\n", 0, semicolon) + 1
    return {
        "expr": text[expression_start:semicolon].strip(),
        "lines": f"{first_line}-{last_line}",
        "fragment": "\n".join(text.splitlines()[first_line - 1:last_line]),
    }


def _node_eval(
    expression: str,
    args: list[str],
    values: list[list[Any]],
) -> list[Any]:
    payload = {"expr": expression, "args": args, "values": values}
    program = r"""
const fs = require('node:fs'), vm = require('node:vm');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = vm.runInNewContext('(function(' + p.args.join(',') +
  '){return (' + p.expr + ');})');
const result = p.values.map(v => {
  const value = fn(...v);
  return value === undefined ? null : value;
});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", program],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return json.loads(completed.stdout)


def _record(
    *,
    test_id: str,
    title_ru: str,
    input_ru: str,
    actual: dict[str, Any],
    expected_actual: dict[str, Any],
    assessment_ru: str,
    scope_ru: str,
    evidence: dict[str, Any],
    assertion_ru: str,
    finding: bool = False,
) -> None:
    # This is deliberately an implementation assertion: expected_actual is
    # the observed current result, not an audit-safe desired result.
    assert actual == expected_actual, (
        f"{test_id}: expected current actual {expected_actual!r}, got {actual!r}"
    )
    CASES.append(
        {
            "test_id": test_id,
            "title_ru": title_ru,
            "input_ru": input_ru,
            "actual": actual,
            "expected_actual": expected_actual,
            "assessment_ru": assessment_ru,
            "scope_ru": scope_ru,
            "evidence": evidence,
            "assertion_ru": assertion_ru,
            "implementation_status": "PASS",
            "audit_assessment": "FINDING" if finding else "PASS",
            "status": "FINDING" if finding else "PASS",
            "assertion_verified": True,
        }
    )


def _probe_exact_parent_leaf() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", analytic="Услуга", obk=100),
                _day(code="90.03", analytic="Услуга", obd=20),
                _day(code="90.02", analytic="Услуга", obd=100),
                _day(code="90.02.1", analytic="Услуга", obd=60),
            ],
        )
    }
    source, result = _build(parsed)
    card = _cards(result)["cogs"]
    facts = _pl_facts(source, Date(2026, 1, 1), Date(2026, 1, 31))
    actual = {
        "card_value": card["value"],
        "analytic_values": [item["value"] for item in card["analytics"]],
        "cost_paths_used": [row["path3"] for row in facts["cost_rows"]],
    }
    expected_actual = {
        "card_value": 60.0,
        "analytic_values": [60.0],
        "cost_paths_used": ["90.02.1"],
    }
    _record(
        test_id="COGS-01-parent-leaf-no-double-count",
        title_ru="90.02 и лист 90.02.1: родитель не удваивает себестоимость",
        input_ru=(
            "На одну дату даны 90.02=100 и более глубокий 90.02.1=60 "
            "с одинаковой аналитикой «Услуга», а также 90.01=100 и 90.03=20."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: _leaf_rows оставляет более глубокую строку для одной "
            "даты и аналитического ключа; 90.02 не складывается с 90.02.1."
        ),
        scope_ru="Только синтетический источник 90 и чистые helpers; не проверка XLS.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:312-331 (_leaf_rows)",
                "osv-dashboard/key_indicators.py:400-416 (_pl_facts)",
            ],
            "observation": "В cost_rows фактически остался только путь 90.02.1.",
        },
        assertion_ru="Фактическая карточка, аналитика и выбранные пути равны expected_actual.",
    )


def _probe_prefix_boundary() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", analytic="А", obk=100),
                _day(code="90.03", analytic="А", obd=20),
                _day(code="90.02", analytic="А", obd=100),
                _day(code="90.02.1", analytic="А", obd=60),
                _day(code="90.020", analytic="Б", obd=9),
            ],
        )
    }
    source, result = _build(parsed)
    facts = _pl_facts(
        source,
        Date(2026, 1, 1),
        Date(2026, 1, 31),
    )
    card = _cards(result)["cogs"]
    actual = {
        "card_value": card["value"],
        "cost_paths_used": [row["path3"] for row in facts["cost_rows"]],
    }
    expected_actual = {
        "card_value": 69.0,
        "cost_paths_used": ["90.02.1", "90.020"],
    }
    _record(
        test_id="COGS-02-string-prefix-boundary",
        title_ru="Граница строкового префикса 90.02",
        input_ru=(
            "Кроме родителя 90.02 и листа 90.02.1 synthetic snapshot содержит "
            "искусственный код 90.020=9 с аналитикой «Б»."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING / УСЛОВНО: текущий _has_prefix использует "
            "str.startswith('90.02'), поэтому synthetic 90.020 тоже принят. "
            "Это наблюдение контракта helper, а не утверждение о реальной ОСВ."
        ),
        scope_ru=(
            "Искусственный код 90.020 не заявляется как допустимый код 1С; "
            "проверяется только текущая граница строки."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:334-336 (_has_prefix)",
                "osv-dashboard/key_indicators.py:400-408 (_pl_facts)",
            ],
            "observation": "Текущие cost_rows содержат 90.02.1 и 90.020.",
        },
        assertion_ru="Наблюдаемые card_value и cost_paths_used совпадают с expected_actual.",
        finding=True,
    )


def _probe_distinct_analytic_paths() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _path2_day("2026-01-31", "Сегмент А", code="90.02",
                           analytic="Услуга", obd=100),
                _path2_day("2026-01-31", "Сегмент А", code="90.02.1",
                           analytic="Услуга", obd=60),
                _path2_day("2026-01-31", "Сегмент Б", code="90.02",
                           analytic="Услуга", obd=40),
            ],
        )
    }
    source, result = _build(parsed)
    facts = _pl_facts(source, Date(2026, 1, 1), Date(2026, 1, 31))
    card = _cards(result)["cogs"]
    actual = {
        "card_value": card["value"],
        "cost_paths_used": [
            {"path2": row["path2"], "path3": row["path3"], "obd": row["obd"]}
            for row in facts["cost_rows"]
        ],
        "analytics": [
            {"label": row["label"], "value": row["value"]}
            for row in card["analytics"]
        ],
    }
    expected_actual = {
        "card_value": 100.0,
        "cost_paths_used": [
            {"path2": "Сегмент А", "path3": "90.02.1", "obd": 60.0},
            {"path2": "Сегмент Б", "path3": "90.02", "obd": 40.0},
        ],
        "analytics": [
            {"label": "«Услуга» / «Сегмент А»", "value": 60.0},
            {"label": "«Услуга» / «Сегмент Б»", "value": 40.0},
        ],
    }
    _record(
        test_id="COGS-03-leaf-scope-is-analytic-coordinate",
        title_ru="Leaf-dedup действует внутри analytic coordinate, не между путями",
        input_ru=(
            "Для «Сегмент А» есть 90.02=100 и leaf 90.02.1=60; "
            "для другого path2 «Сегмент Б» отдельно есть 90.02=40."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО как текущий контракт: для одной analytic coordinate "
            "остаётся deepest account code, но строка 90.02 другого path2 "
            "не удаляется как чужой total. Поэтому итог 60+40=100 — это "
            "не parent/leaf double count, а две разные координаты."
        ),
        scope_ru=(
            "Пути path2 заданы synthetic; probe не решает, является ли "
            "отдельный parent path фактическим итогом или самостоятельной аналитикой."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:312-331 (_leaf_rows)",
                "osv-dashboard/key_indicators.py:317-320 (analytic coordinate grouping)",
            ],
            "observation": (
                "A uses 90.02.1=60; B retains 90.02=40; both are included."
            ),
        },
        assertion_ru="Фактические cost paths, card value и analytics равны expected_actual.",
    )


def _probe_signed_missing_partial() -> None:
    signed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", obk=100),
                _day(code="90.03", obd=20),
                _day(code="90.02", analytic="Тепло", obd=70),
                _day(code="90.02", analytic="Тепло", obd=-20),
            ],
        )
    }
    missing = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", obk=100),
                _day(code="90.03", obd=20),
                _day(code="90.02", analytic="Тепло", obd=None),
            ],
        )
    }
    partial = {
        "90": _account(
            "90",
            period="за 1 квартал 2026 г.",
            daily=[
                _day("2026-01-15", code="90.01", obk=100),
                _day("2026-01-15", code="90.03", obd=20),
                _day("2026-01-15", code="90.02", obd=70),
            ],
        )
    }
    signed_source, signed_result = _build(signed)
    missing_source, missing_result = _build(missing)
    partial_source, partial_result = _build(
        partial, period="month", selected_date="2026-01-15"
    )
    missing_row = missing_source["accounts"]["90"]["daily"][-1]
    actual = {
        "signed_cost": _cards(signed_result)["cogs"]["value"],
        "signed_known_flags": [
            row.get("obd_known") for row in signed_source["accounts"]["90"]["daily"]
            if row["path3"] == "90.02"
        ],
        "missing_cost": _cards(missing_result)["cogs"]["value"],
        "missing_cost_analytic": _cards(missing_result)["cogs"]["analytics"][0]["value"],
        "missing_obd_normalized": missing_row["obd"],
        "missing_obd_known": missing_row["obd_known"],
        "partial_cost": _cards(partial_result)["cogs"]["value"],
        "partial_warning": any(
            "неполное" in warning
            for warning in partial_result["period"]["warnings"]
        ),
        "partial_period": {
            "start": partial_result["period"]["start"],
            "end": partial_result["period"]["end"],
        },
    }
    expected_actual = {
        "signed_cost": 50.0,
        "signed_known_flags": [True, True],
        "missing_cost": None,
        "missing_cost_analytic": None,
        "missing_obd_normalized": 0.0,
        "missing_obd_known": False,
        "partial_cost": 70.0,
        "partial_warning": True,
        "partial_period": {"start": "2026-01-01", "end": "2026-01-15"},
    }
    _record(
        test_id="COGS-04-signed-missing-and-partial",
        title_ru="Знаковый оборот, неизвестная сумма и неполный период",
        input_ru=(
            "Проверены cost=70−20; отдельно cost с obd=None; отдельно "
            "15 дней января при заявленном квартале."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: отрицательная известная строка сохраняет знак и входит "
            "в сумму; неизвестный obd становится 0 только в snapshot, но "
            "_known_sum возвращает null; неполный конец периода помечен warning."
        ),
        scope_ru=(
            "Synthetic source snapshots; null означает неизвестную сумму, "
            "а не подтверждённый нулевой оборот."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:45-48 (_number)",
                "osv-dashboard/key_indicators.py:366-369 (_known_sum)",
                "osv-dashboard/key_indicators.py:692-703 (partial warning)",
            ],
            "observation": (
                "В source missing obd=0.0, obd_known=False; итог cogs и "
                "analytic value остаются None."
            ),
        },
        assertion_ru="Все три synthetic состояния точно равны expected_actual.",
    )


def _probe_card_analytics_reconcile() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", analytic="Тепло", obk=100),
                _day(code="90.03", analytic="Тепло", obd=20),
                _day(code="90.02", analytic="Тепло", obd=60),
                _day(code="90.02", analytic="Вода", obd=40),
            ],
        )
    }
    _, result = _build(parsed)
    card = _cards(result)["cogs"]
    actual = {
        "card_value": card["value"],
        "analytics": [
            {"label": item["label"], "value": item["value"]}
            for item in card["analytics"]
        ],
        "analytics_sum": round(
            sum(item["value"] for item in card["analytics"]), 2
        ),
        "shares": [
            round(item["value"] / card["value"] * 100, 2)
            for item in card["analytics"]
        ],
    }
    expected_actual = {
        "card_value": 100.0,
        "analytics": [
            {"label": "«Вода»", "value": 40.0},
            {"label": "«Тепло»", "value": 60.0},
        ],
        "analytics_sum": 100.0,
        "shares": [40.0, 60.0],
    }
    _record(
        test_id="COGS-05-card-analytics-reconciliation",
        title_ru="Карточка себестоимости сверяется с аналитиками 90.02",
        input_ru=(
            "90.02 содержит две конечные аналитики: «Тепло»=60 и «Вода»=40; "
            "родительских строк в snapshot нет."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: сумма фактически построенных аналитик равна card.value; "
            "расчёт долей не требует отдельной суммы."
        ),
        scope_ru="Сверка Python card и Python analytics на одной synthetic дате.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:713-726 (cogs analytics)",
                "osv-dashboard/key_indicators.py:987-994 (cogs card)",
            ],
            "observation": "40+60=100 и доли 40%/60%.",
        },
        assertion_ru="Карточка, строки аналитики, сумма и доли равны expected_actual.",
    )


def _probe_missing_vat_and_gross_guard() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", obk=100),
                _day(code="90.02", obd=60),
            ],
        )
    }
    _, result = _build(parsed)
    cards = _cards(result)
    cost_ratio = JS_EXTRACTIONS.setdefault("costRatio", _extract_const_after(
        "costRatio", "renderCogsModal(card)"
    ))
    ratio = _node_eval(
        cost_ratio["expr"],
        ["current", "netRevenue"],
        [[cards["cogs"]["value"], None]],
    )[0]
    actual = {
        "revenue_value": cards["revenue"]["value"],
        "revenue_title": cards["revenue"]["title"],
        "revenue_formula": cards["revenue"]["formula"],
        "vat_detail": next(
            item for item in cards["revenue"]["details"] if item["label"] == "НДС"
        ),
        "gross_profit": cards["profit"]["gross_profit"],
        "profit_card_value": cards["profit"]["value"],
        "cost_ratio_with_gross_guard": ratio,
    }
    expected_actual = {
        "revenue_value": 100.0,
        "revenue_title": "Выручка брутто (НДС недоступен)",
        "revenue_formula": "ОбК(90.01*), брутто",
        "vat_detail": {"label": "НДС", "value": None, "unit": "RUB",
                       "reason": "Строки 90.03 отсутствуют"},
        "gross_profit": None,
        "profit_card_value": None,
        "cost_ratio_with_gross_guard": None,
    }
    _record(
        test_id="COGS-06-no-vat-gross-revenue-guard",
        title_ru="Без 90.03 выручка брутто не попадает в ratio и прибыль",
        input_ru=(
            "Есть 90.01 кредит=100 и 90.02 дебет=60; строки 90.03 в "
            "synthetic snapshot отсутствуют."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: backend показывает брутто и оставляет VAT detail null; "
            "извлечённое JS-условие не считает COGS/нетто-выручка при gross guard."
        ),
        scope_ru="Тест отсутствия 90.03 и реального cogs ratio expression без DOM.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:400-416 (_pl_facts)",
                "osv-dashboard/key_indicators.py:956-962 (revenue card)",
            ],
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "expression": "costRatio",
                "lines": cost_ratio["lines"],
            },
        },
        assertion_ru="Фактические backend-поля и JS ratio равны expected_actual.",
    )


def _probe_js_ratio_denominators() -> None:
    extraction = JS_EXTRACTIONS.setdefault(
        "costRatio", _extract_const_after("costRatio", "renderCogsModal(card)")
    )
    values = _node_eval(
        extraction["expr"],
        ["current", "netRevenue"],
        [[0, 100], [50, 0], [50, -100], [50, 100]],
    )
    actual = {
        "expression_lines": extraction["lines"],
        "ratio_zero_numerator": values[0],
        "ratio_zero_denominator": values[1],
        "ratio_negative_denominator": values[2],
        "ratio_positive": values[3],
    }
    expected_actual = {
        "expression_lines": extraction["lines"],
        "ratio_zero_numerator": 0,
        "ratio_zero_denominator": None,
        "ratio_negative_denominator": -50,
        "ratio_positive": 50,
    }
    _record(
        test_id="COGS-07-js-ratio-zero-and-negative-denominator",
        title_ru="Фактический JS ratio: нулевой числитель и отрицательный знаменатель",
        input_ru=(
            "Node VM исполняет актуальное costRatio expression для "
            "(current, netRevenue)=(0,100),(50,0),(50,-100),(50,100)."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: текущий truthiness guard возвращает 0 при нулевом "
            "числителе, null при нулевом знаменателе и -50 при отрицательной "
            "нетто-выручке. Последнее не является audit-safe процентом, но "
            "фиксирует реальный JS-контракт без подмены desired-результатом."
        ),
        scope_ru="Только извлечённое выражение frontend; не browser/UI тест.",
        evidence={
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "expression": "costRatio",
                "lines": extraction["lines"],
                "fragment": extraction["fragment"],
            },
            "execution": "Node vm; no DOM, network, application, or browser.",
        },
        assertion_ru="Четыре результата Node VM точно равны expected_actual.",
        finding=True,
    )


def _probe_gross_profit_fallback() -> None:
    extraction = JS_EXTRACTIONS.setdefault(
        "cogsGrossProfit",
        _extract_const_after("cogsGrossProfit", "renderCogsModal(card)"),
    )
    _, normal_result = _build(
        {
            "90": _account(
                "90",
                daily=[
                    _day(code="90.01", obk=100),
                    _day(code="90.03", obd=20),
                    _day(code="90.02", obd=70),
                    _day(code="90.07", obd=5),
                    _day(code="90.08", obd=3),
                ],
            )
        }
    )
    normal_profit = _cards(normal_result)["profit"]
    values = _node_eval(
        extraction["expr"],
        ["profitCard"],
        [
            [{
                "gross_profit": normal_profit["gross_profit"],
                "value": normal_profit["value"],
            }],
            [{"gross_profit": None, "value": 10}],
            [{"value": 2}],
        ],
    )
    actual = {
        "expression_lines": extraction["lines"],
        "normal_backend_profit_card": {
            "gross_profit": normal_profit["gross_profit"],
            "value": normal_profit["value"],
        },
        "gross_present_prefers_gross_profit": values[0],
        "gross_null_falls_back_to_value": values[1],
        "gross_omitted_falls_back_to_value": values[2],
    }
    expected_actual = {
        "expression_lines": extraction["lines"],
        "normal_backend_profit_card": {"gross_profit": 10.0, "value": 2.0},
        "gross_present_prefers_gross_profit": 10,
        "gross_null_falls_back_to_value": 10,
        "gross_omitted_falls_back_to_value": 2,
    }
    _record(
        test_id="COGS-08-gross-profit-card-fallback-contract",
        title_ru="Валовая прибыль COGS-модалки и fallback profitCard.value",
        input_ru=(
            "Node VM получает profitCard с gross_profit=10/value=2, "
            "gross_profit=null/value=10 и без gross_profit/value=2."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО / УСЛОВНЫЙ КОНТРАКТ: при наличии gross_profit берётся "
            "именно он; fallback на value срабатывает только при null/undefined. "
            "Текущий обычный backend payload с 90.07/90.08 даёт "
            "gross_profit=10 и value=2, поэтому модалка не подменяет gross "
            "profit более широким profitCard.value. Без backend-контракта, "
            "что value в fallback-состоянии является валовой прибылью, "
            "fallback сам по себе не доказывает смысл метрики."
        ),
        scope_ru=(
            "Synthetic profitCard objects и реальное JS expression; не "
            "утверждается, что произвольный payload приложения корректен."
        ),
        evidence={
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "expression": "cogsGrossProfit",
                "lines": extraction["lines"],
                "fragment": extraction["fragment"],
            },
            "python_contract": "key_indicators.py:775-776, 1004-1007",
        },
        assertion_ru="Результаты трёх ветвей extracted JS равны expected_actual.",
    )


def _delta_source(
    *,
    dates_and_analytics: list[tuple[str, str, float]],
    period: str,
) -> dict[str, Any]:
    daily = []
    for dt, analytic, cost in dates_and_analytics:
        daily.extend(
            [
                _day(dt, code="90.01", analytic=analytic, obk=100),
                _day(dt, code="90.03", analytic=analytic, obd=20),
                _day(dt, code="90.02", analytic=analytic, obd=cost),
            ]
        )
    return {"90": _account("90", daily=daily, period=period)}


def _cogs_card(
    parsed: dict[str, Any],
    *,
    selected_date: str,
) -> dict[str, Any]:
    _, result = _build(parsed, period="month", selected_date=selected_date)
    card = _cards(result)["cogs"]
    return {
        "value": card["value"],
        "delta": card["delta"],
        "period": result["period"],
    }


def _probe_delta_guards() -> None:
    complete_same = _cogs_card(
        _delta_source(
            dates_and_analytics=[
                ("2026-01-31", "А", 70),
                ("2026-02-28", "А", 80),
            ],
            period="за 1 квартал 2026 г.",
        ),
        selected_date="2026-02-28",
    )
    gap = _cogs_card(
        _delta_source(
            dates_and_analytics=[
                ("2026-01-31", "А", 70),
                ("2026-03-31", "А", 80),
            ],
            period="за 1 квартал 2026 г.",
        ),
        selected_date="2026-03-31",
    )
    partial = _cogs_card(
        _delta_source(
            dates_and_analytics=[
                ("2025-12-31", "А", 70),
                ("2026-01-15", "А", 80),
            ],
            period="2025-12-01 - 2026-03-31",
        ),
        selected_date="2026-01-15",
    )
    changed_analytics = _cogs_card(
        _delta_source(
            dates_and_analytics=[
                ("2026-01-31", "А", 70),
                ("2026-02-28", "Б", 80),
            ],
            period="за 1 квартал 2026 г.",
        ),
        selected_date="2026-02-28",
    )
    actual = {
        "complete_same_analytics": {
            "value": complete_same["value"],
            "delta": complete_same["delta"],
            "warning": any(
                "неполное" in warning
                for warning in complete_same["period"]["warnings"]
            ),
        },
        "gap_month": {
            "value": gap["value"],
            "delta": gap["delta"],
            "available_dates": gap["period"]["options"],
            "warning": any(
                "неполное" in warning for warning in gap["period"]["warnings"]
            ),
        },
        "partial_month": {
            "value": partial["value"],
            "delta": partial["delta"],
            "period_end": partial["period"]["end"],
            "warning": any(
                "неполное" in warning
                for warning in partial["period"]["warnings"]
            ),
        },
        "changed_analytics": {
            "value": changed_analytics["value"],
            "delta": changed_analytics["delta"],
            "warning": any(
                "неполное" in warning
                for warning in changed_analytics["period"]["warnings"]
            ),
        },
    }
    expected_actual = {
        "complete_same_analytics": {
            "value": 80.0,
            "delta": {"value": 10.0, "unit": "RUB",
                      "label": "к предыдущему сопоставимому периоду"},
            "warning": False,
        },
        "gap_month": {
            "value": 80.0,
            "delta": {"value": 10.0, "unit": "RUB",
                      "label": "к предыдущему сопоставимому периоду"},
            "available_dates": [
                {"value": "2026-01-31", "label": "Янв 2026"},
                {"value": "2026-03-31", "label": "Мар 2026"},
            ],
            "warning": False,
        },
        "partial_month": {
            "value": 80.0,
            "delta": {"value": 10.0, "unit": "RUB",
                      "label": "к предыдущему сопоставимому периоду"},
            "period_end": "2026-01-15",
            "warning": True,
        },
        "changed_analytics": {
            "value": 80.0,
            "delta": {"value": 10.0, "unit": "RUB",
                      "label": "к предыдущему сопоставимому периоду"},
            "warning": False,
        },
    }
    _record(
        test_id="COGS-09-delta-gap-partial-analytics",
        title_ru="Гарантии delta: полный период, пропуск месяца, partial и смена аналитики",
        input_ru=(
            "Сравнены четыре synthetic snapshots: январь→февраль с одной "
            "аналитикой; январь→март с пропуском; 15 дней января после "
            "декабря; январь «А»→февраль «Б»."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: текущая delta появляется для объявленных полностью "
            "покрытых периодов даже при календарном gap и смене аналитики; "
            "для partial-периода warning есть, но delta также рассчитана. "
            "_account_covers проверяет наличие одного span на всё окно, "
            "а не union фактически наблюдённых дат. "
            "Единственная явно проверенная безопасная ветвь здесь — "
            "последовательные полные месяцы с одинаковой аналитикой."
        ),
        scope_ru=(
            "Синтетические даты и аналитики; проверяется фактический delta "
            "contract build_key_indicators, а не нормативное сравнение."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:1208-1238 (delta)",
                "osv-dashboard/key_indicators.py:692-703 (partial warning)",
            ],
            "observation": (
                "Для gap, partial и changed analytics фактическая delta "
                "равна 10.0 RUB и не блокируется."
            ),
        },
        assertion_ru="Все четыре ветви period/value/delta/warning совпадают с expected_actual.",
        finding=True,
    )


def _probe_cost_movements_not_added() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", obk=100),
                _day(code="90.03", obd=20),
                _day(code="90.02", obd=60),
            ],
        ),
        "20": _account("20", daily=[_day(analytic="Топливо", obd=40)]),
        "25": _account("25", daily=[_day(analytic="Цех", obd=4)]),
        "26": _account("26", daily=[_day(analytic="АУП", obd=3)]),
        "44": _account("44", daily=[_day(analytic="Сбыт", obd=2)]),
    }
    _, result = _build(parsed)
    card = _cards(result)["cogs"]
    actual = {
        "cogs_card": card["value"],
        "cost_movements": {
            item["account"]: item["value"] for item in card["cost_movements"]
        },
        "sum_movements": round(
            sum(item["value"] for item in card["cost_movements"]), 2
        ),
        "note": card["notes"][0],
    }
    expected_actual = {
        "cogs_card": 60.0,
        "cost_movements": {"20": 40.0, "25": 4.0, "26": 3.0, "44": 2.0},
        "sum_movements": 49.0,
        "note": (
            "Счета 20, 25, 26 и 44 не прибавляются к 90.02: "
            "их суммы могут уже быть включены после закрытия периода."
        ),
    }
    _record(
        test_id="COGS-10-forming-accounts-not-added",
        title_ru="20, 25, 26 и 44 показываются отдельно и не добавляются",
        input_ru=(
            "90.02=60; справочные дебетовые обороты 20=40, 25=4, 26=3, "
            "44=2 добавлены отдельными счетами."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: cost_movements сохраняют 49 отдельно, а card.value "
            "остаётся ровно 60 по признанному 90.02."
        ),
        scope_ru="Проверка только разделения признанных и формирующих затрат.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:727-738 (cost_movements)",
                "osv-dashboard/key_indicators.py:987-994 (cogs card note)",
            ],
            "observation": "60 не превращается в 109.",
        },
        assertion_ru="Карточка, четыре movement values, их сумма и note равны expected_actual.",
    )


def _probe_recognized_and_objects() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", analytic="Тепло", obk=100),
                _day(code="90.03", analytic="Тепло", obd=20),
                _day(code="90.02", analytic="Тепло", obd=70),
                _day(code="90.07", obd=5),
                _day(code="90.08", obd=3),
            ],
        )
    }
    _, result = _build(parsed)
    cards = _cards(result)
    cogs = cards["cogs"]
    objects = result["objects"]
    actual = {
        "cogs": cogs["value"],
        "recognized_costs": {
            item["account"]: item["value"] for item in cogs["recognized_costs"]
        },
        "profit_card": {
            "gross_profit": cards["profit"]["gross_profit"],
            "profit_from_sales": cards["profit"]["profit_from_sales"],
            "value": cards["profit"]["value"],
        },
        "objects_rows": objects["rows"],
        "objects_totals": objects["totals"],
    }
    expected_actual = {
        "cogs": 70.0,
        "recognized_costs": {"90.02": 70.0, "90.07": 5.0, "90.08": 3.0},
        "profit_card": {
            "gross_profit": 10.0,
            "profit_from_sales": 2.0,
            "value": 2.0,
        },
        "objects_rows": [
            {"label": "Тепло", "revenue": 80.0, "cost": 70.0,
             "profit": 10.0, "margin": 12.5}
        ],
        "objects_totals": {
            "revenue": 80.0, "cost": 70.0, "profit": 10.0, "margin": 12.5
        },
    }
    _record(
        test_id="COGS-11-recognized-expenses-and-objects",
        title_ru="Признанные 90.07/90.08 и объектная сверка 90.01/90.02/90.03",
        input_ru=(
            "Для «Тепло»: 90.01 кредит=100, 90.03 дебет=20, 90.02 дебет=70; "
            "отдельно 90.07=5 и 90.08=3 без аналитики."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: 90.07 и 90.08 признаны отдельно; gross profit=10, "
            "profitCard.value=profit_from_sales=2; объектная таблица сверяет "
            "нетто-выручку 80 и 90.02=70 в gross result=10."
        ),
        scope_ru=(
            "Объектная сверка возможна только для совпадающего synthetic path1; "
            "распределение 90.07/90.08 по объектам не выдумывается."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:739-764 (recognized costs)",
                "osv-dashboard/key_indicators.py:1313-1349 (objects)",
            ],
            "observation": "Итого objects.profit равен gross_profit, а не profit_from_sales.",
        },
        assertion_ru="recognized costs, profit card и objects фактически равны expected_actual.",
    )


def _path2_day(
    dt: str,
    path2: str,
    *,
    code: str,
    analytic: str,
    obd: Any = 0,
    obk: Any = 0,
) -> dict[str, Any]:
    row = _day(dt, code=code, analytic=analytic, obd=obd, obk=obk)
    row["path2"] = path2
    return row


def _probe_object_identity_merge() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _path2_day("2026-01-31", "Сегмент А", code="90.01",
                           analytic="Услуга", obk=100),
                _path2_day("2026-01-31", "Сегмент А", code="90.03",
                           analytic="Услуга", obd=20),
                _path2_day("2026-01-31", "Сегмент А", code="90.02",
                           analytic="Услуга", obd=60),
                _path2_day("2026-01-31", "Сегмент Б", code="90.01",
                           analytic="Услуга", obk=50),
                _path2_day("2026-01-31", "Сегмент Б", code="90.03",
                           analytic="Услуга", obd=10),
                _path2_day("2026-01-31", "Сегмент Б", code="90.02",
                           analytic="Услуга", obd=20),
            ],
        )
    }
    _, result = _build(parsed)
    cards = _cards(result)
    actual = {
        "cogs_analytics": [
            {"label": row["label"], "value": row["value"]}
            for row in cards["cogs"]["analytics"]
        ],
        "object_row_count": len(result["objects"]["rows"]),
        "object_rows": result["objects"]["rows"],
        "object_totals": result["objects"]["totals"],
    }
    expected_actual = {
        "cogs_analytics": [
            {"label": "«Услуга» / «Сегмент А»", "value": 60.0},
            {"label": "«Услуга» / «Сегмент Б»", "value": 20.0},
        ],
        "object_row_count": 1,
        "object_rows": [
            {"label": "Услуга", "revenue": 120.0, "cost": 80.0,
             "profit": 40.0, "margin": 33.33}
        ],
        "object_totals": {
            "revenue": 120.0, "cost": 80.0, "profit": 40.0, "margin": 33.33
        },
    }
    _record(
        test_id="COGS-13-object-first-component-merge",
        title_ru="Объекты используют только первый компонент аналитики",
        input_ru=(
            "Две полные аналитические цепочки имеют общий path1 «Услуга», "
            "но разные path2 «Сегмент А» и «Сегмент Б»."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: cogs analytics сохраняет два полных analytic identity, "
            "а objects группирует по _analytic_label — первому не-счётному "
            "компоненту — и сливает оба сегмента в одну строку. Это точное "
            "наблюдение текущей реализации на synthetic hierarchy."
        ),
        scope_ru=(
            "Пути path1/path2 искусственно заданы для проверки identity; "
            "не утверждается, что такая комбинация встречается в XLS."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:339-344 (_analytic_label)",
                "osv-dashboard/key_indicators.py:1286-1332 (objects grouping)",
                "osv-dashboard/key_indicators.py:353-363 (_analytic_identity)",
            ],
            "observation": (
                "Полные labels A/B раздельны у COGS analytics, но objects "
                "label равен одному первому component «Услуга»."
            ),
        },
        assertion_ru="Фактические COGS analytics и слияние objects равны expected_actual.",
        finding=True,
    )


def _probe_missing_vat_object_warning_and_modal() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=[
                _day(code="90.01", analytic="Тепло", obk=100),
                _day(code="90.02", analytic="Тепло", obd=60),
            ],
        )
    }
    _, result = _build(parsed)
    objects = result["objects"]
    text = JS_SOURCE.read_text(encoding="utf-8")
    start = text.index("renderCogsModal(card)")
    end = text.index("renderProfitModal(card)", start)
    cogs_modal_source = text[start:end]
    actual = {
        "object_rows": objects["rows"],
        "object_reason": objects["reason"],
        "object_notes": objects["notes"],
        "modal_reads_object_rows": "state.data.objects" in cogs_modal_source
        and ".rows" in cogs_modal_source,
        "modal_reads_object_reason": "objects.reason" in cogs_modal_source,
        "modal_reads_object_notes": "objects.notes" in cogs_modal_source,
    }
    expected_actual = {
        "object_rows": [
            {"label": "Тепло", "revenue": 100.0, "cost": 60.0,
             "profit": None, "margin": None}
        ],
        "object_reason": "Строки НДС 90.03 отсутствуют; выручка показана брутто",
        "object_notes": [
            "Прибыль и рентабельность объектов недоступны без НДС."
        ],
        "modal_reads_object_rows": True,
        "modal_reads_object_reason": False,
        "modal_reads_object_notes": False,
    }
    _record(
        test_id="COGS-14-missing-vat-object-warning-modal",
        title_ru="Без VAT объекты остаются брутто, а COGS-модалка не показывает warning",
        input_ru=(
            "Есть 90.01=100 и 90.02=60 для «Тепло», строки 90.03 нет; "
            "backend objects и исходник renderCogsModal проверены отдельно."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: backend оставляет object revenue=100 (брутто), "
            "profit=null и заполняет reason/notes о недоступном НДС. "
            "Текущий renderCogsModal читает только objects.rows и не читает "
            "objects.reason или objects.notes, поэтому предупреждение в этой "
            "модалке теряется. Это source inspection, не browser render test."
        ),
        scope_ru=(
            "Проверены фактический Python payload и текст текущей функции "
            "renderCogsModal; DOM/UI не запускались."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:1333-1349 (object VAT reason/notes)",
                "osv-dashboard/key_indicators.py:1308-1329 (object revenue/profit)",
            ],
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "function": "renderCogsModal(card)",
                "observation": (
                    "objectRows берутся из state.data.objects.rows; "
                    "objects.reason и objects.notes в функции не упоминаются."
                ),
            },
        },
        assertion_ru="Backend object payload и три статических modal-флага равны expected_actual.",
        finding=True,
    )


def _probe_monthly_series_conservation() -> None:
    parsed = {
        "90": _account(
            "90",
            period="за 1 квартал 2026 г.",
            daily=[
                _day("2026-01-31", code="90.01", obk=100),
                _day("2026-01-31", code="90.03", obd=20),
                _day("2026-01-31", code="90.02", obd=70),
                _day("2026-02-28", code="90.01", obk=50),
                _day("2026-02-28", code="90.03", obd=20),
                _day("2026-02-28", code="90.02", obd=30),
            ],
        )
    }
    _, result = _build(
        parsed, period="quarter", selected_date="2026-03-31"
    )
    cogs = _cards(result)["cogs"]
    actual = {
        "card_value": cogs["value"],
        "series_kind": result["period"]["series_kind"],
        "series": cogs["series"],
        "known_series_sum": round(
            sum(item["value"] for item in cogs["series"]
                if item["value"] is not None), 2
        ),
        "series_null_count": sum(
            item["value"] is None for item in cogs["series"]
        ),
    }
    expected_actual = {
        "card_value": 100.0,
        "series_kind": "month",
        "series": [
            {"label": "Янв 2026", "value": 70.0},
            {"label": "Фев 2026", "value": 30.0},
            {"label": "Мар 2026", "value": None},
        ],
        "known_series_sum": 100.0,
        "series_null_count": 1,
    }
    _record(
        test_id="COGS-12-monthly-series-conservation",
        title_ru="Месячная серия COGS сохраняет источник и пропуск марта",
        input_ru=(
            "В квартале есть 90.02=70 за январь и 30 за февраль; "
            "март выбранной границы не содержит строки оборота."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: card.value=100 равен сумме известных точек серии; "
            "отсутствующий март остаётся null, а не превращается в ноль."
        ),
        scope_ru="Synthetic monthly source conservation; календарное заполнение UI не тестируется.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:1104-1122 (series dates)",
                "osv-dashboard/key_indicators.py:1143-1148 (monthly cogs value)",
            ],
            "observation": "Известные точки 70+30 сохраняют card.value=100.",
        },
        assertion_ru="Card, series, kind, conservation sum и null gap равны expected_actual.",
    )


def _node_version() -> str:
    completed = subprocess.run(
        ["node", "--version"], text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def main() -> int:
    scope = (
        "COGS: счета 90.02, 90.07, 90.08; выручка 90.01 и НДС 90.03; "
        "формирующие счета 20, 25, 26, 44; Python helpers и extracted COGS JS."
    )
    _probe_exact_parent_leaf()
    _probe_prefix_boundary()
    _probe_distinct_analytic_paths()
    _probe_signed_missing_partial()
    _probe_card_analytics_reconcile()
    _probe_missing_vat_and_gross_guard()
    _probe_js_ratio_denominators()
    _probe_gross_profit_fallback()
    _probe_delta_guards()
    _probe_cost_movements_not_added()
    _probe_recognized_and_objects()
    _probe_monthly_series_conservation()
    _probe_object_identity_merge()
    _probe_missing_vat_object_warning_and_modal()

    source_files = {
        str(path.relative_to(ROOT)): {
            "sha256": _sha256(path),
            "role": role,
        }
        for path, role in (
            (PY_SOURCE, "pure Python calculation module imported by probes"),
            (JS_SOURCE, "frontend COGS expressions extracted and executed in Node VM"),
            (RUNNER_SOURCE, "this bounded probe runner"),
        )
    }
    findings = sum(case["status"] == "FINDING" for case in CASES)
    report = {
        "report_id": "cogs-audit-probes",
        "purpose_ru": (
            "Изолированные synthetic probes текущей методики COGS: "
            "признанная себестоимость, аналитика, gross guard, расходы 90, "
            "object reconciliation, delta и monthly series."
        ),
        "scope_ru": scope,
        "status_semantics_ru": (
            "implementation_status=PASS означает, что фактический результат "
            "текущей реализации совпал с expected_actual и assert прошёл. "
            "audit_assessment=FINDING означает расхождение наблюдаемого "
            "контракта с audit-safe ожиданием, а не падение runner."
        ),
        "execution": {
            "runner": "reports/cogs_audit_probes.py",
            "command": "PYTHONDONTWRITEBYTECODE=1 python3 reports/cogs_audit_probes.py",
            "python": sys.version.split()[0],
            "node": _node_version(),
            "imports": ["key_indicators"],
            "forbidden_imports_not_used": [
                "Flask",
                "app",
                "key_indicators_service",
                "database",
                "os.environ",
            ],
            "network_calls": False,
            "database_connections": False,
            "application_writes": False,
            "services_started": False,
            "node_vm_only": True,
        },
        "source_files": source_files,
        "js_extractions": {
            name: {
                "lines": value["lines"],
                "fragment": value["fragment"],
            }
            for name, value in JS_EXTRACTIONS.items()
        },
        "case_count": len(CASES),
        "assertions_verified": len(CASES),
        "finding_count": findings,
        "cases": CASES,
        "limitations_ru": [
            (
                "Все входы synthetic source snapshots, созданные в памяти; "
                "реальные XLS, предприятия, БД и service layer не читались."
            ),
            (
                "Проверены только текущие pure helpers key_indicators и "
                "выбранные JS expressions; Flask, DOM, browser и UI не запускались."
            ),
            (
                "Синтетический код 90.020 в COGS-02 нужен для проверки "
                "строковой границы startswith и не доказывает наличие такого "
                "кода в реальной выгрузке."
            ),
            (
                "Оборот 90.02 может быть отрицательным как signed synthetic "
                "строка; probe фиксирует поведение helper, не классифицирует "
                "сторно или корректировки в учёте."
            ),
            (
                "Missing obd сохраняется как obd_known=false; это нельзя "
                "интерпретировать как подтверждённый нулевой оборот."
            ),
            (
                "Delta findings ограничены synthetic gap, partial и сменой "
                "аналитики; они не являются доказательством фактического "
                "enterprise calendar or source coverage."
            ),
            (
                "Objects profit — валовой результат совпадающих аналитик; "
                "распределение 90.07/90.08 и полный EBIT/чистая прибыль "
                "этим отчётом не утверждаются."
            ),
        ],
    }
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: "
        f"{len(CASES)} cases, {findings} finding(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())