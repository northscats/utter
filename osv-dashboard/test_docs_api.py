# -*- coding: utf-8 -*-
"""Интеграционный тест бэкенда /docs/* (flask test_client, без сети).

Изолированная среда: временная SQLite-БД и временное хранилище файлов —
тест НЕ трогает рабочую базу и uploads/.
"""
import io
import json
import os
import sys
import tempfile
import time
import zipfile

_TMP = tempfile.mkdtemp(prefix='docs_api_test_')
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP}/test.db'
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-admin-passphrase-2026')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db
from database import User, db

app.config['DOCS_STORAGE'] = os.path.join(_TMP, 'docs')
os.makedirs(app.config['DOCS_STORAGE'], exist_ok=True)
init_db()

app.config['WTF_CSRF_ENABLED'] = False  # тестируем логику, не CSRF
client = app.test_client()

with app.app_context():
    u = User.query.filter_by(role='admin').first()
    admin_username = u.username

# Логинимся напрямую через session (flask-login)
with app.app_context():
    admin = User.query.filter_by(role='admin').first()
    admin_id = admin.id
with client.session_transaction() as s:
    s['_user_id'] = str(admin_id)
    s['_fresh'] = True
    s['password_version'] = 1

def check(name, cond, extra=''):
    print(('PASS' if cond else 'FAIL'), name, extra)
    if not cond:
        sys.exit(1)

# 1. Каталоги: список (создаст системный)
r = client.get('/docs/catalogs')
check('GET /docs/catalogs', r.status_code == 200, str(r.status_code))
cats = r.get_json()
check('системный каталог создан', any(c['is_system'] for c in cats))

# 2. Создание каталога + правил
r = client.post('/docs/catalogs', json=dict(name='Акты', description='Акты выполненных работ'))
check('POST /docs/catalogs', r.status_code == 201, r.get_data(as_text=True)[:200])
cat_id = r.get_json()['id']

r = client.post(f'/docs/catalogs/{cat_id}/routing-rules',
                json=dict(rule_type='keyword', pattern='акт'))
check('POST routing-rule keyword', r.status_code == 201)
r = client.post(f'/docs/catalogs/{cat_id}/routing-rules',
                json=dict(rule_type='extension', pattern='txt'))
check('POST routing-rule extension', r.status_code == 201)
r = client.post(f'/docs/catalogs/{cat_id}/check-rules',
                json=dict(name='Наличие печати', prompt_text='Проверь наличие печати на документе'))
check('POST check-rule', r.status_code == 201)

# невалидное правило
r = client.post(f'/docs/catalogs/{cat_id}/routing-rules',
                json=dict(rule_type='bogus', pattern='x'))
check('невалидный rule_type отклонён', r.status_code == 400)

# 3. ZipSlip отклоняется
evil = io.BytesIO()
with zipfile.ZipFile(evil, 'w') as zf:
    zf.writestr('../../evil.txt', 'pwned')
evil.seek(0)
r = client.post('/docs/packages', data={'zipfile': (evil, 'evil.zip')},
                content_type='multipart/form-data')
check('ZipSlip отклонён', r.status_code == 400, r.get_data(as_text=True)[:150])
_evil_id = r.get_json()['id']
_evil_dir = os.path.join(app.config['DOCS_STORAGE'], str(_evil_id))
check('файлы отклонённого пакета удалены', not os.path.exists(_evil_dir), _evil_dir)

# 4. Пустой архив отклоняется
empty = io.BytesIO()
with zipfile.ZipFile(empty, 'w'):
    pass
empty.seek(0)
r = client.post('/docs/packages', data={'zipfile': (empty, 'empty.zip')},
                content_type='multipart/form-data')
check('пустой архив отклонён', r.status_code == 400)

# 4b. Граничные размеры: файл ровно 100 МБ принят, 100 МБ + 1 байт — отклонён
def _zip_with_member(size_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('big.txt', b'\x00' * size_bytes)
    buf.seek(0)
    return buf

import doc_pipeline
r = client.post('/docs/packages',
                data={'zipfile': (_zip_with_member(doc_pipeline.MAX_SINGLE_FILE + 1), 'big.zip')},
                content_type='multipart/form-data')
check('файл >100 МБ отклонён', r.status_code == 400, r.get_data(as_text=True)[:150])

r = client.post('/docs/packages',
                data={'zipfile': (_zip_with_member(doc_pipeline.MAX_SINGLE_FILE), 'ok.zip')},
                content_type='multipart/form-data')
check('файл ровно 100 МБ принят', r.status_code == 201, r.get_data(as_text=True)[:150])
_boundary_pkg = r.get_json()['id']

# 5. Нормальный пакет: txt (конвертация через LibreOffice) + cp866-имя
good = io.BytesIO()
with zipfile.ZipFile(good, 'w') as zf:
    zf.writestr('Акт выполненных работ.txt', 'Акт №1 от 01.08.2026\nПодпись: Иванов И.И.')
    zf.writestr('прочий документ.txt', 'Прочее содержимое документа')
    zf.writestr('data.bin', b'\x00\x01\x02')  # неподдерживаемый формат
good.seek(0)
r = client.post('/docs/packages', data={'zipfile': (good, 'test.zip'),
                                        'title': 'Тестовый пакет'},
                content_type='multipart/form-data')
check('POST /docs/packages', r.status_code == 201, r.get_data(as_text=True)[:200])
pkg_id = r.get_json()['id']

# 6. Ждём обработки (poll)
for _ in range(60):
    r = client.get(f'/docs/packages/{pkg_id}/status')
    st = r.get_json()
    if st['status'] in ('done', 'error'):
        break
    time.sleep(1)
check('пакет обработан', st['status'] == 'done', json.dumps(st, ensure_ascii=False))

# 7. Детали пакета: до нажатия «Распределить» все файлы остаются
# в системном каталоге
r = client.get(f'/docs/packages/{pkg_id}')
d = r.get_json()
files = {f['orig_name']: f for f in d['files']}
akt = files.get('Акт выполненных работ.txt')
check('акт до ручного запуска не распределён',
      akt and akt['catalog']['name'] == 'Не распределено',
      json.dumps(d['files'], ensure_ascii=False)[:400])
check('акт готов, есть страницы', akt['status'] == 'ready' and akt['pages_count'] >= 1)
binf = files.get('data.bin')
check('bin помечен ошибкой', binf and binf['status'] == 'error')

# 8. Ручное распределение тем же API, который вызывает кнопка
r = client.post(f'/docs/packages/{pkg_id}/route-all')
route_result = r.get_json()
check('ручное распределение выполнено',
      r.status_code == 200 and route_result.get('routed', 0) >= 1,
      r.get_data(as_text=True))
r = client.get(f'/docs/packages/{pkg_id}')
d = r.get_json()
files = {f['orig_name']: f for f in d['files']}
akt = files.get('Акт выполненных работ.txt')
check('акт распределён в «Акты» после нажатия',
      akt and akt['catalog']['name'] == 'Акты',
      json.dumps(d['files'], ensure_ascii=False)[:400])

# 9. Текст страницы
r = client.get(f"/docs/files/{akt['id']}")
fd = r.get_json()
page1 = fd['pages'][0]
check('текст извлечён', 'Акт' in page1['text'], page1['text'][:100])

# 10. Изображение страницы
if page1['has_image']:
    r = client.get(f"/docs/files/{akt['id']}/pages/1/image")
    check('изображение страницы', r.status_code == 200 and r.data[:4] == b'\x89PNG')
else:
    check('изображение страницы', False, 'нет изображения')

# 10. Ручное перемещение файла
other = files.get('прочий документ.txt')
r = client.post(f"/docs/files/{other['id']}/set-catalog", json=dict(catalog_id=cat_id))
check('ручное перемещение', r.status_code == 200)

# Очистка временной среды
import shutil
shutil.rmtree(_TMP, ignore_errors=True)
print('\nВСЕ ТЕСТЫ ПРОЙДЕНЫ')
