---
name: Текст SVG в Playwright
description: Надёжная проверка текстовых подписей внутри SVG в браузерных тестах.
---

Для SVG-элементов `<text>` в Playwright следует читать DOM-свойство `textContent`, а не полагаться на `innerText`, `inner_text()` или `all_inner_texts()`.

**Why:** В Chromium подписи фактически отображались на графике, но API чтения `innerText` возвращал пустые или `None`-значения и создавал ложное падение визуальной проверки.

**How to apply:** При проверке подписей осей и легенд использовать `eval_on_selector_all(..., els => els.map(e => e.textContent || ''))`; видимость самого графика проверять отдельно.