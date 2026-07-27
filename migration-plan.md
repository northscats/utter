# План миграции: Flask MPA → Flask JSON API + React SPA

## Контекст проекта

**Что за приложение:**  
Внутренняя аналитическая система «ГИС УЗТЭР. Модуль: Анализ ФХД РСО» — загружает оборотно-сальдовые ведомости (ОСВ) из 1С, строит финансовые дашборды по предприятиям. Пользователи — бухгалтеры/аналитики, ~10–50 человек.

**Текущий стек:**
- Backend: Python 3, Flask, Flask-Login (сессии), Flask-SQLAlchemy
- БД: PostgreSQL
- Frontend: Jinja2-шаблоны (MPA, 7 HTML-файлов), чистый CSS, нет фреймворка
- Хостинг: Replit (один процесс, один порт)
- Монорепо: pnpm workspace

**Почему мигрируем:**  
MPA с полными перезагрузками страниц ощущается устаревшим. Хотим SPA с чистыми URL, без `.html` в адресной строке, с реактивным UI.

---

## Целевая архитектура

```
[ PostgreSQL ]
      │
[ Flask (Python) ]
  ├── /api/*          ← JSON REST API (все данные)
  ├── /dashboard/*    ← статические HTML дашборды (без изменений)
  └── /*              ← отдаёт React index.html (SPA)

[ React (Vite + TypeScript) ]
  └── osv-dashboard/frontend/
        dist/ → Flask раздаёт как static files
```

**Ключевое решение:** один сервер (Flask), React собирается в папку внутри Flask-проекта и раздаётся им же. Не нужно настраивать прокси, CORS, дополнительные порты или workflow.

---

## Что НЕ меняется (принципиально)

| Компонент | Статус |
|---|---|
| `osvparser.py` — парсинг XLS/ZIP файлов | ❌ не трогаем |
| `builder.py` — построение JSON-датасета дашборда | ❌ не трогаем |
| `database.py` — модели SQLAlchemy (User, Company, Upload, DailyRow, Setting, EmailRule) | ❌ не трогаем |
| IMAP-логика (фоновый опрос почты) | ❌ не трогаем |
| Генерация дашбордов (HTML-файлы в `output/<company_id>/`) | ❌ не трогаем |
| Схема PostgreSQL | ❌ не трогаем |
| Ролевая модель (admin/user) | ❌ не трогаем |

---

## ЗАДАЧА 1: Flask → JSON API

### Суть изменений

Flask перестаёт рендерить HTML. Каждый маршрут возвращает JSON. Все маршруты получают префикс `/api/`.

### Таблица переименования маршрутов

| Старый URL | Метод | Новый URL | Ответ |
|---|---|---|---|
| `/login` | GET/POST | `/api/auth/login` | `{ok, user: {username, role}}` |
| `/logout` | GET | `/api/auth/logout` | `{ok: true}` |
| — | GET | `/api/auth/me` | `{username, role}` или 401 |
| `/` | GET | `/api/companies` | `[{id, name, last_period, files_total, closure, profit_sign, last_upload, has_dashboard}]` |
| `/company/new` | POST | `/api/companies` | `{ok, company_id, audit, period_label, dashboard_url, error?}` |
| `/company/<id>` | GET | `/api/companies/<id>` | `{company, uploads, annual_coverage}` |
| `/company/<id>/upload-all` | POST | `/api/companies/<id>/upload` | `{ok, audit, period_label, dashboard_url, error?}` |
| `/company/<id>/upload-zip` | POST | `/api/companies/<id>/upload-zip` | `{ok, rows_loaded, error?}` |
| `/company/<id>/rename` | POST | `/api/companies/<id>/rename` | `{ok}` |
| `/company/<id>/delete` | POST | `/api/companies/<id>` (DELETE) | `{ok}` |
| `/settings` | GET/POST | `/api/settings` | `{imap_cfg, rules, companies}` |
| `/settings/test-imap` | POST | `/api/settings/test-imap` | `{ok, msg}` |
| `/settings/sync-imap` | POST | `/api/settings/sync-imap` | `{ok, msg}` |
| `/users` | GET | `/api/users` | `[{id, username, role, created_at}]` |
| `/users/create` | POST | `/api/users` | `{ok, user}` или `{error}` |
| `/users/<id>/delete` | POST | `/api/users/<id>` (DELETE) | `{ok}` |
| `/users/<id>/set-password` | POST | `/api/users/<id>/password` | `{ok}` |
| `/users/<id>/set-role` | POST | `/api/users/<id>/role` | `{ok}` |
| `/dashboard/<id>/<name>` | GET | `/dashboard/<id>/<name>` | HTML-файл (БЕЗ ИЗМЕНЕНИЙ) |

### Авторизация в API

Оставляем Flask-Login с сессионными cookie. Это работает для SPA на том же домене без CORS. Альтернатива (JWT) сложнее и не нужна для внутреннего приложения.

- `POST /api/auth/login` — устанавливает сессионный cookie
- `GET /api/auth/me` — возвращает текущего пользователя или 401
- `GET /api/auth/logout` — сбрасывает сессию

### Обработка ошибок

Все ошибки возвращаются как JSON:
```json
{"error": "Неверный логин или пароль"}   // 401
{"error": "Доступ запрещён"}              // 403
{"error": "Не найдено"}                   // 404
{"error": "Внутренняя ошибка сервера"}   // 500
```

### Загрузка файлов

Маршруты загрузки (`/api/companies` POST, `/api/companies/<id>/upload` POST) принимают `multipart/form-data` как и раньше. Возвращают JSON с результатом вместо `render_template`.

### Catch-all для React

Последний маршрут Flask:
```python
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_react(path):
    dist = os.path.join(BASE, 'frontend', 'dist')
    # если запрашивается статический ассет — отдаём его
    file_path = os.path.join(dist, path)
    if path and os.path.exists(file_path):
        return send_from_directory(dist, path)
    # иначе — SPA index.html
    index = os.path.join(dist, 'index.html')
    if os.path.exists(index):
        return send_from_directory(dist, 'index.html')
    return 'Frontend not built', 503
```

---

## ЗАДАЧА 2: React SPA фронтенд

### Структура проекта

```
osv-dashboard/
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    └── src/
        ├── main.tsx
        ├── App.tsx              ← роутер
        ├── api/                 ← функции для fetch к /api/*
        │   ├── auth.ts
        │   ├── companies.ts
        │   ├── settings.ts
        │   └── users.ts
        ├── context/
        │   └── AuthContext.tsx  ← текущий пользователь, роль
        ├── components/
        │   ├── AppLayout.tsx    ← шапка, навигация
        │   ├── ProtectedRoute.tsx
        │   └── AdminRoute.tsx
        └── pages/
            ├── LoginPage.tsx
            ├── CompaniesPage.tsx    (/)
            ├── CompanyPage.tsx      (/company/:id)
            ├── NewCompanyPage.tsx   (/company/new)
            ├── SettingsPage.tsx     (/settings)
            └── UsersPage.tsx        (/users)
```

### Зависимости (npm)

| Пакет | Назначение |
|---|---|
| `react-router-dom` v6 | Клиентская маршрутизация, чистые URL |
| `@tanstack/react-query` | Кэширование запросов к API, состояния загрузки |
| `axios` | HTTP-клиент (или нативный fetch) |

Намеренно без UI-библиотек (MUI, Ant Design) — переносим существующий CSS вручную, чтобы сохранить визуальный стиль.

### Маршрутизация React Router

```
/login                → LoginPage (публичный)
/                     → CompaniesPage (protected)
/company/new          → NewCompanyPage (protected)
/company/:id          → CompanyPage (protected)
/settings             → SettingsPage (admin only)
/users                → UsersPage (admin only)
```

### AuthContext

```tsx
// Что хранит:
{
  user: { username: string, role: 'admin' | 'user' } | null,
  isAdmin: boolean,
  isLoading: boolean,
  login(username, password): Promise<void>,
  logout(): Promise<void>,
}
```

При старте приложения делает `GET /api/auth/me`. Если 401 — пользователь не авторизован, редирект на `/login`.

### Страницы — что делает каждая

**LoginPage (`/login`)**
- Форма: логин + пароль
- `POST /api/auth/login`
- При успехе → редирект на `/`
- При ошибке → сообщение под полем

**CompaniesPage (`/`)**
- `GET /api/companies`
- Таблица предприятий (колонки: название, период, загрузок, закрытие, результат, действие)
- Кнопка «+ Добавить предприятие» → `/company/new`
- Клик по строке → `/company/:id`

**NewCompanyPage (`/company/new`)**
- Drag-and-drop зона для XLS/XLSX файлов
- Если API возвращает `ask_name: true` — показать поле «Название предприятия»
- После успеха → показать протокол обработки, кнопка «Открыть дашборд»

**CompanyPage (`/company/:id`)**
- `GET /api/companies/:id`
- Название + кнопки «Переименовать» / «Удалить»
- Таблица/аккордеон истории загрузок (период, дата, счета, закрытие, прибыль)
- Кнопка «Открыть дашборд» → `window.open('/dashboard/:id/dashboard_current.html')`
- Форма загрузки новых файлов (XLS или ZIP)
- Отображение расхождений (discrepancy) если есть

**SettingsPage (`/settings`)** ← только admin
- IMAP-настройки: хост, порт, логин, пароль, папка, интервал
- Кнопки «Тестировать» и «Синхронизировать» (AJAX)
- Таблица правил маршрутизации + форма добавления правила

**UsersPage (`/users`)** ← только admin
- Таблица пользователей: логин, роль, дата создания
- Смена пароля (inline)
- Повышение/понижение роли
- Удаление пользователя
- Форма создания нового пользователя

### Визуальный стиль

Переносим CSS-переменные и классы из существующих шаблонов:
```css
:root {
  --ink: #1b2733;
  --mut: #6b7a86;
  --paper: #e9edf0;
  --panel: #fff;
  --line: #d3dade;
  --ok: #2e7d4f;
  --bad: #bf3222;
  --water: #1f7a9c;
  --warn: #c98a12;
}
```

Шапка, таблицы, карточки, кнопки — переносятся как React-компоненты с тем же CSS.

### Сборка и интеграция

`vite.config.ts`:
```ts
export default {
  base: '/',
  build: {
    outDir: '../frontend/dist',   // Flask подхватывает автоматически
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',  // dev-режим: проксируем к Flask
      '/dashboard': 'http://localhost:8080',
    }
  }
}
```

Команда workflow остаётся одна:
```bash
cd /home/runner/workspace/osv-dashboard && \
  (cd frontend && npm install && npm run build) && \
  python app.py
```

---

## Риски и спорные моменты

| Риск | Комментарий |
|---|---|
| Сессионная авторизация vs JWT | Выбрана сессия — проще, работает для same-origin. JWT нужен если фронт и бэк на разных доменах. |
| Нет CSRF-защиты на API | При сессионных cookie нужен CSRF-токен на POST-запросах. Можно добавить эндпоинт `/api/auth/csrf` и отправлять токен в заголовке. |
| Двухэтапное создание предприятия | Текущая логика с `tmp_key` (сначала загружаем файлы, потом подтверждаем название) нетривиально переносится в React. Возможно упростить. |
| Время сборки в workflow | `npm install && npm run build` добавит 30–60 секунд к старту. Можно пре-собирать и коммитить `dist/`. |
| Drag-and-drop + большие файлы | Нужно добавить индикатор прогресса загрузки (сейчас его нет). |

---

## Объём работы (оценка)

| Задача | Оценка |
|---|---|
| Flask → JSON API (задача #4) | 4–6 часов кода, мало рисков |
| React SPA (задача #5) | 15–25 часов кода, основной объём |
| Итого | 2–4 дня при выделенном времени |

**Самая трудоёмкая часть:** CompanyPage со всеми состояниями (история загрузок, двухэтапная загрузка файлов, расхождения, годовое покрытие).

---

## Альтернативы, которые мы отклонили

**HTMX поверх Flask** — убирает перезагрузки с минимальными усилиями, но не даёт чистых URL и React-экосистемы. Хорошо для небольших улучшений, не для долгосрочного развития.

**FastAPI + React** — FastAPI лучше Flask для нового проекта, но миграция бизнес-логики с Flask на FastAPI добавила бы ещё неделю работы без видимой пользы.

**Next.js fullstack** — избыточно для внутреннего инструмента, сложнее деплоить на Replit.
