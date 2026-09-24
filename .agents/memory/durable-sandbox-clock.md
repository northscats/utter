---
name: Часы CodeExecution
description: Ограничение доступа к текущему времени в durable CodeExecution runtime
---

Не предполагать, что `Date.now()` доступен на верхнем уровне CodeExecution. В runtime durable PTC v1 прямой вызов может вернуть ошибку `Date.now() is disabled in durableptc v1`, несмотря на общее описание окружения.

**Why:** При запросе deployment logs верхнеуровневый расчёт `afterTimestamp` остановил весь блок до вызова callback; тот же запрос с необязательным timestamp пропущенным выполнился.

**How to apply:** Если фильтр времени необязателен, сначала выполняй независимые callback без него; не позволяй чтению часов блокировать диагностику. Используй явное время из доверенного контекста либо документированный impure boundary только когда timestamp обязателен.