# -*- coding: utf-8 -*-
"""Регрессия CSRF и быстрого запуска фоновой загрузки ОСВ."""
import io
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from unittest.mock import patch
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
    _accepted_daily_files,
    _compute_company_completeness,
    _dataset_accounts,
    _delete_company_records,
    app,
    init_db,
)
from database import (
    AiProvider, AiSummary, Company, OsvFileAiSummary, Upload, User, db,
)
from jobs import create_job, finish_job, get_job
from osvparser import parse_many
from source_manifest import build_manifest

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
check('монитор показывает имена обрабатываемых файлов',
      'Сейчас обрабатываются:' in upload_monitor
      and "names.textContent = files.join(', ')" in upload_monitor)

files_job_id = create_job(
    company_id, ['ОСВ 08.xls', 'ОСВ 51.xlsx'])
files_status = client.get(
    f'/company/{company_id}/job/{files_job_id}').get_json()
check('API статуса возвращает обрабатываемые файлы',
      files_status['status'] == 'processing'
      and files_status['files'] == ['ОСВ 08.xls', 'ОСВ 51.xlsx']
      and get_job(files_job_id)['files'] == files_status['files'])

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

daily_path = os.path.join(_TMP, 'ОСВ счёт 62 за день.xlsx')
book = Workbook()
sheet = book.active
sheet.append([
    'Оборотно-сальдовая ведомость по счету 62 за 5 июля 2026 г.'
])
sheet.append(['Счет'])
sheet.append(['Итого', 10, 0, 20, 15, 15, 0])
book.save(daily_path)
book.close()
daily_zip = os.path.join(_TMP, 'daily.zip')
with zipfile.ZipFile(daily_zip, 'w') as archive:
    archive.write(daily_path, arcname=os.path.basename(daily_path))
accepted_dir = os.path.join(_TMP, 'accepted-daily')
_successes, _errors = app_module.parse_zip(
    daily_zip, accepted_dir=accepted_dir)
daily_manifest = build_manifest(
    _accepted_daily_files([item['path'] for item in _successes]), _TMP)
check('суточная ОСВ из ZIP сохраняется отдельным источником',
      len(_successes) == len(daily_manifest) == 1
      and os.path.isfile(_successes[0]['path'])
      and daily_manifest[0]['account'] == '62'
      and daily_manifest[0]['granularity'] == 'day'
      and daily_manifest[0]['observed_start'] == '2026-07-05')

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

relative = '20260101_120000/report.xlsx'
digest = 'a' * 64
source_id = hashlib.sha256(
    f'{relative}\0{digest}'.encode('utf-8')).hexdigest()
manifest = [{
    'source_id': source_id, 'sha256': digest, 'relative_path': relative,
    'account': '51', 'period': 'Январь 2025',
    'report_start': '2025-01-01', 'report_end': '2025-01-31',
}]
with app.app_context():
    db.session.add(Upload(
        company_id=company_id, uploaded_by=admin_id, period_label='Январь 2025',
        accounts_loaded='["51"]', files_count=1,
        source_manifest_json=json.dumps(manifest)))
    db.session.add(AiSummary(
        company_id=company_id, content='УСТАРЕВШЕЕ ЗАКЛЮЧЕНИЕ',
        source_revision='old-source-set'))
    db.session.commit()
    check('новая ОСВ атомарно меняет маркер набора источников',
          db.session.get(Company, company_id).osv_source_revision.startswith(
              'upload:'))
    from osv_ai_summary import ensure_file_summaries
    ensure_file_summaries(company_id, app_module.UPLOADS, start_workers=False)
    ensure_file_summaries(company_id, app_module.UPLOADS, start_workers=False)
    check('повторная постановка не дублирует файловое заключение',
          OsvFileAiSummary.query.filter_by(
              company_id=company_id, source_id=source_id).count() == 1)

page = client.get(f'/company/{company_id}').get_data(as_text=True)
check('общее заключение скрывается после изменения набора ОСВ',
      'УСТАРЕВШЕЕ ЗАКЛЮЧЕНИЕ' not in page
      and source_id in page
      and 'Январь 2025' in page)

with app.app_context():
    db.session.add(AiProvider(
        name='Ошибочный тестовый провайдер',
        base_url='https://provider.invalid', model='test',
        is_active=True, is_default=True))
    db.session.commit()
csrf = client.get('/rating-api/csrf').get_json()['csrf_token']
with patch(
        'ai_client.call_ai_provider',
        side_effect=Exception('AI HTTP 500: INTERNAL_PROVIDER_SECRET')):
    failed_summary = client.post(
        f'/company/{company_id}/ai-summary',
        headers={'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'})
failed_payload = failed_summary.get_json()
check('ошибка общего ИИ не раскрывает ответ провайдера',
      failed_summary.status_code == 502
      and 'временно недоступен' in failed_payload['error']
      and 'INTERNAL_PROVIDER_SECRET' not in failed_payload['error'])

with app.app_context():
    disposable = Company(name='Удаляемое предприятие', created_by=admin_id)
    db.session.add(disposable)
    db.session.flush()
    disposable_id = disposable.id
    db.session.add(OsvFileAiSummary(
        company_id=disposable_id, source_id='f' * 64,
        source_sha256='e' * 64, relative_path='batch/file.xlsx',
        filename='file.xlsx'))
    db.session.commit()
    _delete_company_records(disposable)
    db.session.commit()
    check('файловые заключения не мешают удалить предприятие',
          db.session.get(Company, disposable_id) is None
          and OsvFileAiSummary.query.filter_by(
              company_id=disposable_id).count() == 0)

print('\\nВСЕ ТЕСТЫ ЗАГРУЗКИ ОСВ ПРОЙДЕНЫ')