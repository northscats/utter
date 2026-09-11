#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Изолированные synthetic probes расчёта прибыли.

Runner imports only the pure ``key_indicators`` module.  Inputs are small
in-memory ОСВ snapshots.  The one frontend expression used for the
decomposition table is extracted from the current source and executed in a
Node VM with a local ``rub`` stub.  Flask, the application, service layer,
database drivers, environment variables and network are deliberately absent.
"""

from __future__ import annotations

import hashlib
import json
import re
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
    revenue_analytic_key,
)


DAY = "2026-01-31"
PERIOD = "за январь 2026 г."
UNSET = object()
CASES: list[dict[str, Any]] = []
JS_EXTRACTIONS: dict[str, dict[str, Any]] = {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _day(
    dt: str = DAY,
    *,
    code: str = "",
    analytic: str = "",
    path2: str = "",
    obd: Any = 0,
    obk: Any = 0,
    **balance: Any,
) -> dict[str, Any]:
    row = {
        "date": dt,
        "month": dt[:7],
        "path1": analytic,
        "path2": path2,
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


def _node_eval_with_rub(
    expression: str, values: list[list[Any]]
) -> list[Any]:
    """Run the actual shown-expression with a deterministic local rub stub."""
    payload = {"expr": expression, "values": values}
    program = r"""
const fs = require('node:fs'), vm = require('node:vm');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = vm.runInNewContext(
  '(function(value,sign,rub){return (' + p.expr + ');})'
);
const rub = value => 'stub-rub(' + value + ')';
const result = p.values.map(v => fn(v[0], v[1], rub));
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


def _profit_card(result: dict[str, Any]) -> dict[str, Any]:
    card = _cards(result)["profit"]
    return {
        "gross_profit": card["gross_profit"],
        "profit_from_sales": card["profit_from_sales"],
        "other_income": card["other_income"],
        "other_expense": card["other_expense"],
        "other_result": card["other_result"],
        "profit_before_tax": card["profit_before_tax"],
        "value": card["value"],
        "title": card["title"],
        "status": card["status"],
        "status_label": card["status_label"],
    }


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
    # expected_actual is the observed current implementation result, not a
    # desired result.  Findings are therefore asserted implementation facts.
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


def _base_90(
    *,
    dt: str = DAY,
    revenue: Any = 100,
    vat: Any = 20,
    cogs: Any = 60,
    selling: Any = UNSET,
    admin: Any = UNSET,
) -> list[dict[str, Any]]:
    rows = [
        _day(dt, code="90.01", obk=revenue),
        _day(dt, code="90.03", obd=vat),
        _day(dt, code="90.02", obd=cogs),
    ]
    if selling is not UNSET:
        rows.append(_day(dt, code="90.07", obd=selling))
    if admin is not UNSET:
        rows.append(_day(dt, code="90.08", obd=admin))
    return rows


def _probe_full_decomposition() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=_base_90(selling=5, admin=3),
        ),
        "91": _account(
            "91",
            daily=[
                _day(code="91.01", obk=10),
                _day(code="91.02", obd=4),
            ],
        ),
    }
    _, result = _build(parsed)
    card = _profit_card(result)
    actual = {
        key: card[key]
        for key in (
            "gross_profit",
            "profit_from_sales",
            "other_result",
            "profit_before_tax",
            "value",
        )
    }
    expected_actual = {
        "gross_profit": 20.0,
        "profit_from_sales": 12.0,
        "other_result": 6.0,
        "profit_before_tax": 18.0,
        "value": 12.0,
    }
    _record(
        test_id="PROFIT-01-full-level-decomposition",
        title_ru="Полная цепочка: валовая, от продаж, прочий результат и до налога",
        input_ru=(
            "90.01 Кт=100, 90.03 Дт=20, 90.02 Дт=60, 90.07 Дт=5, "
            "90.08 Дт=3; 91.01 Кт=10 и 91.02 Дт=4."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: чистые pure helpers разделяют gross=20, sales=12, "
            "other=6 и pretax=18; основной value карточки равен прибыли от продаж."
        ),
        scope_ru=(
            "Один synthetic период; налог, проценты и чистая прибыль намеренно "
            "не восстанавливаются."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:708-776",
                "osv-dashboard/key_indicators.py:995-1012",
            ],
            "observation": "20−5−3=12; 10−4=6; 12+6=18.",
        },
        assertion_ru="Пять уровней фактического результата равны expected_actual.",
    )


def _probe_missing_expenses_vs_zero() -> None:
    missing = {"90": _account("90", daily=_base_90())}
    explicit_zero = {
        "90": _account(
            "90",
            daily=_base_90(selling=0, admin=0),
        )
    }
    _, missing_result = _build(missing)
    _, zero_result = _build(explicit_zero)
    missing_card = _cards(missing_result)["profit"]
    zero_card = _cards(zero_result)["profit"]
    actual = {
        "missing": {
            "gross_profit": missing_card["gross_profit"],
            "selling_expense": missing_card["selling_expense"],
            "admin_expense": missing_card["admin_expense"],
            "profit_from_sales": missing_card["profit_from_sales"],
            "value": missing_card["value"],
            "title": missing_card["title"],
        },
        "explicit_zero": {
            "gross_profit": zero_card["gross_profit"],
            "selling_expense": zero_card["selling_expense"],
            "admin_expense": zero_card["admin_expense"],
            "profit_from_sales": zero_card["profit_from_sales"],
            "value": zero_card["value"],
            "title": zero_card["title"],
        },
    }
    expected_actual = {
        "missing": {
            "gross_profit": 20.0,
            "selling_expense": None,
            "admin_expense": None,
            "profit_from_sales": None,
            "value": 20.0,
            "title": "Валовая прибыль (отдельные расходы недоступны)",
        },
        "explicit_zero": {
            "gross_profit": 20.0,
            "selling_expense": 0.0,
            "admin_expense": 0.0,
            "profit_from_sales": 20.0,
            "value": 20.0,
            "title": "Прибыль от продаж (до отдельных расходов)",
        },
    }
    _record(
        test_id="PROFIT-02-missing-expenses-vs-explicit-zero-card-fallback",
        title_ru="Отсутствующие 90.07/90.08 не равны явному нулю",
        input_ru=(
            "Две изолированные ОСВ с одинаковыми 90.01=100, 90.03=20 и "
            "90.02=60: в первой нет 90.07/90.08, во второй оба оборота равны 0."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: при пропущенных 90.07/90.08 карточка fallback показывает "
            "gross_profit=20 и маркируется валовой прибылью; только явные нули "
            "разрешают profit_from_sales=20. Значение совпадает, база различна."
        ),
        scope_ru=(
            "Synthetic наличие/отсутствие строк; не вывод о том, что отсутствие "
            "в реальной ОСВ означает ноль."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:747-776",
                "osv-dashboard/key_indicators.py:1017-1024",
            ],
            "observation": "missing -> None/валовая; explicit zero -> 0/от продаж.",
        },
        assertion_ru="Обе ветви карточки и их русские заголовки равны expected_actual.",
        finding=True,
    )


def _probe_9109_excluded() -> None:
    parsed = {
        "90": _account("90", daily=_base_90(selling=5, admin=3)),
        "91": _account(
            "91",
            daily=[
                _day(code="91.01", obk=10),
                _day(code="91.02", obd=4),
                _day(code="91.09", obd=999),
            ],
        ),
    }
    _, result = _build(parsed)
    card = _cards(result)["profit"]
    actual = {
        "other_income": card["other_income"],
        "other_expense": card["other_expense"],
        "other_result": card["other_result"],
        "profit_before_tax": card["profit_before_tax"],
        "other_analytics": card["other_analytics"],
    }
    expected_actual = {
        "other_income": 10.0,
        "other_expense": 4.0,
        "other_result": 6.0,
        "profit_before_tax": 18.0,
        "other_analytics": [
            {
                "analytic_key": revenue_analytic_key([]),
                "label": "Без аналитики",
                "income": 10.0,
                "expense": 4.0,
            }
        ],
    }
    _record(
        test_id="PROFIT-03-9109-is-not-other-result",
        title_ru="Закрывающий 91.09 не попадает в прочий результат",
        input_ru=(
            "К 91.01=10 и 91.02=4 добавлена отдельная строка 91.09 Дт=999; "
            "аналитика у всех строк отсутствует."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: расчёт использует только точные 91.01 и 91.02; "
            "91.09 не увеличивает расход и не меняет результат 6."
        ),
        scope_ru="Проверка точного префикса 91.01/91.02 на synthetic счёте 91.",
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:765-796",
                "osv-dashboard/key_indicators.py:1131-1142",
            ],
            "observation": "999 по 91.09 отсутствует в other_expense и pretax.",
        },
        assertion_ru="Прочие суммы, pretax и аналитика равны expected_actual.",
    )


def _probe_negative_loss_and_js_shown() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=_base_90(revenue=30, vat=5, cogs=40, selling=0, admin=0),
        )
    }
    _, result = _build(parsed)
    card = _cards(result)["profit"]
    extraction = JS_EXTRACTIONS.setdefault(
        "decomposition_shown",
        _extract_const_after("shown", "const decomposition = ["),
    )
    shown = _node_eval_with_rub(
        extraction["expr"],
        [
            [-15, 1],  # negative result row: sign is +1 in the source table
            [-10, -1],  # negative/storno cost row: source sign is -1
            [12, 1],
            [None, -1],
        ],
    )
    actual = {
        "backend": {
            "gross_profit": card["gross_profit"],
            "profit_from_sales": card["profit_from_sales"],
            "value": card["value"],
            "status": card["status"],
            "status_label": card["status_label"],
        },
        "expression_lines": extraction["lines"],
        "shown_with_stub_rub": shown,
    }
    expected_actual = {
        "backend": {
            "gross_profit": -15.0,
            "profit_from_sales": -15.0,
            "value": -15.0,
            "status": "bad",
            "status_label": "Убыток от продаж",
        },
        "expression_lines": extraction["lines"],
        "shown_with_stub_rub": [
            "stub-rub(15)",
            "− stub-rub(10)",
            "+ stub-rub(12)",
            "Нужны данные",
        ],
    }
    _record(
        test_id="PROFIT-04-negative-loss-storno-js-decomposition",
        title_ru="Убыток и сторно: backend знак есть, JS decomposition берёт abs",
        input_ru=(
            "90.01 Кт=30, 90.03 Дт=5, 90.02 Дт=40 и явные нулевые 90.07/90.08 "
            "дают отрицательную прибыль; Node исполняет текущий shown-expression "
            "для убытка −15 и отрицательного сторно −10 со stub rub."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: backend честно возвращает −15 и bad/«Убыток от продаж», "
            "но extracted JS expression форматирует value=-15 с sign=1 как "
            "положительный stub-rub(15): знак убытка теряется. Для строки "
            "расхода sign=-1 всегда даёт визуальный минус перед abs."
        ),
        scope_ru=(
            "Node VM и pure backend only; stub rub заменяет форматирование млн ₽ "
            "и не является browser render test."
        ),
        evidence={
            "python": ["osv-dashboard/key_indicators.py:1092-1099"],
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "expression": "shown",
                "lines": extraction["lines"],
                "fragment": extraction["fragment"],
            },
            "execution": "Node vm with local rub(value) => stub-rub(value).",
        },
        assertion_ru="Backend loss and four actual Node strings equal expected_actual.",
        finding=True,
    )


def _probe_gross_revenue_decomposition_label() -> None:
    parsed = {
        "90": _account(
            "90",
            daily=_base_90(vat=UNSET, selling=0, admin=0),
        )
    }
    # _base_90 omits only explicit expenses, so remove the VAT row here.
    parsed["90"]["daily"] = [
        row for row in parsed["90"]["daily"] if row["path3"] != "90.03"
    ]
    _, result = _build(parsed)
    revenue = _cards(result)["revenue"]
    text = JS_SOURCE.read_text(encoding="utf-8")
    start = text.index("const decomposition = [")
    end = text.index("].map", start)
    fragment = text[start:end]
    first_row = re.search(
        r"\['Нетто-выручка',\s*'([^']+)',\s*revenue\.value", fragment
    )
    actual = {
        "revenue_value": revenue["value"],
        "revenue_title": revenue["title"],
        "revenue_formula": revenue["formula"],
        "decomposition_first_label": first_row.group(0).split("'")[1]
        if first_row
        else None,
        "decomposition_first_formula": first_row.group(1) if first_row else None,
        "decomposition_uses_revenue_value": "revenue.value" in fragment,
        "decomposition_has_gross_guard": "брутто" in fragment,
    }
    expected_actual = {
        "revenue_value": 100.0,
        "revenue_title": "Выручка брутто (НДС недоступен)",
        "revenue_formula": "ОбК(90.01*), брутто",
        "decomposition_first_label": "Нетто-выручка",
        "decomposition_first_formula": "ОбК(90.01) − ОбД(90.03)",
        "decomposition_uses_revenue_value": True,
        "decomposition_has_gross_guard": False,
    }
    _record(
        test_id="PROFIT-05-gross-revenue-mislabeled-decomposition",
        title_ru="Брутто-выручка получает подпись нетто в decomposition",
        input_ru=(
            "Есть 90.01 Кт=100, 90.02 Дт=60 и явные нули 90.07/90.08; "
            "90.03 отсутствует. Дополнительно проверен исходник renderProfitModal."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: backend правильно маркирует revenue как брутто при "
            "отсутствии 90.03, но source decomposition всегда подписывает "
            "revenue.value как «Нетто-выручка» и не содержит gross guard."
        ),
        scope_ru=(
            "Source inspection функции renderProfitModal; DOM не запускался. "
            "Проверяется label/formula contract, не внешний текст браузера."
        ),
        evidence={
            "python": ["osv-dashboard/key_indicators.py:955-962, 1082-1083"],
            "frontend": {
                "source": "osv-dashboard/static/key_indicators.js",
                "function": "renderProfitModal(card)",
                "fragment": fragment,
            },
        },
        assertion_ru="Backend gross fields and static decomposition flags equal expected_actual.",
        finding=True,
    )


def _probe_other_analytics_one_sided_unknown() -> None:
    parsed = {
        "90": _account("90", daily=_base_90(selling=0, admin=0)),
        "91": _account(
            "91",
            daily=[
                _day(code="91.01", analytic="Субсидия", obk=10),
                _day(code="91.02", analytic="Субсидия", obd=None),
                _day(code="91.01", analytic="Курсовая разница", obk=None),
                _day(code="91.02", analytic="Курсовая разница", obd=4),
            ],
        ),
    }
    source, result = _build(parsed)
    card = _cards(result)["profit"]
    actual = {
        "other_income": card["other_income"],
        "other_expense": card["other_expense"],
        "other_result": card["other_result"],
        "profit_before_tax": card["profit_before_tax"],
        "analytics": [
            {
                "label": item["label"],
                "income": item["income"],
                "expense": item["expense"],
            }
            for item in card["other_analytics"]
        ],
        "normalized_unknown_flags": [
            {
                "code": row["path3"],
                "analytic": row["path1"],
                "known": row[f"{'obk' if row['path3'] == '91.01' else 'obd'}_known"],
            }
            for row in source["accounts"]["91"]["daily"]
        ],
    }
    expected_actual = {
        "other_income": None,
        "other_expense": None,
        "other_result": None,
        "profit_before_tax": None,
        "analytics": [
            {
                "label": "«Курсовая разница»",
                "income": None,
                "expense": 4.0,
            },
            {
                "label": "«Субсидия»",
                "income": 10.0,
                "expense": None,
            },
        ],
        "normalized_unknown_flags": [
            {"code": "91.01", "analytic": "Курсовая разница", "known": False},
            {"code": "91.02", "analytic": "Курсовая разница", "known": True},
            {"code": "91.01", "analytic": "Субсидия", "known": True},
            {"code": "91.02", "analytic": "Субсидия", "known": False},
        ],
    }
    _record(
        test_id="PROFIT-06-other-analytics-one-sided-unknown",
        title_ru="Аналитики 91 сохраняют односторонность и неизвестную сумму",
        input_ru=(
            "Для «Субсидия» 91.01=10, а 91.02 неизвестен; для «Курсовая "
            "разница» 91.01 неизвестен, а 91.02=4. None — неизвестный оборот, "
            "не подтверждённый ноль."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО как текущий контракт: grouping сохраняет две аналитики "
            "с одной известной стороной, а _known_sum не превращает unknown "
            "в ноль; общий other_result и pretax остаются null."
        ),
        scope_ru=(
            "Pure normalize/build synthetic account 91; не классификация статей "
            "и не утверждение экономического смысла аналитик."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:133-138 (known flags)",
                "osv-dashboard/key_indicators.py:778-797 (91 grouping)",
            ],
            "observation": "Обе строки analytics имеют одну None сторону.",
        },
        assertion_ru="Общие суммы, аналитики и normalized known flags равны expected_actual.",
    )


def _probe_timeline_gross_sales_gap_negative() -> None:
    period = "за 1 квартал 2026 г."
    daily = []
    daily.extend(
        _base_90(
            dt="2026-01-31",
            revenue=100,
            vat=20,
            cogs=60,
            selling=5,
            admin=3,
        )
    )
    daily.extend(
        _base_90(
            dt="2026-03-31",
            revenue=30,
            vat=5,
            cogs=40,
            selling=0,
            admin=0,
        )
    )
    parsed = {"90": _account("90", daily=daily, period=period)}
    _, result = _build(
        parsed,
        period="quarter",
        selected_date="2026-03-31",
    )
    card = _cards(result)["profit"]
    actual = {
        "value": card["value"],
        "status": card["status"],
        "gross_series": card["gross_series"],
        "sales_profit_series": card["sales_profit_series"],
        "display_series": card["series"],
        "period_warnings": result["period"]["warnings"],
    }
    expected_actual = {
        "value": -3.0,
        "status": "bad",
        "gross_series": [
            {"label": "Янв 2026", "value": 20.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": -15.0},
        ],
        "sales_profit_series": [
            {"label": "Янв 2026", "value": 12.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": -15.0},
        ],
        "display_series": [
            {"label": "Янв 2026", "value": 12.0},
            {"label": "Фев 2026", "value": None},
            {"label": "Мар 2026", "value": -15.0},
        ],
        "period_warnings": [],
    }
    _record(
        test_id="PROFIT-07-timeline-gross-sales-gap-loss",
        title_ru="Timeline разделяет валовую и продажную прибыль, gap остаётся null",
        input_ru=(
            "В квартале январь: gross=20/sales=12; февральских строк нет; "
            "март: gross=sales=−15 при явных нулевых 90.07/90.08."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "ПРОЙДЕНО: gross_series и sales_profit_series не смешиваются, "
            "февральский gap не интерполируется в 0, отрицательный март "
            "сохраняет знак, а карточка квартала показывает −3."
        ),
        scope_ru=(
            "Synthetic quarterly source with declared full coverage; проверяется "
            "только расчёт series, не календарь фактических загрузок."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:1104-1156",
                "osv-dashboard/key_indicators.py:1143-1148",
            ],
            "observation": "20/12 и −15/−15; отсутствующий февраль — null.",
        },
        assertion_ru="Карточка, три серии и warnings равны expected_actual.",
    )


def _probe_delta_expense_basis_change() -> None:
    period = "за 1 квартал 2026 г."
    daily = []
    daily.extend(
        _base_90(
            dt="2026-01-31",
            revenue=100,
            vat=20,
            cogs=60,
            selling=5,
            admin=3,
        )
    )
    # February omits 90.07/90.08: current card falls back to gross profit.
    daily.extend(
        _base_90(
            dt="2026-02-28",
            revenue=120,
            vat=20,
            cogs=60,
        )
    )
    parsed = {"90": _account("90", daily=daily, period=period)}
    _, current_result = _build(
        parsed,
        period="month",
        selected_date="2026-02-28",
    )
    _, previous_result = _build(
        parsed,
        period="month",
        selected_date="2026-01-31",
    )
    current = _cards(current_result)["profit"]
    previous = _cards(previous_result)["profit"]
    current_display = current["value"]
    previous_display = previous["value"]
    actual = {
        "current": {
            "gross_profit": current["gross_profit"],
            "profit_from_sales": current["profit_from_sales"],
            "value": current_display,
            "delta": current["delta"],
            "title": current["title"],
        },
        "previous_display_value": previous_display,
        "display_value_difference": round(current_display - previous_display, 2),
    }
    expected_actual = {
        "current": {
            "gross_profit": 40.0,
            "profit_from_sales": None,
            "value": 40.0,
            "delta": {
                "value": 20.0,
                "unit": "RUB",
                "label": "к предыдущему сопоставимому периоду",
            },
            "title": "Валовая прибыль (отдельные расходы недоступны)",
        },
        "previous_display_value": 12.0,
        "display_value_difference": 28.0,
    }
    _record(
        test_id="PROFIT-08-delta-expense-basis-mismatch",
        title_ru="Delta не помечает смену базы gross ↔ sales",
        input_ru=(
            "Январь имеет 90.07=5 и 90.08=3: карточка sales=12; февраль "
            "не имеет этих строк: карточка fallback gross=40. Оба периода "
            "имеют полный net VAT basis."
        ),
        actual=actual,
        expected_actual=expected_actual,
        assessment_ru=(
            "FINDING: delta=20 сравнивает текущий gross=40 с предыдущим "
            "gross_profit=20, хотя предыдущая карточка показывала sales=12; "
            "визуальная разница карточек равна 28. Guard проверяет VAT basis, "
            "но не смену доступности 90.07/90.08."
        ),
        scope_ru=(
            "Synthetic consecutive months; это потенциальное несоответствие "
            "basis delta, а не утверждение о фактической полноте ОСВ."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:1208-1238 (delta)",
                "osv-dashboard/key_indicators.py:760-776 (display basis)",
            ],
            "observation": "delta 20 != current display 40 − previous display 12 = 28.",
        },
        assertion_ru="Current card, delta, previous display and arithmetic difference equal expected_actual.",
        finding=True,
    )


def _node_version() -> str:
    completed = subprocess.run(
        ["node", "--version"], text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def main() -> int:
    _probe_full_decomposition()
    _probe_missing_expenses_vs_zero()
    _probe_9109_excluded()
    _probe_negative_loss_and_js_shown()
    _probe_gross_revenue_decomposition_label()
    _probe_other_analytics_one_sided_unknown()
    _probe_timeline_gross_sales_gap_negative()
    _probe_delta_expense_basis_change()

    source_files = {
        str(path.relative_to(ROOT)): {
            "sha256": _sha256(path),
            "role": role,
        }
        for path, role in (
            (PY_SOURCE, "pure Python normalize/build calculation module"),
            (JS_SOURCE, "frontend decomposition expression source inspected/executed"),
            (RUNNER_SOURCE, "this bounded profit probe runner"),
        )
    }
    findings = sum(case["status"] == "FINDING" for case in CASES)
    report = {
        "report_id": "profit-audit-probes",
        "purpose_ru": (
            "Изолированные synthetic probes текущей методики прибыли: "
            "уровни результата 90/91, неизвестные обороты, отрицательные "
            "значения, decomposition, timeline и delta basis."
        ),
        "scope_ru": (
            "Только build_indicator_source (normalize) и build_key_indicators "
            "из pure key_indicators; статически извлечённое выражение "
            "renderProfitModal исполняется отдельно в Node VM."
        ),
        "status_semantics_ru": (
            "implementation_status=PASS означает, что фактический текущий "
            "результат совпал с expected_actual и assert прошёл. "
            "audit_assessment=FINDING означает наблюдаемое расхождение с "
            "audit-safe ожиданием, а не падение runner."
        ),
        "execution": {
            "runner": "reports/profit_audit_probes.py",
            "command": "PYTHONDONTWRITEBYTECODE=1 python3 reports/profit_audit_probes.py",
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
        "observations_ru": [
            "Полная цепочка pure расчёта даёт gross=20, sales=12, other=6, pretax=18.",
            "Пропуск 90.07/90.08 оставляет карточку на gross fallback; явные нули дают sales basis.",
            "91.09 исключён, но gross revenue без 90.03 всё ещё получает подпись «Нетто-выручка» в decomposition.",
            "Односторонние/unknown аналитики 91 не превращаются в нули; gap timeline остаётся null, отрицательные значения сохраняются.",
            "JS decomposition теряет знак отрицательного результата при sign=1; delta при смене gross/sales basis может отличаться от видимой разницы карточек.",
        ],
        "limitations_ru": [
            (
                "Все входы synthetic source snapshots в памяти; реальные XLS, "
                "предприятия, БД и service layer не читались."
            ),
            (
                "Проверены только текущие pure helpers key_indicators и один "
                "извлечённый JS expression; Flask, DOM, browser и UI не запускались."
            ),
            (
                "Отсутствующий оборот фиксируется через *_known=false и null "
                "результата, а не интерпретируется как подтверждённый ноль."
            ),
            (
                "91.09 probe проверяет текущий exact-prefix contract; он не "
                "доказывает корректность закрывающих проводок в реальном учёте."
            ),
            (
                "Delta finding synthetic: он показывает смену отображаемой "
                "базы при пропуске 90.07/90.08, но не оценивает полноту загрузки."
            ),
        ],
        "cases": CASES,
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