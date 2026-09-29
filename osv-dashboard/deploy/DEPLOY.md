# Автовосстановление зависшего HTTP worker

Gunicorn работает строго с одним worker, потому что IMAP и фоновые обработчики
живут внутри worker-процесса. Master-процесс каждые 15 секунд запрашивает
`/healthz` с тайм-аутом 3 секунды. Endpoint не обращается к БД/App Storage и не
запускает IMAP.

Если процесс существует и порт открыт, но все HTTP-потоки зависли, probe
завершается по тайм-ауту. Master отправляет единственному worker `SIGABRT`
(штатный сигнал Gunicorn для зависшего worker);
Gunicorn удаляет его вместе с фоновыми потоками и только затем поднимает замену.
При неожиданном количестве worker автоматический kill блокируется, чтобы не
создать параллельные IMAP/фоновые обработчики.

Проверка сценария:

```sh
python -m unittest test_health_watchdog.py
```

Параметры можно настроить переменными `HEALTH_WATCHDOG_INTERVAL`,
`HEALTH_WATCHDOG_TIMEOUT`, `HEALTH_WATCHDOG_STARTUP_GRACE`.

# Развёртывание ОСВ Дашборда на собственном сервере

## Закрытый перенос настроек MAX

На странице «Лимиты → Бот MAX» администратор сохраняет **новый** токен и ID
группы, затем отдельно нажимает «Запустить бота MAX». Токен, ранее попавший в
отслеживаемую `.replit`, следует **перевыпустить у MAX**: удаление строки из
текущей версии не очищает историю Git и не отзывает токен у MAX. Не копируйте
его в новую форму. Для опубликованного бота настройте
`MAX_LIMITS_WEBHOOK_URL` как публичный HTTPS адрес
`https://ваш-домен/osv/limit-notifications/max/webhook`, если приложение
работает с `SCRIPT_NAME=/osv` (или без `/osv`, если работает в корне домена);
без него запуск в production заблокирован.

Единственный источник реквизитов и намерения запуска — серверный файл
`osv-dashboard/private/max-bot.json` (каталог `0700`, файл `0600`). Файл
исключён из Git и Docker-сборки; браузер, Excel и обычный экспорт данных его
не получают. Закрытый **полный** архив рабочего проекта должен включать файл
явно: обычный Git clone, публичная сборка и экспорты приложения его не
переносят. Если средство экспорта исключает файлы из `.gitignore`, передайте
файл отдельно в зашифрованном архиве по защищённому каналу, например:

```sh
tar -C /путь/к/проекту -cf - osv-dashboard/private/max-bot.json | \
    gpg --symmetric --cipher-algo AES256 -o max-bot-private.tar.gpg
# На новой установке, после переноса закрытого архива:
gpg --decrypt max-bot-private.tar.gpg | tar -C /путь/к/проекту -xf -
chmod 700 /путь/к/проекту/osv-dashboard/private
chmod 600 /путь/к/проекту/osv-dashboard/private/max-bot.json
```

Передавайте пароль архива отдельным каналом, не кладите архив в репозиторий,
статику или публичный образ. Закрытый экспорт полного проекта храните только
в месте с контролируемым доступом. В среде с эфемерной файловой системой
нужно монтировать постоянный закрытый каталог `osv-dashboard/private` и
восстанавливать его **до запуска** приложения; публикация одного только
исходного кода секрет не переносит. После импорта проверьте статус на странице
бота: сохранённое намерение запуска не считается подтверждённым соединением
до повторной проверки MAX. Не включайте этот файл в резервные копии данных
приложения или доступные пользователям выгрузки.

Приложение разворачивается по адресу `https://домен.ru/osv/`.  
ОС: Ubuntu 22.04 / Debian 12. Python 3.11+.

---

## 1. Подготовка сервера

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip \
    postgresql postgresql-contrib nginx
```

---

## 2. База данных PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
CREATE USER осв_user WITH PASSWORD 'ПРИДУМАЙТЕ_ПАРОЛЬ';
CREATE DATABASE осв_db OWNER осв_user ENCODING 'UTF8';
SQL
```

---

## 3. Копирование файлов

```bash
sudo mkdir -p /opt/osv-dashboard
sudo cp -r . /opt/osv-dashboard/
sudo chown -R www-data:www-data /opt/osv-dashboard
```

---

## 4. Python-окружение и зависимости

```bash
cd /opt/osv-dashboard
sudo -u www-data python3.11 -m venv venv
sudo -u www-data venv/bin/pip install --upgrade pip
sudo -u www-data venv/bin/pip install -r requirements.txt
```

---

## 5. Создание таблиц (первый запуск)

```bash
cd /opt/osv-dashboard
sudo -u www-data env \
    DATABASE_URL="postgresql://осв_user:ПАРОЛЬ@localhost:5432/осв_db" \
    SESSION_SECRET="любая-длинная-строка" \
    INITIAL_ADMIN_PASSWORD="секретная фраза не менее 15 Unicode-символов" \
    SCRIPT_NAME="/osv" \
    venv/bin/python - <<'PY'
from app import app, db, init_db
with app.app_context():
    db.create_all()
    init_db()
PY
```

В чистой базе будет создан пользователь **admin** с заданным
`INITIAL_ADMIN_PASSWORD`. Вход сразу переводится на обязательную смену
пароля; секрет не выводится приложением и не записывается в журнал.

---

## 6. Логи

```bash
sudo mkdir -p /var/log/osv-dashboard
sudo chown www-data:www-data /var/log/osv-dashboard
```

---

## 7. systemd-сервис

```bash
sudo cp deploy/osv-dashboard.service /etc/systemd/system/
# Отредактируйте файл: вставьте реальные DATABASE_URL, SESSION_SECRET,
# MAX_LIMITS_WEBHOOK_URL и настройте домен
sudo nano /etc/systemd/system/osv-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable osv-dashboard
sudo systemctl start osv-dashboard
sudo systemctl status osv-dashboard   # убедитесь, что active (running)
```

---

## 8. nginx

```bash
# Добавьте содержимое deploy/nginx.conf в ваш server{}-блок
sudo nano /etc/nginx/sites-available/ваш-домен.conf

sudo nginx -t          # проверка синтаксиса
sudo systemctl reload nginx
```

После этого приложение доступно по адресу `https://домен.ru/osv/`.

---

## 9. Смена пароля admin

Для существующей учётной записи используйте страницу «Сменить пароль».
Администраторский сброс на странице «Пользователи» помечает пароль как
временный и также требует смену при следующем входе. Не записывайте пароль
или его хэш в команды shell/history.

---

## 10. HTTPS (Let's Encrypt)

Если ещё не настроен:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d домен.ru -d www.домен.ru
```

---

## Проверка работы

```bash
curl -I https://домен.ru/osv/
# Ожидается: HTTP/2 200 или 302 (редирект на /osv/login)
```

---

## Обновление приложения

```bash
cd /opt/osv-dashboard
sudo -u www-data git pull          # если используете git
sudo -u www-data venv/bin/pip install -r requirements.txt
sudo systemctl restart osv-dashboard
```
