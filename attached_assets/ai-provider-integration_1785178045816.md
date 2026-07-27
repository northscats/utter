# AI-провайдер: полная инструкция по переносу

> Система хранит в БД список LLM-провайдеров (OpenAI-совместимых — vLLM, Azure, Yandex, etc.).
> Один провайдер помечается как `isDefault`. Все вызовы LLM идут через единственный файл
> `ai-client.ts` — чистый `fetch`, без SDK.

---

## Архитектура

```
Бизнес-логика
     │
     ▼
callAiProvider()          ← server/ai-client.ts
     │  fetch POST /chat/completions
     ▼
LLM-провайдер (OpenAI / vLLM / Azure / Yandex)
     │
     ▼
{ content, tool_calls, usage }

Настройки провайдеров хранятся в таблице ai_providers (Postgres)
Выбор провайдера: getDefaultAiProvider() или getAiProvider(id)
```

---

## Файл 1 — `server/ai-client.ts`

Копируй целиком. Это единственная точка входа для всех LLM-вызовов.

```typescript
export interface AiMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: AiToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface AiToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface CallAiProviderOpts {
  baseUrl: string;
  apiKey: string;
  model: string;
  messages: AiMessage[];
  temperature?: number;       // default: 0.3
  maxTokens?: number;         // default: 512
  enableThinking?: boolean;   // false → отключить рассуждения (см. раздел ниже)
  responseFormatJson?: boolean; // true → { response_format: { type: "json_object" } }
  timeoutMs?: number;         // default: 120 000 мс
  tools?: unknown[];
  toolChoice?: string;
  signal?: AbortSignal;       // для отмены запроса снаружи
}

export interface CallAiProviderResult {
  content: string;
  tool_calls?: AiToolCall[];
  usage?: { prompt_tokens?: number; completion_tokens?: number };
}

/**
 * Централизованный fetch-клиент AI-провайдера.
 *
 * Использует чистый fetch (не openai SDK), чтобы `chat_template_kwargs`
 * попадал в корень тела запроса — это обязательно для vLLM-провайдеров
 * при отключении режима рассуждений.
 */
export async function callAiProvider(opts: CallAiProviderOpts): Promise<CallAiProviderResult> {
  const {
    baseUrl, apiKey, model, messages,
    temperature = 0.3,
    maxTokens = 512,
    enableThinking,
    responseFormatJson = false,
    timeoutMs = 120000,
    tools, toolChoice,
  } = opts;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  if (opts.signal) {
    if (opts.signal.aborted) {
      clearTimeout(timer);
      throw new Error("Запрос отменён");
    }
    opts.signal.addEventListener(
      "abort",
      () => controller.abort(opts.signal!.reason),
      { once: true },
    );
  }

  try {
    const body: Record<string, unknown> = {
      model,
      messages,
      temperature,
      max_tokens: maxTokens,
    };

    if (responseFormatJson) {
      body.response_format = { type: "json_object" };
    }

    // ⚠️  Режим без рассуждений — ТОЛЬКО через корень тела.
    //     Не через options/extra_body SDK — vLLM требует именно так.
    if (enableThinking === false) {
      body.chat_template_kwargs = { enable_thinking: false };
    }

    if (tools && tools.length > 0) {
      body.tools = tools;
      body.tool_choice = toolChoice ?? "auto";
    }

    const url = baseUrl.replace(/\/$/, "") + "/chat/completions";
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      const errorText = await response.text().catch(() => "");
      const err: any = new Error(
        `AI provider HTTP ${response.status}: ${errorText.slice(0, 300)}`,
      );
      err.status = response.status;
      const retryAfter = response.headers.get("retry-after");
      if (retryAfter) err.retryAfter = retryAfter;
      throw err;
    }

    const data = (await response.json()) as any;
    const message = data.choices?.[0]?.message;
    const content: string = message?.content ?? "";
    const tool_calls: AiToolCall[] | undefined =
      message?.tool_calls?.length > 0 ? message.tool_calls : undefined;
    const usage = data.usage as
      | { prompt_tokens?: number; completion_tokens?: number }
      | undefined;

    return { content, tool_calls, usage };
  } finally {
    clearTimeout(timer);
  }
}
```

---

## Файл 2 — Таблица БД

### Drizzle ORM

```typescript
import { pgTable, serial, text, boolean, timestamp } from "drizzle-orm/pg-core";

export const aiProviders = pgTable("ai_providers", {
  id:        serial("id").primaryKey(),
  name:      text("name").notNull(),
  baseUrl:   text("base_url"),           // например: https://api.openai.com/v1
  apiKey:    text("api_key"),
  model:     text("model"),              // gpt-4o-mini, qwen2.5-72b-instruct, …
  isActive:  boolean("is_active").notNull().default(true),
  isDefault: boolean("is_default").notNull().default(false),
  createdAt: timestamp("created_at").defaultNow(),
  updatedAt: timestamp("updated_at").defaultNow(),
});

export type AiProvider       = typeof aiProviders.$inferSelect;
export type InsertAiProvider = typeof aiProviders.$inferInsert;
```

### SQL-миграция

```sql
CREATE TABLE ai_providers (
  id         SERIAL PRIMARY KEY,
  name       TEXT NOT NULL,
  base_url   TEXT,
  api_key    TEXT,
  model      TEXT,
  is_active  BOOLEAN NOT NULL DEFAULT true,
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

---

## Файл 3 — Storage-методы

```typescript
import { and, eq } from "drizzle-orm";
import { db } from "./db";           // ваш Drizzle db-инстанс
import { aiProviders, AiProvider, InsertAiProvider } from "./db-schema";

// Все провайдеры (список для админки)
async function getAiProviders(): Promise<AiProvider[]> {
  return db.select().from(aiProviders).orderBy(aiProviders.createdAt);
}

// Конкретный провайдер по ID
async function getAiProvider(id: number): Promise<AiProvider | undefined> {
  const [row] = await db.select().from(aiProviders).where(eq(aiProviders.id, id));
  return row;
}

// Дефолтный активный провайдер (используй в бизнес-логике)
async function getDefaultAiProvider(): Promise<AiProvider | undefined> {
  // 1. Ищем явно помеченный isDefault = true
  const [def] = await db.select().from(aiProviders)
    .where(and(eq(aiProviders.isDefault, true), eq(aiProviders.isActive, true)));
  if (def) return def;
  // 2. Иначе — первый активный
  const [first] = await db.select().from(aiProviders)
    .where(eq(aiProviders.isActive, true)).limit(1);
  return first;
}

async function createAiProvider(data: InsertAiProvider): Promise<AiProvider> {
  const [row] = await db.insert(aiProviders).values(data).returning();
  return row;
}

async function updateAiProvider(
  id: number,
  updates: Partial<InsertAiProvider>,
): Promise<AiProvider | undefined> {
  const [row] = await db
    .update(aiProviders)
    .set({ ...updates, updatedAt: new Date() })
    .where(eq(aiProviders.id, id))
    .returning();
  return row;
}

async function deleteAiProvider(id: number): Promise<boolean> {
  const result = await db.delete(aiProviders).where(eq(aiProviders.id, id)).returning();
  return result.length > 0;
}
```

---

## Как вызывать из бизнес-логики

```typescript
import { getDefaultAiProvider } from "./storage";
import { callAiProvider } from "./ai-client";

const provider = await getDefaultAiProvider();
if (!provider?.baseUrl || !provider?.apiKey || !provider?.model) {
  throw new Error("AI-провайдер не настроен");
}

const result = await callAiProvider({
  baseUrl:  provider.baseUrl,
  apiKey:   provider.apiKey,
  model:    provider.model,
  messages: [
    { role: "system", content: "Ты помощник." },
    { role: "user",   content: "Привет!" },
  ],
  temperature:        0.3,
  maxTokens:          512,
  timeoutMs:          30000,
  enableThinking:     false,   // отключить рассуждения
  responseFormatJson: true,    // ожидаем JSON в ответе
});

// result.content    — строка с ответом модели
// result.tool_calls — вызовы инструментов (если передавали tools)
// result.usage      — { prompt_tokens, completion_tokens }
```

---

## ⚠️ Режим без рассуждений (`enableThinking: false`)

### Почему это нетривиально

vLLM-провайдеры на базе Qwen / DeepSeek (например `aiplatform.mosreg.ru`) по умолчанию
включают внутренние рассуждения. Это увеличивает latency в 3–10× и тратит лишние токены.

### Как это работает технически

| Что | Как |
|---|---|
| Параметр | `chat_template_kwargs: { enable_thinking: false }` в корне тела запроса |
| Почему не SDK | OpenAI SDK обёртывает доп. поля — они не доходят до vLLM. Нужен чистый `fetch` |
| Когда поле добавляется | Только при `enableThinking === false`. При `true` или `undefined` поле не добавляется |
| Совместимость | Обычные OpenAI/Azure-провайдеры игнорируют неизвестные поля → безопасно |

### Стрипинг `<think>` блоков из ответа

Даже при отключённом режиме некоторые провайдеры могут вернуть теги рассуждений.
Всегда чисти ответ перед парсингом:

```typescript
const rawText = result.content ?? "";
const text = rawText.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
// Дальше работай с `text`, не с `rawText`
```

### Управление флагом через настройки приложения

```typescript
// Флаг хранится как строка в таблице app_settings
// "true"  → рассуждения выключены (по умолчанию для экономии токенов)
// "false" → рассуждения включены

const disableThinking =
  (await storage.getAppSetting("ai_disable_thinking")) !== "false";

const result = await callAiProvider({
  ...providerOpts,
  enableThinking: disableThinking ? false : undefined,
});
```

---

## Admin REST API

Файл: `server/routes/ai-providers.ts`

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/admin/ai-providers` | Список всех (apiKey замаскирован `****xxxx`) |
| `POST` | `/api/admin/ai-providers` | Создать `{name, baseUrl, apiKey, model, isActive, isDefault}` |
| `PUT` | `/api/admin/ai-providers/:id` | Частичное обновление (при `isDefault:true` сбрасывает флаг у остальных) |
| `DELETE` | `/api/admin/ai-providers/:id` | Удалить |
| `POST` | `/api/admin/ai-providers/test` | Тест подключения: шлёт короткий запрос `{baseUrl, apiKey, model}` |
| `GET` | `/api/ai/providers` | Публичный список активных провайдеров для фронтенда (без ключей) |

Ответ публичного `/api/ai/providers`:
```json
{
  "enabled": true,
  "providers": [{ "id": "1", "name": "Main vLLM", "available": true }],
  "default": "1"
}
```

---

## Структура ответа провайдера (OpenAI Chat Completions)

Провайдер возвращает стандартный OpenAI-формат:

```json
{
  "choices": [{
    "message": {
      "content": "Текст ответа (null если только tool_calls)",
      "tool_calls": [
        {
          "id": "call_abc123",
          "type": "function",
          "function": {
            "name": "myTool",
            "arguments": "{\"key\": \"value\"}"
          }
        }
      ]
    }
  }],
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 18
  }
}
```

`callAiProvider` возвращает уже распакованные поля: `{ content, tool_calls, usage }`.

---

## Обработка ошибок

```typescript
try {
  const result = await callAiProvider({ ... });
} catch (err: any) {
  if (err.status === 401) { /* неверный API-ключ */ }
  if (err.status === 429) {
    const retryAfter = err.retryAfter; // секунды из заголовка Retry-After
  }
  if (err.status === 404) { /* неверная модель или base URL */ }
  if (err.name === "AbortError") { /* таймаут */ }
}
```

---

## Чеклист для переноса в новый проект

- [ ] Скопировать `server/ai-client.ts` без изменений
- [ ] Создать таблицу `ai_providers` в БД (SQL или Drizzle schema)
- [ ] Добавить 6 storage-методов (`getAiProviders`, `getAiProvider`, `getDefaultAiProvider`, `create`, `update`, `delete`)
- [ ] Добавить роуты admin CRUD + тест подключения
- [ ] В бизнес-логике вызывать через `callAiProvider` + `getDefaultAiProvider()`
- [ ] Если провайдер vLLM: передавать `enableThinking: false` и стрипать `<think>` блоки
- [ ] Если нужен JSON-ответ: передавать `responseFormatJson: true` + явно просить JSON в промпте
- [ ] Хранить флаг `disableThinking` в `app_settings` для управления без деплоя
