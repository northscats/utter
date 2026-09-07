# -*- coding: utf-8 -*-
"""Control checkpoint for the RSO portfolio summary."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="portfolio_test_")
os.environ["DATABASE_URL"] = "sqlite:///{}/test.db".format(_TMP)
os.environ["ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import (Company, CompanyMembership, HistoricalImport,
                      HistoricalMetricValue, HistoricalYearVersion, User,
                      UserModuleAccess, db, utcnow)
from historical_parser import canonical_metric_key
from portfolio import build_portfolio, choose_portfolio_year

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


def add_year(company, admin, year, values, partial=False):
    attempt = HistoricalImport(
        company_id=company.id, uploaded_by=admin.id,
        original_filename="{}.xlsx".format(year), stored_path="/tmp/source.xlsx",
        file_hash="{}-{}".format(company.id, year), status="active",
        is_partial=partial, partial_confirmed=partial)
    db.session.add(attempt)
    db.session.flush()
    version = HistoricalYearVersion(
        import_id=attempt.id, company_id=company.id, year=year,
        version_no=1, is_active=True, activated_at=utcnow(), activated_by=admin.id)
    db.session.add(version)
    db.session.flush()
    for section, label, value, unit in values:
        db.session.add(HistoricalMetricValue(
            year_version_id=version.id, section_key=section,
            section_label=section, metric_key=canonical_metric_key(label),
            metric_label=label, value_numeric=value, unit=unit,
            series_key="value", period_label=str(year), is_missing=value is None))


check("полное покрытие выбирает новый год",
      choose_portfolio_year({1: {2025}, 2: {2025}})[:2] == (2025, {2025: 1.0}))
year, coverage, met = choose_portfolio_year({
    1: {2024, 2025}, 2: {2024, 2025}, 3: {2024, 2025},
    4: {2024, 2025}, 5: {2024}})
check("ровно 80 процентов считается общим годом",
      year == 2025 and coverage[2025] == .8 and met)
year, coverage, met = choose_portfolio_year({1: {2025}, 2: {2024}, 3: set()})
check("при недостаточном покрытии показан новый доступный год с предупреждением",
      year == 2025 and not met)

with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    reader = User(username="portfolio-reader", password_hash="x", role="user")
    db.session.add(reader); db.session.flush()
    db.session.add(UserModuleAccess(user_id=reader.id, module_key="analytics"))
    companies = [Company(name="РСО {}".format(i), created_by=admin.id) for i in range(1, 6)]
    hidden = Company(name="Организация не РСО", created_by=admin.id, is_rso=False)
    db.session.add_all(companies + [hidden]); db.session.flush()
    for company in companies:
        db.session.add(CompanyMembership(user_id=reader.id, company_id=company.id))
    db.session.add(CompanyMembership(user_id=reader.id, company_id=hidden.id))
    base = [
        ("financial_results", "Выручка", 100, "thousand_rubles"),
        ("financial_results", "Прибыль (убыток) от продаж", 10, "thousand_rubles"),
        ("profitability", "Рентабельность продаж (прибыль от продаж / выручка)", 10, "percent"),
        ("liquidity", "Коэффициент текущей (общей) ликвидности", 2, "ratio"),
        ("financial_stability", "Коэффициент автономии", .6, "ratio"),
    ]
    for index, company in enumerate(companies):
        add_year(company, admin, 2024, base)
        if index < 4:
            values = list(base)
            values[0] = ("financial_results", "Выручка",
                         10000 if index == 0 else 100 + index, "thousand_rubles")
            if index == 1:
                values = [
                    item for item in values
                    if item[1] not in (
                        "Коэффициент автономии",
                        "Рентабельность продаж (прибыль от продаж / выручка)",
                    )]
            add_year(company, admin, 2025, values, partial=index == 1)
    add_year(hidden, admin, 2025, base)
    db.session.commit()
    portfolio = build_portfolio(companies)
    check("выбрать 2025 при покрытии 80 процентов",
          portfolio["year"] == 2025 and portfolio["with_data"] == 4)
    check("предприятие без года остаётся в матрице",
          any(not row["has_data"] for row in portfolio["rows"]))
    check("пропуск не заменяется нулём",
          next(row for row in portfolio["rows"] if row["name"] == "РСО 2")
          ["cells"]["autonomy"]["value"] is None)
    check("составной KPI не скрывает отсутствие оценочного показателя",
          next(row for row in portfolio["rows"] if row["name"] == "РСО 2")
          ["cells"]["sales_profit"]["quality"] == "Неполно")
    check("выброс не превращает медиану в среднее",
          portfolio["medians"]["revenue"] == 102.5)
    check("организация не РСО не влияет на портфель",
          portfolio["total"] == 5
          and all(row["name"] != "Организация не РСО" for row in portfolio["rows"]))
    admin_id, reader_id = admin.id, reader.id

client = app.test_client()
login_as(client, reader_id)
body = client.get("/home").get_data(as_text=True)
check("портфель является стартовым экраном аналитики",
      "Портфельная сводка" in body and "Организация не РСО" not in body)
check("общий год выбирается кнопками периодов",
      "Общий год" not in body
      and 'class="period-label">Периоды:' in body
      and body.count('class="year-btn') == 2
      and 'class="year-btn active"' in body
      and 'href="/home?year=2025"' in body)
check("все содержательные столбцы портфеля сортируются кликом",
      body.count('data-sort="number"') == 9
      and body.count('data-sort="text"') == 1
      and 'data-sort="number">Выручка' in body)
check("ссылки сохраняют год и оба режима",
      "view=executive" in body and "view=expert" in body and "year=2025" in body)
check("прежний экран доступен одним переключателем",
      "Выберите предприятие слева" in client.get("/home?legacy=1").get_data(as_text=True))
embedded_body = client.get("/home?embedded=1").get_data(as_text=True)
check("портфель открывается внутри аналитики без второй шапки",
      "Портфельная сводка" in embedded_body
      and "ГИС УЗТЭР · Портфель РСО" not in embedded_body
      and "embedded=1" in embedded_body)
legacy_body = client.get("/home?legacy=1").get_data(as_text=True)
check("портфельная сводка и общий рейтинг вынесены в раздел портфеля",
      "<span>Портфель</span>" in legacy_body
      and legacy_body.index("<span>Портфель</span>") < legacy_body.index('data-nav="portfolio"')
      < legacy_body.index('data-nav="rating"') < legacy_body.index("<span>Аналитика предприятия</span>")
      and "view === 'portfolio'" in legacy_body)
check("аналитическое меню использует новые названия без раздела алертов",
      "Анализ ОСВ" in legacy_body
      and "Данные и отчетность" in legacy_body
      and "Финансовый анализ" in legacy_body
      and 'data-nav="alerts"' not in legacy_body
      and "renderAlertsView" not in legacy_body
      and 'data-nav="summary"' not in legacy_body
      and "renderSummaryView" not in legacy_body)
app.config["PORTFOLIO_HOME_ENABLED"] = False
check("флаг возвращает прежний стартовый экран",
      "Выберите предприятие слева" in client.get("/home").get_data(as_text=True))

print("\nВСЕ ПРОВЕРКИ ПОРТФЕЛЬНОЙ СВОДКИ ПРОЙДЕНЫ")