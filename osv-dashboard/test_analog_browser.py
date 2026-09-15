"""Opt-in browser regression for the analogue import preview.

Run from osv-dashboard:
    python test_analog_browser.py --public-preview
Requires Playwright and system Chromium. Never run against production.
"""
import argparse
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import uuid


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
    ca_file = ssl.get_default_verify_paths().cafile
    if ca_file:
        os.environ.setdefault('NODE_EXTRA_CA_CERTS', ca_file)

    from app import app
    from database import AnalogImport, User, db
    from playwright.sync_api import expect, sync_playwright

    assets = Path(__file__).parent.parent / 'attached_assets'
    conclusion = next(assets.glob('*signature_*.xml'))
    ssr = next(assets.glob('*.gge'))
    marker = uuid.uuid4().hex
    user_id = None
    app.config['TESTING'] = True
    broken = Path(tempfile.mkdtemp(prefix='analog_browser_')) / 'broken.xml'
    broken.write_text('<broken><xml>', encoding='utf-8')
    try:
        with app.app_context():
            user = User(username='analog-browser-' + marker,
                        password_hash='not-a-login-password', role='admin')
            user.password_change_required = False
            db.session.add(user)
            db.session.commit()
            user_id = user.id
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
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            page.goto(origin + '/analog-search', wait_until='networkidle')
            page.locator('input[name="conclusion"]').set_input_files(str(conclusion))
            page.locator('input[name="ssr"]').set_input_files(str(ssr))
            page.get_by_role('button', name='Предварительный просмотр').click()
            expect(page.locator('#analog-preview-page')).to_be_attached(timeout=60000)
            expect(page.get_by_role('heading',
                                    name='Проверка пары источников')).to_be_visible()
            expect(page.get_by_role('button',
                                    name='Подтвердить импорт')).to_be_visible()
            assert '/analog-search/imports/' in page.url
            assert page.url.endswith('/preview')
            page.reload(wait_until='networkidle')
            expect(page.get_by_role('heading',
                                    name='Проверка пары источников')).to_be_visible()
            expect(page.get_by_role('button',
                                    name='Подтвердить импорт')).to_be_visible()

            page.goto(origin + '/analog-search', wait_until='networkidle')
            page.locator('input[name="conclusion"]').set_input_files(str(broken))
            page.locator('input[name="ssr"]').set_input_files(str(ssr))
            page.get_by_role('button', name='Предварительный просмотр').click()
            expect(page.locator('#analog-import-error')).to_contain_text(
                'повреждённый XML')
            expect(page.get_by_role('button',
                                    name='Предварительный просмотр')).to_be_enabled()
            assert not page_errors, page_errors
            browser.close()
        print('PASS analogue preview: real files, automatic preview, parse error retry')
    finally:
        with app.app_context():
            if user_id is not None:
                AnalogImport.query.filter_by(created_by=user_id).delete()
                user = db.session.get(User, user_id)
                if user:
                    db.session.delete(user)
                db.session.commit()
        shutil.rmtree(broken.parent, ignore_errors=True)


if __name__ == '__main__':
    main()