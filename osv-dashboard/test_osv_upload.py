# -*- coding: utf-8 -*-
"""Регрессия CSRF и быстрого запуска фоновой загрузки ОСВ."""
import io
import os
import re
import sys
import tempfile
from openpyxl import Workbook

_TMP = tempfile.mkdtemp(prefix='osv_upload_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')
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
from jobs import create_job, finish_job
from osvparser import parse_many

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
    admin.password_change_required = False
    company = Company(name='Тест загрузки ОСВ', created_by=admin.id)
    db.session.add(company)
    db.session.commit()
    admin_id, company_id = admin.id, company.id

client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(admin_id)
    session['_fresh'] = True
    session['password_version'] = 1

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
with open(os.path.join(
        os.path.dirname(__file__), 'static', 'upload_job_monitor.js'),
        encoding='utf-8') as stream:
    upload_monitor = stream.read()
check('статус загрузки сохраняется при переходе между страницами',
      'brain4sales.activeUploadJobs' in upload_monitor
      and 'localStorage.setItem' in upload_monitor
      and 'setInterval(pollAll' in upload_monitor)
check('завершение и ошибка показываются всплывающим уведомлением',
      "data.status === 'done'" in upload_monitor
      and "data.status === 'error'" in upload_monitor
      and "toast('Загрузка завершена'" in upload_monitor
      and "toast('Ошибка загрузки:" in upload_monitor)

contradictory_path = os.path.join(
    _TMP, 'ОСВ 51 с противоречивым итогом.xlsx')
book = Workbook()
sheet = book.active
sheet.append([
    'Оборотно-сальдовая ведомость по счету 51 '
    'за 01.07.2026 - 03.07.2026'
])
sheet.append(['Счет'])
sheet.append(['Итого', 100, 0, 0, 0, 100, 0])
book.save(contradictory_path)
book.close()

_data, contradictory_audit = parse_many([contradictory_path])
completed_job_id = create_job(company_id)
finish_job(completed_job_id, {
    'audit': contradictory_audit,
    'discrepancy': [],
    'period_label': '01.07.2026–03.07.2026',
})
result_page = client.get(
    f'/company/{company_id}?completed_job={completed_job_id}'
).get_data(as_text=True)
check(
    'противоречивый итог виден в протоколе завершённой загрузки',
    'противоречивая пустая ОСВ' in result_page
    and '«Итого» содержит ненулевые значения' in result_page,
)
check(
    'противоречивый файл не показан успешно прошедшим контроль',
    contradictory_audit[0]['ok'] is False
    and '<td class="st-bad">' in result_page
    and 'контрольное равенство НЕ сходится' in result_page,
)

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