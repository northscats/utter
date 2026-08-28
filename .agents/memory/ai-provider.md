---
name: AI provider integration
description: Архитектура и критические детали интеграции LLM-провайдера в Flask-приложение
---

## Правило
`ai_client.py` использует чистый `urllib` (не OpenAI SDK), потому что vLLM требует
`chat_template_kwargs: {enable_thinking: false}` строго в корне тела POST-запроса.
SDK-обёртки (openai.extra_body) это поле не пробрасывают.

**Why:** vLLM игнорирует поле если оно приходит через extra_body SDK.
Только корень тела `{"chat_template_kwargs": {...}}` работает надёжно.

**How to apply:** Всегда передавать `enable_thinking=False` в `call_ai_provider()`.
Добавлять `body['chat_template_kwargs'] = {'enable_thinking': False}` до сериализации.

## DeepSeek reasoning

Для официального OpenAI-compatible API DeepSeek отключение рассуждений задаётся
через `thinking: {type: disabled}`. Один `chat_template_kwargs` там игнорируется.

**Why:** На документных запросах DeepSeek израсходовал весь лимит генерации на
`reasoning_content`, завершил ответ по `length` и оставил `content` пустым.

**How to apply:** Для официального домена DeepSeek отправлять оба совместимых
параметра отключения thinking. Структурированные запросы должны требовать JSON,
а `finish_reason=length` и пустой content — отклоняться до JSON-парсинга.

## Детали

- `AiProvider` — таблица `ai_providers`: name, base_url, api_key (зашифрован Fernet), model, is_active, is_default
- `AiSummary` — таблица `ai_summaries`: unique per company_id, upsert при каждой генерации
- api_key шифруется тем же `encrypt_value()`/`decrypt_value()` из app.py (Fernet, ENCRYPTION_KEY)
- `<think>...</think>` стрипаются regex в ai_client.py на выходе
- При is_default=True нового провайдера — `AiProvider.query.update({'is_default': False})` перед добавлением
- company_detail передаёт `ai_summary` и `ai_provider_configured` в шаблон
- Другие маршруты, рендерящие company.html, передают эти переменные не всегда → в шаблоне использованы `| default(false)` / `| default(none)` для безопасности
- Маршрут `/company/<id>/ai-summary` — POST, login_required (не admin_required)
- Маршрут `/settings/test-ai` — POST, admin_required
- timeout=90s для генерации, timeout=20s для теста
