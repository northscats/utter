# -*- coding: utf-8 -*-
"""Изолированная регрессия восстановления и удаления предприятий.

Этот набор намеренно запускается отдельным процессом: DATABASE_URL и пути
хранилища задаются до импорта app.  Он не использует рабочую БД, фоновые
обработчики и резервное копирование приложения.
"""
import atexit
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import date
from unittest.mock import patch


_TMP = tempfile.mkdtemp(prefix='company_restore_test_')
_STORAGE = os.path.join(_TMP, 'storage')
os.makedirs(_STORAGE, exist_ok=True)
os.environ['DATABASE_URL'] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ['ENCRYPTION_KEY'] = (
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
os.environ.pop('DURABLE_BACKUP_DIR', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
import app as app_module
from database import (
    AiSummary, AuditLog, Company, CompanyMembership, DailyRow, EmailRule,
    HistoricalImport, HistoricalMapping, RevenueAnalyticMapping, Setting,
    Upload, User, UserModuleAccess, db,
)
from directory_service import company_identity_key


app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db(perform_durable=False)

# Do not touch real uploads/output, App Storage, or the IMAP loop.  Keep the
# real Thread class under an alias because the concurrency check needs it.
app_module.UPLOADS = os.path.join(_STORAGE, 'uploads')
app_module.OUTPUT = os.path.join(_STORAGE, 'output')
app_module.DOCS_STORAGE = os.path.join(app_module.UPLOADS, 'docs')
os.makedirs(app_module.UPLOADS, exist_ok=True)
os.makedirs(app_module.OUTPUT, exist_ok=True)
os.makedirs(app_module.DOCS_STORAGE, exist_ok=True)
app_module._ensure_imap_thread = lambda: None
app_module._durable_backup = lambda *args, **kwargs: None

_RealThread = threading.Thread
worker_targets = []
worker_starts = []


class _NoopWorker:
    """Records a worker request without processing a test file."""

    def __init__(self, *args, **kwargs):
        worker_targets.append(kwargs.get('target'))

    def start(self):
        worker_starts.append(True)


# app.threading is the stdlib module object, so save/restore the class around
# this isolated process.  _RealThread remains usable for this script.
app_module.threading.Thread = _NoopWorker


def _cleanup():
    app_module.threading.Thread = _RealThread
    try:
        db.session.remove()
    except Exception:
        pass
    shutil.rmtree(_TMP, ignore_errors=True)


atexit.register(_cleanup)


failures = []


def check(label, condition):
    if condition:
        print(f'PASS {label}')
    else:
        print(f'FAIL {label}')
        failures.append(label)
    return condition


def login_as(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


def company_counts(company_id):
    with app.app_context():
        return {
            'memberships': CompanyMembership.query.filter_by(
                company_id=company_id).count(),
            'uploads': Upload.query.filter_by(company_id=company_id).count(),
            'daily_rows': DailyRow.query.filter_by(company_id=company_id).count(),
            'mail_rules': EmailRule.query.filter_by(company_id=company_id).count(),
            'ai_summaries': AiSummary.query.filter_by(
                company_id=company_id).count(),
            'historical_imports': HistoricalImport.query.filter_by(
                company_id=company_id).count(),
            'historical_mappings': HistoricalMapping.query.filter_by(
                company_id=company_id).count(),
            'revenue_mappings': RevenueAnalyticMapping.query.filter_by(
                company_id=company_id).count(),
        }


def company_snapshot(company_id):
    with app.app_context():
        company = db.session.get(Company, company_id)
        return {
            'id': company.id if company else None,
            'name': company.name if company else None,
            'note': company.note if company else None,
            'status': company.status if company else None,
            'inn': company.inn if company else None,
            'kpp': company.kpp if company else None,
            'ogrn': company.ogrn if company else None,
            'identity_key': company.identity_key if company else None,
        }


def create_company(
    admin_id,
    regular_id,
    *,
    name,
    inn='',
    kpp='',
    ogrn='',
    status='archived',
    note='',
    rich=False,
):
    """Create a fixture without exercising a creation endpoint."""
    with app.app_context():
        company = Company(
            name=name,
            created_by=admin_id,
            inn=inn,
            kpp=kpp,
            ogrn=ogrn,
            identity_key=company_identity_key(inn, ogrn, kpp),
            note=note,
            status=status,
        )
        db.session.add(company)
        db.session.flush()
        db.session.add_all([
            CompanyMembership(
                user_id=admin_id, company_id=company.id,
                participation_type='creator', is_primary=True),
            CompanyMembership(
                user_id=regular_id, company_id=company.id,
                participation_type='auditor', is_primary=False),
        ])
        if rich:
            db.session.add_all([
                Upload(
                    company_id=company.id, uploaded_by=admin_id,
                    period_label='янв–июн 2026',
                    accounts_loaded=json.dumps(['51', '62']),
                    files_count=2, source='manual'),
                DailyRow(
                    company_id=company.id, date=date(2026, 9, 1), acc='51',
                    obd=12.5, obk=5.0),
                EmailRule(
                    company_id=company.id,
                    sender_pattern='restore@example.test'),
                AiSummary(
                    company_id=company.id, content='saved summary',
                    provider_name='test', model_name='test-model'),
            ])
            historical = HistoricalImport(
                company_id=company.id, uploaded_by=admin_id,
                original_filename='history.xlsx',
                stored_path='/isolated/history.xlsx',
                file_hash='a' * 64,
                status='confirmed',
                preview_json='{}', validation_json='{}',
            )
            db.session.add(historical)
            db.session.flush()
            db.session.add(HistoricalMapping(
                company_id=company.id, kind='metric',
                source_label='Выручка', source_normalized='выручка',
                canonical_key='revenue', canonical_label='Выручка',
                confirmed_by=admin_id,
            ))
            db.session.add(RevenueAnalyticMapping(
                company_id=company.id, account='90',
                analytic_key='restore-key', analytic_label='Продажи',
                category='sales', updated_by=admin_id,
            ))
        db.session.commit()
        return company.id


def post_directory_create(client, **fields):
    payload = {'action': 'create'}
    payload.update(fields)
    return client.post('/directories/companies/new', data=payload)


def upload_file(name='restore.xlsx'):
    # _validate_upload only needs the OOXML ZIP signature; the worker is
    # replaced in this test and must not parse the body.
    return (io.BytesIO(b'PK\x03\x04isolated-test'), name)


with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    regular = User(
        username='restore-reader', password_hash='unused', role='user')
    db.session.add(regular)
    db.session.flush()
    db.session.add_all([
        UserModuleAccess(user_id=regular.id, module_key='directories'),
        UserModuleAccess(user_id=regular.id, module_key='analytics'),
    ])
    db.session.commit()
    admin_id, regular_id = admin.id, regular.id

admin_client = app.test_client()
regular_client = app.test_client()
login_as(admin_client, admin_id)
login_as(regular_client, regular_id)


# ---------------------------------------------------------------------------
# Directory restoration by all identity fields, including data preservation.
# ---------------------------------------------------------------------------
primary_id = create_company(
    admin_id, regular_id,
    name='Каноническое предприятие',
    inn='7701234567', kpp='770101001', ogrn='1027700000000',
    note='Сохранённая заметка', rich=True)
primary_before = company_snapshot(primary_id)
primary_counts = company_counts(primary_id)
with app.app_context():
    total_before = Company.query.count()

restore_response = post_directory_create(
    admin_client,
    name='Новое ошибочное имя',
    inn='7701234567', kpp='770101001', ogrn='1027700000000')
check('directory create restores archived company with 302',
      restore_response.status_code == 302
      and restore_response.headers.get('Location', '').endswith(
          f'/directories/companies/{primary_id}'))
restored_profile = admin_client.get(
    restore_response.headers.get('Location', ''))
restored_body = restored_profile.get_data(as_text=True)
check('restoration flash is shown in company profile',
      restored_profile.status_code == 200
      and 'найдено в архиве и восстановлено' in restored_body)
primary_after = company_snapshot(primary_id)
check('restore keeps the archived ID and legal identifiers',
      primary_after['id'] == primary_id
      and primary_after['inn'] == primary_before['inn']
      and primary_after['kpp'] == primary_before['kpp']
      and primary_after['ogrn'] == primary_before['ogrn']
      and primary_after['identity_key'] == primary_before['identity_key'])
check('restore preserves original name and note',
      primary_after['name'] == 'Каноническое предприятие'
      and primary_after['note'] == 'Сохранённая заметка')
check('restore preserves memberships/uploads/data counts',
      company_counts(primary_id) == primary_counts)
with app.app_context():
    check('restore does not insert a second company',
          Company.query.count() == total_before)

# Once active, the same identity is a hard duplicate, not a new company.
active_duplicate = post_directory_create(
    admin_client,
    name='Активный дубль',
    inn='7701234567', kpp='770101001', ogrn='1027700000000')
with app.app_context():
    active_duplicate_count = Company.query.filter_by(name='Активный дубль').count()
check('active duplicate is blocked',
      active_duplicate.status_code == 200
      and 'конфликтующая запись' in active_duplicate.get_data(as_text=True)
      and active_duplicate_count == 0)


# ---------------------------------------------------------------------------
# Name fallback, ambiguity, and conflicting identifiers.
# ---------------------------------------------------------------------------
fallback_id = create_company(
    admin_id, regular_id, name='  Имя  без  реквизитов  ', note='name note')
fallback_response = post_directory_create(
    admin_client, name=' имя без реквизитов ')
check('same-name case/whitespace fallback restores without identifiers',
      fallback_response.status_code == 302
      and fallback_response.headers.get('Location', '').endswith(
          f'/directories/companies/{fallback_id}'))
check('same-name fallback preserves exact stored display name',
      company_snapshot(fallback_id)['name'] == '  Имя  без  реквизитов  '
      and company_snapshot(fallback_id)['status'] == 'active')

ambiguous_one = create_company(
    admin_id, regular_id, name='Неоднозначная организация')
ambiguous_two = create_company(
    admin_id, regular_id, name='  Неоднозначная   организация ')
ambiguous_response = post_directory_create(
    admin_client, name=' неоднозначная организация ')
check('ambiguous name does not restore either archive',
      ambiguous_response.status_code == 200
      and 'неоднозначно' in ambiguous_response.get_data(as_text=True)
      and company_snapshot(ambiguous_one)['status'] == 'archived'
      and company_snapshot(ambiguous_two)['status'] == 'archived')

conflicting_id = create_company(
    admin_id, regular_id,
    name='Реквизиты с конфликтом',
    inn='7812345678', kpp='781201001', ogrn='1027812345678')
conflicting_response = post_directory_create(
    admin_client,
    name='Не восстанавливать конфликт',
    inn='7812345678', kpp='781201001', ogrn='1027812345679')
with app.app_context():
    conflicting_new_count = Company.query.filter_by(
        name='Не восстанавливать конфликт').count()
check('conflicting supplied identifiers do not restore',
      conflicting_response.status_code == 200
      and 'конфликтующая запись' in conflicting_response.get_data(as_text=True)
      and company_snapshot(conflicting_id)['status'] == 'archived'
      and conflicting_new_count == 0)


# DaData may reveal the archived OGRN after the initial submitted identifier
# misses it.  The transient Company must never be flushed before this match.
dadata_id = create_company(
    admin_id, regular_id,
    name='Архивная DaData организация', ogrn='1037700000000')
with app.app_context():
    Setting.set('dadata_enabled', '1')
    db.session.commit()
dadata_before_count = None
with app.app_context():
    dadata_before_count = Company.query.count()
dadata_party = {'data': {'inn': '7707654321', 'ogrn': '1037700000000'}}
dadata_normalized = {
    'inn': '7707654321', 'ogrn': '1037700000000', 'kpp': ''}
with patch.object(
        app_module, '_find_dadata_party',
        return_value=(dadata_party, dadata_normalized)) as dadata_lookup:
    dadata_response = post_directory_create(
        admin_client,
        name='Временное имя DaData',
        inn='7707654321', confirm_dadata='1')
with app.app_context():
    dadata_after_count = Company.query.count()
    dadata_transient_count = Company.query.filter_by(
        name='Временное имя DaData').count()
check('DaData normalized archived duplicate is looked up',
      dadata_lookup.called and dadata_response.status_code == 302
      and dadata_response.headers.get('Location', '').endswith(
          f'/directories/companies/{dadata_id}'))
check('DaData archived match restores without transient insert',
      dadata_after_count == dadata_before_count
      and dadata_transient_count == 0
      and company_snapshot(dadata_id)['status'] == 'active')


# ---------------------------------------------------------------------------
# Non-admin protections and legacy /company/new restoration.
# ---------------------------------------------------------------------------
archive_primary = admin_client.post(
    f'/directories/companies/{primary_id}/archive',
    data={'archive': '1'})
check('admin can archive primary fixture before non-admin checks',
      archive_primary.status_code == 302
      and company_snapshot(primary_id)['status'] == 'archived')

non_admin_archive = regular_client.post(
    f'/directories/companies/{primary_id}/archive',
    data={'archive': '0'})
non_admin_delete = regular_client.post(
    f'/directories/companies/{primary_id}/delete',
    data={'confirmation_name': 'Каноническое предприятие'})
non_admin_workers_before = len(worker_starts)
non_admin_new = regular_client.post(
    '/company/new',
    data={
        'org_name': 'Каноническое предприятие',
        'files': upload_file(),
    },
    content_type='multipart/form-data')
check('non-admin cannot auto-restore an archived company',
      non_admin_new.status_code == 200
      and 'обратитесь к администратору' in
      non_admin_new.get_data(as_text=True)
      and company_snapshot(primary_id)['status'] == 'archived'
      and len(worker_starts) == non_admin_workers_before)
check('non-admin cannot archive/auto-restore',
      non_admin_archive.status_code in (403, 409)
      and company_snapshot(primary_id)['status'] == 'archived')
check('non-admin cannot permanently delete',
      non_admin_delete.status_code == 403
      and company_snapshot(primary_id)['status'] == 'archived')

# Remove only this test's rejected-upload staging directory before checking
# the administrator path; it is inside the temporary storage root.
shutil.rmtree(os.path.join(app_module.UPLOADS, 'tmp'), ignore_errors=True)
primary_upload_count_before_legacy = company_counts(primary_id)['uploads']
workers_before_legacy = len(worker_starts)
legacy_restore = admin_client.post(
    '/company/new',
    data={
        'org_name': 'Каноническое предприятие',
        'files': upload_file('legacy_restore.xlsx'),
    },
    content_type='multipart/form-data')
legacy_profile = admin_client.get(
    legacy_restore.headers.get('Location', ''))
legacy_body = legacy_profile.get_data(as_text=True)
check('legacy /company/new restores the archived ID',
      legacy_restore.status_code == 302
      and legacy_restore.headers.get('Location', '').endswith(
          f'/directories/companies/{primary_id}')
      and company_snapshot(primary_id)['status'] == 'active')
check('legacy restore explains that files were not imported',
      legacy_profile.status_code == 200
      and 'файлы не импортированы' in legacy_body)
check('legacy restore leaves uploads unchanged and does not call worker',
      company_counts(primary_id)['uploads'] == primary_upload_count_before_legacy
      and len(worker_starts) == workers_before_legacy
      and len(worker_targets) == 0)

legacy_active_before = company_snapshot(primary_id)
legacy_workers_before = len(worker_starts)
legacy_active_block = admin_client.post(
    '/company/new',
    data={
        'org_name': '  каноническое   предприятие ',
        'files': upload_file('active_duplicate.xlsx'),
    },
    content_type='multipart/form-data')
with app.app_context():
    legacy_new_count = Company.query.filter_by(
        name='  каноническое   предприятие ').count()
check('legacy /company/new blocks an existing active company',
      legacy_active_block.status_code == 200
      and 'конфликтующая запись' in legacy_active_block.get_data(as_text=True)
      and company_snapshot(primary_id) == legacy_active_before
      and legacy_new_count == 0
      and len(worker_starts) == legacy_workers_before)


# ---------------------------------------------------------------------------
# RLock regression: concurrent directory submissions restore one target once.
# ---------------------------------------------------------------------------
concurrent_id = create_company(
    admin_id, regular_id,
    name='Конкурентное архивное предприятие',
    inn='7819876543', kpp='781901001', ogrn='1047819876543')
client_a = app.test_client()
client_b = app.test_client()
login_as(client_a, admin_id)
login_as(client_b, admin_id)
barrier = threading.Barrier(2)
concurrent_results = []


def concurrent_submit(client):
    barrier.wait()
    response = post_directory_create(
        client,
        name='Конкурентная новая запись',
        inn='7819876543', kpp='781901001', ogrn='1047819876543')
    concurrent_results.append(
        (response.status_code, response.get_data(as_text=True)))


thread_a = _RealThread(target=concurrent_submit, args=(client_a,))
thread_b = _RealThread(target=concurrent_submit, args=(client_b,))
thread_a.start()
thread_b.start()
thread_a.join()
thread_b.join()
with app.app_context():
    concurrent_company_count = Company.query.filter_by(
        id=concurrent_id).count()
    concurrent_new_count = Company.query.filter_by(
        name='Конкурентная новая запись').count()
check('concurrent submissions produce one restore and one duplicate block',
      len(concurrent_results) == 2
      and sorted(status for status, _body in concurrent_results) == [200, 302]
      and company_snapshot(concurrent_id)['status'] == 'active'
      and concurrent_company_count == 1
      and concurrent_new_count == 0)


# ---------------------------------------------------------------------------
# Archived list deletion link, CSRF/name guards, and disposable cleanup.
# ---------------------------------------------------------------------------
delete_id = create_company(
    admin_id, regular_id,
    name='Удаляемое тестовое предприятие',
    note='disposable deletion fixture', rich=True)
with app.app_context():
    delete_company = db.session.get(Company, delete_id)
    delete_company.status = 'archived'
    db.session.commit()
os.makedirs(os.path.join(app_module.UPLOADS, str(delete_id)), exist_ok=True)
os.makedirs(os.path.join(app_module.OUTPUT, str(delete_id)), exist_ok=True)
with open(os.path.join(app_module.UPLOADS, str(delete_id), 'keep.txt'), 'w') as stream:
    stream.write('temporary')
with open(os.path.join(app_module.OUTPUT, str(delete_id), 'keep.html'), 'w') as stream:
    stream.write('temporary')

archived_list = admin_client.get('/directories/companies?status=archived')
archived_list_body = archived_list.get_data(as_text=True)
check('archived list exposes the permanent deletion link',
      archived_list.status_code == 200
      and f'href="/directories/companies/{delete_id}#permanent-delete"' in
      archived_list_body
      and 'Перейти к полному удалению предприятия' in archived_list_body)

app.config['WTF_CSRF_ENABLED'] = True
delete_profile = admin_client.get(f'/directories/companies/{delete_id}')
delete_profile_body = delete_profile.get_data(as_text=True)
csrf_match = re.search(
    r'name="csrf_token"\s+value="([^"]+)"', delete_profile_body)
csrf_token = csrf_match.group(1) if csrf_match else ''
missing_csrf = admin_client.post(
    f'/directories/companies/{delete_id}/delete',
    data={'confirmation_name': 'Удаляемое тестовое предприятие'})
wrong_name = admin_client.post(
    f'/directories/companies/{delete_id}/delete',
    data={
        'confirmation_name': 'Удаляемое предприятие',
        'csrf_token': csrf_token,
    })
check('permanent deletion rejects missing CSRF',
      missing_csrf.status_code == 403
      and company_snapshot(delete_id)['status'] == 'archived')
check('permanent deletion requires exact company name',
      csrf_token
      and wrong_name.status_code == 400
      and company_snapshot(delete_id)['status'] == 'archived'
      and company_counts(delete_id)['historical_mappings'] == 1
      and company_counts(delete_id)['revenue_mappings'] == 1)

# This is a disposable fixture in the temporary SQLite database, so the
# positive path can verify all historical/revenue dependencies and storage
# cleanup without touching real data.
correct_delete = admin_client.post(
    f'/directories/companies/{delete_id}/delete',
    data={
        'confirmation_name': 'Удаляемое тестовое предприятие',
        'csrf_token': csrf_token,
    })
with app.app_context():
    deleted_audit_count = AuditLog.query.filter_by(
        action='directory_company_deleted').count()
    deleted_mappings = (
        HistoricalMapping.query.filter_by(company_id=delete_id).count()
        + RevenueAnalyticMapping.query.filter_by(company_id=delete_id).count())
delete_counts_after = company_counts(delete_id)
delete_snapshot_after = company_snapshot(delete_id)
delete_storage_after = (
    os.path.exists(os.path.join(app_module.UPLOADS, str(delete_id)))
    or os.path.exists(os.path.join(app_module.OUTPUT, str(delete_id))))
delete_ok = (
    correct_delete.status_code == 302
    and 'deleted=1' in correct_delete.headers.get('Location', '')
    and delete_snapshot_after['id'] is None
    and all(value == 0 for value in delete_counts_after.values())
    and deleted_mappings == 0
    and deleted_audit_count >= 1
    and not delete_storage_after)
if not delete_ok:
    print(
        '  deletion details:',
        correct_delete.status_code,
        correct_delete.headers.get('Location'),
        delete_snapshot_after,
        delete_counts_after,
        deleted_mappings,
        deleted_audit_count,
        delete_storage_after,
    )
check('exact-name deletion removes only the disposable archived fixture',
      delete_ok)


if failures:
    print(f'\nFAILURES ({len(failures)}):')
    for failure in failures:
        print(f' - {failure}')
    raise SystemExit(1)
print('\nВСЕ ТЕСТЫ ВОССТАНОВЛЕНИЯ ПРЕДПРИЯТИЙ ПРОЙДЕНЫ')