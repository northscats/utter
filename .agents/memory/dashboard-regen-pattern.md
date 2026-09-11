---
name: Dashboard regeneration pattern
description: Как правильно пересобрать статические HTML-дашборды после изменения шаблона
---

## Правило

`_rebuild_dashboard_with_daily(company_id, name)` только **возвращает dataset** — НЕ пишет файл на диск.

Чтобы реально обновить `dashboard_current.html`, нужна полная цепочка:

```python
from app import app, db, _rebuild_dashboard_with_daily, render_dashboard, OUTPUT
from database import Company
import os

with app.app_context():
    for c in Company.query.all():
        dataset, audit = _rebuild_dashboard_with_daily(c.id, c.name)
        if dataset is None:
            continue
        html = render_dashboard(dataset, back_url=f'/company/{c.id}')
        out_dir = os.path.join(OUTPUT, str(c.id))
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, 'dashboard_current.html'), 'w', encoding='utf-8') as f:
            f.write(html)
```

**Why:** Вызов только `_rebuild_dashboard_with_daily` без последующего `render_dashboard` + записи файла — тихая ошибка: функция вернёт OK, но файл на диске останется старым.

**How to apply:** Использовать эту цепочку при явно разрешённой пересборке данных.

Для исправлений только подписей предпочитать узкое исправление представления при выдаче сохранённого отчёта, а не массовый повторный расчёт.

**Why:** Изменение шаблона само по себе не обновляет сохранённые отчёты. Однако повторная сборка из текущих источников может изменить исторические суммы и выходит за рамки исправления текста.

**How to apply:** Проверять также выдачу старого HTML через настоящий маршрут. Сохранять встроенный набор данных и файл неизменными; совместимость проверять на повторное применение.
