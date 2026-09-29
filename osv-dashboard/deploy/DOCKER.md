# Развёртывание через Docker / Podman + Apache

Приложение поднимается двумя контейнерами (PostgreSQL + web) и проксируется Apache на подпуть `/osv`.

ОС: Ubuntu 22.04 / Debian 12.  
Порт хоста: **4545**. URL: `https://домен.ru/osv/`.

---

## Требования

- Docker ≥ 24 **или** Podman ≥ 4 + `podman-compose`
- Apache2 с модулями `proxy proxy_http headers rewrite`
- Открытый порт 4545 (только localhost — наружу не выставлять)

---

## 1. Получить код

```bash
git clone <url-репозитория> /opt/osv-dashboard
cd /opt/osv-dashboard/osv-dashboard
```

---

## 2. Создать `.env`

```bash
cp .env.example .env
nano .env
```

Обязательно заменить:

| Переменная | Что поставить |
|---|---|
| `POSTGRES_PASSWORD` | Сложный пароль БД |
| `SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `INITIAL_ADMIN_PASSWORD` | Секретная фраза не менее 15 Unicode-символов (только для чистой БД) |
| `MAX_LIMITS_WEBHOOK_URL` | Публичный HTTPS-адрес `https://ваш-домен/osv/limit-notifications/max/webhook` для бота MAX |

В Docker-развёртывании webhook обязателен: без корректного URL кнопка
«Запустить бота MAX» не запускает подключение.

Остальные значения можно оставить по умолчанию. Настройки MAX сохраняются на
странице «Лимиты → Бот MAX» в закрытом файле внутри named volume
`max_bot_private`; том создаётся при первом запуске с владельцем процесса
приложения (UID 10001) и правами каталога `0700`, а сам файл имеет права
`0600`. В образ и Git токен не включается. Перед использованием перевыпустите
ранее опубликованный в `.replit` токен у MAX.

---

## 3. Собрать и запустить

```bash
# Docker Compose
docker compose up -d --build

# Podman Compose
podman-compose up -d --build
```

Проверить, что оба контейнера живы:

```bash
docker compose ps
# или
podman ps
```

Прямая проверка приложения (без Apache):

```bash
curl -I http://127.0.0.1:4545/osv/login
# Ожидается: HTTP/1.1 200 OK
```

---

## 4. Apache — подключить конфиг

```bash
# Включить нужные модули (один раз)
sudo a2enmod proxy proxy_http headers rewrite

# Вставить содержимое deploy/apache-osv.conf в ваш VirtualHost
# (внутрь блока <VirtualHost *:443>)
sudo nano /etc/apache2/sites-available/ваш-домен.conf

sudo apache2ctl configtest   # проверка синтаксиса
sudo systemctl reload apache2
```

Содержимое для вставки — в файле `deploy/apache-osv.conf`.

---

## 5. Первый вход

Откройте `https://домен.ru/osv/login`

Логин: **admin**. Пароль берётся из `INITIAL_ADMIN_PASSWORD` только при
первом создании базы, не печатается приложением и не попадает в логи.
После входа система требует задать новый пароль через страницу смены пароля.

---

## 6. Обновление приложения

```bash
cd /opt/osv-dashboard

git pull

cd osv-dashboard
docker compose up -d --build
```

Данные (БД, загруженные файлы и закрытые настройки MAX) хранятся в named
volumes и **не удаляются** при пересборке образа. Не запускайте
`docker compose down -v`: это удаляет все тома, включая закрытый файл бота.
После обновления проверьте статус на странице «Бот MAX» — подключение
подтверждается повторно через MAX, а не по старому имени пользователя.
Для переезда в другую установку перенесите этот том закрытым архивом по
инструкции в `deploy/DEPLOY.md`; одного `git clone` недостаточно.

---

## 7. Podman без docker-compose (вручную)

Если `podman-compose` недоступен — запуск вручную (как делал девопс):

```bash
# БД
sudo podman run -d \
  --name app-db \
  --restart unless-stopped \
  -p 5433:5432 \
  -e POSTGRES_USER=osv_user \
  -e POSTGRES_PASSWORD=<ПАРОЛЬ> \
  -e POSTGRES_DB=osv_db \
  -v postgres_data:/var/lib/postgresql/data \
  postgres:16-alpine

# Подождать ~5 сек, затем запустить приложение
sudo podman run -d \
  --name app-web \
  --restart unless-stopped \
  --network host \
  -e DATABASE_URL=postgresql://osv_user:<ПАРОЛЬ>@localhost:5433/osv_db \
  -e SESSION_SECRET=<СЕКРЕТ> \
   -e INITIAL_ADMIN_PASSWORD=<СЕКРЕТНАЯ_ФРАЗА_НЕ_МЕНЕЕ_15_СИМВОЛОВ> \
  -e PORT=4545 \
  -e SCRIPT_NAME=/osv \
  -e MAX_LIMITS_WEBHOOK_URL=https://ваш-домен/osv/limit-notifications/max/webhook \
  -e MAX_LIMITS_REQUIRE_WEBHOOK=1 \
  -v uploads_data:/app/uploads \
  -v output_data:/app/output \
  -v max_bot_private:/app/private \
  localhost/osv-dashboard-web:latest
```

Собрать образ перед этим:

```bash
sudo podman build -t osv-dashboard-web .
```

---

## 8. Автозапуск контейнеров (Podman + systemd)

Чтобы контейнеры поднимались после перезагрузки:

```bash
sudo podman generate systemd --new --files --name app-db
sudo podman generate systemd --new --files --name app-web
sudo systemctl daemon-reload
sudo systemctl enable --now container-app-db.service
sudo systemctl enable --now container-app-web.service
```

Проверка после reconnect: `sudo podman ps`

---

## Диагностика

```bash
# Логи приложения
docker compose logs -f web

# Логи БД
docker compose logs -f db

# Зайти в контейнер
docker compose exec web sh
```
