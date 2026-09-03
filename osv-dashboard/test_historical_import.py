# -*- coding: utf-8 -*-
"""End-to-end regressions for staged, versioned historical imports."""
import io
import os
import sys
import tempfile
import threading
from pathlib import Path

from openpyxl import Workbook


_TMP = tempfile.mkdtemp(prefix="historical_import_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["ENCRYPTION_KEY"] = (
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import (
    Company,
    HistoricalImport,
    HistoricalMetricValue,
    HistoricalRawValue,
    HistoricalYearVersion,
    Upload,
    User,
    db,
)


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


def post_file(client, company_id, data, name="history.xlsx"):
    return client.post(
        f"/company/{company_id}/historical/preview",
        data={"historical_file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def partial_book(unknown=False):
    wb = Workbook()
    assets = wb.active
    assets.title = "1. Активы"
    assets.append(["Показатель", "31.12.2025"])
    assets.append(["ВАЛЮТА БАЛАНСА", "1 000"])
    if unknown:
        assets.append(["Совершенно новый показатель", "<0,01"])
    liabilities = wb.create_sheet("2. Пассивы")
    liabilities.append(["Показатель", "31.12.2025"])
    liabilities.append(["ВАЛЮТА БАЛАНСА", "1 000"])
    finance = wb.create_sheet("8. Фин. результаты")
    finance.append(["Показатель", "2025"])
    finance.append(["Выручка", "500"])
    finance.append(["Чистая прибыль (убыток)", "25"])
    liquidity = wb.create_sheet("6. Ликвидность")
    liquidity.append(["Показатель", "2025"])
    liquidity.append(["Коэффициент текущей (общей) ликвидности", "1,2"])
    liquidity.append(["Коэффициент быстрой (промежуточной) ликвидности", "0,8"])
    liquidity.append(["Коэффициент абсолютной ликвидности", "0,1"])
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    company = Company(name="Историческое предприятие", created_by=admin.id)
    db.session.add(company)
    db.session.commit()
    admin_id, company_id = admin.id, company.id

client = app.test_client()
login_as(client, admin_id)
sample = Path(
    os.path.dirname(os.path.dirname(__file__)),
    "attached_assets",
    "Balashikha_Kommunalnye_Sistemy_2022_2025_1788419445681.xlsx",
).read_bytes()

# Пример: предпросмотр не затрагивает действующие данные.
r = post_file(client, company_id, sample, "Балашиха.xlsx")
check("пример принят в предпросмотр", r.status_code == 302)
with app.app_context():
    attempt = HistoricalImport.query.order_by(HistoricalImport.id.desc()).first()
    attempt_id = attempt.id
    check("в примере найдены все листы и годы",
          attempt.preview["preview"]["section_count"] == 15
          and attempt.preview["preview"]["years"] == [2022, 2023, 2024, 2025])
    check("сводная таблица не создаёт значений",
          attempt.preview["reconciliation"]["emitted_values"] == 0)
    check("до подтверждения нет активных версий",
          HistoricalYearVersion.query.count() == 0)
preview_page = client.get(
    f"/company/{company_id}?historical_preview={attempt_id}")
preview_body = preview_page.get_data(as_text=True)
check("карточка отображает полный предпросмотр",
      preview_page.status_code == 200
      and "Годовая историческая аналитика" in preview_body
      and "Разделов: 15" in preview_body
      and "Подтвердить и активировать версии" in preview_body)

r = client.post(
    f"/company/{company_id}/historical/{attempt_id}/confirm",
    data={"mapping_count": "0"},
)
check("пример подтверждён", r.status_code == 302)
with app.app_context():
    active = HistoricalYearVersion.query.filter_by(is_active=True).all()
    check("созданы четыре активных годовых среза",
          {row.year for row in active} == {2022, 2023, 2024, 2025})
    upper_metric = HistoricalMetricValue.query.filter_by(
        is_upper_bound=True).first()
    missing_metric = HistoricalMetricValue.query.filter_by(
        is_missing=True).first()
    check("верхняя граница сохранена отдельно от исходной строки",
          upper_metric is not None
          and upper_metric.value_numeric == 0.01
          and upper_metric.raw_value.raw_text == "<0,01")
    check("тире сохранено как исходный пропуск",
          missing_metric is not None
          and missing_metric.value_numeric is None
          and missing_metric.raw_value.raw_text == "–")
    check("исходная строка есть у каждого значения",
          HistoricalRawValue.query.count() == HistoricalMetricValue.query.count())
history_page = client.get(f"/company/{company_id}")
history_body = history_page.get_data(as_text=True)
check("карточка показывает действующие версии и исходный файл",
      history_page.status_code == 200
      and "Годовые версии" in history_body
      and "действующая" in history_body
      and "Балашиха.xlsx" in history_body)

# Повторная загрузка создаёт v2; возврат включает v1 без удаления v2.
r = post_file(client, company_id, sample, "Балашиха повтор.xlsx")
with app.app_context():
    repeat_id = HistoricalImport.query.order_by(
        HistoricalImport.id.desc()).first().id
client.post(
    f"/company/{company_id}/historical/{repeat_id}/confirm",
    data={"mapping_count": "0"},
)
with app.app_context():
    versions_2025 = HistoricalYearVersion.query.filter_by(
        company_id=company_id, year=2025).order_by(
            HistoricalYearVersion.version_no).all()
    v1_id = versions_2025[0].id
    check("повторная загрузка создаёт новую версию",
          [row.version_no for row in versions_2025] == [1, 2]
          and versions_2025[1].is_active)
client.post(
    f"/company/{company_id}/historical/version/{v1_id}/activate")
with app.app_context():
    versions_2025 = HistoricalYearVersion.query.filter_by(
        company_id=company_id, year=2025).order_by(
            HistoricalYearVersion.version_no).all()
    check("возврат не удаляет последующую версию",
          len(versions_2025) == 2
          and versions_2025[0].is_active
          and not versions_2025[1].is_active)
client.post(
    f"/company/{company_id}/historical/version/{v1_id}/activate")
with app.app_context():
    check("повторная активация действующей версии идемпотентна",
          HistoricalYearVersion.query.filter_by(
              company_id=company_id, year=2025, is_active=True).count() == 1
          and db.session.get(HistoricalYearVersion, v1_id).is_active)

# Частичная книга требует явного подтверждения, хотя обязательные блоки есть.
post_file(client, company_id, partial_book(), "частичная.xlsx")
with app.app_context():
    partial = HistoricalImport.query.order_by(HistoricalImport.id.desc()).first()
    partial_id = partial.id
    before = HistoricalYearVersion.query.filter_by(
        company_id=company_id, year=2025, is_active=True).one().id
r = client.post(
    f"/company/{company_id}/historical/{partial_id}/confirm",
    data={"mapping_count": "0"},
)
check("частичный импорт без подтверждения отклонён",
      "historical_error=" in r.headers.get("Location", ""))
with app.app_context():
    check("отклонённый импорт не переключил версию",
          HistoricalYearVersion.query.filter_by(
              company_id=company_id, year=2025, is_active=True).one().id == before)
client.post(
    f"/company/{company_id}/historical/{partial_id}/confirm",
    data={"mapping_count": "0", "confirm_partial": "1"},
)
with app.app_context():
    partial = db.session.get(HistoricalImport, partial_id)
    check("явно подтверждённый частичный импорт активирован",
          partial.status == "active" and partial.partial_confirmed)

# Неизвестная строка требует явного сопоставления.
post_file(client, company_id, partial_book(unknown=True), "неизвестная.xlsx")
with app.app_context():
    unknown = HistoricalImport.query.order_by(HistoricalImport.id.desc()).first()
    unknown_id = unknown.id
    check("неизвестная строка видима в предпросмотре",
          unknown.preview["unknown_metrics"][0]["raw"]
          == "Совершенно новый показатель")
r = client.post(
    f"/company/{company_id}/historical/{unknown_id}/confirm",
    data={"mapping_count": "0", "confirm_partial": "1"},
)
check("неизвестная строка без сопоставления отклонена",
      "historical_error=" in r.headers.get("Location", ""))
r = client.post(
    f"/company/{company_id}/historical/{unknown_id}/confirm",
    data={
        "mapping_count": "1",
        "mapping_source_0": "Совершенно новый показатель",
        "mapping_kind_0": "metric",
        "mapping_label_0": "Новый подтверждённый показатель",
        "mapping_canonical_0": "confirmed_custom_metric",
        "confirm_partial": "1",
    },
)
check("подтверждённое сопоставление импортировано", r.status_code == 302)

# Два параллельных подтверждения одной попытки: версии создаёт только одно.
with app.app_context():
    concurrent_company = Company(
        name="Параллельное подтверждение", created_by=admin_id)
    db.session.add(concurrent_company)
    db.session.commit()
    concurrent_company_id = concurrent_company.id
post_file(
    client, concurrent_company_id, partial_book(),
    "параллельная.xlsx")
with app.app_context():
    concurrent_attempt_id = HistoricalImport.query.filter_by(
        company_id=concurrent_company_id).one().id
barrier = threading.Barrier(2)
concurrent_results = []


def confirm_concurrently():
    from historical_service import HistoricalImportError, confirm_and_activate
    with app.app_context():
        item = db.session.get(HistoricalImport, concurrent_attempt_id)
        barrier.wait()
        try:
            confirm_and_activate(
                item, admin_id, [], confirm_partial=True)
            concurrent_results.append("success")
        except HistoricalImportError:
            db.session.rollback()
            concurrent_results.append("already_processed")


threads = [
    threading.Thread(target=confirm_concurrently),
    threading.Thread(target=confirm_concurrently),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=20)
check("параллельные подтверждения завершились",
      all(not thread.is_alive() for thread in threads))
with app.app_context():
    check("одну попытку подтверждает только один запрос",
          sorted(concurrent_results) == ["already_processed", "success"]
          and HistoricalYearVersion.query.filter_by(
              company_id=concurrent_company_id, year=2025).count() == 1
          and HistoricalYearVersion.query.filter_by(
              company_id=concurrent_company_id, year=2025,
              is_active=True).count() == 1)

# Повреждённая и отменённая загрузки не меняют активный срез.
with app.app_context():
    active_before = HistoricalYearVersion.query.filter_by(
        company_id=company_id, year=2025, is_active=True).one().id
post_file(client, company_id, b"not xlsx", "сломанный.xlsx")
with app.app_context():
    broken = HistoricalImport.query.order_by(HistoricalImport.id.desc()).first()
    check("повреждённый XLSX хранится как неуспешная попытка",
          broken.status == "failed" and bool(broken.error_text))
    check("ошибка не меняет активный срез",
          HistoricalYearVersion.query.filter_by(
              company_id=company_id, year=2025, is_active=True).one().id
          == active_before)
post_file(client, company_id, partial_book(), "отмена.xlsx")
with app.app_context():
    cancelled_id = HistoricalImport.query.order_by(
        HistoricalImport.id.desc()).first().id
client.post(
    f"/company/{company_id}/historical/{cancelled_id}/cancel")
with app.app_context():
    check("отмена не меняет активную версию",
          db.session.get(HistoricalImport, cancelled_id).status == "cancelled"
          and HistoricalYearVersion.query.filter_by(
              company_id=company_id, year=2025, is_active=True).one().id
          == active_before)

# Один переключатель возвращает прежнюю карточку и блокирует новый вход.
with app.app_context():
    osv_count = Upload.query.filter_by(company_id=company_id).count()
app.config["HISTORICAL_IMPORT_ENABLED"] = False
body = client.get(f"/company/{company_id}").get_data(as_text=True)
blocked = post_file(client, company_id, partial_book(), "выключено.xlsx")
check("выключатель скрывает исторический контур",
      "Годовая историческая аналитика" not in body and blocked.status_code == 404)
with app.app_context():
    check("поток ОСВ не изменён", Upload.query.filter_by(
        company_id=company_id).count() == osv_count)

print("\nВСЕ ТЕСТЫ ИСТОРИЧЕСКОГО ИМПОРТА ПРОЙДЕНЫ")