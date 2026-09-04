# -*- coding: utf-8 -*-
"""Регрессия архивирования предприятия только после клиентского таймера."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix='company_archive_test_')
os.environ['DATABASE_URL'] = f"sqlite:///{_TMP}/test.db"
os.environ.setdefault(
    'ENCRYPTION_KEY',
    'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import Company, User, db

app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
init_db()


def check(label, condition):
    print(('PASS' if condition else 'FAIL'), label)
    if not condition:
        raise AssertionError(label)


with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    company = Company(
        name='Предприятие без текстового подтверждения',
        created_by=admin.id,
        status='active',
    )
    db.session.add(company)
    db.session.commit()
    admin_id = admin.id
    company_id = company.id

client = app.test_client()
with client.session_transaction() as session:
    session['_user_id'] = str(admin_id)
    session['_fresh'] = True

page = client.get(f'/company/{company_id}').get_data(as_text=True)
check('поле подтверждения названием отсутствует',
      'Для подтверждения введите название предприятия' not in page
      and 'del-company-name' not in page)
check('таймер подтверждения сохранён',
      'id="del-timer">7</span>' in page)

response = client.post(f'/company/{company_id}/delete')
check('архивирование не требует названия', response.status_code == 302)
with app.app_context():
    check('предприятие архивировано',
          db.session.get(Company, company_id).status == 'archived')

print('\nВСЕ ТЕСТЫ АРХИВИРОВАНИЯ ПРЕДПРИЯТИЯ ПРОЙДЕНЫ')