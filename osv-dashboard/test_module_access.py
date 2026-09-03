# -*- coding: utf-8 -*-
"""Регрессии каталога модулей и серверной авторизации."""
import os
import sys
import tempfile
import re

_TMP = tempfile.mkdtemp(prefix='module_access_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

r = client.post(
    f"/users/{ids['none']}/set-companies",
    data={
        'companies': [str(company_id)],
        'primary_company_id': str(company_id),
        'participation_type': 'curator',
    })
check('назначение предприятия сохраняется из мультиселекта',
      r.status_code == 302)
with app.app_context():
    membership = CompanyMembership.query.filter_by(
        user_id=ids['none'], company_id=company_id).one()
    check('основное предприятие и тип участия сохранены',
          membership.is_primary and membership.participation_type == 'curator')

r = client.post('/users/create', data={
    'username': 'created-with-company',
    'password': 'pass1234',
    'role': 'user',
    'modules': ['documents'],
    'companies': [str(company_id)],
})
check('при создании можно назначить предприятие',
      r.status_code == 302)
with app.app_context():
    created = User.query.filter_by(username='created-with-company').one()
    check('предприятие назначено новому пользователю',
          CompanyMembership.query.filter_by(
              user_id=created.id, company_id=company_id).count() == 1)

r = client.get('/modules')
body = r.get_data(as_text=True)
check('администратор видит оба модуля',
      r.status_code == 200
      and 'Аналитика ФХД РСО' in body
      and 'Проверка документов' in body)
check('администратор открывает аналитику', client.get('/home').status_code == 200)
check('администратор открывает документы', client.get('/docs/').status_code == 200)

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