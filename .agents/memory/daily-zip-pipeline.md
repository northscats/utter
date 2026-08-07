---
name: Daily ZIP pipeline + dashboard build
description: Как работают суточные ZIP-файлы, DailyRow, и как собирается дашборд из суточных + месячных данных. Включает архитектурные решения по builder.py и шаблону.
---

## Поток данных

1. **Суточный XLS или ZIP** → `_upsert_daily_rows` → `DailyRow` (snd/snk/obd/obk/skd/skk)
2. **Месячный XLS** → `parse_many` → data dict с `rows` + `daily` (строки "Обороты за...")
3. **`_rebuild_dashboard_with_daily(company_id, company_name='')`** — объединяет:
   - `parse_many(monthly_paths)` → base data
   - `DailyRow` → добавляет в `data[acc]['daily']` с полями: `date`, `month`, `path1/2/3=''`, `snd`, `snk`, `obd`, `obk`, `skd`, `skk`
   - Важно: `skd`/`skk`/`snd`/`snk` из DailyRow теперь включаются в daily-записи — нужны для суточных балансовых графиков

## build_dataset(data, company_name='')

- Если `path3=''` у всех daily-записей → `has_path3=False` → упрощённый P&L (account-level totals)
- Если `path3` есть → полный P&L с субсчётами + фолбек через `_try_msum`
- Всегда вызывает `build_daily_charts(data)` для суточных рядов
- Возвращает `sections` dict с флагами наличия данных по разделам
- Возвращает `company_name` — передаётся во все вызовы

## build_daily_charts(data)

- Читает `data[acc]['daily']` — поля `date`, `obd`, `obk`, `skd` (опционально), `skk` (опционально)
- Если `skd`/`skk` есть — строит `balance` (для балансовых линейных графиков)
- Возвращает `{dates, accounts: {acc: {label, obd, obk, balance}}}`

## dashboard_template.html — ключевые решения

- `D.company_name` → header badge + h1 + title
- `D.period_end_label` → даты в заголовках, фильтр-нотах, таблицах долгов/налогов
- `D.sections` → JS show/hide разделов + tabs (условные вкладки)
- `D.brief.is_loss` → цвет левого бордера brief-карточки (красный/зелёный)
- DIRC: заменён на `DIR_PALETTE` — динамические цвета по индексу, без ЖКХ-специфики
- Суточные графики: `dailyBarsPair` + `dailyLine` — показываются в exec-разделе и вкладке "Суточная динамика"
- Вкладка "Суточная динамика" добавляется только если `SEC.has_daily`
- Все ЖКХ-специфичные тексты ("тепло и воду", "Газпром", "отопительный сезон") удалены → нейтральные data-driven

**Why:** Дашборд должен работать для любого предприятия, не только МУП КГХ. Суточные данные без субсчётов дают другой путь расчёта P&L.

**How to apply:** При загрузке данных для нового предприятия — всё адаптируется автоматически через `sections` и `has_subaccs`.

**Кодировка 1С .xls:** 1С нередко выгружает .xls без записи CODEPAGE — xlrd декодирует iso-8859-1, русский текст превращается в кракозябры («Íîìåí…»), парсер не находит «Счет»/«Организация». Решение уже в `_read_xls`: если `book.codepage is None` — перечитать с `encoding_override='cp1251'`. Название организации в шапке — строка перед «Оборотно-сальдовая ведомость…» (фолбэк, если нет «Организация:»).
