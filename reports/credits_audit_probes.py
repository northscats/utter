#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Изолированные probes кредитов и займов по счетам 66/67.

Runner imports only the pure ``key_indicators`` module.  Fixtures are built in
memory in the source shape accepted by ``build_indicator_source``; no Flask,
service, database, network, environment, or full report runner is imported.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
OSV = ROOT / "osv-dashboard"
BACKEND = OSV / "key_indicators.py"
FRONTEND = OSV / "static" / "key_indicators.js"
SERVICE = OSV / "key_indicators_service.py"
PARSER = OSV / "osvparser.py"
OUTPUT = Path(__file__).with_suffix(".json")

# Deliberately import only the pure calculation module.
sys.path.insert(0, str(OSV))
from key_indicators import (  # noqa: E402
    _account_balance_analytics,
    _top_balance,
    build_indicator_source,
    build_key_indicators,
)


DAY = "2026-01-31"
FEB = "2026-02-28"
MAR = "2026-03-31"
MONTH = "за январь 2026 г."
QUARTER = "за 1 квартал 2026 г."
UNSET = object()
CASES: list[dict[str, Any]] = []

SCOPE = (
    "Счета 66 и 67: подтверждённое кредитовое сальдо ОСВ, "
    "охват и осторожная динамика. Это не реестр кредитных договоров, "
    "платежей, погашения, процентов или просрочки."
)


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
    period: str = "",
    top: dict[str, Any] | object = UNSET,
    balance_reports: tuple[dict[str, Any], ...]
    | list[dict[str, Any]] = (),
    coverage_reports: tuple[Any, ...] | list[Any] = (),
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
    if balance_reports:
        result["balance_reports"] = list(balance_reports)
    if coverage_reports:
        result["coverage_reports"] = list(coverage_reports)
    return result


def _report(
    period: str,
    *,
    top: dict[str, Any],
    source_file: str,
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "period": period,
        "top": top,
        "rows": list(rows),
        "source_file": source_file,
    }


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


def _series_summary(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(series),
        "known": [item for item in series if item["value"] is not None],
        "null_count": sum(item["value"] is None for item in series),
    }


def _credit_card_view(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": card["value"],
        "reason": card["reason"],
        "coverage": card["coverage"],
        "accounts": [
            {
                "account": item["account"],
                "value": item["value"],
                "reason": item["reason"],
                "share": item["share"],
            }
            for item in card["accounts"]
        ],
    }


def _record(
    *,
    test_id: str,
    title_ru: str,
    input_ru: str,
    actual: dict[str, Any],
    expected_actual: dict[str, Any],
    expected_audit_ru: str,
    assessment_ru: str,
    scope_ru: str,
    source_refs: dict[str, list[str]],
    assertion_ru: str,
    finding: bool = False,
) -> None:
    # expected_actual is the exact current implementation result.  It is
    # separate from expected_audit_ru so a finding cannot be hidden by a
    # desired-value assertion.
    assert actual == expected_actual, (
        f"{test_id}: expected {expected_actual!r}, got {actual!r}"
    )
    CASES.append(
        {
            "test_id": test_id,
            "title_ru": title_ru,
            "input_ru": input_ru,
            "actual": actual,
            "expected_actual": expected_actual,
            "expected_audit_ru": expected_audit_ru,
            "assessment_ru": assessment_ru,
            "scope_ru": scope_ru,
            "source_refs": source_refs,
            "assertion_ru": assertion_ru,
            "implementation_status": "PASS",
            "audit_assessment": "FINDING" if finding else "PASS",
            "status": "FINDING" if finding else "PASS",
            "assertion_verified": True,
        }
    )


def _probe_complete_66_67() -> None:
    parsed = {
        "66": _account(
            "66",
            daily=[_day(skd=20, skk=120)],
            period=MONTH,
            top=_top(skd=20, skk=120),
        ),
        "67": _account(
            "67",
            daily=[_day(skd=30, skk=230)],
            period=MONTH,
            top=_top(skd=30, skk=230),
        ),
    }
    source, result = _build(parsed)
    card = _cards(result)["credits"]
    actual = {
        "card": _credit_card_view(card),
        "source_dates": source["dates"],
        "series": _series_summary(card["series"]),
    }
    expected = {
        "card": {
            "value": 300.0,
            "reason": None,
            "coverage": {
                "loaded": ["66", "67"],
                "missing": [],
                "complete": True,
            },
            "accounts": [
                {"account": "66", "value": 100.0, "reason": None, "share": 33.33},
                {"account": "67", "value": 200.0, "reason": None, "share": 66.67},
            ],
        },
        "source_dates": [DAY],
        "series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 300.0}],
            "null_count": 30,
        },
    }
    _record(
        test_id="CR-01-complete-66-67",
        title_ru="Полный охват 66 и 67",
        input_ru=(
            "На точную дату 31.01.2026 оба счёта имеют кредитовое сальдо: "
            "66 = 120−20 = 100, 67 = 230−30 = 200."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "При полном точном сальдо обоих счетов допустима сумма 300 как "
            "подтверждённый бухгалтерский долг 66+67."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: оба счёта загружены, coverage.complete=true, "
            "сумма 300 и доли 33.33%/66.67% рассчитаны от кредитового сальдо."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:884-919"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2452-2464"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:121-140"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru=(
            "Ровно оба account values, coverage и сумма серии совпадают с "
            "expected_actual."
        ),
    )


def _probe_partial_66_only() -> None:
    parsed = {
        "66": _account(
            "66",
            daily=[_day(skd=0, skk=90)],
            period=MONTH,
            top=_top(skd=0, skk=90),
        )
    }
    _, result = _build(parsed)
    card = _cards(result)["credits"]
    actual = _credit_card_view(card)
    expected = {
        "value": 90.0,
        "reason": None,
        "coverage": {
            "loaded": ["66"],
            "missing": ["67"],
            "complete": False,
        },
        "accounts": [
            {"account": "66", "value": 90.0, "reason": None, "share": 100.0},
            {
                "account": "67",
                "value": None,
                "reason": "Счёт 67 не загружен",
                "share": None,
            },
        ],
    }
    _record(
        test_id="CR-02-partial-66-only",
        title_ru="Частичный охват: только 66",
        input_ru=(
            "На 31.01.2026 загружен только счёт 66 с кредитовым сальдо 90; "
            "источника счёта 67 нет."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "90 нельзя называть полным долгом 66+67: это доступный долг 66 "
            "при coverage.complete=false; отсутствующий 67 не равен нулю."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: реализация оставляет value=90, но явно возвращает "
            "missing=['67'], complete=false и строку 67 без значения. "
            "Частичный результат не выдан за полный."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:899-919", "osv-dashboard/key_indicators.py:1025-1043"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2452-2456", "osv-dashboard/static/key_indicators.js:2560-2563"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:147-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:251-266"],
        },
        assertion_ru="Значение 66 сохранено, а отсутствие 67 отражено в coverage и account row.",
    )


def _probe_missing_66_67() -> None:
    _, result = _build({})
    card = _cards(result)["credits"]
    actual = _credit_card_view(card)
    expected = {
        "value": None,
        "reason": "Счета 66/67 не загружены",
        "coverage": {
            "loaded": [],
            "missing": ["66", "67"],
            "complete": False,
        },
        "accounts": [
            {"account": "66", "value": None, "reason": "Счёт 66 не загружен", "share": None},
            {"account": "67", "value": None, "reason": "Счёт 67 не загружен", "share": None},
        ],
    }
    _record(
        test_id="CR-03-missing-66-67",
        title_ru="Оба счёта отсутствуют",
        input_ru="В source snapshot нет ни 66, ни 67.",
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Долг недоступен; отсутствие файлов/счётов не следует трактовать "
            "как подтверждённый ноль."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: карточка возвращает null с причиной и обе missing "
            "строки, а не нулевой долг."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:884-919", "osv-dashboard/key_indicators.py:1025-1043"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2436-2440", "osv-dashboard/static/key_indicators.js:2560-2563"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:161-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:251-266"],
        },
        assertion_ru="Значение, причины, loaded/missing и complete совпадают с expected_actual.",
    )


def _probe_confirmed_zero() -> None:
    parsed = {
        "66": _account(
            "66",
            daily=[_day(skd=0, skk=0)],
            period=MONTH,
            top=_top(),
        ),
        "67": _account(
            "67",
            daily=[_day(skd=0, skk=0)],
            period=MONTH,
            top=_top(),
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["credits"]
    actual = _credit_card_view(card)
    expected = {
        "value": 0.0,
        "reason": None,
        "coverage": {
            "loaded": ["66", "67"],
            "missing": [],
            "complete": True,
        },
        "accounts": [
            {"account": "66", "value": 0.0, "reason": None, "share": None},
            {"account": "67", "value": 0.0, "reason": None, "share": None},
        ],
    }
    _record(
        test_id="CR-04-confirmed-zero-66-67",
        title_ru="Подтверждённый нулевой долг",
        input_ru=(
            "Оба присутствующих счёта имеют точные СКд=0 и СКк=0 на "
            "31.01.2026."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Ноль можно считать подтверждённым только потому, что оба счёта "
            "присутствуют и имеют точное нулевое сальдо."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: 0.0 сохранён как известное значение, coverage полный, "
            "а доли не превращены в NaN/100% при нулевом знаменателе."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:510-531", "osv-dashboard/key_indicators.py:914-919"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2440-2443", "osv-dashboard/static/key_indicators.js:2560-2564"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru="Нулевые values, null shares и полный coverage совпадают с expected_actual.",
    )


def _probe_date_mismatch() -> None:
    parsed = {
        account: _account(
            account,
            daily=[_day(DAY, obd=10, obk=20)],
            period=QUARTER,
            top=_top(skd=0, skk=100),
        )
        for account in ("66", "67")
    }
    source, result = _build(parsed, selected_date=DAY)
    card = _cards(result)["credits"]
    actual = {
        "value": card["value"],
        "reason": card["reason"],
        "account_reasons": {
            item["account"]: item["reason"] for item in card["accounts"]
        },
        "available_dates": source["dates"],
        "series": _series_summary(card["series"]),
    }
    expected = {
        "value": None,
        "reason": (
            "66: Сальдо доступно только на исходную границу отчёта; "
            "67: Сальдо доступно только на исходную границу отчёта"
        ),
        "account_reasons": {
            "66": "Сальдо доступно только на исходную границу отчёта",
            "67": "Сальдо доступно только на исходную границу отчёта",
        },
        "available_dates": [DAY, MAR],
        "series": {
            "count": 31,
            "known": [],
            "null_count": 31,
        },
    }
    _record(
        test_id="CR-05-exact-date-mismatch",
        title_ru="Баланс только на другой границе периода",
        input_ru=(
            "Для обоих счетов есть оборот 31.01, но единственное top-сальдо "
            "относится к границе 31.03 квартала; выбран 31.01."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Мартовское сальдо нельзя перенести на январскую дату; январское "
            "кредитовое сальдо должно оставаться недоступным."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: exact-date contract не подставляет top квартала и не "
            "превращает январский оборот в январское сальдо."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:419-443", "osv-dashboard/key_indicators.py:510-531"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2520-2532"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:127-140"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru="Оба account balance отсутствуют, причина exact-date и январская series точны.",
    )


def _probe_credit_sign_and_debit_contract() -> None:
    parsed = {
        "66": _account(
            "66",
            daily=[
                _day(skd=120, skk=200),
                _day(analytic="Банк А", skd=120, skk=200),
            ],
            period=MONTH,
            top=_top(skd=120, skk=200),
        ),
        "67": _account(
            "67",
            daily=[
                _day(skd=300, skk=250),
                _day(analytic="Банк Б", skd=300, skk=250),
            ],
            period=MONTH,
            top=_top(skd=300, skk=250),
        ),
    }
    source, result = _build(parsed)
    card = _cards(result)["credits"]
    actual = {
        "card_value": card["value"],
        "account_credit_values": {
            item["account"]: item["value"] for item in card["accounts"]
        },
        "direct_balances": {
            account: {
                "credit": _top_balance(
                    source, account, Date(2026, 1, 31), credit=True
                )[0],
                "debit": _top_balance(
                    source, account, Date(2026, 1, 31), credit=False
                )[0],
            }
            for account in ("66", "67")
        },
        "analytics": [
            {
                "label": item["label"],
                "value": item["value"],
                "credit_balance": item["credit_balance"],
                "debit_balance": item["debit_balance"],
            }
            for item in card["analytics"]
        ],
    }
    expected = {
        "card_value": 80.0,
        "account_credit_values": {"66": 80.0, "67": 0.0},
        "direct_balances": {
            "66": {"credit": 80.0, "debit": 0.0},
            "67": {"credit": 0.0, "debit": 50.0},
        },
        "analytics": [
            {
                "label": "«Банк А»",
                "value": 80.0,
                "credit_balance": 80.0,
                "debit_balance": 0.0,
            },
            {
                "label": "«Банк Б»",
                "value": 0.0,
                "credit_balance": 0.0,
                "debit_balance": 50.0,
            },
        ],
    }
    _record(
        test_id="CR-06-credit-sign-and-debit-balance",
        title_ru="Знак сальдо: кредитовая и дебетовая стороны",
        input_ru=(
            "66: СКд=120, СКк=200; 67: СКд=300, СКк=250. Для каждого "
            "добавлена аналитика банка с теми же сторонами."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Кредитовый долг считается как max(СКк−СКд,0); дебетовая "
            "разница не становится отрицательным кредитом и должна быть "
            "видна только отдельным debit_balance."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: 66 даёт 80 кредитового долга, 67 — 0 при дебетовом "
            "сальдо 50; analytic contract отдельно сохраняет обе стороны."
        ),
        scope_ru=SCOPE + " Аналитики в этом тесте synthetic и сверены с родителем.",
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:419-443", "osv-dashboard/key_indicators.py:534-582"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2457-2464", "osv-dashboard/static/key_indicators.js:2493-2518"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-298"],
        },
        assertion_ru="Card, direct debit/credit helpers и две analytic rows совпадают с expected_actual.",
    )


def _quarter_account(
    account: str,
    values: dict[str, float],
    *,
    include_period: bool = True,
) -> dict[str, Any]:
    return _account(
        account,
        daily=[_day(dt, skd=0, skk=value) for dt, value in values.items()],
        period=QUARTER if include_period else "",
    )


def _probe_delta_same_account_set() -> None:
    complete = {
        "66": _quarter_account("66", {DAY: 100, FEB: 110}),
        "67": _quarter_account("67", {DAY: 50, FEB: 55}),
    }
    changed = {
        "66": _quarter_account("66", {DAY: 100, FEB: 110}),
        "67": _quarter_account("67", {FEB: 55}),
    }
    _, complete_result = _build(complete, selected_date=FEB)
    changed_source, changed_result = _build(changed, selected_date=FEB)
    complete_card = _cards(complete_result)["credits"]
    changed_card = _cards(changed_result)["credits"]
    previous_67 = _top_balance(
        changed_source, "67", Date(2026, 1, 31), credit=True
    )[0]
    actual = {
        "same_set": {
            "value": complete_card["value"],
            "delta": complete_card["delta"],
            "coverage_complete": complete_card["coverage"]["complete"],
        },
        "changed_set": {
            "value": changed_card["value"],
            "delta": changed_card["delta"],
            "coverage_complete": changed_card["coverage"]["complete"],
            "current_accounts": [
                item["account"]
                for item in changed_card["accounts"]
                if item["value"] is not None
            ],
            "previous_67_balance": previous_67,
        },
    }
    expected = {
        "same_set": {
            "value": 165.0,
            "delta": {
                "value": 15.0,
                "unit": "RUB",
                "label": "к предыдущей сопоставимой дате",
            },
            "coverage_complete": True,
        },
        "changed_set": {
            "value": 165.0,
            "delta": None,
            "coverage_complete": True,
            "current_accounts": ["66", "67"],
            "previous_67_balance": None,
        },
    }
    _record(
        test_id="CR-07-delta-same-account-set",
        title_ru="Delta только при одинаковом наборе счетов",
        input_ru=(
            "Сравнены февраль с январём: в полном варианте 66=100→110 и "
            "67=50→55; во втором варианте январское точное сальдо 67 "
            "отсутствует, хотя февральское есть."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Delta 15 допустима только для одинакового набора подтверждённых "
            "счетов. При появлении 67 без предыдущего точного сальдо delta "
            "должна быть недоступна, а не сравнивать 165 с неполной базой."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: same-set даёт +15; changed-set оставляет delta=null, "
            "несмотря на текущий complete=true для выбранного месяца. "
            "Предыдущий баланс 67 фактически отсутствует."
        ),
        scope_ru=SCOPE + " Даты и наборы счетов synthetic; это не банковский cash-flow.",
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:1208-1280"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2465-2474", "osv-dashboard/static/key_indicators.js:2560-2564"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:121-140"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru="Обе ветви value/coverage и наличие либо отсутствие delta совпадают с expected_actual.",
    )


def _probe_timeline_coverage_changes() -> None:
    parsed = {
        "66": _account(
            "66",
            balance_reports=[
                _report(
                    MONTH,
                    top=_top(skd=0, skk=100),
                    source_file="66-январь.xlsx",
                ),
                _report(
                    "за март 2026 г.",
                    top=_top(skd=0, skk=120),
                    source_file="66-март.xlsx",
                ),
            ],
            coverage_reports=[MONTH, "за март 2026 г."],
        ),
        "67": _account(
            "67",
            balance_reports=[
                _report(
                    MONTH,
                    top=_top(skd=0, skk=50),
                    source_file="67-январь.xlsx",
                )
            ],
            coverage_reports=[MONTH],
        ),
    }
    source, result = _build(
        parsed,
        period="quarter",
        selected_date=MAR,
    )
    card = _cards(result)["credits"]
    actual = {
        "coverage_intervals": {
            account: [
                {"start": item["start"], "end": item["end"]}
                for item in source["accounts"][account]["coverage"]
            ]
            for account in ("66", "67")
        },
        "value_at_march": card["value"],
        "coverage": card["coverage"],
        "series": card["series"],
        "partial_warning": any(
            "неполное" in warning for warning in result["period"]["warnings"]
        ),
    }
    expected = {
        "coverage_intervals": {
            "66": [
                {"start": "2026-01-01", "end": "2026-01-31"},
                {"start": "2026-03-01", "end": "2026-03-31"},
            ],
            "67": [{"start": "2026-01-01", "end": "2026-01-31"}],
        },
        "value_at_march": 120.0,
        "coverage": {
            "loaded": ["66"],
            "missing": ["67"],
            "complete": False,
        },
        "series": [
            {"label": "Янв 2026", "value": 150.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": 120.0},
        ],
        "partial_warning": True,
    }
    _record(
        test_id="CR-08-timeline-coverage-changes",
        title_ru="Изменение охвата по временной шкале",
        input_ru=(
            "66 имеет точные отчёты января и марта, 67 — только января; "
            "квартальная серия строится на январь, февраль и март."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Февральский пробел должен быть null, мартовское значение 120 — "
            "частичный охват 66, а не полный долг 66+67; warning о неполном "
            "квартале обязателен."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: calendar gap не интерполируется в ноль, мартовский "
            "card.value не объявлен полным, missing=['67'] и partial warning "
            "сохранены."
        ),
        scope_ru=SCOPE + " Timeline построен по synthetic balance_reports/coverage_reports.",
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:198-263", "osv-dashboard/key_indicators.py:1104-1122", "osv-dashboard/key_indicators.py:1186-1191"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2520-2532"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:121-140"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru="Интервалы, series null-gap, card coverage и warning совпадают с expected_actual.",
    )


def _probe_turnover_is_not_paid() -> None:
    parsed = {
        "66": _account(
            "66",
            daily=[_day(skd=0, skk=100, obd=30, obk=120)],
            period=MONTH,
            top=_top(skd=0, skk=100),
        ),
        "67": _account(
            "67",
            daily=[_day(skd=0, skk=50, obd=10, obk=40)],
            period=MONTH,
            top=_top(skd=0, skk=50),
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["credits"]
    modal_source = FRONTEND.read_text(encoding="utf-8")
    modal_start = modal_source.index("renderCreditsModal(card)")
    modal_end = modal_source.index("renderSalaryModal(card)", modal_start)
    modal = modal_source[modal_start:modal_end]
    actual = {
        "value": card["value"],
        "accounts": [
            {
                "account": item["account"],
                "value": item["value"],
                "debit_turnover": item["debit_turnover"],
                "credit_turnover": item["credit_turnover"],
            }
            for item in card["accounts"]
        ],
        "static_turnover_caveat": (
            "не называются погашением или привлечением" in modal
        ),
        "static_credit_turnover_column": "Кредитовый оборот" in modal,
    }
    expected = {
        "value": 150.0,
        "accounts": [
            {
                "account": "66",
                "value": 100.0,
                "debit_turnover": 30.0,
                "credit_turnover": 120.0,
            },
            {
                "account": "67",
                "value": 50.0,
                "debit_turnover": 10.0,
                "credit_turnover": 40.0,
            },
        ],
        "static_turnover_caveat": True,
        "static_credit_turnover_column": True,
    }
    _record(
        test_id="CR-09-turnover-not-paid",
        title_ru="Обороты 66/67 не равны оплате или привлечению",
        input_ru=(
            "Для 66 переданы СКк=100, ОбД=30, ОбК=120; для 67 СКк=50, "
            "ОбД=10, ОбК=40. Статический текст renderCreditsModal сверяет "
            "подпись оборотов."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Оборот можно показать как бухгалтерское движение, но нельзя "
            "называть его полученным кредитом или погашением без "
            "корреспонденции и регистра платежей."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: backend возвращает обороты отдельно от сальдо, а "
            "frontend прямо оговаривает, что они не называются погашением "
            "или привлечением. Это не доказательство фактических платежей."
        ),
        scope_ru=SCOPE + " Платёжный регистр в probe отсутствует намеренно.",
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:885-910"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2477-2490"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-289"],
        },
        assertion_ru="Обороты account rows и два статических caveat-флага совпадают с expected_actual.",
    )


def _probe_parser_analytic_balance_availability() -> None:
    rows = [
        {
            "lvl": 0,
            "label": "66",
            "p0": "66",
            "p1": "",
            "p2": "",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 30,
            "obk": 120,
            "skd": 10,
            "skk": 100,
        },
        {
            "lvl": 1,
            "label": "Банк А",
            "p0": "66",
            "p1": "Банк А",
            "p2": "",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 30,
            "obk": 120,
            "skd": 10,
            "skk": 100,
        },
    ]
    parsed = {
        "66": _account(
            "66",
            # This is the current parser shape: date rows carry turnover,
            # report rows carry balances and analytic hierarchy.
            daily=[_day(DAY, code="66.01", obd=30, obk=120)],
            rows=rows,
            period=MONTH,
            top=_top(obd=30, obk=120, skd=10, skk=100),
        )
    }
    source, result = _build(parsed)
    card = _cards(result)["credits"]
    parser_text = PARSER.read_text(encoding="utf-8").splitlines()
    daily_fragment = "\n".join(parser_text[281:289])
    report_fragment = "\n".join(parser_text[293:299])
    modal_source = FRONTEND.read_text(encoding="utf-8")
    modal_start = modal_source.index("renderCreditsModal(card)")
    modal_end = modal_source.index("renderSalaryModal(card)", modal_start)
    modal = modal_source[modal_start:modal_end]
    actual = {
        "card_value": card["value"],
        "source_daily_balance_keys": sorted(
            key
            for key in ("skd", "skk")
            if key in source["accounts"]["66"]["daily"][0]
        ),
        "source_report_row_count": len(source["accounts"]["66"]["rows"]),
        "analytic_rows": [
            {
                "account": item["account"],
                "label": item["label"],
                "value": item["value"],
                "debit_turnover": item["debit_turnover"],
                "credit_turnover": item["credit_turnover"],
            }
            for item in card["analytics"]
        ],
        "direct_analytic_rows": [
            {
                "label": item["label"],
                "value": item["value"],
            }
            for item in _account_balance_analytics(
                source, "66", Date(2026, 1, 31), credit=True
            )
        ],
        "parser_daily_turnover_only": (
            "obd=obd" in daily_fragment
            and "obk=obk" in daily_fragment
            and "skd" not in daily_fragment
            and "skk" not in daily_fragment
        ),
        "parser_report_rows_keep_balances": (
            "skd=skd" in report_fragment and "skk=skk" in report_fragment
        ),
        "js_reads_credit_analytics": "card.analytics || []" in modal,
        "js_has_empty_analytics_message": (
            "Аналитические сальдо 66/67 не представлены." in modal
        ),
    }
    expected = {
        "card_value": 90.0,
        "source_daily_balance_keys": [],
        "source_report_row_count": 2,
        "analytic_rows": [
            {
                "account": "66",
                "label": "Банк А",
                "value": 90.0,
                "debit_turnover": 30.0,
                "credit_turnover": 120.0,
            }
        ],
        "direct_analytic_rows": [{"label": "Банк А", "value": 90.0}],
        "parser_daily_turnover_only": True,
        "parser_report_rows_keep_balances": True,
        "js_reads_credit_analytics": True,
        "js_has_empty_analytics_message": True,
    }
    _record(
        test_id="CR-10-parser-analytic-balance-availability",
        title_ru="Аналитическое сальдо: фактическая форма parser и backend fallback",
        input_ru=(
            "В parser-shaped source daily row 66.01 содержит только ОбД/ОбК; "
            "rows содержат 66 и дочернюю аналитику «Банк А» с СКд=10, СКк=100. "
            "Проверены также статические ветки renderCreditsModal."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Отсутствие skd/skk в daily не должно само по себе уничтожать "
            "аналитику, если report rows с уровнями и балансами доступны; "
            "при отсутствии обоих источников аналитика должна быть Н/Д."
        ),
        assessment_ru=(
            "ПРОЙДЕНО для текущего parser-shaped snapshot: backend "
            "_account_balance_analytics использует report rows и возвращает "
            "«Банк А»=90, несмотря на пустые daily balance keys; JS читает "
            "card.analytics и имеет явное empty-state. Это не доказательство "
            "наличия такой аналитики в каждом реальном файле."
        ),
        scope_ru=(
            SCOPE
            + " Проверена форма текущего parser source, но не прочитан реальный XLS."
        ),
        source_refs={
            "R1_backend": ["osv-dashboard/key_indicators.py:534-582", "osv-dashboard/key_indicators.py:912-913"],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2493-2518"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:127-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:282-298"],
        },
        assertion_ru=(
            "Actual parser flags, source shape, backend card/analytic fallback "
            "and static JS flags совпадают с expected_actual."
        ),
    )


def main() -> int:
    _probe_complete_66_67()
    _probe_partial_66_only()
    _probe_missing_66_67()
    _probe_confirmed_zero()
    _probe_date_mismatch()
    _probe_credit_sign_and_debit_contract()
    _probe_delta_same_account_set()
    _probe_timeline_coverage_changes()
    _probe_turnover_is_not_paid()
    _probe_parser_analytic_balance_availability()

    source_files = {
        "R1_backend": {
            "path": str(BACKEND.relative_to(ROOT)),
            "sha256": _sha256(BACKEND),
            "role": "pure Python key_indicators calculation and source builder",
        },
        "R2_JS": {
            "path": str(FRONTEND.relative_to(ROOT)),
            "sha256": _sha256(FRONTEND),
            "role": "static credits modal contract inspection; no arithmetic expression executed",
        },
        "R3_service": {
            "path": str(SERVICE.relative_to(ROOT)),
            "sha256": _sha256(SERVICE),
            "role": "source reference for assembled reports and observations; not imported",
        },
        "R4_parser": {
            "path": str(PARSER.relative_to(ROOT)),
            "sha256": _sha256(PARSER),
            "role": "source reference for daily turnover versus report balances; not imported",
        },
        "runner": {
            "path": str(Path(__file__).relative_to(ROOT)),
            "sha256": _sha256(Path(__file__)),
            "role": "bounded isolated probe runner",
        },
    }
    findings = sum(case["status"] == "FINDING" for case in CASES)
    report = {
        "report_id": "credits-audit-probes",
        "purpose_ru": (
            "8–10 isolated probes фактического контракта карточки кредитов "
            "по счетам 66/67 на source snapshots."
        ),
        "scope_ru": SCOPE,
        "status_semantics_ru": (
            "implementation_status=PASS означает, что текущий pure backend "
            "результат и assert воспроизведены. audit_assessment=FINDING "
            "означает отличие от audit-safe ожидания, а не падение runner. "
            "Частичный охват никогда не маркируется как полный."
        ),
        "execution": {
            "runner": "reports/credits_audit_probes.py",
            "command": "python3 -B reports/credits_audit_probes.py",
            "python": sys.version.split()[0],
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
            "services_started": False,
            "node_used": False,
            "js_extraction": None,
            "js_note": (
                "В credits modal нет отдельной вычислительной JS-формулы, "
                "которую нужно было бы исполнять; проверены только статические "
                "строки и ветки источника."
            ),
        },
        "source_files": source_files,
        "case_count": len(CASES),
        "assertions_verified": len(CASES),
        "finding_count": findings,
        "cases": CASES,
        "limitations_ru": [
            (
                "Все входы synthetic source snapshots в памяти; реальные XLS, "
                "предприятия, БД, service layer и сеть не читались."
            ),
            (
                "Кредитовое сальдо 66/67 — бухгалтерская величина; отчёт не "
                "подтверждает тело договора, ставку, проценты, график, "
                "платёж, погашение или просрочку."
            ),
            (
                "Partial cases показывают доступные счета только с "
                "coverage.complete=false; отсутствующий счёт не заменён нулём "
                "и не включён в вывод как полный долг."
            ),
            (
                "Timeline gap проверен на balance_reports/coverage_reports: "
                "null означает отсутствие точного сальдо, а не подтверждённый "
                "нулевой месяц."
            ),
            (
                "Parser analytic probe подтверждает только текущую форму "
                "daily/report rows и fallback pure helper; отсутствие "
                "аналитики в другом XLS этим не исключается."
            ),
            (
                "R2 JS проверен статически без DOM/browser; Node expression "
                "не запускался, так как отдельного вычислительного credits "
                "expression в проверенных ветках нет."
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