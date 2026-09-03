# -*- coding: utf-8 -*-
"""Регрессии универсального справочника предприятий и DaData."""
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix='company_directory_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, encrypt_value, init_db
from database import (AiSummary, AuditLog, Company, CompanyContact,
                      CompanyDadataSnapshot, CompanyMembership, CompanyOkved,
                      DailyRow, EmailRule, Setting, Upload, User,
                      UserModuleAccess, db)
from dadata_client import DaDataError, find_party

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def check(label, condition):
    print(('PASS' if condition else 'FAIL'), label)
    if not condition:
        raise AssertionError(label)


def login_as(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


party = {
    'value': 'ООО "Тестовая организация"',
    'data': {
        'inn': '7701234567', 'kpp': '770101001', 'ogrn': '1027700000000',
        'ogrn_date': 1028073600000, 'registration_date': 1028073600000,
        'actuality_date': 1788134400000,
        'name': {
            'short_with_opf': 'ООО "Тест"',
            'full_with_opf': 'ООО "Тестовая организация"',
        },
        'opf': {'short': 'ООО'},
        'state': {
            'status': 'ACTIVE',
            'registration_date': 1028073600000,
            'actuality_date': 1788134400000,
        },
        'branch_type': 'MAIN',
        'address': {
            'value': 'г Москва, ул Тестовая, д 1',
            'unrestricted_value': 'г Москва, ул Тестовая, д 1',
            'data': {'fias_id': 'fias-1', 'qc': '0', 'postal_code': '101000'},
        },
        'management': {'name': 'Иванов Иван Иванович', 'post': 'Директор'},
        'okved': '35.30',
        'okveds': [{'code': '36.00', 'name': 'Забор и очистка воды'}],
        'licenses': [{'number': 'Л-1', 'activities': ['Теплоснабжение'],
                      'status': 'ACTIVE'}],
        'finance': {'year': 2025, 'revenue': 1000, 'expense': 900, 'profit': 100},
        'finance_history': [{
            'year': 2024,
            'metrics': [
                {'code': '2110', 'value': 800},
                {'code': '2500', 'value': 75},
            ],
        }],
        'phones': [{
            'value': '+7 495 123-45-67',
            'unrestricted_value': '+7 495 123-45-67',
            'data': {'type': 'Стационарный', 'contact': 'Приёмная'},
        }],
        'emails': [{'value': 'INFO@EXAMPLE.RU'}],
        'sites': [{'value': 'example.ru'}],
        'founders': [{'name': 'Учредитель', 'share': {'value': 10000}}],
        'managers': [{
            'post': 'Директор',
            'type': 'EMPLOYEE',
            'inn': '770000000001',
            'fio': {'source': 'Иванов Иван Иванович'},
        }],
        'authorities': {
            'fts_registration': {
                'code': '7701', 'name': 'Инспекция ФНС',
            },
            'pf': {
                'code': '087-001', 'name': 'Отделение Пенсионного фонда',
            },
        },
    },
}

with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    user = User(username='reader', password_hash='unused', role='user')
    db.session.add(user)
    db.session.flush()
    db.session.add(UserModuleAccess(user_id=user.id, module_key='directories'))
    Setting.set('dadata_enabled', '1')
    Setting.set('dadata_api_token', encrypt_value('secret-dadata-token'))
    db.session.commit()
    admin_id, user_id = admin.id, user.id

client = app.test_client()
login_as(client, admin_id)

# Предприятие создаётся без ОСВ и получает прежний канонический id.
r = client.post('/directories/companies/new', data={
    'action': 'create', 'name': 'Внутреннее имя', 'inn': '7701234567',
    'kpp': '770101001', 'ogrn': '1027700000000', 'is_rso': '1',
    'director_phone': '+7 000 000-00-00',
})
check('ручное создание без файлов ОСВ', r.status_code == 302)
with app.app_context():
    company = Company.query.filter_by(name='Внутреннее имя').one()
    company.director_phone = '+7 000 000-00-00'
    company.classifications_json = '{"sector":"тепло"}'
    db.session.add(CompanyMembership(
        user_id=user_id, company_id=company.id,
        participation_type='auditor', is_primary=True))
    db.session.commit()
    company_id = company.id

# Дубликат с тем же ИНН/КПП не сохраняется незаметно.
r = client.post('/directories/companies/new', data={
    'action': 'create', 'name': 'Дубликат', 'inn': '7701234567',
    'kpp': '770101001', 'ogrn': '1027700000000',
})
check('конфликт дубля показан пользователю',
      r.status_code == 200 and 'конфликтующая запись' in r.get_data(as_text=True))
with app.app_context():
    check('дубликат не создан', Company.query.filter_by(name='Дубликат').count() == 0)

# Получение снимка не применяет изменения до явного принятия.
with patch('dadata_client.find_party', return_value=(party, __import__(
        'directory_service').normalize_party(party))):
    r = client.post(f'/directories/companies/{company_id}/dadata/refresh')
check('снимок DaData получен', r.status_code == 302)
with app.app_context():
    company = db.session.get(Company, company_id)
    snapshot = CompanyDadataSnapshot.query.filter_by(company_id=company_id).first()
    check('официальные изменения ожидают принятия',
          snapshot is not None and company.full_name == '')
    snapshot_id = snapshot.id

r = client.post(
    f'/directories/companies/{company_id}/dadata/accept',
    data={'snapshot_id': snapshot_id})
check('изменения DaData приняты', r.status_code == 302)
with app.app_context():
    company = db.session.get(Company, company_id)
    check('базовые реквизиты нормализованы',
          company.full_name == 'ООО "Тестовая организация"'
          and company.primary_okved == '35.30'
          and company.legal_status == 'ACTIVE')
    check('ручные данные не затёрты',
          company.name == 'Внутреннее имя'
          and company.director_phone == '+7 000 000-00-00'
          and company.classifications.get('sector') == 'тепло')
    check('дополнительные ОКВЭД нормализованы',
          {row.code for row in CompanyOkved.query.filter_by(
              company_id=company_id).all()} == {'35.30', '36.00'})
    contacts = CompanyContact.query.filter_by(
        company_id=company_id, source='DaData').all()
    check('контакты DaData приняты',
          {(item.contact_type, item.value) for item in contacts} == {
              ('phone', '+7 495 123-45-67'),
              ('email', 'INFO@EXAMPLE.RU'),
              ('website', 'example.ru'),
          })
    check('дата регистрации взята из state',
          company.registration_date is not None)
    check('финансовая история нормализована',
          {(row.year, row.revenue, row.profit)
           for row in company.finance_rows} == {
              (2025, 1000, 100),
              (2024, 800, 75),
          })

r = client.get(f'/directories/companies/{company_id}')
body = r.get_data(as_text=True)
directory_body = client.get('/directories/companies').get_data(as_text=True)
check('в списке нет упоминания провайдера',
      'Автоматические данные' in directory_body
      and 'DaData ·' not in directory_body
      and 'DaData:' not in directory_body
      and 'Ручные данные' in Path(
          'templates/directory_companies.html').read_text())
check('в списке скрыты ненужные столбцы и есть иконка просмотра',
      'Связи' not in directory_body
      and 'Заполнено' not in directory_body
      and 'aria-label="Открыть предприятие"' in directory_body
      and '<svg viewBox="0 0 24 24"' in directory_body)
check('из списка доступно архивирование предприятия',
      'Архивировать предприятие' in directory_body
      and 'name="archive"' in directory_body
      and 'name="csrf_token"' in directory_body)
check('в справочнике используется общая шапка модулей',
      '<div class="hd-logo-icon">АР</div>' in directory_body
      and 'ГИС УЗТЭР · Справочник предприятий' in directory_body
      and '← Все модули' in directory_body
      and 'class="hd-uname"' in directory_body)
with app.app_context():
    second_company = Company(
        name='Второе предприятие', created_by=admin_id, status='active')
    db.session.add(second_company)
    db.session.commit()
page_one = client.get('/directories/companies?per_page=1&page=1')
page_two = client.get('/directories/companies?per_page=1&page=2')
search_page = client.get(
    '/directories/companies?per_page=1&q=Второе%20предприятие')
check('список предприятий разбит на страницы на сервере',
      page_one.status_code == 200
      and page_two.status_code == 200
      and page_one.get_data(as_text=True).count('class="row company-row"') == 1
      and page_two.get_data(as_text=True).count('class="row company-row"') == 1
      and 'page=2' in page_one.get_data(as_text=True))
check('поиск предприятий выполняется на сервере',
      search_page.status_code == 200
      and 'Второе предприятие' in search_page.get_data(as_text=True)
      and 'Внутреннее имя' not in search_page.get_data(as_text=True))
check('дополнительные сведения отображаются без названия источника',
      r.status_code == 200
      and 'Дополнительные сведения' in body
      and 'Данные DaData' not in body
      and 'Обновление DaData' not in body)
check('технический JSON не выводится в карточке',
      'Техническое представление полученных данных' not in body)
check('обновление сведений доступно компактной кнопкой',
      '↻ Обновить' in body and 'id="sources"' not in body)
check('ИНН ОГРН и КПП редактируются в подтверждённых сведениях',
      'name="inn"' in body and 'name="ogrn"' in body
      and 'name="kpp"' in body and 'form="dadata-refresh-form"' in body)
check('блоки дополнительных сведений сворачиваются',
      body.count('class="data-block"') >= 6)
check('все блоки дополнительных сведений закрыты по умолчанию',
      '<details class="data-block" open>' not in body)
check('ОКВЭД, контакты и финансы видны в карточке',
      '36.00' in body
      and '+7 495 123-45-67' in body
      and 'Финансовые сведения' in body
      and '2024' in body)
check('служебные статусы переведены на русский',
      'Действует' in body
      and 'Основная организация' in body
      and 'ACTIVE' not in body
      and '>MAIN<' not in body)
check('типы руководителей и органов переведены на русский',
      'Физическое лицо' in body
      and 'ФНС — регистрация' in body
      and 'Пенсионный фонд' in body
      and 'EMPLOYEE' not in body
      and 'fts_registration' not in body)

# Сбой не удаляет последний успешный снимок и не раскрывает токен.
with patch('dadata_client.find_party', side_effect=DaDataError('DaData недоступна')):
    client.post(f'/directories/companies/{company_id}/dadata/refresh')
with app.app_context():
    company = db.session.get(Company, company_id)
    check('последний успешный профиль сохранён при сбое',
          company.dadata_profile is not None
          and CompanyDadataSnapshot.query.filter_by(
              company_id=company_id, status='success').count() == 1)
r = client.get('/settings')
body = r.get_data(as_text=True)
check('токен отсутствует в HTML', 'secret-dadata-token' not in body)
with app.app_context():
    check('токен отсутствует в аудите',
          all('secret-dadata-token' not in (row.details or '')
              for row in AuditLog.query.all()))

# Пустой токен при сохранении не меняет зашифрованное значение.
with app.app_context():
    previous = Setting.get('dadata_api_token')
client.post('/settings', data={'action': 'save_dadata', 'dadata_enabled': '1',
                               'dadata_api_token': ''})
with app.app_context():
    check('пустой токен не затирает сохранённый',
          Setting.get('dadata_api_token') == previous)

# Серверный scope применяется к каталогу и API.
login_as(client, user_id)
check('назначенное предприятие доступно в API',
      client.get(f'/api/companies/{company_id}/profile').status_code == 200)
with app.app_context():
    other = Company(name='Чужое', created_by=admin_id)
    db.session.add(other)
    db.session.commit()
    other_id = other.id
check('чужое предприятие закрыто по прямому API URL',
      client.get(f'/api/companies/{other_id}/profile').status_code == 403)
check('чужое предприятие не видно в каталоге',
      'Чужое' not in client.get('/directories/companies').get_data(as_text=True))

# Архивирование сохраняет запись и все связи.
login_as(client, admin_id)
client.post(f'/directories/companies/{company_id}/archive', data={'archive': '1'})
default_directory = client.get('/directories/companies').get_data(as_text=True)
archived_directory = client.get(
    '/directories/companies?status=archived').get_data(as_text=True)
all_directory = client.get(
    '/directories/companies?status=all').get_data(as_text=True)
company_profile_path = f'/directories/companies/{company_id}'
check('архивные предприятия по умолчанию скрыты',
      company_profile_path not in default_directory
      and company_profile_path in archived_directory
      and company_profile_path in all_directory)
blocked_write = client.post(
    f'/directories/companies/{company_id}/edit',
    data={'name': 'Не должно сохраниться'})
check('архивное предприятие доступно только для чтения',
      blocked_write.status_code == 409)
with app.app_context():
    company = db.session.get(Company, company_id)
    check('архивирование не удаляет предприятие',
          company is not None and company.status == 'archived'
          and len(company.memberships) == 1)

# Выключенный провайдер нельзя обойти прямым POST-запросом.
client.post(f'/directories/companies/{company_id}/archive', data={'archive': '0'})
with app.app_context():
    Setting.set('dadata_enabled', '0')
    db.session.commit()
check('выключенную DaData нельзя вызвать напрямую',
      client.post(
          f'/directories/companies/{company_id}/dadata/refresh').status_code == 409)

# Клиент не принимает поиск по названию вместо точного идентификатора.
try:
    find_party('token', inn='ООО Ромашка')
    exact_rejected = False
except DaDataError:
    exact_rejected = True
check('поиск без точного ИНН/ОГРН отклонён до сети', exact_rejected)

# Полное удаление разрешено только администратору, только после архивирования
# и удаляет записи без каскадных внешних ключей.
with app.app_context():
    deletion_company_name = db.session.get(Company, company_id).name
check('действующее предприятие нельзя удалить полностью',
      client.post(
          f'/directories/companies/{company_id}/delete',
          data={'confirmation_name': deletion_company_name}).status_code == 409)
client.post(f'/directories/companies/{company_id}/archive', data={'archive': '1'})
archived_profile = client.get(
    f'/directories/companies/{company_id}').get_data(as_text=True)
check('кнопка полного удаления видна в архивной карточке',
      'Удалить полностью' in archived_profile
      and 'name="confirmation_name"' in archived_profile)
login_as(client, user_id)
non_admin_delete = client.post(
    f'/directories/companies/{company_id}/delete',
    data={'confirmation_name': deletion_company_name})
check(f'обычный пользователь не может удалить предприятие '
      f'(HTTP {non_admin_delete.status_code})',
      non_admin_delete.status_code == 403)
login_as(client, admin_id)
check('для удаления нужно точное название предприятия',
      client.post(
          f'/directories/companies/{company_id}/delete',
          data={'confirmation_name': 'Тест'}).status_code == 400)
with app.app_context():
    db.session.add_all([
        EmailRule(company_id=company_id, sender_pattern='delete@example.test'),
        Upload(company_id=company_id, uploaded_by=admin_id),
        AiSummary(company_id=company_id, content='delete me'),
        DailyRow(company_id=company_id, date=date(2026, 9, 3), acc='51'),
    ])
    db.session.commit()
delete_response = client.post(
    f'/directories/companies/{company_id}/delete',
    data={'confirmation_name': deletion_company_name})
check(f'полное удаление возвращает в справочник '
      f'(HTTP {delete_response.status_code}, '
      f'{delete_response.headers.get("Location", "без Location")})',
      delete_response.status_code == 302
      and 'deleted=1' in delete_response.headers.get('Location', ''))
with app.app_context():
    check('предприятие и связанные данные удалены полностью',
          db.session.get(Company, company_id) is None
          and EmailRule.query.filter_by(company_id=company_id).count() == 0
          and Upload.query.filter_by(company_id=company_id).count() == 0
          and AiSummary.query.filter_by(company_id=company_id).count() == 0
          and DailyRow.query.filter_by(company_id=company_id).count() == 0
          and CompanyMembership.query.filter_by(company_id=company_id).count() == 0)
    check('полное удаление записано в аудит',
          AuditLog.query.filter_by(
              action='directory_company_deleted').count() == 1)

# Ошибка очистки файлов сохраняется как долговечная задача и повторяется.
with app.app_context():
    cleanup_company = Company(
        name='Предприятие для проверки очистки',
        created_by=admin_id,
        status='archived',
    )
    db.session.add(cleanup_company)
    db.session.commit()
    cleanup_company_id = cleanup_company.id
app.config['WTF_CSRF_ENABLED'] = True
csrf_rejected = client.post(
    f'/directories/companies/{cleanup_company_id}/delete',
    data={'confirmation_name': 'Предприятие для проверки очистки'})
app.config['WTF_CSRF_ENABLED'] = False
check(f'полное удаление защищено CSRF (HTTP {csrf_rejected.status_code})',
      csrf_rejected.status_code == 403)
with app.app_context():
    check('CSRF-ошибка не удаляет предприятие',
          db.session.get(Company, cleanup_company_id) is not None)
with patch('app._remove_company_storage',
           return_value=['uploads:PermissionError']):
    cleanup_response = client.post(
        f'/directories/companies/{cleanup_company_id}/delete',
        data={'confirmation_name': 'Предприятие для проверки очистки'})
check('ошибка очистки файлов не маскируется полным успехом',
      cleanup_response.status_code == 302
      and 'cleanup=pending' in cleanup_response.headers.get('Location', ''))
with app.app_context():
    check('незавершённая очистка сохранена для повтора',
          db.session.get(Company, cleanup_company_id) is None
          and AuditLog.query.filter_by(
              action='directory_company_delete_cleanup_pending').count() == 1)
client.get('/directories/companies')
with app.app_context():
    cleanup_audit = AuditLog.query.filter(
        AuditLog.details.contains(
            f'"company_id": {cleanup_company_id}')).order_by(
                AuditLog.id.desc()).first()
    check('очистка файлов автоматически повторяется',
          cleanup_audit is not None
          and cleanup_audit.action == 'directory_company_deleted')

print('\nВСЕ ТЕСТЫ СПРАВОЧНИКА ПРЕДПРИЯТИЙ ПРОЙДЕНЫ')