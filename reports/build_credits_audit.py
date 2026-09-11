"""Build a source-evidenced loans audit without changing application data."""
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook
from audit_workbook import build_audit
from credits_audit_content import METRICS
from credits_audit_ui import TABLES, CHARTS
from credits_audit_assessment import (
    CONCLUSIONS, QUALITY, REQUIRED, ALTERNATES, GLOSSARY,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/credits_loans_metrics_audit.xlsx"
probes = json.loads((ROOT / "reports/credits_audit_probes.json").read_text())
for key, item in probes["source_files"].items():
    path = item.get("path", key)
    assert sha256((ROOT / path).read_bytes()).hexdigest() == item["sha256"], path
assert probes["case_count"] == probes["assertions_verified"] == len(probes["cases"])
for case in probes["cases"]:
    assert case["assertion_verified"]
    assert case["actual"] == case["expected_actual"]

# Keep exact observed values, alongside Russian descriptions and limitations.
labels = {
    "value": "Значение", "total": "Итог", "credits": "Кредиты",
    "share": "Доля,%", "coverage": "Охват", "loaded": "Доступны",
    "missing": "Отсутствуют", "complete": "Полный", "account": "Счёт",
    "accounts": "Счета", "delta": "Изменение", "series": "Ряд",
    "label": "Подпись", "reason": "Причина", "analytics": "Аналитики",
    "debit_turnover": "Дебетовый оборот", "credit_turnover": "Кредитовый оборот",
    "credit_balance": "Кредитовый остаток", "debit_balance": "Дебетовый остаток",
}


def readable(value):
    if isinstance(value, dict):
        return {labels.get(k, k): readable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [readable(v) for v in value]
    if value is None:
        return "Н/Д"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return value


TESTS = [["ID", "Проверка", "Искусственный вход", "Фактический результат",
          "Аудиторская оценка", "Границы и источник"]]
for i, case in enumerate(probes["cases"], 1):
    TESTS.append([
        f"P{i:02d}", case["title_ru"], case["input_ru"],
        json.dumps(readable(case["actual"]), ensure_ascii=False),
        case["assessment_ru"],
        case["scope_ru"] + f" R9/R10: {case['test_id']}. "
        "Совпадение actual/expected подтверждено; не заверяет реальные суммы предприятия.",
    ])

specs = [
    ("R1", "osv-dashboard/key_indicators.py",
     "110–263;312–397;510–582;881–919;1025–1043;1104–1207;1270–1279",
     "Сальдо, обороты, аналитики, охват, точные даты, дельта и серия"),
    ("R2", "osv-dashboard/static/key_indicators.js",
     "415–469;2402–2587;3531–3645",
     "Все KPI, таблицы и график текущего кредитного окна"),
    ("R3", "osv-dashboard/key_indicators_service.py", "90–224",
     "Принятые ОСВ, слияние дат и отметка обновления источника"),
    ("R4", "osv-dashboard/osvparser.py", "45–54;58–107;207–313",
     "Даты, строки с сальдо, иерархия, обороты и контроль"),
    ("R5", "osv-dashboard/annual_key_indicators.py", "18–178",
     "Иная методика принятых годовых метрик; отдельной кредитной карточки нет"),
    ("R6", "osv-dashboard/static/annual_key_indicators.js", "184–200;303–369;442–465",
     "Отдельное годовое представление"),
    ("R7", "osv-dashboard/builder.py", "44–58;332–341;551–610",
     "Legacy-источник кредитов и оценочных показателей"),
    ("R8", "osv-dashboard/templates/dashboard_template.html", "1102–1169",
     "Старые кредитные коэффициенты и состояния"),
    ("R9", "reports/credits_audit_probes.py", "Полный файл",
     "Изолированные проверки фактического кода"),
    ("R10", "reports/credits_audit_probes.json", "cases,source_files,execution",
     "Фактические результаты, условия и хеши"),
]
SOURCES = [["ID", "Файл", "Строки / разделы", "Что подтверждает", "SHA-256", "Граница"]]
for ref, path, lines, purpose in specs:
    data = (ROOT / path).read_bytes()
    SOURCES.append([ref, path, lines, purpose, sha256(data).hexdigest(),
                    "Локальная версия на момент аудита. Не подтверждает совпадение "
                    "с опубликованной версией и достоверность первичных сумм."])

notes = [
    ("Предмет", "Критический аудит «Кредитов и займов» в действующем "
     "Flask-экране «Основные показатели → ОСВ»: все внутренние метрики, "
     "столбцы таблиц, график, подсказки и недоступные договорные показатели."),
    ("Состав", f"{len(METRICS)-1} паспортов; все16 столбцов трёх таблиц; "
     f"{len(TABLES)-1} табличных и {len(CHARTS)-1} графических наблюдений; "
     f"{len(TESTS)-1} контрольных примеров. Источники/формулы и "
     "необходимость отображения оценены отдельно для каждого элемента."),
    ("Главный вывод", "66/67 дают бухгалтерский остаток, не доказанное тело долга. "
     "Экран полезен для контроля ОСВ, но не заменяет казначейский реестр "
     "договоров, будущих платежей, ставок и ковенант."),
    ("Важные ограничения", "Частичный охват может давать числовой итог; "
     "временной ряд может менять состав счетов без явной маркировки. "
     "Свёрнутый остаток и развёрнутая аналитика требуют сверки. "
     "Обороты не являются автоматически привлечением/погашением денег."),
    ("Что не рассчитывается", "Договорные ставки/сроки, основной долг,12месяцев "
     "платежей, проценты и финансовые ковенанты — явно обозначенные "
     "заглушки. Предлагаемые формулы не являются действующей реализацией."),
    ("Граница достоверности", "Первичные документы, фактические кредитные договоры, "
     "закрытие, production-БД и опубликованная версия не сверялись. "
     "Это аудит кода и методики, не аудиторское заверение обязательств."),
    ("Проверки", "Изолированные искусственные входы подтверждают поведение "
     "расчётной функции. Успешныйassert не означает отсутствие недостатков; "
     "контрактный риск не объявляется реальным финансовым инцидентом."),
    ("Формулы и единицы", "Формулы приведены текстом; книга не пересчитывает "
     "долг предприятия. Исходные рубли отображаются в млн₽. "
     "Н/Д не равно0; точные остатки нельзя интерполировать или суммировать по месяцам."),
    ("Изменения", "Приложение, расчёты и данные не менялись. Созданы только "
     "материалы аудита; рекомендации не внедрены."),
    ("Навигация", "Начните с «Заключение», далее паспорта и таблицы. "
     "R1–R10 — проверенные исходники и хеши; P — контрольные примеры. "
     "Проценты точности/полноты без доказательной базы не назначаются."),
]
sections = [
    ("Заключение", CONCLUSIONS, [8,16,48,76,72,38]),
    ("Метрики", METRICS, [8,42,72,64,63,66,48,66,64,38]),
    ("Таблицы", TABLES, [8,43,74,74,52,65,38]),
    ("Графики", CHARTS, [8,43,74,74,52,65,38]),
    ("Качество источников", QUALITY, [8,38,76,76,76,37]),
    ("Необходимые данные", REQUIRED, [8,40,86,74,28]),
    ("Другие экраны", ALTERNATES, [8,40,78,76,68,38]),
    ("Контрольные примеры", TESTS, [8,46,76,100,85,76]),
    ("Источники анализа", SOURCES, [8,65,45,66,70,70]),
    ("Термины и оценки", GLOSSARY, [36,80,100]),
]
build_audit(OUT, "Кредиты и займы: критический аудит", notes, sections)
with ZipFile(OUT) as z:
    assert z.testzip() is None
    assert not any(n.startswith("xl/externalLinks/") for n in z.namelist())
    assert "xl/vbaProject.bin" not in z.namelist()
wb = load_workbook(OUT)
assert len(wb.sheetnames) == 11 and not wb._external_links
assert all(c.data_type != "f" for ws in wb for row in ws for c in row)
print("Verified source hashes, assertions, all cell contents and ZIP; no formulas, macros or external links.")