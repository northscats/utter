# -*- coding: utf-8 -*-
"""Регрессия CSRF и быстрого запуска фоновой загрузки ОСВ."""
import io
import os
import re
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix='osv_upload_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module
from app import (
    _company_summary,
    _compute_company_completeness,
    _dataset_accounts,
    app,
    init_db,
)
from database import Company, Upload, User, db

app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
init_db()


def check(label, condition):
    print(('PASS' if condition else 'FAIL'), label)
    if not condition:
        raise AssertionError(label)


class FakeThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        pass


app_module.threading.Thread = FakeThread
backup_calls = []
app_module._durable_backup = lambda *args, **kwargs: backup_calls.append(
    (args, kwargs))

with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    company = Company(name='Тест загрузки ОСВ', created_by=admin.id)
    db.session.add(company)
    db.session.commit()
    admin_id, company_id = admin.id, company.id

client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(admin_id)
    session['_fresh'] = True

page = client.get(f'/company/{company_id}').get_data(as_text=True)
stale_token = re.search(
    r'name="csrf_token" value="([^"]+)"', page).group(1)
with client.session_transaction() as session:
    session['csrf_token'] = 'token-refreshed-before-upload'

check('старый токен отклоняется', client.post(
    f'/company/{company_id}/upload-all',
    data={'csrf_token': stale_token,
          'files': (io.BytesIO(b'PK\\x03\\x04test'), 'osv.xlsx')},
    content_type='multipart/form-data').status_code == 403)

fresh = client.get('/rating-api/csrf').get_json()['csrf_token']
response = client.post(
    f'/company/{company_id}/upload-all',
    data={'csrf_token': fresh,
          'files': (io.BytesIO(b'PK\\x03\\x04test'), 'osv.xlsx')},
    content_type='multipart/form-data')
check('свежий токен принимает multipart-загрузку',
      response.status_code == 302 and 'job=' in response.headers['Location'])
check('ответ не ждёт резервную копию', not backup_calls)

page = client.get(f'/company/{company_id}').get_data(as_text=True)
check('форма обновляет токен перед загрузкой',
      "cache:'no-store'" in page
      and 'form.requestSubmit()' in page
      and 'Загрузка не началась:' in page)

check('список счетов берётся из фактического dataset',
      _dataset_accounts({
          'accounts_loaded': ['91', 20, '20', '', None],
          'accounts': {'ошибочное старое поле': {}},
      }) == ['20', '91'])

with app.app_context():
    company = db.session.get(Company, company_id)
    db.session.add(Upload(
        company_id=company_id,
        uploaded_by=admin_id,
        period_label='Январь–Август 2026',
        accounts_loaded='[]',
        files_count=20,
    ))
    db.session.commit()

    original_output = app_module.OUTPUT
    app_module.OUTPUT = os.path.join(_TMP, 'output')
    company_output = os.path.join(app_module.OUTPUT, str(company_id))
    os.makedirs(company_output, exist_ok=True)
    with open(os.path.join(company_output, 'dashboard_current.html'),
              'w', encoding='utf-8') as dashboard:
        dashboard.write(
            '<script>const D={"accounts_loaded":'
            '["01","08","10","20","26","44","50","51","52","55","60","62","66",'
            '"67","68","69","70","76","90","91"]};</script>')
    try:
        completeness = _company_summary(company)['last_completeness']
    finally:
        app_module.OUTPUT = original_output

check('старые загрузки с пустым метаданным читают счета из дашборда',
      completeness['status'] == 'full'
      and completeness['found'] == completeness['total'] == 20)

print('\\nВСЕ ТЕСТЫ ЗАГРУЗКИ ОСВ ПРОЙДЕНЫ')