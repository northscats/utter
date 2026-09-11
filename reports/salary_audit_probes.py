#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Изолированные synthetic probes расчёта зарплатной карточки.

Runner imports only the pure ``key_indicators`` module.  All inputs are small
in-memory ОСВ snapshots in the shape accepted by ``build_indicator_source``.
Flask, service layer, database, environment, network and real payroll data are
deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any


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
    "Счета 70 и 69: точное бухгалтерское сальдо, дебетовая сторона, "
    "обороты и аналитика ОСВ. Это не зарплатный или кадровый регистр: "
    "ОСВ не доказывает ведомость, срок выплаты, просрочку, ФОТ, "
    "численность, фактический платёж или состав страховых взносов."
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


def _series_summary(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(series),
        "known": [item for item in series if item["value"] is not None],
        "null_count": sum(item["value"] is None for item in series),
    }


def _salary_view(card: dict[str, Any]) -> dict[str, Any]:
    insurance = card["insurance"]
    return {
        "value": card["value"],
        "reason": card["reason"],
        "debit_balance": card["debit_balance"],
        "debit_turnover": card["debit_turnover"],
        "credit_turnover": card["credit_turnover"],
        "delta": card["delta"],
        "insurance": {
            "credit_balance": insurance["credit_balance"],
            "debit_balance": insurance["debit_balance"],
            "debit_turnover": insurance["debit_turnover"],
            "credit_turnover": insurance["credit_turnover"],
            "turnover_ratio": insurance["turnover_ratio"],
        },
    }


def _analytic_view(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "account": item["account"],
            "label": item["label"],
            "value": item["value"],
            "credit_balance": item["credit_balance"],
            "debit_balance": item["debit_balance"],
            "debit_turnover": item["debit_turnover"],
            "credit_turnover": item["credit_turnover"],
        }
        for item in card["analytics"]
    ]


def _salary_modal() -> str:
    text = FRONTEND.read_text(encoding="utf-8")
    start = text.index("renderSalaryModal(card)")
    end = text.index("renderCapexModal(card)", start)
    return text[start:end]


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
    # expected_actual is the exact current implementation result, not a
    # desired value.  Audit limitations are evaluated separately in prose.
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


def _probe_complete_salary_and_insurance() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(obd=40, obk=100)],
            period=MONTH,
            top=_top(skd=20, skk=140),
        ),
        "69": _account(
            "69",
            daily=[_day(obd=15, obk=30)],
            period=MONTH,
            top=_top(skd=5, skk=35),
        ),
    }
    source, result = _build(parsed)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "accrual_series": _series_summary(card["accrual_series"]),
        "insurance_series": _series_summary(card["insurance_series"]),
        "source_dates": source["dates"],
    }
    expected = {
        "salary": {
            "value": 120.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 40.0,
            "credit_turnover": 100.0,
            "delta": None,
            "insurance": {
                "credit_balance": 30.0,
                "debit_balance": 0.0,
                "debit_turnover": 15.0,
                "credit_turnover": 30.0,
                "turnover_ratio": 30.0,
            },
        },
        "salary_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 120.0}],
            "null_count": 30,
        },
        "accrual_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 100.0}],
            "null_count": 30,
        },
        "insurance_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 30.0}],
            "null_count": 30,
        },
        "source_dates": [DAY],
    }
    _record(
        test_id="SAL-01-complete-70-69",
        title_ru="Полное точное сальдо 70 и 69",
        input_ru=(
            "На 31.01.2026 счёт 70 имеет СКд=20, СКк=140 и "
            "ОбД=40, ОбК=100; счёт 69 имеет СКд=5, СКк=35 и "
            "ОбД=15, ОбК=30."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Pure расчёт возвращает кредитовое сальдо 70=120, "
            "кредитовое сальдо 69=30 и справочное отношение кредитовых "
            "оборотов 69/70=30%."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: точная граница счёта 70 даёт 120, дебетовая "
            "сторона неотрицательна, обороты и месячные серии сохранены "
            "раздельно; 69 показан отдельным insurance-блоком."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:510-531",
                "osv-dashboard/key_indicators.py:607-615",
                "osv-dashboard/key_indicators.py:920-931",
                "osv-dashboard/key_indicators.py:1044-1063",
                "osv-dashboard/key_indicators.py:1192-1203",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2636-2655",
                "osv-dashboard/static/key_indicators.js:2771-2785",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:90-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-313"],
        },
        assertion_ru=(
            "Значения salary, insurance и три месячные серии совпадают "
            "с expected_actual."
        ),
    )


def _probe_missing_70_not_zero() -> None:
    parsed = {
        "69": _account(
            "69",
            daily=[_day(obd=5, obk=20)],
            period=MONTH,
            top=_top(skd=0, skk=20),
        )
    }
    _, result = _build(parsed)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "accrual_series": _series_summary(card["accrual_series"]),
    }
    expected = {
        "salary": {
            "value": None,
            "reason": "Счёт 70 не загружен",
            "debit_balance": None,
            "debit_turnover": None,
            "credit_turnover": None,
            "delta": None,
            "insurance": {
                "credit_balance": 20.0,
                "debit_balance": 0.0,
                "debit_turnover": 5.0,
                "credit_turnover": 20.0,
                "turnover_ratio": None,
            },
        },
        "salary_series": {"count": 31, "known": [], "null_count": 31},
        "accrual_series": {"count": 31, "known": [], "null_count": 31},
    }
    _record(
        test_id="SAL-02-missing-70",
        title_ru="Счёт 70 отсутствует и не заменяется нулём",
        input_ru=(
            "В source snapshot есть только счёт 69 с кредитовым сальдо 20; "
            "счёт 70 полностью отсутствует."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Отсутствие счёта 70 не доказывает отсутствие долга работникам; "
            "карточка и серия должны быть Н/Д, а не 0."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: salary.value, salary debit и обороты равны null с "
            "явной причиной «Счёт 70 не загружен»; известный счёт 69 "
            "не маскирует отсутствие 70."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:510-513",
                "osv-dashboard/key_indicators.py:920-927",
                "osv-dashboard/key_indicators.py:1044-1057",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2712-2742",
                "osv-dashboard/static/key_indicators.js:2768-2775",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:147-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:250-266"],
        },
        assertion_ru=(
            "Причина, null-значения и null-серия salary совпадают с "
            "expected_actual."
        ),
    )


def _probe_confirmed_zero() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(obd=0, obk=0)],
            period=MONTH,
            top=_top(),
        ),
        "69": _account(
            "69",
            daily=[_day(obd=0, obk=0)],
            period=MONTH,
            top=_top(),
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "accrual_series": _series_summary(card["accrual_series"]),
    }
    expected = {
        "salary": {
            "value": 0.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 0.0,
            "credit_turnover": 0.0,
            "delta": None,
            "insurance": {
                "credit_balance": 0.0,
                "debit_balance": 0.0,
                "debit_turnover": 0.0,
                "credit_turnover": 0.0,
                "turnover_ratio": None,
            },
        },
        "salary_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 0.0}],
            "null_count": 30,
        },
        "accrual_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 0.0}],
            "null_count": 30,
        },
    }
    _record(
        test_id="SAL-03-confirmed-zero",
        title_ru="Подтверждённый нулевой остаток 70",
        input_ru=(
            "Присутствующие счета 70 и 69 имеют точные СКд=0, СКк=0 и "
            "нулевые обороты на 31.01.2026."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Ноль допустим только при присутствующем счёте и точном "
            "нулевом сальдо; отсутствие 70 проверяется отдельным probe."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: 0.0 остаётся известным значением, shares/ratio не "
            "создаются, а нулевой кредитовый оборот не используется как "
            "делитель."
        ),
        scope_ru=SCOPE,
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:45-48",
                "osv-dashboard/key_indicators.py:510-531",
                "osv-dashboard/key_indicators.py:928-931",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2624-2634",
                "osv-dashboard/static/key_indicators.js:2712-2739",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:147-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru=(
            "Нулевые balances, turnover и известная нулевая salary series "
            "совпадают с expected_actual."
        ),
    )


def _probe_stale_exact_date() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(DAY, obd=5, obk=10)],
            period=MONTH,
            top=_top(skd=0, skk=80),
        ),
        # A separate account supplies a February option without supplying a
        # February balance for account 70.
        "90": _account(
            "90",
            daily=[_day(FEB, code="90.01", obk=10)],
            period="",
        ),
    }
    source, result = _build(parsed, selected_date=FEB)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "available_dates": source["dates"],
    }
    expected = {
        "salary": {
            "value": None,
            "reason": "Сальдо доступно только на исходную границу отчёта",
            "debit_balance": None,
            "debit_turnover": None,
            "credit_turnover": None,
            "delta": None,
            "insurance": {
                "credit_balance": None,
                "debit_balance": None,
                "debit_turnover": None,
                "credit_turnover": None,
                "turnover_ratio": None,
            },
        },
        "salary_series": {"count": 28, "known": [], "null_count": 28},
        "available_dates": [DAY, FEB],
    }
    _record(
        test_id="SAL-04-stale-exact-date",
        title_ru="Январское сальдо не переносится на февраль",
        input_ru=(
            "Для 70 единственное top-сальдо относится к 31.01.2026; "
            "отдельный synthetic счёт 90 добавляет доступную дату 28.02, "
            "но февральского сальдо 70 нет."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Старое сальдо 80 нельзя отображать как февральскую зарплатную "
            "задолженность; на выбранную дату должно быть Н/Д."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: exact-date helper не использует январский top для "
            "февраля; февральская salary series полностью null."
        ),
        scope_ru=SCOPE + " Счёт 90 здесь только создаёт synthetic option даты.",
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:510-531",
                "osv-dashboard/key_indicators.py:649-675",
                "osv-dashboard/key_indicators.py:1192-1203",
            ],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2712-2739"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:121-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:300-313"],
        },
        assertion_ru=(
            "Причина stale-date, список дат и 28 null-значений серии "
            "совпадают с expected_actual."
        ),
    )


def _probe_debit_side() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(obd=60, obk=80, skd=250, skk=200)],
            period=MONTH,
            top=_top(skd=250, skk=200),
        )
    }
    source, result = _build(parsed)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "direct_balances": {
            "credit": _top_balance(
                source, "70", Date(2026, 1, 31), credit=True
            )[0],
            "debit": _top_balance(
                source, "70", Date(2026, 1, 31), credit=False
            )[0],
        },
    }
    expected = {
        "salary": {
            "value": 0.0,
            "reason": None,
            "debit_balance": 50.0,
            "debit_turnover": 60.0,
            "credit_turnover": 80.0,
            "delta": None,
            "insurance": {
                "credit_balance": None,
                "debit_balance": None,
                "debit_turnover": None,
                "credit_turnover": None,
                "turnover_ratio": None,
            },
        },
        "direct_balances": {"credit": 0.0, "debit": 50.0},
    }
    _record(
        test_id="SAL-05-debit-side-not-negative-credit",
        title_ru="Дебетовая сторона 70 не становится отрицательной зарплатой",
        input_ru=(
            "На 31.01.2026 для 70 СКд=250, СКк=200, ОбД=60 и ОбК=80: "
            "дебетовая разница равна 50."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Дебетовый остаток 50 должен быть показан отдельно, а "
            "кредитовая задолженность — 0, без отрицательного долга."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: salary.value=max(СКк−СКд,0)=0, "
            "debit_balance=max(СКд−СКк,0)=50; обе стороны и обороты "
            "не смешаны."
        ),
        scope_ru=SCOPE + " Дебетовая природа synthetic остатка не классифицируется.",
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:510-531",
                "osv-dashboard/key_indicators.py:920-923",
                "osv-dashboard/key_indicators.py:1044-1049",
            ],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:2641-2645"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-313"],
        },
        assertion_ru=(
            "Salary, direct credit/debit helpers и turnovers совпадают с "
            "expected_actual."
        ),
    )


def _probe_turnover_is_not_payment_or_fot() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(obd=40, obk=100)],
            period=MONTH,
            top=_top(skd=0, skk=100),
        ),
        "69": _account(
            "69",
            daily=[_day(obd=10, obk=50)],
            period=MONTH,
            top=_top(skd=0, skk=50),
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["salary"]
    modal = _salary_modal()
    actual = {
        "salary": _salary_view(card),
        "static_not_payment": (
            "не называется выплатой зарплаты без корреспонденции." in modal
        ),
        "static_not_full_fot": "не весь оборот обязательно входит в ФОТ." in modal,
        "static_not_paid_insurance": (
            "не называется начисленными взносами без классификации." in modal
        ),
        "static_not_overdue": (
            "Бухгалтерское сальдо не равно просроченной зарплате." in modal
        ),
        "static_unconfirmed_fot_label": (
            "ОбК 70 · не подтверждённый ФОТ" in modal
        ),
    }
    expected = {
        "salary": {
            "value": 100.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 40.0,
            "credit_turnover": 100.0,
            "delta": None,
            "insurance": {
                "credit_balance": 50.0,
                "debit_balance": 0.0,
                "debit_turnover": 10.0,
                "credit_turnover": 50.0,
                "turnover_ratio": 50.0,
            },
        },
        "static_not_payment": True,
        "static_not_full_fot": True,
        "static_not_paid_insurance": True,
        "static_not_overdue": True,
        "static_unconfirmed_fot_label": True,
    }
    _record(
        test_id="SAL-06-turnover-not-payment-or-fot",
        title_ru="Обороты 70/69 не объявляются выплатой, ФОТ или уплатой",
        input_ru=(
            "Для 70 заданы ОбД=40, ОбК=100 и СКк=100; для 69 "
            "ОбД=10, ОбК=50 и СКк=50. Проверены статические caveat-тексты "
            "renderSalaryModal."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Обороты ОСВ могут быть показаны как движения, но не равны "
            "фактической выплате, утверждённому ФОТ или уплаченным взносам "
            "без корреспонденции и payroll/payment регистров."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: pure backend возвращает сырые обороты, а salary modal "
            "явно помечает их как не подтверждённый ФОТ и не платёж; "
            "сальдо отдельно оговорено как не доказательство просрочки."
        ),
        scope_ru=SCOPE + " Платёжный и payroll-регистры намеренно отсутствуют.",
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:607-615",
                "osv-dashboard/key_indicators.py:920-931",
                "osv-dashboard/key_indicators.py:1060-1063",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2646-2668",
                "osv-dashboard/static/key_indicators.js:2768-2775",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-313"],
        },
        assertion_ru=(
            "Backend values and all five static safety/caveat flags equal "
            "expected_actual."
        ),
    )


def _probe_parser_analytic_balance_contract() -> None:
    rows = [
        {
            "lvl": 0,
            "label": "70",
            "p0": "70",
            "p1": "",
            "p2": "",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 40,
            "obk": 100,
            "skd": 0,
            "skk": 120,
        },
        {
            "lvl": 1,
            "label": "Производственный персонал",
            "p0": "70",
            "p1": "Производственный персонал",
            "p2": "",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 40,
            "obk": 100,
            "skd": 0,
            "skk": 120,
        },
    ]
    parsed = {
        "70": _account(
            "70",
            # Current parser shape: daily date rows have turnover only.
            daily=[_day(code="70.01", obd=40, obk=100)],
            rows=rows,
            period=MONTH,
            top=_top(obd=40, obk=100, skd=0, skk=120),
        )
    }
    source, result = _build(parsed)
    card = _cards(result)["salary"]
    parser_text = PARSER.read_text(encoding="utf-8").splitlines()
    daily_fragment = "\n".join(parser_text[281:289])
    report_fragment = "\n".join(parser_text[293:299])
    modal = _salary_modal()
    actual = {
        "card_value": card["value"],
        "source_daily_balance_keys": sorted(
            key
            for key in ("skd", "skk")
            if key in source["accounts"]["70"]["daily"][0]
        ),
        "source_report_row_count": len(source["accounts"]["70"]["rows"]),
        "analytic_rows": _analytic_view(card),
        "parser_daily_turnover_only": (
            "obd=obd" in daily_fragment
            and "obk=obk" in daily_fragment
            and "skd" not in daily_fragment
            and "skk" not in daily_fragment
        ),
        "parser_report_rows_keep_balances": (
            "skd=skd" in report_fragment and "skk=skk" in report_fragment
        ),
        "js_reads_salary_analytics": "card.analytics || []" in modal,
        "js_has_empty_analytics_message": (
            "Аналитики счёта ${account} не представлены." in modal
        ),
    }
    expected = {
        "card_value": 120.0,
        "source_daily_balance_keys": [],
        "source_report_row_count": 2,
        "analytic_rows": [
            {
                "account": "70",
                "label": "Производственный персонал",
                "value": 120.0,
                "credit_balance": 120.0,
                "debit_balance": 0.0,
                "debit_turnover": 40.0,
                "credit_turnover": 100.0,
            }
        ],
        "parser_daily_turnover_only": True,
        "parser_report_rows_keep_balances": True,
        "js_reads_salary_analytics": True,
        "js_has_empty_analytics_message": True,
    }
    _record(
        test_id="SAL-07-parser-analytic-balance-contract",
        title_ru="Аналитическое сальдо 70 в фактической форме parser",
        input_ru=(
            "Daily row 70.01 содержит только ОбД=40 и ОбК=100; report "
            "rows содержат строку 70 и дочернюю аналитику "
            "«Производственный персонал» с СКд=0, СКк=120. "
            "Проверены также статические ветки salary modal."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Отсутствие skd/skk в daily parser row не должно уничтожать "
            "аналитику, если report rows с балансами доступны; без обоих "
            "источников аналитика должна быть Н/Д."
        ),
        assessment_ru=(
            "ПРОЙДЕНО для текущей parser-shaped формы: pure fallback "
            "возвращает дочернюю аналитику 70=120, daily сохраняет только "
            "обороты, а UI читает card.analytics и имеет empty-state."
        ),
        scope_ru=(
            SCOPE
            + " Проверена форма parser source, но не прочитан реальный XLS "
            "и не определена категория персонала по названию."
        ),
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:534-582",
                "osv-dashboard/key_indicators.py:912-923",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2691-2710",
                "osv-dashboard/static/key_indicators.js:2787-2795",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:127-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:272-313"],
        },
        assertion_ru=(
            "Parser flags, source shape, analytic row and static UI flags "
            "совпадают с expected_actual."
        ),
    )


def _probe_delta_exact_salary_balances() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[
                _day(DAY, obd=20, obk=80, skd=0, skk=100),
                _day(FEB, obd=30, obk=90, skd=0, skk=140),
            ],
            period="",
        )
    }
    _, result = _build(parsed, selected_date=FEB)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "accrual_series": _series_summary(card["accrual_series"]),
    }
    expected = {
        "salary": {
            "value": 140.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 30.0,
            "credit_turnover": 90.0,
            "delta": {
                "value": 40.0,
                "unit": "RUB",
                "label": "к предыдущей сопоставимой дате",
            },
            "insurance": {
                "credit_balance": None,
                "debit_balance": None,
                "debit_turnover": None,
                "credit_turnover": None,
                "turnover_ratio": None,
            },
        },
        "salary_series": {
            "count": 28,
            "known": [{"label": "28.02.2026", "value": 140.0}],
            "null_count": 27,
        },
        "accrual_series": {
            "count": 28,
            "known": [{"label": "28.02.2026", "value": 90.0}],
            "null_count": 27,
        },
    }
    _record(
        test_id="SAL-08-delta-exact-balance",
        title_ru="Delta зарплатного сальдо между точными датами",
        input_ru=(
            "На 31.01 salary balance=100, на 28.02=140; обе даты "
            "содержат точные daily СКд/СКк и отдельные обороты 70."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "При двух точных сопоставимых датах изменение кредитового "
            "сальдо должно быть 140−100=40, а обороты не заменяют баланс."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: delta=40 строится по exact salary balances; "
            "февральские обороты 70=90 и balance=140 остаются разными "
            "величинами."
        ),
        scope_ru=SCOPE + " Delta synthetic; это не изменение фактически выплаченной зарплаты.",
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:1208-1215",
                "osv-dashboard/key_indicators.py:1280-1284",
            ],
            "R2_JS": ["osv-dashboard/static/key_indicators.js:800-808"],
            "R3_service": ["osv-dashboard/key_indicators_service.py:141-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-313"],
        },
        assertion_ru=(
            "Current salary, delta and two selected-period series совпадают "
            "с expected_actual."
        ),
    )


def _probe_timeline_gap() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[
                _day(DAY, obd=20, obk=80, skd=0, skk=100),
                _day(MAR, obd=30, obk=90, skd=0, skk=130),
            ],
            period=QUARTER,
        )
    }
    _, result = _build(
        parsed,
        period="quarter",
        selected_date=MAR,
    )
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": card["series"],
        "accrual_series": card["accrual_series"],
        "insurance_series": card["insurance_series"],
    }
    expected = {
        "salary": {
            "value": 130.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 50.0,
            "credit_turnover": 170.0,
            "delta": None,
            "insurance": {
                "credit_balance": None,
                "debit_balance": None,
                "debit_turnover": None,
                "credit_turnover": None,
                "turnover_ratio": None,
            },
        },
        "salary_series": [
            {"label": "Янв 2026", "value": 100.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": 130.0},
        ],
        "accrual_series": [
            {"label": "Янв 2026", "value": 80.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": 90.0},
        ],
        "insurance_series": [
            {"label": "Янв 2026", "value": None},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": None},
        ],
    }
    _record(
        test_id="SAL-09-timeline-gap",
        title_ru="Пробел февраля остаётся Н/Д в зарплатной timeline",
        input_ru=(
            "В квартале у 70 есть точные строки января и марта с "
            "сальдо 100 и 130; строк февраля нет. Обороты января и марта "
            "равны 80 и 90 по кредиту."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Отсутствующий февраль не является нулевым начислением или "
            "нулевым остатком; месячные balance/accrual series должны "
            "сохранить null-gap."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: январь и март сохранены, февраль не интерполирован "
            "в 0 ни для salary balance, ни для ОбК 70; отсутствие 69 "
            "даёт отдельную полностью null insurance series."
        ),
        scope_ru=(
            SCOPE
            + " Timeline построена из synthetic daily balance/turnover rows; "
            "она не является реестром ведомостей."
        ),
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:1104-1122",
                "osv-dashboard/key_indicators.py:1192-1206",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2712-2742",
                "osv-dashboard/static/key_indicators.js:2781-2785",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:121-172"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-313"],
        },
        assertion_ru=(
            "Salary, accrual and insurance timeline rows совпадают с "
            "expected_actual, включая февральские null."
        ),
    )


def _probe_unknown_turnover_not_zero() -> None:
    parsed = {
        "70": _account(
            "70",
            daily=[_day(obd=10, obk=None)],
            period=MONTH,
            top=_top(skd=0, skk=90),
        ),
        "69": _account(
            "69",
            daily=[_day(obd=None, obk=20)],
            period=MONTH,
            top=_top(skd=0, skk=20),
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["salary"]
    actual = {
        "salary": _salary_view(card),
        "salary_series": _series_summary(card["series"]),
        "accrual_series": _series_summary(card["accrual_series"]),
        "insurance_series": _series_summary(card["insurance_series"]),
    }
    expected = {
        "salary": {
            "value": 90.0,
            "reason": None,
            "debit_balance": 0.0,
            "debit_turnover": 10.0,
            "credit_turnover": None,
            "delta": None,
            "insurance": {
                "credit_balance": 20.0,
                "debit_balance": 0.0,
                "debit_turnover": None,
                "credit_turnover": 20.0,
                "turnover_ratio": None,
            },
        },
        "salary_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 90.0}],
            "null_count": 30,
        },
        "accrual_series": {"count": 31, "known": [], "null_count": 31},
        "insurance_series": {
            "count": 31,
            "known": [{"label": "31.01.2026", "value": 20.0}],
            "null_count": 30,
        },
    }
    _record(
        test_id="SAL-10-unknown-turnover",
        title_ru="Неизвестный оборот не превращается в нулевое начисление",
        input_ru=(
            "Сальдо 70 известно: СКк=90. Но ОбК 70 отсутствует (None); "
            "для 69 известны ОбК=20, а ОбД отсутствует."
        ),
        actual=actual,
        expected_actual=expected,
        expected_audit_ru=(
            "Известный остаток не делает неизвестный кредитовый оборот "
            "нулевым начислением; неизвестная сторона 69 также не равна "
            "нулевой уплате."
        ),
        assessment_ru=(
            "ПРОЙДЕНО: *_known=false приводит к credit_turnover 70=null "
            "и null accrual_series, ratio не вычисляется; известное "
            "сальдо 70=90 при этом сохраняется."
        ),
        scope_ru=SCOPE + " None означает неизвестный оборот, а не подтверждённый ноль.",
        source_refs={
            "R1_backend": [
                "osv-dashboard/key_indicators.py:123-149",
                "osv-dashboard/key_indicators.py:366-369",
                "osv-dashboard/key_indicators.py:920-931",
                "osv-dashboard/key_indicators.py:1194-1203",
            ],
            "R2_JS": [
                "osv-dashboard/static/key_indicators.js:2723-2739",
                "osv-dashboard/static/key_indicators.js:2771-2775",
            ],
            "R3_service": ["osv-dashboard/key_indicators_service.py:151-160"],
            "R4_parser": ["osv-dashboard/osvparser.py:280-289"],
        },
        assertion_ru=(
            "Salary, insurance, balance series and null accrual series "
            "совпадают с expected_actual."
        ),
    )


def main() -> int:
    _probe_complete_salary_and_insurance()
    _probe_missing_70_not_zero()
    _probe_confirmed_zero()
    _probe_stale_exact_date()
    _probe_debit_side()
    _probe_turnover_is_not_payment_or_fot()
    _probe_parser_analytic_balance_contract()
    _probe_delta_exact_salary_balances()
    _probe_timeline_gap()
    _probe_unknown_turnover_not_zero()

    source_files = {
        "R1_backend": {
            "path": str(BACKEND.relative_to(ROOT)),
            "sha256": _sha256(BACKEND),
            "role": "pure Python salary balance, turnover, timeline and delta calculation",
        },
        "R2_JS": {
            "path": str(FRONTEND.relative_to(ROOT)),
            "sha256": _sha256(FRONTEND),
            "role": "static salary modal caveat, analytics and timeline contract inspection",
        },
        "R3_service": {
            "path": str(SERVICE.relative_to(ROOT)),
            "sha256": _sha256(SERVICE),
            "role": "source reference for assembled reports; not imported",
        },
        "R4_parser": {
            "path": str(PARSER.relative_to(ROOT)),
            "sha256": _sha256(PARSER),
            "role": "source reference for daily turnovers versus report balances; not imported",
        },
        "runner": {
            "path": str(Path(__file__).relative_to(ROOT)),
            "sha256": _sha256(Path(__file__)),
            "role": "bounded isolated salary probe runner",
        },
    }
    findings = sum(case["status"] == "FINDING" for case in CASES)
    report = {
        "report_id": "salary-audit-probes",
        "purpose_ru": (
            "10 isolated probes фактической pure key_indicators логики "
            "зарплатной карточки по счетам 70/69: точные нули, отсутствие, "
            "stale-date, стороны сальдо, обороты, parser analytics, delta "
            "и timeline."
        ),
        "scope_ru": SCOPE,
        "status_semantics_ru": (
            "implementation_status=PASS означает, что текущий pure backend "
            "результат совпал с expected_actual и assert прошёл. "
            "audit_assessment=FINDING означало бы наблюдаемое расхождение "
            "с audit-safe ожиданием, а не падение runner. Ограничения "
            "зарплатной методики вынесены отдельно и не подменяются желаемыми "
            "значениями."
        ),
        "execution": {
            "runner": "reports/salary_audit_probes.py",
            "command": "PYTHONDONTWRITEBYTECODE=1 python3 -B reports/salary_audit_probes.py",
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
                "R2 salary modal читался статически для caveat/analytics "
                "контракта; JavaScript, DOM и browser не запускались."
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
                "предприятия, сотрудники, ведомости, БД, service layer и сеть "
                "не читались."
            ),
            (
                "Кредитовое сальдо 70 — расчётный бухгалтерский остаток на "
                "точную дату. Оно не определяет наступление срока выплаты, "
                "просрочку или сумму к фактической выплате."
            ),
            (
                "Кредитовый оборот 70 оставлен как raw turnover. Он не "
                "подтверждает ФОТ, начисление конкретным работникам или "
                "платёж; дебетовый оборот может включать выплаты, НДФЛ, "
                "удержания и корректировки."
            ),
            (
                "ОСВ 69/70 не даёт payroll/payment классификацию: отношение "
                "credit turnover 69 к credit turnover 70 — справочное "
                "арифметическое отношение, не ставка взносов, не доля "
                "уплаченного и не compliance status."
            ),
            (
                "Аналитический label не доказывает категорию персонала, вид "
                "начисления, численность, FTE, тариф или срок обязательства; "
                "средняя зарплата, просрочка и фактические выплаты намеренно "
                "остаются недоступны."
            ),
            (
                "Exact-date и timeline probes сохраняют null для отсутствия "
                "сальдо/месяца; null не означает подтверждённый ноль. "
                "Synthetic balance rows не заменяют политику полноты отчётности."
            ),
            (
                "R2 JS проверен статически без DOM/browser. R3 service и R4 "
                "parser использованы только как source references; они не "
                "импортировались и источники/БД не запускались."
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