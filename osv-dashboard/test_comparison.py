# -*- coding: utf-8 -*-
"""Контрольный чекпоинт сравнения предприятий по годовым метрикам."""
import os
import sys
import tempfile
import json

_TMP = tempfile.mkdtemp(prefix="comparison_test_")
os.environ["DATABASE_URL"] = "sqlite:///{}/test.db".format(_TMP)
os.environ["ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from comparison import build_comparison, metric_ref
from database import (Company, CompanyMembership, HistoricalImport,
                      HistoricalMetricValue, HistoricalYearVersion, Upload, User,
                      UserModuleAccess, db, utcnow)
from historical_parser import canonical_metric_key

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def check(label, condition):
    print(("PASS" if condition else "FAIL"), label)
    if not condition:
        raise AssertionError(label)


def login_as(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def add_year(company, admin, year, revenue=None, liquidity=None):
    attempt = HistoricalImport(
        company_id=company.id, uploaded_by=admin.id,
        original_filename="{}-{}.xlsx".format(company.id, year),
        stored_path="/tmp/source.xlsx", file_hash="{}-{}".format(company.id, year),
        status="active")
    db.session.add(attempt); db.session.flush()
    version = HistoricalYearVersion(
        import_id=attempt.id, company_id=company.id, year=year,
        version_no=1, is_active=True, activated_at=utcnow(),
        activated_by=admin.id)
    db.session.add(version); db.session.flush()
    rows = [
        ("financial_results", "Выручка", revenue, "thousand_rubles"),
        ("financial_results", "Управленческие расходы",
         -10 if revenue is not None else None, "thousand_rubles"),
        ("liquidity", "Коэффициент текущей (общей) ликвидности",
         liquidity, "ratio"),
        ("profitability", "Рентабельность собственного капитала (ROE)",
         liquidity - 1.0 if liquidity is not None else None, "percent"),
    ]
    for section, label, value, unit in rows:
        if value is None:
            continue
        db.session.add(HistoricalMetricValue(
            year_version_id=version.id, section_key=section,
            section_label=section, metric_key=canonical_metric_key(label),
            metric_label=label, series_key="value", period_label=str(year),
            value_numeric=value, unit=unit, is_missing=False))


with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    reader = User(username="compare-reader", password_hash="x", role="user")
    db.session.add(reader); db.session.flush()
    db.session.add(UserModuleAccess(user_id=reader.id, module_key="analytics"))
    companies = [Company(name="РСО {}".format(i), created_by=admin.id)
                 for i in range(1, 7)]
    db.session.add_all(companies); db.session.flush()
    for company in companies[:5]:
        db.session.add(CompanyMembership(user_id=reader.id, company_id=company.id))
    for index, company in enumerate(companies[:5]):
        add_year(company, admin, 2022, 100 * (index + 1), 1.0 + index / 10)
        if index != 1:
            add_year(company, admin, 2023,
                     100000 if index == 4 else 120 * (index + 1),
                     1.5 + index / 10)
        add_year(company, admin, 2024, 150 * (index + 1), 1.8 + index / 10)
    # ОСВ за отсутствующий утверждённый 2023 год не должна закрывать разрыв.
    db.session.add(Upload(
        company_id=companies[1].id, uploaded_by=admin.id,
        period_label="за 2023 год", closure_status=True,
        metrics_json=json.dumps({"adm_pct": 88.0})))
    # Значительно конфликтующий источник не участвует в peer-медиане.
    db.session.add(Upload(
        company_id=companies[0].id, uploaded_by=admin.id,
        period_label="за 2024 год", closure_status=True,
        metrics_json=json.dumps({"adm_pct": 99.0})))
    db.session.add(Upload(
        company_id=companies[0].id, uploaded_by=admin.id,
        period_label="за 2022 год", closure_status=True,
        metrics_json=json.dumps({"adm_pct": 99.0})))
    add_year(companies[5], admin, 2024, 999999, 9.9)
    bound_version = HistoricalYearVersion.query.filter_by(
        company_id=companies[2].id, year=2023, is_active=True).one()
    bound_revenue = HistoricalMetricValue.query.filter_by(
        year_version_id=bound_version.id,
        metric_key=canonical_metric_key("Выручка")).one()
    bound_revenue.is_upper_bound = True
    db.session.add(HistoricalMetricValue(
        year_version_id=bound_version.id,
        section_key=bound_revenue.section_key,
        section_label=bound_revenue.section_label,
        metric_key=bound_revenue.metric_key,
        metric_label=bound_revenue.metric_label,
        series_key="percent",
        period_label=bound_revenue.period_label,
        value_numeric=42.0,
        unit="percent",
        is_missing=False,
    ))
    db.session.commit()
    admin_id, reader_id = admin.id, reader.id
    visible_ids = [company.id for company in companies[:5]]
    hidden_id = companies[5].id

    base = build_comparison(companies[:5], start_year=2022, end_year=2024)
    visible_metric_names = [
        (metric["section_label"], metric["option_label"])
        for metric in base["metrics"]]
    check("параллельные серии не выглядят дубликатами в выборе метрик",
          len(visible_metric_names) == len(set(visible_metric_names))
          and any("Выручка (сумма, тыс. руб.)" == metric["option_label"]
                  for metric in base["metrics"])
          and any("Выручка (доля, %)" == metric["option_label"]
                  for metric in base["metrics"]))
    revenue = next(m for m in base["metrics"] if m["label"] == "Выручка")
    liquidity = next(m for m in base["metrics"]
                     if m["label"].startswith("Коэффициент текущей"))
    normalized = build_comparison(
        companies[:5], [liquidity["ref"]], 2022, 2024, "normalized")
    check("нормативная метрика использует отклонение от нормы",
          normalized["series"][0]["lines"][0]["points"][0]["value"] is not None)
    percentile = build_comparison(
        companies[:5], [revenue["ref"]], 2022, 2024, "normalized")
    check("метрика без нормы использует устойчивый годовой перцентиль",
          percentile["series"][0]["lines"][4]["points"][1]["value"] == 100.0)
    roe = next(m for m in base["metrics"] if "(ROE)" in m["label"])
    zero_norm = build_comparison(
        companies[:5], [roe["ref"]], 2022, 2024, "normalized")
    check("нулевая норма считается в процентных пунктах, не перцентилем",
          zero_norm["series"][0]["lines"][0]["points"][0]["value"] == 0.0
          and abs(zero_norm["series"][0]["lines"][1]["points"][0]["value"]
                  - 0.1) < 1e-9
          and "п.п." in zero_norm["series"][0]["lines"][1]["points"][0]["display"])
    check("несовпадающий год остаётся разрывом",
          percentile["series"][0]["lines"][1]["points"][1]["value"] is None)
    check("верхняя граница помечена на соответствующей точке",
          percentile["series"][0]["lines"][2]["points"][1]["quality"]
          == "Верхняя граница")
    control = next(m for m in base["metrics"]
                   if m["label"].startswith("Контроль:"))
    controlled = build_comparison(
        companies[:5], [control["ref"]], 2023, 2024, "absolute")
    check("ОСВ без утверждённой версии не заполняет годовой разрыв",
          controlled["series"][0]["lines"][1]["points"][0]["value"] is None)
    check("конфликтный источник исключён из итоговой peer-медианы",
          controlled["table"][0]["quality"] == "Конфликт источников"
          and controlled["table"][0]["median"] < 2.0)
    indexed_control = build_comparison(
        companies[:5], [control["ref"]], 2022, 2024, "index")
    check("ранний конфликт не становится базой индекса",
          indexed_control["series"][0]["lines"][0]["points"][0]["value"] is None
          and indexed_control["series"][0]["lines"][0]["points"][1]["value"]
          == 100.0)
    two_company_percentile = build_comparison(
        [companies[0], companies[1]], [revenue["ref"]],
        2023, 2023, "normalized")
    check("единственное качественное наблюдение не получает перцентиль",
          all(point["value"] is None
              for line in two_company_percentile["series"][0]["lines"]
              for point in line["points"]))
    for mode in ("absolute", "median", "change", "index"):
        payload = build_comparison(
            companies[:2], [revenue["ref"]], 2022, 2024, mode)
        check("режим {} формирует точки".format(mode),
              len(payload["series"][0]["lines"]) == 2)
    check("таблица содержит проверяемые поля",
          {"value", "change", "norm", "median", "zone", "quality", "source"}
          <= set(normalized["table"][0]))

client = app.test_client()
login_as(client, reader_id)
query = "&".join("company={}".format(item) for item in visible_ids)
page = client.get("/comparison?" + query)
page_body = page.get_data(as_text=True)
check("пять доступных предприятий открываются на одном сравнении",
      page.status_code == 200 and "Итоговая таблица" in page_body)
single_year_page = client.get(
    "/comparison?" + query + "&start_year=2024&end_year=2024")
single_year_body = single_year_page.get_data(as_text=True)
check("один год использует сравнительную диаграмму без временной шкалы",
      single_year_page.status_code == 200
      and "const renderSingleYear=" in single_year_body
      and "if(years.length===1)" in single_year_body
      and "год · сравнение предприятий" in single_year_body)
check("итоговая таблица начинается с предприятия и сортирует все колонки",
      '<th data-sort="text">Предприятие</th><th data-sort="text">Метрика</th>'
      in page_body
      and page_body.count("<th data-sort=") == 10
      and 'id="comparison-table"' in page_body)
check("более пяти предприятий отклоняются",
      client.get("/comparison?" + query + "&company={}".format(hidden_id)).status_code
      in (400, 403))
check("скрытое предприятие не раскрывается",
      client.get("/comparison?company={}&company={}".format(
          visible_ids[0], hidden_id)).status_code == 403)
check("меньше двух предприятий отклоняется при отсутствии пары",
      client.get("/comparison?company=999999").status_code in (400, 403))
app.config["COMPANY_COMPARISON_ENABLED"] = False
check("флаг отключает только маршрут сравнения",
      client.get("/comparison?" + query).status_code == 404
      and client.get("/home").status_code == 200
      and client.get("/company/{}".format(visible_ids[0])).status_code == 200)
app.config["COMPANY_COMPARISON_ENABLED"] = True
app.config["HISTORICAL_EXPERT_ENABLED"] = False
portfolio_body = client.get("/home").get_data(as_text=True)
check("скрытый исторический контур не оставляет сломанную кнопку",
      client.get("/comparison?" + query).status_code == 404
      and "Сравнить выбранные" not in portfolio_body)

print("\nВСЕ ПРОВЕРКИ СРАВНЕНИЯ ПРЕДПРИЯТИЙ ПРОЙДЕНЫ")