#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small isolated AP probes; no Flask, service, database, or network."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date as Date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OSV = ROOT / "osv-dashboard"
PY = OSV / "key_indicators.py"
JS = OSV / "static" / "key_indicators.js"
PARSER = OSV / "osvparser.py"
SERVICE = OSV / "key_indicators_service.py"
OUT = Path(__file__).with_suffix(".json")
sys.path.insert(0, str(OSV))
from key_indicators import (_sub_balance, _sub_balance_analytics, _top_balance,
                            build_indicator_source, build_key_indicators)  # noqa: E402
DAY = "2026-01-31"
PERIOD = "за январь 2026 г."
UNSET = object()
CASES = []
def T(**kw):
    return {k: kw.get(k, 0) for k in ("snd", "snk", "obd", "obk", "skd", "skk")}
def d(dt=DAY, code="", analytic="", obd=0, obk=0, **balance):
    x = {"date": dt, "month": dt[:7], "path1": analytic, "path2": "",
         "path3": code, "obd": obd, "obk": obk}
    x.update(balance)
    return x
def r(label, lvl=1, p1="", p2="", **values):
    return {"lvl": lvl, "label": label, "p0": "", "p1": p1, "p2": p2,
            "p3": "", **T(**values)}
def a(acc, *, daily=(), rows=(), period=PERIOD, top=UNSET):
    x = {"acc": acc, "daily": list(daily), "rows": list(rows),
         "period": period, "warnings": [], "check_ok": True}
    if top is not UNSET:
        x["top"] = top
    return x
def build(parsed, date=DAY, period="month"):
    source = build_indicator_source(parsed)
    return source, build_key_indicators(source, period=period, date=date)
def cards(result):
    return {item["id"]: item for item in result["cards"]}
def case(cid, title, inp, actual, assessment, scope, refs, assertion,
         finding=False):
    if not assertion():
        raise AssertionError(f"{cid}: assertion returned false")
    CASES.append({
        "id": cid, "title_ru": title, "input_ru": inp,
        "actual_ru": {"values": actual, "assert": "ПРОВЕРЕНО: " + assertion.__doc__},
        "assessment_ru": assessment, "scope_ru": scope,
        "source_refs": refs, "verified": True,
        "status": "FINDING" if finding else "PASS",
    })
def extract(name, anchor):
    text = JS.read_text(encoding="utf-8")
    start = text.index(anchor)
    marker = f"const {name} ="
    pos = text.index(marker, start)
    expr_start = pos + len(marker)
    semi = text.index(";", expr_start)
    lines = text.splitlines()
    first = text.count("\n", 0, pos) + 1
    last = text.count("\n", 0, semi) + 1
    return {"expr": text[expr_start:semi].strip(), "lines": f"{first}-{last}",
            "fragment": "\n".join(lines[first - 1:last])}
JSX = {
    "dpo": extract("dpo", "renderApModal(card)"),
    "days": extract("days", "renderApModal(card)"),
    "ratio": extract("arApRatio", "renderApModal(card)"),
    "analytic_share": extract("share", "const analytics = card.analytics || []"),
    "component_share": extract("share", "const componentRows = components.map"),
    "structure_share": extract("share", "const structure = knownComponents.length"),
}
def node_eval(expression, args, values):
    payload = {"expr": expression, "args": args, "values": values}
    program = r"""
const fs = require('node:fs'), vm = require('node:vm');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = vm.runInNewContext('(function(' + p.args.join(',') +
  '){return (' + p.expr + ');})');
const out = p.values.map(v => { const x = fn(...v); return x === undefined ? null : x; });
process.stdout.write(JSON.stringify(out));
"""
    run = subprocess.run(["node", "-e", program], input=json.dumps(payload),
                         text=True, capture_output=True, check=False)
    if run.returncode:
        raise RuntimeError(run.stderr.strip())
    return json.loads(run.stdout)
def node_days(start, end):
    payload = {"expr": JSX["days"]["expr"], "start": start, "end": end}
    program = r"""
const fs = require('node:fs'), vm = require('node:vm');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const fn = vm.runInNewContext('(function(startDate,endDate){return (' + p.expr + ');})');
process.stdout.write(JSON.stringify(fn(new Date(p.start+'T00:00:00'),
  new Date(p.end+'T00:00:00'))));
"""
    run = subprocess.run(["node", "-e", program], input=json.dumps(payload),
                         text=True, capture_output=True, check=False)
    if run.returncode:
        raise RuntimeError(run.stderr.strip())
    return json.loads(run.stdout)
def main():
    scope = "КЗ: 60.01; компоненты: 68, 69, 70; 66/67 исключены."
    def report(snd, snk, skd, skk):
        p = {"60": a("60", rows=[r("60.01", p1="60.01", snd=snd, snk=snk,
                                   skd=skd, skk=skk)],
                       daily=[d(code="60.01", obk=40)], top=T(
                           snd=snd, snk=snk, skd=skd, skk=skk, obk=40))}
        return cards(build(p)[1])["ap"]
    credit, debit = report(60, 180, 30, 250), report(180, 60, 240, 140)
    actual = {"credit_opening": credit["opening_value"], "credit_closing": credit["value"],
              "debit_position_opening": debit["opening_value"],
              "debit_position_closing": debit["value"]}
    def ok():
        """Кредитовое сальдо 120→220; дебетовая позиция даёт 0 КЗ."""
        return actual == {"credit_opening": 120.0, "credit_closing": 220.0,
                          "debit_position_opening": 0.0, "debit_position_closing": 0.0}
    case("AP-01", "Начальное и конечное сальдо 60.01: кредит и дебетовая позиция",
         "ОСВ 60.01: СН/СК кредитовый вариант 60/180 и 30/250; дебетовая позиция 180/60 и 240/140.",
         actual, "PASS: точные границы дают 120 и 220 кредитовой КЗ; дебетовая позиция 60.01 даёт 0 КЗ и не объявляется авансом 60.02.",
         scope, ["key_indicators.py:419-465", "key_indicators.py:850-860"], ok)
    rows = [r("60.01", p1="60.01", skd=0, skk=100, snd=0, snk=80),
            r("Поставщик А", 2, p1="60.01", p2="Поставщик А",
              skd=0, skk=100, snd=0, snk=80)]
    parsed = {"60": a("60", rows=rows, daily=[d(code="60.01", obk=40)],
                      top=T(skk=100, snk=80, obk=40))}
    source, result = build(parsed)
    ap = cards(result)["ap"]
    actual = {"value": ap["value"], "opening": ap["opening_value"],
              "turnover": ap["credit_turnover"], "analytics": ap["analytics"],
              "daily_balance_keys": [k for k in ("skd", "skk")
                                     if k in source["accounts"]["60"]["daily"][0]]}
    def ok():
        """Карточка 100, оборот 40, analytic detail пуст."""
        return actual["value"] == 100.0 and actual["opening"] == 80.0 and \
            actual["turnover"] == 40.0 and actual["analytics"] == [] and \
            actual["daily_balance_keys"] == []
    case("AP-02", "Стандартные строки ОСВ: баланс аналитики есть, daily — только оборот",
         "Строки отчёта 60.01/Поставщик А содержат СК, daily-строка содержит только ОбК=40.",
         actual, "FINDING: баланс поставщика принят для общей 60.01, но _sub_balance_analytics читает только explicit observations и возвращает пусто.",
         scope, ["osvparser.py:284-298", "key_indicators.py:468-500", "key_indicators.py:850-860"], ok, True)
    def component_fixture(include_69=True):
        p = {"60": a("60", rows=[r("60.01", p1="60.01", skk=100)],
                      top=T(skk=100))}
        for acc, value in (("68", 20), ("69", 30), ("70", 50)):
            if include_69 or acc != "69":
                p[acc] = a(acc, top=T(skk=value))
        return cards(build(p)[1])["ap"]
    full, partial = component_fixture(), component_fixture(False)
    def vals(card):
        return {item["account"]: item["value"] for item in card["components"]}
    actual = {"full_components": vals(full), "full_known_total": full["known_total"],
              "partial_components": vals(partial), "partial_known_total": partial["known_total"]}
    def ok():
        """Известные компоненты суммируются; отсутствующий 69 не равен нулю."""
        return actual == {"full_components": {"60.01": 100.0, "68": 20.0, "69": 30.0, "70": 50.0},
                          "full_known_total": 200.0,
                          "partial_components": {"60.01": 100.0, "68": 20.0, "69": None, "70": 50.0},
                          "partial_known_total": 170.0}
    case("AP-03", "Структура 60.01/68/69/70: полная и частичная известная сумма",
         "Полный набор: 100+20+30+50; частичный: 60.01=100, 68=20, 70=50, 69 отсутствует.",
         actual, "PASS: known_total равен 200 и 170 соответственно; отсутствующий 69 исключён из суммы.",
         scope, ["key_indicators.py:853-880", "key_indicators.js:1739-1749"], ok)
    p = {}
    for acc, value in (("68", 30), ("69", 40), ("70", 50)):
        item = d(code="", skd=0, skk=value)
        x = a(acc, daily=[item], top=T(skk=value))
        x["balance_observations"] = [dict(item)]
        p[acc] = x
    source, result = build(p)
    comp = vals(cards(result)["ap"])
    direct68 = _top_balance(source, "68", Date(2026, 1, 31), credit=True)[0]
    actual = {"source_counts": {acc: [len(source["accounts"][acc][key])
                                      for key in ("balance_observations", "daily")]
                               for acc in ("68", "69", "70")},
              "direct_68": direct68, "components": comp}
    def ok():
        """_top_balance выбирает последнюю explicit строку, а не суммирует списки."""
        return actual["source_counts"] == {"68": [1, 1], "69": [1, 1], "70": [1, 1]} and \
            actual["direct_68"] == 30.0 and comp == {"60.01": None, "68": 30.0, "69": 40.0, "70": 50.0}
    case("AP-04", "DailyRow с пустыми path в balance_observations+daily",
         "Для 68/69/70 одна и та же blank-path строка СКк лежит в обоих списках.",
         actual, "PASS: exact implementation _top_balance берёт explicit[-1], поэтому 68/69/70 не удваиваются. Это не доказывает, что parser daily несёт баланс.",
         scope, ["key_indicators.py:510-531", "key_indicators_service.py:151-160", "osvparser.py:284-289"], ok)
    p = {"60": a("60", daily=[d(code="60.01", skk=50)], top=T())}
    source, result = build(p)
    ap = cards(result)["ap"]
    row = source["accounts"]["60"]["daily"][0]
    actual = {"source_balance_keys": [k for k in ("skd", "skk") if k in row],
              "value": ap["value"], "reason": ap["reason"]}
    def ok():
        """При отсутствии СКд helper подставляет 0 и не сообщает причину."""
        return actual == {"source_balance_keys": ["skk"], "value": 50.0, "reason": None}
    case("AP-05", "Пропущенная сторона сальдо 60.01",
         "Daily observation содержит только СКк=50; СКд отсутствует.",
         actual, "FINDING: _sub_balance использует r.get('skd', 0), поэтому неизвестная дебетовая сторона выглядит как подтверждённая КЗ 50.",
         scope, ["key_indicators.py:419-443", "key_indicators.py:227-233"], ok, True)
    p = {"60": a("60", daily=[d(code="60.01", skk=100),
                              d(code="60.01", analytic="Поставщик Б", skk=100)],
                 top=T())}
    source, result = build(p)
    ap = cards(result)["ap"]
    direct = _sub_balance(source, "60", "60.01", Date(2026, 1, 31), credit=True)[0]
    actual = {"card": ap["value"], "direct": direct,
              "analytic_values": [x["value"] for x in ap["analytics"]]}
    def ok():
        """Exact-code helper sums parent and analytic rows; detail keeps leaf."""
        return actual == {"card": 200.0, "direct": 200.0, "analytic_values": [100.0]}
    case("AP-06", "60.01 parent и analytic с одинаковым кодом",
         "В один день: root path3=60.01 СКк=100 и Поставщик Б path3=60.01 СКк=100.",
         actual, "УСЛОВНОЕ НАБЛЮДЕНИЕ: helper contract даёт total 200 при detail 100, если вход одновременно содержит родительскую и аналитическую exact-code строки.",
         scope + " Условно: риск зависит от формы входных строк; не утверждение о реальном выгрузе.",
         ["key_indicators.py:419-443", "key_indicators.py:468-500"], ok, True)
    p = {"60": a("60", daily=[d(code="60.01", skd=40, skk=100),
                              d(code="60.01.1", analytic="Поставщик Ж", skd=0, skk=100),
                              d(code="60.01.2", analytic="Поставщик З", skd=40, skk=0)],
                 top=T())}
    _, result = build(p)
    ap = cards(result)["ap"]
    shares = node_eval(JSX["analytic_share"]["expr"], ["current", "row"],
                       [[ap["value"], {"value": x["value"]}] for x in ap["analytics"]])
    actual = {"total": ap["value"], "analytic_values": [x["value"] for x in ap["analytics"]],
              "shares": shares, "display": [f"{float(x):.1f}%" for x in shares]}
    def ok():
        """Parent 60 and leaf 100/0 produce extracted share 166.7%."""
        return actual["total"] == 60.0 and actual["analytic_values"] == [100.0, 0.0] and \
            actual["display"] == ["166.7%", "0.0%"] and shares[0] > 100
    case("AP-07", "Несогласованная иерархия листьев: доля больше 100%",
         "Согласованная валовая иерархия: корень 100−40=60; листы поставщиков 100−0=100 и 0−40=0.",
         actual, "УСЛОВНОЕ НАБЛЮДЕНИЕ: синтетическая валовая иерархия не сверяется до UI-доли; expression показывает 166.7%.",
         scope + " Условно: согласованные синтетические итоги родителя и листьев; не утверждение о реальном выгрузе.",
         ["key_indicators.py:468-500", "key_indicators.js:1768-1771"], ok, True)
    p = {"60": a("60", daily=[d("2026-01-30", code="60.01", skk=80),
                              d("2026-01-31", code="60.01")],
                 period="за 1 квартал 2026 г.", top=T(skk=100))}
    source, result = build(p)
    ap = cards(result)["ap"]
    actual = {"value": ap["value"], "reason": ap["reason"],
              "dates": source["dates"]}
    def ok():
        """No 31-Jan balance; 30-Jan and 31-Mar are not substituted."""
        return actual["value"] is None and actual["reason"] == \
            "Сальдо доступно только на исходную границу отчёта" and "2026-03-31" in actual["dates"]
    case("AP-08", "Пробел точной даты закрывающего сальдо",
         "СКк 80 есть 30-Jan, turnover-only row есть 31-Jan, top/report boundary — 31-Mar.",
         actual, "PASS: exact-date rule leaves AP unavailable instead of leaking a prior or later balance.",
         scope, ["key_indicators.py:419-443", "key_indicators.py:1104-1106"], ok)
    dpo = node_eval(JSX["dpo"]["expr"], ["opening", "current", "card", "days"],
                    [[80, 120, {"credit_turnover": 100}, 31],
                     [80, 120, {"credit_turnover": 0}, 31],
                     [80, 120, {"credit_turnover": -100}, 31]])
    partial = {"60": a(
        "60", rows=[r("60.01", p1="60.01", snk=80, skk=100)],
        daily=[d("2026-01-05", code="60.01", obk=100)],
        period="01.01.2026 - 15.01.2026", top=T(snk=80, skk=100))}
    _, partial_result = build(partial, date="2026-01-15")
    partial_ap = cards(partial_result)["ap"]
    partial_start, partial_end = partial_result["period"]["start"], partial_result["period"]["end"]; partial_days = node_days(partial_start, partial_end)
    partial_dpo = node_eval(JSX["dpo"]["expr"], ["opening", "current", "card", "days"], [[partial_ap["opening_value"], partial_ap["value"], {"credit_turnover": partial_ap["credit_turnover"]}, partial_days]])[0]
    actual = {"normal_days31": dpo[0], "zero_turnover": dpo[1],
              "negative_turnover": dpo[2], "partial_start": partial_start,
              "partial_end": partial_end, "partial_days": partial_days,
              "partial_daily_credit_turnover": partial_ap["credit_turnover"],
              "partial_warning": any("неполное" in x for x in partial_result["period"]["warnings"]),
              "partial_dpo": partial_dpo}
    def ok():
        """Extracted DPO is 31/null/-31; valid 1–15 period gives 13.5."""
        return actual == {"normal_days31": 31, "zero_turnover": None,
                          "negative_turnover": -31, "partial_start": "2026-01-01",
                          "partial_end": "2026-01-15", "partial_days": 15,
                          "partial_daily_credit_turnover": 100.0,
                           "partial_warning": True, "partial_dpo": 13.5}
    case("AP-09", "Извлечённый DPO: обычный, нулевой, отрицательный и неполный период",
         "Node vm исполняет реальные dpo/days; выбранные 1–15 января — допустимый неполный месяц, один день операции имеет ОбК=100.",
         actual, "FINDING только для отрицательного оборота (-31): partial warning не блокирует расчёт, а 13.5 не является ошибкой и не доказывает пропуски оборотов или финансовое искажение; один день операции может быть полным наблюдением.",
         scope, ["key_indicators.js:1661-1667", "key_indicators_service.py:151-160", "key_indicators.py:503-508"], ok, True)
    p = {"60": a("60", daily=[d(code="60.01", skd=100, skk=0)], top=T()),
         "62": a("62", daily=[d(code="62.01", skd=0, skk=10)], top=T())}
    _, result = build(p)
    c = cards(result)
    ratios = node_eval(JSX["ratio"]["expr"], ["current", "arCard"],
                       [[c["ap"]["value"], {"value": c["ar"]["value"]}],
                        [100, {"value": 0}]])
    actual = {"ap_zero": c["ap"]["value"], "ar_zero": c["ar"]["value"],
              "ratio_ap_zero": ratios[0], "ratio_positive_ap_ar_zero": ratios[1]}
    def ok():
        """Zero AP is guarded to null; positive AP with zero AR is numeric 0."""
        return actual == {"ap_zero": 0.0, "ar_zero": 0.0,
                          "ratio_ap_zero": None, "ratio_positive_ap_ar_zero": 0}
    case("AP-10", "Нулевые защиты ДЗ/КЗ и ratio",
         "60.01: чистый дебет 100; 62.01: чистый кредит 10; ratio expression извлечён из renderApModal.",
         actual, "PASS: JS truthiness guard avoids division by zero; no Infinity/NaN reaches the modal.",
         scope, ["key_indicators.js:1658-1659", "key_indicators.py:419-443"], ok)
    p = {"60": a("60", rows=[r("60.01", p1="60.01", skd=0, skk=0)],
                 top=T()),
         "68": a("68", top=T()), "69": a("69", top=T()), "70": a("70", top=T())}
    _, result = build(p)
    ap = cards(result)["ap"]
    table_share = node_eval(JSX["component_share"]["expr"], ["knownTotal", "item"],
                            [[ap["known_total"], {"value": 0}]])[0]
    structure_share = node_eval(JSX["structure_share"]["expr"], ["knownTotal", "item"],
                                [[ap["known_total"], {"value": 0}]])[0]
    actual = {"known_total": ap["known_total"], "table_share": table_share,
              "structure_share": structure_share}
    def ok():
        """Known total 0 gives null in table and 0 in structure bar."""
        return actual == {"known_total": 0.0, "table_share": None, "structure_share": 0}
    case("AP-11", "Нулевая known_total: доля таблицы и доля structure",
         "Все четыре известных компонента равны нулю; исполнены два реальных share expression.",
         actual, "PASS: table intentionally renders Н/Д (null), while structure expression uses numeric 0 fallback.",
         scope, ["key_indicators.js:1747-1749", "key_indicators.js:1796-1802"], ok)
    findings = sum(item["status"] == "FINDING" for item in CASES)
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (PY, JS, PARSER, SERVICE)}
    report = {
        "report_id": "ap-audit-probes",
        "scope_ru": scope,
        "purpose_ru": "Чистые synthetic probes текущего key_indicators и извлечённых renderApModal expressions.",
        "status_semantics_ru": "verified=true означает, что фактическая реализация воспроизведена и assert прошёл; FINDING означает методологическое расхождение, не ошибку runner.",
        "execution": {"runner": "reports/ap_audit_probes.py",
                      "command": "python3 reports/ap_audit_probes.py",
                      "python": sys.version.split()[0],
                      "node": node_version(), "imports": ["key_indicators"],
                      "forbidden_imports_not_used": ["Flask", "app", "key_indicators_service", "database"],
                      "network_calls": False, "database_connections": False},
        "source_hashes": hashes,
        "js_extractions": {k: {"lines": v["lines"], "fragment": v["fragment"]}
                           for k, v in JSX.items()},
        "case_count": len(CASES), "finding_count": findings,
        "cases": CASES,
        "limitations_ru": [
            "Все входы synthetic source snapshots; enterprise files, DB и service не читались.",
            "OSV не подтверждает сроки оплаты, просрочку или фактический DPO по документам.",
            "AP-04 доказывает exact _top_balance (last explicit), но не добавляет баланс в parser daily.",
            "AP-06/07 conditional: parent/leaf mismatch зависит от upstream формы строк.",
            "Node vm исполняет expression без DOM, browser и network.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(CASES)} cases, {findings} findings")
def node_version():
    run = subprocess.run(["node", "--version"], text=True, capture_output=True, check=False)
    return run.stdout.strip() if run.returncode == 0 else "unavailable"
if __name__ == "__main__":
    main()