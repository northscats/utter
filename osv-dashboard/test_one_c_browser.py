"""Opt-in public preview smoke test. Creates and removes its own dev fixtures.

Run from osv-dashboard:
    python test_one_c_browser.py --public-preview
Requires Playwright and system Chromium. Never run against production.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import ssl
import uuid
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-preview', action='store_true', required=True)
    parser.parse_args()
    if os.environ.get('REPLIT_DEPLOYMENT'):
        raise RuntimeError('Development preview only')
    host = os.environ['REPLIT_DEV_DOMAIN']
    if not host.endswith('.replit.dev'):
        raise RuntimeError('Only a development preview domain is permitted')
    origin = 'https://' + host
    # Playwright's Node API client must trust the same system CA bundle as
    # urllib and Chromium; do not disable TLS verification for route.fetch.
    ca_file = ssl.get_default_verify_paths().cafile
    if ca_file:
        os.environ.setdefault('NODE_EXTRA_CA_CERTS', ca_file)
    from app import app
    from database import Company, OneCChannel, User, db
    from playwright.sync_api import sync_playwright, expect
    marker = uuid.uuid4().hex
    user_id = company_id = None
    app.config['TESTING'] = True
    try:
        with app.app_context():
            user = User(username='one-c-browser-' + marker,
                        password_hash='not-a-login-password', role='admin')
            db.session.add(user)
            db.session.flush()
            user_id = user.id
            company = Company(name='Проверка приёмника 1С ' + marker[:6],
                              inn='1234567890', created_by=user_id,
                              status='active')
            db.session.add(company)
            db.session.commit()
            company_id = company.id
        fixture_client = app.test_client()
        with fixture_client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['password_version'] = 1
        session_value = fixture_client.get_cookie('session').value
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=shutil.which('chromium'),
                                         headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1400, 'height': 1000})
            context.add_cookies([{'name': 'session', 'value': session_value,
                                 'domain': host, 'path': '/', 'secure': True}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/settings?tab=integrations', wait_until='networkidle')
            expect(page.locator('#one_c_channels_container')).not_to_contain_text(
                'Загрузка каналов')
            page.get_by_role('button', name='Создать канал обмена данными').click()
            page.locator('#one_c_new_company').select_option(str(company_id))
            page.locator('#one_c_btn_create').click()
            expect(page.locator('#one_c_creds_pass')).not_to_have_value('')
            channel_url = page.locator('#one_c_creds_url').input_value()
            login = page.locator('#one_c_creds_login').input_value()
            password = page.locator('#one_c_creds_pass').input_value()
            assert channel_url.startswith(origin + '/api/1c/push/')
            channel_id = int(channel_url.rsplit('/', 1)[1])
            page.keyboard.press('Escape')
            expect(page.locator('#one_c_creds_pass')).to_have_value('')
            page.close()
            # No administrator page is open; Basic Auth alone receives data.
            rows = [{'ОрганизацияИНН': '1234567890',
                     'Аналитика2': '001', 'Аналитика10': None,
                     'Период': '2026-09-07T21:00:00Z', 'Пустая': '',
                     **{f'Аналитика{n}': str(n) for n in range(11, 76)}}
                    for _ in range(65)]
            rows[-1]['Только в последней строке'] = '<img src=x onerror=alert(1)>'
            raw = json.dumps({'ErrorCode': 0, 'ErrorDescription': '',
                              'Result': rows}, ensure_ascii=False).encode()
            auth = base64.b64encode((login + ':' + password).encode()).decode()

            def push(body):
                req = Request(channel_url, data=body, method='POST', headers={
                    'Content-Type': 'application/json; charset=utf-8',
                    'Authorization': 'Basic ' + auth})
                with urlopen(req, timeout=30) as response:
                    assert response.status == 200
                    return json.load(response)['Result']['receipt_id']

            receipt = push(raw)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/settings?tab=integrations', wait_until='networkidle')
            row = page.locator('#one_c_channels_container tr').filter(has_text=marker[:6])
            expect(row).to_contain_text('UTC')
            expect(row).not_to_contain_text('Invalid Date')
            row.get_by_role('button', name='Последние данные').click()
            expect(page.locator('#one_c_data_content tbody tr')).to_have_count(50)
            assert page.locator('#one_c_data_content th').count() <= 31
            page.evaluate('OneC.dataPage(1)')
            expect(page.locator('#one_c_data_content tbody tr')).to_have_count(15)
            page.evaluate('OneC.colPage(1)')
            expect(page.locator('#one_c_col_info')).to_contain_text('31')
            page.evaluate('OneC.colPage(1)')
            expect(page.locator('#one_c_data_content')).to_contain_text(
                '<img src=x onerror=alert(1)>')
            assert page.locator('#one_c_data_content img').count() == 0
            screenshots = Path('../screenshots')
            screenshots.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshots / 'one-c-table-desktop.png'))
            push(b'{"ErrorCode":0,"ErrorDescription":"","Result":[]}')
            page.evaluate('OneC.refreshData()')
            expect(page.locator('#one_c_data_content')).to_contain_text('Пустой результат')
            page.keyboard.press('Escape')
            row.get_by_role('button', name='Настройки').click()
            expect(page.locator('#one_c_btn_rotate')).to_be_enabled()
            expect(page.locator('#one_c_edit_enabled')).to_be_enabled()
            expect(page.locator('#one_c_edit_url')).not_to_have_value('')
            expect(page.locator('#one_c_edit_login')).to_have_value(login)
            expect(page.locator('#one_c_creds_pass')).to_have_value('')
            held = []
            context.route('**/settings/1c/channels/*/rotate',
                          lambda route: held.append(route))
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('#one_c_btn_rotate').click()
            page.wait_for_timeout(100)
            assert held, 'Rotation request was not captured'
            page.keyboard.press('Escape')
            row.get_by_role('button', name='Настройки').click()
            delayed_response = held[0].fetch()
            held[0].fulfill(response=delayed_response)
            page.wait_for_timeout(300)
            expect(page.locator('#one_c_creds_pass')).to_have_value('')
            expect(page.locator('#one_c_creds_result')).not_to_be_visible()
            expect(page.locator('#one_c_btn_rotate')).to_be_enabled()
            context.unroute('**/settings/1c/channels/*/rotate')
            page.keyboard.press('Escape')
            page.set_viewport_size({'width': 390, 'height': 844})
            row.get_by_role('button', name='Последние данные').click()
            expect(page.locator('#one_c_data_content')).to_contain_text('Пустой результат')
            expect(page.locator('#one_c_data_status')).to_contain_text('Получено')
            page.screenshot(path=str(screenshots / 'one-c-table-mobile.png'))
            page.keyboard.press('Escape')
            assert not errors, errors
            browser.close()
        print('PASS public HTTPS -> Flask; create, one-time credentials, '
              'Basic Auth without open admin page, 65 rows/71 columns, late keys, '
              'pagination, XSS, empty snapshot, delayed rotation, desktop/mobile dialogs')
    finally:
        with app.app_context():
            if company_id is not None:
                company = db.session.get(Company, company_id)
                if company:
                    db.session.delete(company)
            if user_id is not None:
                user = db.session.get(User, user_id)
                if user:
                    db.session.delete(user)
            db.session.commit()


if __name__ == '__main__':
    main()