# -*- coding: utf-8 -*-
"""Регрессии каталога модулей и серверной авторизации."""
import os
import sys
import tempfile
import re
import threading
import time

_TMP = tempfile.mkdtemp(prefix='module_access_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from app import app, init_db
from database import Company, CompanyMembership, User, UserModuleAccess, db
from module_registry import MODULES, module_key_for_path

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def login_as(client, user):
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True


def check(label, condition, detail=''):
    print(('PASS' if condition else 'FAIL'), label, detail)
    if not condition:
        raise AssertionError(label)


with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    no_access = User(username='none', password_hash='unused', role='user')
    analytics = User(username='analytics', password_hash='unused', role='user')
    both = User(username='both', password_hash='unused', role='user')
    db.session.add_all([no_access, analytics, both])
    db.session.flush()
    db.session.add(UserModuleAccess(user_id=analytics.id, module_key='analytics'))
    db.session.add_all([
        UserModuleAccess(user_id=both.id, module_key='analytics'),
        UserModuleAccess(user_id=both.id, module_key='documents'),
    ])
    company = Company(
        name='Тестовое предприятие', created_by=admin.id, status='active')
    db.session.add(company)
    db.session.commit()
    ids = {u.username: u.id for u in (admin, no_access, analytics, both)}
    company_id = company.id

client = app.test_client()

# Новый модуль одной записью регистрирует и карточку, и серверную область.
MODULES['future'] = {
    'key': 'future', 'name': 'Будущий модуль', 'description': '',
    'icon': 'documents', 'endpoint': 'modules_dashboard',
    'exact_paths': ('/future',), 'path_prefixes': ('/future-api/',),
}
check('реестр определяет HTML будущего модуля',
      module_key_for_path('/future') == 'future')
check('реестр определяет API будущего модуля',
      module_key_for_path('/future-api/items') == 'future')
del MODULES['future']

# Успешный и повторный вход ведут в каталог, а не внутрь аналитики.
app.config['WTF_CSRF_ENABLED'] = True
login_page = client.get('/login').get_data(as_text=True)
login_token = re.search(
    r'name="login_csrf_token" value="([^"]+)"', login_page).group(1)
cross_origin = client.post('/login', data={
    'username': 'admin', 'password': 'admin123',
    'login_csrf_token': login_token,
}, headers={
    'Origin': 'https://evil.example',
    'X-Forwarded-Host': 'preview.example',
})
check('чужой Origin не может выполнить login-CSRF',
      cross_origin.status_code == 403)
r = client.post('/login', data={
    'username': 'admin', 'password': 'admin123',
    'login_csrf_token': login_token,
}, headers={
    'Origin': 'https://preview.example',
    'X-Forwarded-Host': 'preview.example',
})
check('успешный вход ведёт в каталог модулей',
      r.status_code == 302 and r.headers['Location'].endswith('/modules'))
app.config['WTF_CSRF_ENABLED'] = False
r = client.get('/')
check('повторное открытие ведёт в каталог модулей',
      r.status_code == 302 and r.headers['Location'].endswith('/modules'))

# Администратор автоматически получает текущие модули без строк назначений.
with app.app_context():
    admin = db.session.get(User, ids['admin'])
login_as(client, admin)
users_page = client.get('/users')
users_body = users_page.get_data(as_text=True)
check('страница пользователей использует всю ширину',
      users_page.status_code == 200
      and 'main{width:100%;max-width:none' in users_body)
check('модули и предприятия выбираются во всплывающих списках',
      users_body.count('class="multi-picker"') >= 4
      and 'aria-label="Доступ к модулям"' in users_body
      and 'aria-label="Доступ к предприятиям"' in users_body)
check('в управлении доступом нет основного предприятия и типа участия',
      'name="primary_company_id"' not in users_body
      and 'name="participation_type"' not in users_body)

r = client.post(
    f"/users/{ids['none']}/set-companies",
    data={
        'companies': [str(company_id)],
    })
check('назначение предприятия сохраняется из мультиселекта',
      r.status_code == 302)
with app.app_context():
    membership = CompanyMembership.query.filter_by(
        user_id=ids['none'], company_id=company_id).one()
    check('новое назначение получает нейтральные параметры',
          not membership.is_primary and membership.participation_type == 'employee')

backup_started = threading.Event()
backup_release = threading.Event()
original_durable_backup = app_module._durable_backup


def blocking_durable_backup(*_args, **_kwargs):
    backup_started.set()
    backup_release.wait(2)


app_module._durable_backup = blocking_durable_backup
app.config['DURABLE_BACKUP_ASYNC_TEST'] = True
started_at = time.monotonic()
r = client.post('/users/create', data={
    'username': 'created-with-company',
    'password': 'pass1234',
    'role': 'user',
    'modules': ['documents'],
    'companies': [str(company_id)],
})
response_elapsed = time.monotonic() - started_at
check('резервный снимок не задерживает создание пользователя',
      r.status_code == 303 and response_elapsed < 0.5
      and backup_started.wait(1))
backup_thread = app_module._durable_safety_thread
backup_release.set()
if backup_thread:
    backup_thread.join(timeout=2)
app.config.pop('DURABLE_BACKUP_ASYNC_TEST', None)
app_module._durable_backup = original_durable_backup
check('при создании можно назначить предприятие',
      r.status_code == 303)
with app.app_context():
    created = User.query.filter_by(username='created-with-company').one()
    check('предприятие назначено новому пользователю',
          CompanyMembership.query.filter_by(
              user_id=created.id, company_id=company_id).count() == 1)

users_body = client.get('/users').get_data(as_text=True)
check('создание пользователя вынесено в модальное окно',
      'id="open-create-user"' in users_body
      and 'Добавить пользователя' in users_body
      and 'id="create-user-modal"' in users_body
      and 'class="add-user-grid"' not in users_body
      and 'Создать пользователя' in users_body)
check('после создания показывается подтверждение',
      'Пользователь «created-with-company» создан.' in
      client.get('/users?created=created-with-company').get_data(as_text=True))

# Ошибка создания снова открывает модальное окно и не теряет логин.
r = client.post('/users/create', data={
    'username': 'created-with-company',
    'password': 'pass1234',
    'role': 'user',
})
error_body = r.get_data(as_text=True)
check('ошибка создания сохраняет модальное окно',
      r.status_code == 200
      and 'aria-hidden="false"' in error_body
      and 'value="created-with-company"' in error_body
      and '<div class="alert alert-error" role="alert">' in error_body)

r = client.get('/modules')
body = r.get_data(as_text=True)
check('администратор видит оба модуля',
      r.status_code == 200
      and 'Аналитика ФХД РСО' in body
      and 'Проверка документов' in body)
check('администратор открывает аналитику', client.get('/home').status_code == 200)
check('администратор открывает документы', client.get('/docs/').status_code == 200)
new_modules = ['analog_search', 'limit_notifications', 'investment_reconciliation']
for key in new_modules:
    module = MODULES[key]
    path = module['exact_paths'][0]
    start = client.get(path)
    check(f'карточка и стартовая страница: {key}',
          module['name'] in body and start.status_code == 200
          and module['name'] in start.get_data(as_text=True)
          and 'пока не подключены' in start.get_data(as_text=True)
          and 'href="/modules"' in start.get_data(as_text=True))

# Пустое состояние не выдаёт права неявно.
with app.app_context():
    no_access = db.session.get(User, ids['none'])
login_as(client, no_access)
r = client.get('/modules')
check('пользователь без назначений видит пустое состояние',
      r.status_code == 200 and 'Нет доступных модулей' in r.get_data(as_text=True))
check('прямой HTML URL аналитики закрыт', client.get('/home').status_code == 403)
check('прямой API URL документов закрыт',
      client.get('/docs/packages').status_code == 403)
for key in new_modules:
    check(f'неназначенный модуль закрыт: {key}',
          client.get(MODULES[key]['exact_paths'][0]).status_code == 403)

# Одно назначение открывает только свой модуль.
with app.app_context():
    analytics = db.session.get(User, ids['analytics'])
login_as(client, analytics)
r = client.get('/modules')
body = r.get_data(as_text=True)
check('виден только назначенный модуль',
      'Аналитика ФХД РСО' in body and 'Проверка документов' not in body)
check('назначенная аналитика открывается', client.get('/home').status_code == 200)
check('неназначенные документы закрыты', client.get('/docs/').status_code == 403)

# Администратор назначает и отзывает доступ отдельной операцией.
with app.app_context():
    admin = db.session.get(User, ids['admin'])
login_as(client, admin)
r = client.post(
    f"/users/{ids['none']}/set-modules",
    data={'modules': ['documents']})
check('назначение сохранено', r.status_code == 302)
with app.app_context():
    user = db.session.get(User, ids['none'])
    check('назначен один модуль', user.assigned_module_keys == {'documents'})

r = client.post(f"/users/{ids['none']}/set-modules", data={})
check('отзыв сохранён', r.status_code == 302)
with app.app_context():
    user = db.session.get(User, ids['none'])
    check('назначения удалены', not user.assigned_module_keys)

# Смена роли не уничтожает ручные назначения.
r = client.post(
    f"/users/{ids['analytics']}/set-role", data={'role': 'admin'})
check('повышение роли выполнено', r.status_code == 302)
r = client.post(
    f"/users/{ids['analytics']}/set-role", data={'role': 'user'})
check('понижение роли выполнено', r.status_code == 302)
with app.app_context():
    user = db.session.get(User, ids['analytics'])
    check('назначение пережило смену роли',
          user.role == 'user' and user.assigned_module_keys == {'analytics'})

print('\nВСЕ ТЕСТЫ ДОСТУПА К МОДУЛЯМ ПРОЙДЕНЫ')