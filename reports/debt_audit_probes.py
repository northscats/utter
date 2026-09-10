#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated, read-only AR/accounting probes.

This runner intentionally imports only the pure ``key_indicators`` module.  It
does not import Flask, the application, a service, a database driver, or
connect to a database.  The fixtures use the source snapshot shape accepted by
``build_indicator_source`` and every assertion is about an observed result of
the current implementation.

The JSON report is generated next to this file.  This is a probe/report, not a
change to production calculations.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
OSV_DIR = ROOT / "osv-dashboard"
KEY_INDICATORS_PATH = OSV_DIR / "key_indicators.py"
FRONTEND_PATH = OSV_DIR / "static" / "key_indicators.js"
OUTPUT_PATH = Path(__file__).with_suffix(".json")

# key_indicators.py is a pure module and is deliberately imported without
# importing app.py, key_indicators_service.py, Flask, or database.py.
sys.path.insert(0, str(OSV_DIR))
from key_indicators import (  # noqa: E402
    _balance_provenance,
    _sub_balance,
    _sub_balance_analytics,
    _sub_opening_balance,
    build_indicator_source,
    build_key_indicators,
)


_UNSET = object()
RECORDS: list[dict[str, Any]] = []


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
    dt: str,
    *,
    code: str = "",
    analytic: str = "",
    obd: Any = 0,
    obk: Any = 0,
    **balance: Any,
) -> dict[str, Any]:
    """Build one valid parsed-data daily row.

    ``path3`` is the account/subaccount code and ``path1`` is the first
    analytic coordinate, matching the helpers in test_key_indicators.py.
    Balance sides are optional because real source snapshots can carry an
    unknown side; this is used explicitly by the missing-side probe.
    """

    row: dict[str, Any] = {
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
    period: str = "за январь 2026 г.",
    top: dict[str, Any] | object = _UNSET,
    source_file: str = "",
) -> dict[str, Any]:
    """Build the parsed-data account shape used by build_indicator_source."""

    result: dict[str, Any] = {
        "acc": account,
        "daily": list(daily),
        "rows": list(rows),
        "period": period,
        "warnings": [],
        "check_ok": True,
    }
    if top is not _UNSET:
        result["top"] = top
    if source_file:
        result["source_file"] = source_file
    return result


def _cards(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {card["id"]: card for card in result["cards"]}


def _build(
    parsed: dict[str, Any],
    *,
    period: str = "month",
    selected_date: str = "2026-01-31",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a source snapshot and call the real indicator calculation."""

    source = build_indicator_source(parsed)
    result = build_key_indicators(source, period=period, date=selected_date)
    return source, result


def _record(
    *,
    test_id: str,
    input_data: dict[str, Any],
    actual: dict[str, Any],
    expected: dict[str, Any],
    interpretation: str,
    evidence: dict[str, Any],
    assertion: str,
    verify: Callable[[], None],
    finding: bool = False,
) -> None:
    """Run and preserve a probe assertion.

    For a finding, ``expected`` is the audit-safe/methodological expectation
    while ``actual`` records the implementation behavior.  ``verify`` still
    asserts the exact observed behavior, so a finding cannot be silently
    produced by a malformed fixture or a failed probe.
    """

    verify()
    RECORDS.append(
        {
            "test_id": test_id,
            "input": input_data,
            "actual": actual,
            "expected": expected,
            "interpretation": interpretation,
            "summary_ru": (
                f"Реализация: ПРОЙДЕНА; "
                f"аудит: {'НАЙДЕНО' if finding else 'ПРОЙДЕНО'}; {test_id}; "
                f"фактически: {json.dumps(actual, ensure_ascii=False, sort_keys=True)}"
            ),
            "evidence": evidence,
            "reproducibility": {
                "assertion": assertion,
                "assertion_verified": True,
                "runner": "reports/debt_audit_probes.py",
                "command": "python3 reports/debt_audit_probes.py",
                "isolation": [
                    "pure key_indicators import only",
                    "no Flask/app import",
                    "no service import",
                    "no database connection",
                ],
            },
            # The probe assertion itself always passes. FINDING means the
            # observed implementation behavior differs from the audit-safe
            # expectation, not that the runner failed.
            "implementation_status": "PASS",
            "audit_assessment": "FINDING" if finding else "PASS",
            "status": "FINDING" if finding else "PASS",
        }
    )


def _probe_total_and_leaf_dedup() -> None:
    parsed = {
        "62": _account(
            "62",
            daily=[
                _day(
                    "2026-01-31",
                    code="62.01",
                    obd=0,
                    obk=0,
                    skd=100,
                    skk=0,
                ),
                _day(
                    "2026-01-31",
                    code="62.01",
                    analytic="Покупатель А",
                    obd=0,
                    obk=0,
                    skd=100,
                    skk=0,
                ),
            ],
            top=_top(),
        )
    }
    source, result = _build(parsed)
    cards = _cards(result)
    ar = cards["ar"]
    direct_ar = _sub_balance(
        source, "62", "62.01", Date(2026, 1, 31)
    )[0]
    direct_analytics = _sub_balance_analytics(
        source, "62", "62.01", Date(2026, 1, 31)
    )
    actual = {
        "ar_total": ar["value"],
        "ar_analytic_values": [row["value"] for row in ar["analytics"]],
        "direct_ar": direct_ar,
        "direct_ar_analytics": [row["value"] for row in direct_analytics],
    }
    expected = {
        "ar_total": 100,
        "ar_analytic_values": [70],
        "direct_ar": 100,
        "direct_ar_analytics": [70],
    }
    _record(
        test_id="AR-001-root-and-analytic-total-double-count",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "FINDING: a root 62.01 row without analytics and a same-code "
            "62.01 row for Buyer A are both included by _sub_balance, so "
            "the total is 200 while the analytic detail is 100. This is "
            "exactly the total-plus-detail double-count shape requested; "
            "the synthetic fixture is not asserted to be an enterprise "
            "export."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:419-443 (_sub_balance)",
                "osv-dashboard/key_indicators.py:468-500 "
                "(_sub_balance_analytics)",
            ],
            "source_built_by": "build_indicator_source",
            "observation": (
                "Same date: root path3=62.01, analytic='' has skd=100; "
                "Buyer A path3=62.01 has skd=100. The helper sums both "
                "exact-code rows, while detail excludes the root row."
            ),
        },
        assertion=(
            "actual AR total == 200, exactly one analytic detail == 100, "
            "and direct helper agrees"
        ),
        verify=lambda: (
            assert_equal(actual["ar_total"], 200),
            assert_equal(actual["ar_analytic_values"], [100]),
            assert_equal(actual["direct_ar"], 200),
            assert_equal(actual["direct_ar_analytics"], [100]),
        ),
        finding=True,
    )


def _probe_duplicate_balance_lists() -> None:
    observation = _day(
        "2026-01-31",
        code="62.01",
        analytic="Покупатель Д",
        skd=80,
        skk=0,
    )
    account = _account("62", daily=[observation], top=_top())
    # This is a source-builder-shaped snapshot: the same observation is in
    # both lists. It is intentionally not claimed to be the current
    # DailyRow end-to-end shape (the loader's DailyRow observation has empty
    # path fields).
    account["balance_observations"] = [dict(observation)]
    parsed = {"62": account}
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    direct = _sub_balance(source, "62", "62.01", Date(2026, 1, 31))
    actual = {
        "source_daily_count": len(source["accounts"]["62"]["daily"]),
        "source_balance_observation_count": len(
            source["accounts"]["62"]["balance_observations"]
        ),
        "card_value": ar["value"],
        "analytic_value": ar["analytics"][0]["value"],
        "direct_value": direct[0],
    }
    expected = {
        "audit_safe_value": 80,
        "implementation_value": 160,
        "implementation_rule": (
            "(balance_observations + daily) are both traversed by "
            "_sub_balance"
        ),
    }
    _record(
        test_id="AR-002-duplicate-balance-lists-helper-risk",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "FINDING / CONDITIONAL: with the same per-subaccount "
            "observation in balance_observations and daily, _sub_balance "
            "returns 160 instead of 80. The loader can append an uncovered "
            "DailyRow observation to balance_observations and daily "
            "(key_indicators_service.py:151-160), but that runtime "
            "DailyRow observation has path1/path2/path3 all empty "
            "(lines 151-154). Therefore this is a pure-helper contract "
            "risk conditional on future/upstream per-subaccount observations, "
            "not an established current DailyRow AR incident."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:424-433 "
                "(_sub_balance concatenates observations and daily)",
                "osv-dashboard/key_indicators_service.py:151-160 "
                "(DailyRow observation is put in both lists when uncovered)",
            ],
            "source_built_by": "build_indicator_source",
            "observation": (
                "The source snapshot contains one identical path3=62.01 "
                "observation in each list; the report explicitly limits "
                "the finding to that helper contract."
            ),
        },
        assertion=(
            "source has one item in each list and card/direct/analytic "
            "values are all 160 from one underlying 80 observation"
        ),
        verify=lambda: (
            assert_equal(actual["source_daily_count"], 1),
            assert_equal(actual["source_balance_observation_count"], 1),
            assert_equal(actual["card_value"], 160),
            assert_equal(actual["analytic_value"], 160),
            assert_equal(actual["direct_value"], 160),
        ),
        finding=True,
    )


def _probe_standard_rows_have_no_ar_analytics() -> None:
    report_rows = [
        {
            "lvl": 1,
            "label": "62.01",
            "p0": "62",
            "p1": "62.01",
            "p2": "",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 100,
            "obk": 0,
            "skd": 100,
            "skk": 0,
        },
        {
            "lvl": 2,
            "label": "Покупатель Е",
            "p0": "62",
            "p1": "62.01",
            "p2": "Покупатель Е",
            "p3": "",
            "snd": 0,
            "snk": 0,
            "obd": 100,
            "obk": 0,
            "skd": 100,
            "skk": 0,
        },
    ]
    parsed = {
        "62": _account(
            "62",
            # Parser daily rows carry turnovers only; balances live in rows.
            daily=[_day("2026-01-31", code="62.01", obd=100, obk=0)],
            rows=report_rows,
            period="за январь 2026 г.",
            top=_top(obd=100, skd=100, skk=0),
        )
    }
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    direct_analytics = _sub_balance_analytics(
        source, "62", "62.01", Date(2026, 1, 31)
    )
    actual = {
        "card_value": ar["value"],
        "analytic_rows": ar["analytics"],
        "analytic_row_count": len(ar["analytics"]),
        "direct_analytic_rows": direct_analytics,
        "source_daily_balance_keys": sorted(
            key
            for key in ("skd", "skk")
            if key in source["accounts"]["62"]["daily"][0]
        ),
        "source_report_row_count": len(source["accounts"]["62"]["rows"]),
    }
    expected = {
        "card_value": 100,
        "analytic_row_count": 0,
        "pipeline_contract": (
            "standard parser report rows carry balances, but "
            "_sub_balance_analytics reads explicit observations only"
        ),
    }
    _record(
        test_id="AR-003-standard-source-analytic-detail-gap",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "FINDING / MAIN PIPELINE GAP: the accepted parser-shaped source "
            "has a 62.01 report row and a child analytic report row, each "
            "with skd=100, while its daily row has only obd/obk. The AR "
            "card correctly obtains 100 from the report row, but the "
            "analytic table is empty because _sub_balance_analytics does "
            "not fall back to report rows. This is distinct from the "
            "conditional duplicate-list helper risk."
        ),
        evidence={
            "python": [
                "osv-dashboard/osvparser.py:283-289 "
                "(daily rows carry obd/obk only)",
                "osv-dashboard/osvparser.py:294-298 "
                "(report rows retain skd/skk)",
                "osv-dashboard/key_indicators.py:468-500 "
                "(_sub_balance_analytics has no rows fallback)",
                "osv-dashboard/key_indicators.py:850-852 "
                "(AR card wiring)",
            ],
            "source_built_by": "build_indicator_source",
            "observation": (
                "A standard monthly report-shaped source has two balance "
                "rows but no explicit daily/balance_observations balance."
            ),
        },
        assertion=(
            "card_value == 100, report row count == 2, daily balance keys "
            "are empty, and analytic rows remain []"
        ),
        verify=lambda: (
            assert_equal(actual["card_value"], 100),
            assert_equal(actual["source_report_row_count"], 2),
            assert_equal(actual["source_daily_balance_keys"], []),
            assert_equal(actual["analytic_row_count"], 0),
            assert_equal(actual["direct_analytic_rows"], []),
        ),
        finding=True,
    )


def _probe_ar_netting() -> None:
    parsed = {
        "62": _account(
            "62",
            daily=[
                _day(
                    "2026-01-31",
                    code="62.01",
                    analytic="Покупатель Б",
                    skd=120,
                    skk=200,
                )
            ],
            top=_top(),
        )
    }
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    direct = _sub_balance(source, "62", "62.01", Date(2026, 1, 31))
    actual = {
        "card_value": ar["value"],
        "card_reason": ar["reason"],
        "direct_value": direct[0],
        "direct_reason": direct[1],
        "analytic_value": ar["analytics"][0]["value"],
    }
    expected = {
        "card_value": 0,
        "direct_value": 0,
        "analytic_value": 0,
        "rule": "max(СКд − СКк, 0)",
    }
    _record(
        test_id="AR-004-debit-credit-netting",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "PASS: a debit balance of 120 netted against credit 200 is not "
            "reported as a negative receivable; the debit AR result is zero."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:419-443 (_sub_balance)",
                "osv-dashboard/key_indicators.py:490-499 "
                "(_sub_balance_analytics)",
            ],
            "source_built_by": "build_indicator_source",
            "observed_input_rule": "СКд=120, СКк=200",
        },
        assertion=(
            "card, direct helper, and leaf analytic all return 0 for "
            "СКд=120 and СКк=200"
        ),
        verify=lambda: (
            assert_equal(actual["card_value"], 0),
            assert_equal(actual["direct_value"], 0),
            assert_equal(actual["analytic_value"], 0),
        ),
    )


def _extract_const_expression_after(
    source: str, name: str, after: str
) -> tuple[str, int, int, str]:
    search_start = source.index(after)
    marker = f"const {name} ="
    start = source.index(marker, search_start)
    expression_start = start + len(marker)
    semicolon = source.index(";", expression_start)
    expression = source[expression_start:semicolon].strip()
    start_line = source.count("\n", 0, start) + 1
    end_line = source.count("\n", 0, semicolon) + 1
    fragment = "\n".join(source.splitlines()[start_line - 1 : end_line])
    return expression, start_line, end_line, fragment


def _run_extracted_ar_share(
    current: float, values: list[float]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frontend_source = FRONTEND_PATH.read_text(encoding="utf-8")
    share_expr, share_start, share_end, share_fragment = (
        _extract_const_expression_after(
            frontend_source, "share", "renderArModal(card)"
        )
    )
    node_program = r"""
const fs = require('node:fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const share = Function(
  'current', 'row', `return (${payload.share_expr});`
);
const result = payload.values.map((value, index) => {
  const raw = share(payload.current, {value});
  return {
    index,
    value,
    share: raw,
    display: raw === null ? 'Н/Д' : `${Number(raw).toFixed(1)}%`
  };
});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", node_program],
        input=json.dumps(
            {"share_expr": share_expr, "current": current, "values": values},
            ensure_ascii=False,
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "extracted frontend share probe failed: "
            + completed.stderr.strip()
        )
    return json.loads(completed.stdout), {
        "frontend": str(FRONTEND_PATH.relative_to(ROOT)),
        "expression_extracted_at_runtime": {
            "lines": f"{share_start}-{share_end}",
            "fragment": share_fragment,
        },
        "execution": "node -e with extracted AR share expression; no DOM/app/network",
    }


def _probe_hierarchy_mismatch_share() -> None:
    parsed = {
        "62": _account(
            "62",
            daily=[
                _day(
                    "2026-01-31",
                    code="62.01",
                    skd=100,
                    skk=40,
                ),
                _day(
                    "2026-01-31",
                    code="62.01.1",
                    analytic="Покупатель Ж",
                    skd=100,
                    skk=0,
                ),
                _day(
                    "2026-01-31",
                    code="62.01.2",
                    analytic="Покупатель З",
                    skd=0,
                    skk=40,
                ),
            ],
            top=_top(),
        )
    }
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    values = [row["value"] for row in ar["analytics"]]
    shares, frontend_evidence = _run_extracted_ar_share(ar["value"], values)
    actual = {
        "total_value": ar["value"],
        "analytic_values": values,
        "analytic_labels": [row["label"] for row in ar["analytics"]],
        "frontend_shares": shares,
    }
    expected = {
        "total_value": 60,
        "analytic_values": [100, 0],
        "share_for_100_detail": 166.66666666666666,
        "displayed_share_for_100_detail": "166.7%",
        "safe_interpretation": (
            "hierarchy totals must reconcile before presenting detail shares"
        ),
    }
    _record(
        test_id="AR-005-hierarchy-mismatch-share-over-100",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "FINDING / CONDITIONAL SYNTHETIC HIERARCHY CASE: the exact "
            "parent 62.01 nets to 60, while leaf details net to 100 and 0. "
            "The extracted frontend share expression therefore displays "
            "166.7% for Buyer Ж. This demonstrates hierarchy mismatch "
            "behavior, not an assertion that an enterprise export contains "
            "these inconsistent totals."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:419-443 (_sub_balance)",
                "osv-dashboard/key_indicators.py:468-500 "
                "(_sub_balance_analytics and leaf selection)",
            ],
            "frontend": frontend_evidence,
            "source_built_by": "build_indicator_source",
            "observation": (
                "Synthetic same-date parent net=60; child A net=100; "
                "child B net=0. The helper does not reconcile parent and "
                "children before the UI share expression."
            ),
        },
        assertion=(
            "real helper returns total 60 and details [100,0]; extracted "
            "frontend expression returns raw share 166.666... and display "
            "166.7% for the 100 detail"
        ),
        verify=lambda: (
            assert_equal(actual["total_value"], 60),
            assert_equal(actual["analytic_values"], [100, 0]),
            assert_equal(actual["frontend_shares"][0]["display"], "166.7%"),
            assert_true(actual["frontend_shares"][0]["share"] > 100),
        ),
        finding=True,
    )


def _probe_missing_side_zero_fill() -> None:
    parsed = {
        "62": _account(
            "62",
            daily=[
                _day(
                    "2026-01-31",
                    code="62.01",
                    analytic="Покупатель В",
                    skd=55,
                    # skk intentionally absent: source-side unknown, not an
                    # invented numeric zero.
                )
            ],
            top=_top(),
        )
    }
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    source_row = source["accounts"]["62"]["daily"][0]
    direct = _sub_balance(source, "62", "62.01", Date(2026, 1, 31))
    actual = {
        "source_balance_keys": sorted(
            key for key in ("skd", "skk") if key in source_row
        ),
        "card_value": ar["value"],
        "card_reason": ar["reason"],
        "direct_value": direct[0],
        "direct_reason": direct[1],
    }
    expected = {
        "audit_safe_value": None,
        "audit_safe_reason": "credit closing side is unknown",
        "implementation_behavior": "missing skk is read as 0",
    }
    _record(
        test_id="AR-006-missing-balance-side-zero-fill",
        input_data={"parsed_data": parsed, "selected_date": "2026-01-31"},
        actual=actual,
        expected=expected,
        interpretation=(
            "FINDING: build_indicator_source preserves that skk is absent, "
            "but _sub_balance then uses r.get('skk', 0). A missing credit "
            "side becomes a numeric AR value 55 with no missing-data reason. "
            "That is not evidence that the credit side is truly zero."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:419-443 (_sub_balance)",
                "osv-dashboard/key_indicators.py:430-432 "
                "(missing side default)",
                "osv-dashboard/key_indicators.py:227-233 "
                "(optional balance sides in source builder)",
            ],
            "source_built_by": "build_indicator_source",
            "observed_input_rule": "only skd=55 is supplied; skk is absent",
        },
        assertion=(
            "source row has skd but no skk, while the card and direct helper "
            "both return 55 and no reason"
        ),
        verify=lambda: (
            assert_equal(actual["source_balance_keys"], ["skd"]),
            assert_equal(actual["card_value"], 55),
            assert_equal(actual["direct_value"], 55),
            assert_equal(actual["direct_reason"], None),
        ),
        finding=True,
    )


def _probe_exact_date_missing() -> None:
    parsed = {
        "62": _account(
            "62",
            daily=[
                _day("2026-01-30", code="62.01", skd=15, skk=0),
                _day("2026-01-31", code="62.01"),
            ],
            # The report top is for 31 March, not 31 January. It must not
            # leak into a 31 January exact-date balance.
            period="за 1 квартал 2026 г.",
            top=_top(skd=100, skk=0),
        )
    }
    source, result = _build(
        parsed, period="day", selected_date="2026-01-31"
    )
    ar = _cards(result)["ar"]
    direct = _sub_balance(
        source, "62", "62.01", Date(2026, 1, 31)
    )
    actual = {
        "ar_card_value": ar["value"],
        "ar_card_reason": ar["reason"],
        "direct_value": direct[0],
        "direct_reason": direct[1],
        "available_dates": source["dates"],
    }
    expected = {
        "ar_card_value": None,
        "direct_value": None,
        "rule": "balance is valid only on the exact requested date",
    }
    _record(
        test_id="AR-007-exact-date-missing-no-cross-date-leak",
        input_data={
            "parsed_data": parsed,
            "selected_date": "2026-01-31",
            "period_kind": "day",
        },
        actual=actual,
        expected=expected,
        interpretation=(
            "PASS: a balance observed on 30 January and a report balance "
            "at 31 March do not create a 31 January 62.01 balance. The "
            "missing exact date remains unavailable."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:419-443 (_sub_balance)",
                "osv-dashboard/key_indicators.py:1104-1106 "
                "(exact-date chart rule)",
            ],
            "source_built_by": "build_indicator_source",
            "observed_input_rule": (
                "balance on 2026-01-30; no balance on 2026-01-31; "
                "report boundary on 2026-03-31"
            ),
        },
        assertion=(
            "AR card and direct _sub_balance for 2026-01-31 are both None"
        ),
        verify=lambda: (
            assert_equal(actual["ar_card_value"], None),
            assert_equal(actual["direct_value"], None),
            assert_true("2026-01-31" in actual["available_dates"]),
        ),
    )


def _probe_opening_closing_provenance() -> tuple[int, int]:
    report_row = {
        "lvl": 1,
        "label": "62.01",
        "p0": "62",
        "p1": "62.01",
        "p2": "",
        "p3": "",
        "snd": 40,
        "snk": 0,
        "obd": 20,
        "obk": 0,
        "skd": 60,
        "skk": 0,
    }
    parsed = {
        "62": _account(
            "62",
            rows=[report_row],
            period="за январь 2026 г.",
            top=_top(obd=20, skd=60),
            source_file="osv62-2026-01.xlsx",
        )
    }
    source, result = _build(parsed)
    ar = _cards(result)["ar"]
    endpoint = Date(2026, 1, 31)
    direct_opening = _sub_opening_balance(
        source, "62", "62.01", Date(2026, 1, 1)
    )
    direct_provenance = _balance_provenance(source, "62", endpoint)
    actual = {
        "closing_value": ar["value"],
        "opening_value": ar["opening_value"],
        "direct_opening_value": direct_opening,
        "provenance": direct_provenance,
        "card_provenance_notes": [
            note for note in ar["notes"] if "Источник сальдо" in note
        ],
        "period_bounds": {
            "start": source["accounts"]["62"]["report_start"],
            "end": source["accounts"]["62"]["report_end"],
        },
    }
    expected = {
        "closing_value": 60,
        "opening_value": 40,
        "direct_opening_value": 40,
        "provenance_contains": [
            "за январь 2026 г.",
            "osv62-2026-01.xlsx",
        ],
    }
    _record(
        test_id="AR-008-opening-closing-and-provenance",
        input_data={
            "parsed_data": parsed,
            "selected_date": "2026-01-31",
            "opening_boundary": "2026-01-01",
        },
        actual=actual,
        expected=expected,
        interpretation=(
            "PASS: the exact report row supplies opening debit 40 and "
            "closing debit 60; the AR card also carries the report period "
            "and source-file provenance. Opening and closing are not "
            "reconstructed from an unrelated date."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:446-465 "
                "(_sub_opening_balance)",
                "osv-dashboard/key_indicators.py:618-632 "
                "(_balance_provenance)",
                "osv-dashboard/key_indicators.py:850-852 "
                "(AR card wiring)",
            ],
            "source_built_by": "build_indicator_source",
            "observed_input_rule": (
                "report row: snd=40, skd=60; period and source file are "
                "provided explicitly"
            ),
        },
        assertion=(
            "closing == 60, opening == 40, direct opening == 40, and "
            "provenance contains period plus source filename"
        ),
        verify=lambda: (
            assert_equal(actual["closing_value"], 60),
            assert_equal(actual["opening_value"], 40),
            assert_equal(actual["direct_opening_value"], 40),
            assert_true("за январь 2026 г." in actual["provenance"]),
            assert_true("osv62-2026-01.xlsx" in actual["provenance"]),
            assert_true(actual["card_provenance_notes"]),
        ),
    )
    return 40, 60


def _probe_vat_scope() -> float:
    with_vat = {
        "90": _account(
            "90",
            daily=[
                _day(
                    "2026-01-31",
                    code="90.01",
                    analytic="Услуга",
                    obk=100,
                ),
                _day(
                    "2026-01-31",
                    code="90.03",
                    analytic="Услуга",
                    obd=20,
                ),
                _day(
                    "2026-01-31",
                    code="90.02",
                    analytic="Услуга",
                    obd=60,
                ),
            ],
            top=_top(obd=80, obk=100),
        )
    }
    without_vat = {
        "90": _account(
            "90",
            daily=[
                _day(
                    "2026-01-31",
                    code="90.01",
                    analytic="Услуга",
                    obk=100,
                ),
                _day(
                    "2026-01-31",
                    code="90.02",
                    analytic="Услуга",
                    obd=60,
                ),
            ],
            top=_top(obd=60, obk=100),
        )
    }
    _, net_result = _build(with_vat)
    _, gross_result = _build(without_vat)
    net_card = _cards(net_result)["revenue"]
    gross_card = _cards(gross_result)["revenue"]
    actual = {
        "with_90_03": {
            "value": net_card["value"],
            "formula": net_card["formula"],
            "title": net_card["title"],
            "vat_detail": next(
                item for item in net_card["details"] if item["label"] == "НДС"
            ),
        },
        "without_90_03": {
            "value": gross_card["value"],
            "formula": gross_card["formula"],
            "title": gross_card["title"],
            "vat_detail": next(
                item for item in gross_card["details"] if item["label"] == "НДС"
            ),
        },
    }
    expected = {
        "with_90_03": {
            "value": 80,
            "basis": "ОбК(90.01*) − ОбД(90.03*)",
        },
        "without_90_03": {
            "value": 100,
            "basis": "ОбК(90.01*), брутто",
            "vat_value": None,
        },
    }
    _record(
        test_id="AR-009-vat-scope-for-net-revenue",
        input_data={
            "with_vat": with_vat,
            "without_vat": without_vat,
            "selected_date": "2026-01-31",
        },
        actual=actual,
        expected=expected,
        interpretation=(
            "PASS: only an exact 90.03 debit turnover reduces 90.01 "
            "gross revenue. When 90.03 is absent, the implementation labels "
            "the result gross and does not invent VAT."
        ),
        evidence={
            "python": [
                "osv-dashboard/key_indicators.py:400-417 (_pl_facts)",
                "osv-dashboard/key_indicators.py:950-963 "
                "(revenue card formula and VAT detail)",
            ],
            "source_built_by": "build_indicator_source",
            "observed_input_rule": (
                "90.01 credit=100; with VAT 90.03 debit=20; "
                "without VAT no 90.03 row"
            ),
        },
        assertion=(
            "with 90.03 value == 80 and net formula; without 90.03 value == "
            "100, gross formula, and VAT detail is null"
        ),
        verify=lambda: (
            assert_equal(actual["with_90_03"]["value"], 80),
            assert_equal(
                actual["with_90_03"]["formula"],
                "ОбК(90.01*) − ОбД(90.03*)",
            ),
            assert_equal(actual["without_90_03"]["value"], 100),
            assert_equal(
                actual["without_90_03"]["formula"],
                "ОбК(90.01*), брутто",
            ),
            assert_equal(actual["without_90_03"]["vat_detail"]["value"], None),
        ),
    )
    # The net value is used to connect the source-built accounting probe to
    # the extracted frontend DSO probe below.
    return float(actual["with_90_03"]["value"])


def _extract_const_expression(
    source: str, name: str
) -> tuple[str, int, int, str]:
    marker = f"const {name} ="
    start = source.index(marker)
    expression_start = start + len(marker)
    semicolon = source.index(";", expression_start)
    expression = source[expression_start:semicolon].strip()
    start_line = source.count("\n", 0, start) + 1
    end_line = source.count("\n", 0, semicolon) + 1
    fragment = "\n".join(source.splitlines()[start_line - 1 : end_line])
    return expression, start_line, end_line, fragment


def _run_extracted_frontend_dso(
    scenarios: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute expressions copied from the real frontend source in Node.

    This does not reimplement DSO or VAT semantics in Python.  It extracts the
    current expressions from key_indicators.js and evaluates those expressions
    in a tiny Node context with synthetic values.
    """

    frontend_source = FRONTEND_PATH.read_text(encoding="utf-8")
    revenue_net_expr, revenue_net_start, revenue_net_end, revenue_net_fragment = (
        _extract_const_expression(frontend_source, "revenueIsNet")
    )
    dso_expr, dso_start, dso_end, dso_fragment = _extract_const_expression(
        frontend_source, "dso"
    )
    days_expr, days_start, days_end, days_fragment = _extract_const_expression(
        frontend_source, "days"
    )
    payload = {
        "revenue_net_expr": revenue_net_expr,
        "dso_expr": dso_expr,
        "days_expr": days_expr,
        "scenarios": scenarios,
    }
    node_program = r"""
const fs = require('node:fs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const revenueNet = Function(
  'revenueCard', `return (${payload.revenue_net_expr});`
);
const dso = Function(
  'current', 'opening', 'revenueCard', 'revenueIsNet', 'days',
  `return (${payload.dso_expr});`
);
const days = Function(
  'startDate', 'endDate', `return (${payload.days_expr});`
);

function normalized(value) {
  if (value === null || value === undefined) {
    return {value: null, js_value: String(value), is_finite: null};
  }
  if (Number.isNaN(value)) {
    return {value: null, js_value: 'NaN', is_finite: false};
  }
  if (value === Infinity) {
    return {value: null, js_value: 'Infinity', is_finite: false};
  }
  if (value === -Infinity) {
    return {value: null, js_value: '-Infinity', is_finite: false};
  }
  return {value, js_value: String(value), is_finite: Number.isFinite(value)};
}

const result = payload.scenarios.map(item => {
  const revenueCard = {value: item.revenue, title: item.title};
  const revenueIsNet = revenueNet(revenueCard);
  const startDate = item.start
    ? new Date(`${item.start}T00:00:00`) : null;
  const endDate = item.end
    ? new Date(`${item.end}T00:00:00`) : null;
  const periodDays = days(startDate, endDate);
  const value = dso(
    item.current, item.opening, revenueCard, revenueIsNet, periodDays
  );
  return {
    id: item.id,
    revenue_is_net: Boolean(revenueIsNet),
    days: periodDays,
    result: normalized(value)
  };
});
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", node_program],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "extracted frontend probe failed: " + completed.stderr.strip()
        )
    results = json.loads(completed.stdout)
    evidence = {
        "frontend": str(FRONTEND_PATH.relative_to(ROOT)),
        "expressions_extracted_at_runtime": {
            "revenueIsNet": {
                "lines": f"{revenue_net_start}-{revenue_net_end}",
                "fragment": revenue_net_fragment,
            },
            "dso": {
                "lines": f"{dso_start}-{dso_end}",
                "fragment": dso_fragment,
            },
            "days": {
                "lines": f"{days_start}-{days_end}",
                "fragment": days_fragment,
            },
        },
        "execution": "node -e with extracted expressions; no DOM/app/network",
    }
    return results, evidence


def _probe_frontend_dso(opening: int, closing: int, net_revenue: float) -> None:
    scenarios = [
        {
            "id": "net-positive",
            "current": closing,
            "opening": opening,
            "revenue": net_revenue,
            "title": "Выручка",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
        {
            "id": "zero-ar",
            "current": 0,
            "opening": 0,
            "revenue": 100,
            "title": "Выручка",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
        {
            "id": "negative-net-revenue",
            "current": 120,
            "opening": 80,
            "revenue": -100,
            "title": "Выручка",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
        {
            "id": "zero-revenue",
            "current": 120,
            "opening": 80,
            "revenue": 0,
            "title": "Выручка",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
        {
            "id": "gross-revenue-no-vat",
            "current": 120,
            "opening": 80,
            "revenue": 100,
            "title": "Выручка брутто (НДС недоступен)",
            "start": "2026-01-01",
            "end": "2026-01-31",
        },
    ]
    results, evidence = _run_extracted_frontend_dso(scenarios)
    by_id = {item["id"]: item for item in results}
    common_input = {
        "formula_source": str(FRONTEND_PATH.relative_to(ROOT)),
        "period": {"start": "2026-01-01", "end": "2026-01-31"},
    }

    net = by_id["net-positive"]
    _record(
        test_id="AR-010-dso-frontend-net-positive",
        input_data={
            **common_input,
            "scenario": scenarios[0],
            "opening_and_closing_from": "AR-008",
            "net_revenue_from": "AR-009",
        },
        actual=net,
        expected={
            "result": {"value": 19.375, "js_value": "19.375"},
            "days": 31,
            "revenue_is_net": True,
        },
        interpretation=(
            "PASS: the real frontend expression produces the expected "
            "accounting estimate from AR-008 opening/closing balances and "
            "AR-009 net revenue; this is not a document-level payment term."
        ),
        evidence=evidence,
        assertion=(
            "extracted JS returns 19.375 for (40+60)/2 / 80 * 31 and "
            "recognizes the title as net revenue"
        ),
        verify=lambda: (
            assert_equal(net["result"]["value"], 19.375),
            assert_equal(net["days"], 31),
            assert_true(net["revenue_is_net"]),
        ),
    )

    zero_ar = by_id["zero-ar"]
    _record(
        test_id="AR-011-dso-frontend-zero-ar",
        input_data={**common_input, "scenario": scenarios[1]},
        actual=zero_ar,
        expected={
            "result": {"value": 0, "js_value": "0"},
            "interpretation": "zero AR with positive net revenue gives zero DSO",
        },
        interpretation=(
            "PASS: zero opening and closing AR produce a numeric zero, not "
            "a missing value, when positive net revenue is available."
        ),
        evidence=evidence,
        assertion="extracted JS result value == 0 and is finite",
        verify=lambda: (
            assert_equal(zero_ar["result"]["value"], 0),
            assert_true(zero_ar["result"]["is_finite"]),
        ),
    )

    negative = by_id["negative-net-revenue"]
    _record(
        test_id="AR-012-dso-frontend-negative-net-revenue",
        input_data={**common_input, "scenario": scenarios[2]},
        actual=negative,
        expected={
            "result": None,
            "safe_interpretation": (
                "DSO should be unavailable when net revenue is non-positive"
            ),
        },
        interpretation=(
            "FINDING: the extracted frontend formula returns -31 days for "
            "negative net revenue because revenueIsNet checks truthiness and "
            "does not require a positive denominator. A negative DSO is not "
            "an audit-safe duration."
        ),
        evidence=evidence,
        assertion=(
            "extracted JS recognizes -100 as net revenue and returns finite "
            "value -31"
        ),
        verify=lambda: (
            assert_true(negative["revenue_is_net"]),
            assert_equal(negative["result"]["value"], -31),
            assert_true(negative["result"]["is_finite"]),
        ),
        finding=True,
    )

    zero_revenue = by_id["zero-revenue"]
    _record(
        test_id="AR-013-dso-frontend-zero-revenue",
        input_data={**common_input, "scenario": scenarios[3]},
        actual=zero_revenue,
        expected={
            "result": None,
            "safe_interpretation": (
                "DSO unavailable when net revenue is zero"
            ),
        },
        interpretation=(
            "PASS with limitation: the frontend truthiness check makes "
            "revenueIsNet false for a zero revenue value, so the formula "
            "returns null before entering the arithmetic branch. The reason "
            "is not surfaced as a distinct zero-revenue diagnostic."
        ),
        evidence=evidence,
        assertion=(
            "extracted JS revenueIsNet is false for zero and result is null"
        ),
        verify=lambda: (
            assert_true(not zero_revenue["revenue_is_net"]),
            assert_equal(zero_revenue["result"]["value"], None),
        ),
    )

    gross = by_id["gross-revenue-no-vat"]
    _record(
        test_id="AR-014-dso-frontend-vat-scope",
        input_data={**common_input, "scenario": scenarios[4]},
        actual=gross,
        expected={
            "result": None,
            "safe_interpretation": (
                "Do not compute DSO from gross revenue when 90.03/VAT is "
                "unavailable"
            ),
        },
        interpretation=(
            "PASS: the real frontend expression refuses DSO when the revenue "
            "card title explicitly marks gross revenue due to unavailable "
            "VAT. This agrees with the backend gross fallback probe."
        ),
        evidence=evidence,
        assertion=(
            "extracted JS sees the gross/VAT-missing title, sets "
            "revenueIsNet false, and returns null"
        ),
        verify=lambda: (
            assert_true(not gross["revenue_is_net"]),
            assert_equal(gross["result"]["value"], None),
        ),
    )


def assert_equal(actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def assert_true(condition: Any) -> None:
    if not condition:
        raise AssertionError("assertion was false")


def main() -> int:
    opening, closing = 40, 60
    _probe_total_and_leaf_dedup()
    _probe_duplicate_balance_lists()
    _probe_standard_rows_have_no_ar_analytics()
    _probe_ar_netting()
    _probe_hierarchy_mismatch_share()
    _probe_missing_side_zero_fill()
    _probe_exact_date_missing()
    opening, closing = _probe_opening_closing_provenance()
    net_revenue = _probe_vat_scope()
    _probe_frontend_dso(opening, closing, net_revenue)

    findings = sum(item["status"] == "FINDING" for item in RECORDS)
    report = {
        "report_id": "debt-audit-probes",
        "purpose": (
            "Synthetic read-only AR/accounting probes against pure "
            "key_indicators helpers and extracted frontend DSO expressions"
        ),
        "scope_note": (
            "The prior 66/67 credit-share probe is excluded from this AR "
            "report; no 66/67 results are delivered here."
        ),
        "status_semantics": (
            "implementation_status=PASS means the source probe assertion "
            "was verified; audit_assessment=FINDING means the observed "
            "behavior differs from the audit-safe/accounting expectation."
        ),
        "execution": {
            "runner": "reports/debt_audit_probes.py",
            "command": "python3 reports/debt_audit_probes.py",
            "python": sys.version.split()[0],
            "node": _node_version(),
            "imports": ["key_indicators"],
            "forbidden_imports_not_used": [
                "app",
                "key_indicators_service",
                "database",
            ],
            "database_connections": False,
            "network_calls": False,
            "services_started": False,
        },
        "source_files": {
            "osv-dashboard/key_indicators.py": {
                "sha256": _sha256(KEY_INDICATORS_PATH),
                "role": "pure Python source under probe",
            },
            "osv-dashboard/static/key_indicators.js": {
                "sha256": _sha256(FRONTEND_PATH),
                "role": "source of extracted DSO/VAT-scope expressions",
            },
            "osv-dashboard/test_key_indicators.py": {
                "sha256": _sha256(OSV_DIR / "test_key_indicators.py"),
                "role": "fixture/source-shape reference inspected for probes",
            },
            "osv-dashboard/osvparser.py": {
                "sha256": _sha256(OSV_DIR / "osvparser.py"),
                "role": "parser evidence for daily/report balance fields",
            },
            "osv-dashboard/key_indicators_service.py": {
                "sha256": _sha256(OSV_DIR / "key_indicators_service.py"),
                "role": "loader evidence; inspected, not imported",
            },
        },
        "assertions_verified": len(RECORDS),
        "finding_count": findings,
        "summary_ru": [
            item["summary_ru"] for item in RECORDS
        ],
        "tests": RECORDS,
        "limitations": [
            (
                "Fixtures are synthetic source snapshots, not a read of an "
                "enterprise file or database; no application/service was "
                "imported or started."
            ),
            (
                "OSV turnover and balance data cannot prove document-level "
                "payment allocation, due dates, aging, overdue status, "
                "subsidies, or a factual payment DSO."
            ),
            (
                "The parent/leaf fixture follows the daily-row shape used by "
                "the tests and source builder; it does not validate every "
                "possible parser export layout."
            ),
            (
                "The frontend probe extracts and executes current JavaScript "
                "expressions in Node, but it is not a browser/DOM rendering "
                "test."
            ),
            (
                "VAT scope is limited to exact 90.03 turnover and does not "
                "validate tax rates, invoices, exemptions, or document-level "
                "VAT allocation."
            ),
            (
                "Missing-side zero-fill is reported as a finding because the "
                "helper cannot distinguish an explicitly booked zero from an "
                "absent balance side."
            ),
            (
                "The duplicate-list 62.01 probe is conditional helper "
                "contract risk only. Current osvparser daily rows carry "
                "turnovers without balance sides, and current DailyRow "
                "loader observations have empty analytic paths; the probe "
                "does not establish a current end-to-end duplicate incident."
            ),
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUTPUT_PATH.relative_to(ROOT)}: "
        f"{len(RECORDS)} assertions verified, {findings} finding(s)"
    )
    return 0


def _node_version() -> str:
    completed = subprocess.run(
        ["node", "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())