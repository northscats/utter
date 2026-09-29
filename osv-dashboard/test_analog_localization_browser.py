"""Isolated Chromium regression for the Russian analogue interface.

Run from osv-dashboard: python test_analog_localization_browser.py
Uses a temporary SQLite database, temporary analogue source directory and
Playwright route interception backed by Flask's test client. Never connects
to a running app, a deployed database, or deployed file storage.
Screenshots are written to screenshots/analog-localization/ by default.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch


def _login(client, user):
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
        session['password_version'] = int(user.password_version or 1)


def _preview(client, conclusion, ssr):
    response = client.post(
        '/analog-search/import/preview',
        data={'conclusion': (BytesIO(conclusion), 'conclusion.xml'),
              'ssr': (BytesIO(ssr), 'summary.gge')},
        content_type='multipart/form-data')
    assert response.status_code == 200, response.get_data(as_text=True)
    result = response.get_json()
    assert result['can_confirm'], result['diagnostics']
    return result['import_id']


def _browser_context(browser, flask_client):
    """Render the actual Flask responses in Chromium without starting a server."""
    context = browser.new_context(viewport={'width': 1440, 'height': 1100})
    session = flask_client.get_cookie('session')
    assert session, 'Flask test client must be authenticated'
    context.add_cookies([{'name': 'session', 'value': session.value,
                          'domain': 'analog.test', 'path': '/'}])

    def respond(route):
        from urllib.parse import urlsplit
        request = route.request
        url = urlsplit(request.url)
        path = url.path + ('?' + url.query if url.query else '')
        response = flask_client.open(
            path, method=request.method, data=request.post_data or None,
            headers={'Cookie': request.headers.get('cookie', '')})
        headers = [(name, value) for name, value in response.headers
                   if name.lower() not in ('content-length', 'content-encoding',
                                            'transfer-encoding', 'set-cookie')]
        route.fulfill(status=response.status_code,
                      headers=dict(headers), body=response.data)

    context.route('https://analog.test/**', respond)
    return context


def _expanded_text(page):
    """Expand every visible nested details section, including GGE cost leaves."""
    page.evaluate("""() => {
      for (let i = 0; i < 10; i++) {
        const closed = [...document.querySelectorAll('#detail-tab details:not([open])')];
        if (!closed.length) break;
        for (const item of closed) item.open = true;
      }
    }""")
    return page.locator('#detail-tab').inner_text()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path,
                        default=Path(__file__).parent.parent /
                                'screenshots' / 'analog-localization')
    args = parser.parse_args()
    if os.environ.get('REPLIT_DEPLOYMENT'):
        raise RuntimeError('The browser regression must not run in deployment')

    # Set the database URI before importing app: importing app initializes db.
    with tempfile.TemporaryDirectory(prefix='analog_localization_') as temp:
        root = Path(temp)
        os.environ['DATABASE_URL'] = 'sqlite:///' + str(root / 'analog.db')
        os.environ['INITIAL_ADMIN_PASSWORD'] = 'test-only-localization-password'
        os.environ.setdefault('ENCRYPTION_KEY',
                              'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=')
        os.environ.setdefault('REPLIT_DOMAINS', 'preview.example')
        from app import app
        from database import (AnalogImport, AnalogPassportVersion, User,
                              UserModuleAccess, db)
        import analog_routes
        from flask import g
        from playwright.sync_api import expect, sync_playwright

        assets = Path(__file__).parent.parent / 'attached_assets'
        conclusion = (assets /
                      '56-1-1-2-044346-2024_24-08-06_09-12-42_signature_1789537746147.xml').read_bytes()
        ssr = (assets / 'Сводный_сметный_расчет_1789537733540.gge').read_bytes()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        # This fixture deliberately retains an app context while driving two
        # independent test clients; Flask-Login normally caches identity on g,
        # so clear it at every request to preserve genuine per-client identity.
        def clear_cached_login():
            g.pop('_login_user', None)
        app.before_request(clear_cached_login)
        app.before_request_funcs[None].remove(clear_cached_login)
        app.before_request_funcs[None].insert(0, clear_cached_login)
        with patch.object(analog_routes, 'ANALOG_STORAGE', str(root / 'sources')):
            with app.app_context():
                db.create_all()
                admin = User(username='localization-admin', password_hash='unused',
                             role='admin', password_change_required=False)
                user = User(username='localization-user', password_hash='unused',
                            role='user', password_change_required=False)
                db.session.add_all([admin, user])
                db.session.flush()
                db.session.add(UserModuleAccess(
                    user_id=user.id, module_key='analog_search'))
                db.session.commit()
                admin_client, user_client = app.test_client(), app.test_client()
                _login(admin_client, admin)
                _login(user_client, user)

                # Each role previews its OWN import: a regular user cannot open
                # another user's preview. Different bytes for one conclusion
                # cannot replace the original document pair.
                admin_preview = _preview(admin_client, conclusion, ssr)
                user_preview = _preview(
                    user_client, conclusion + b'\n<!-- user preview -->', ssr)
                private_preview = _preview(
                    admin_client, conclusion + b'\n<!-- private preview -->',
                    ssr + b'\n<!-- private preview -->')
                assert db.session.get(AnalogImport, admin_preview).created_by == admin.id
                assert db.session.get(AnalogImport, user_preview).created_by == user.id
                preview_access = admin_client.get(
                    f'/analog-search/imports/{admin_preview}/preview')
                assert preview_access.status_code == 200, (
                    preview_access.status_code, admin.id, user.id,
                    db.session.get(AnalogImport, admin_preview).created_by,
                    admin_client.get('/analog-search').status_code)
                first = admin_client.post(
                    f'/analog-search/import/{admin_preview}/confirm', json={})
                assert first.status_code == 200, first.get_data(as_text=True)
                passport_id = first.get_json()['passport_id']
                original_version = db.session.get(
                    AnalogPassportVersion, first.get_json()['version_id'])
                rejected = admin_client.post(
                    '/analog-search/import/preview',
                    data={'conclusion': (BytesIO(conclusion + b'\n<!-- second version -->'),
                                         'conclusion.xml'),
                          'ssr': (BytesIO(ssr), 'summary.gge')},
                    content_type='multipart/form-data')
                assert rejected.status_code == 409
                assert admin_client.post(
                    f'/analog-search/import/{private_preview}/confirm',
                    json={}).status_code == 409
                old_payload = json.loads(original_version.payload_json)
                item = next(i for i in old_payload['ssr']['items']
                            if i['name'] == 'Конструктивные решения')
                assert item['item_type'] == 'LocalEstimate'
                assert item['details']['Building']['WorkersSalary'] == '111.13'
                assert item['link'] == 'Конструктивные решения.gsfx'
                assert old_payload['conclusion']['roles'][0]['role'] == 'expert'
                assert analog_routes.analog_label(
                    'UnknownInternalCode', 'item_type') == 'Иной тип сметной строки'
                assert analog_routes.analog_label(
                    'Строительные работы', 'cost_field') == 'Строительные работы'
                assert 'not well-formed' not in analog_routes.analog_message(
                    'ССР: повреждённый XML: not well-formed: line 1')
                api = admin_client.get(
                    f'/analog-search/passports/{passport_id}?format=json')
                assert api.status_code == 200
                assert api.get_json()['current_version'] is not None
                private_access = user_client.get(
                    f'/analog-search/imports/{private_preview}/preview')
                assert private_access.status_code == 403, (
                    private_access.status_code, private_access.headers.get('Location'))

                screenshot_dir = args.screenshots.resolve()
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                errors = []
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        executable_path=shutil.which('chromium') or None,
                        headless=True, args=['--no-sandbox'])
                    try:
                        for role, client, preview in (
                                ('user', user_client, user_preview),
                                ('admin', admin_client, private_preview)):
                            context = _browser_context(browser, client)
                            page = context.new_page()
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto('https://analog.test/analog-search/simple?view=search',
                                      wait_until='networkidle')
                            expect(page.get_by_role(
                                'heading', name='Поиск объектов-аналогов')).to_be_visible()
                            path = screenshot_dir / f'{role}-search.png'
                            page.screenshot(path=str(path), full_page=True)
                            print(path)
                            page.goto(
                                f'https://analog.test/analog-search/imports/{preview}/preview',
                                wait_until='networkidle')
                            expect(page.locator('#analog-preview-page')).to_be_visible()
                            expect(page.get_by_role(
                                'heading', name='Предварительный просмотр импорта')).to_be_visible()
                            assert 'БМК' in page.locator('#analog-preview-page').inner_text()
                            page.locator('#analog-preview-page details').evaluate_all(
                                '(items) => items.forEach(item => item.open = true)')
                            preview_text = page.locator('#analog-preview-page').inner_text()
                            assert 'Экспертная организация' in preview_text
                            assert 'Заявитель' in preview_text
                            assert not re.search(r'\bexpert\b|\bapplicant\b', preview_text)
                            # A source proper name is NOT a machine key and must
                            # remain exactly as provided by the original document.
                            assert 'Конструктивные решения' in preview_text
                            path = screenshot_dir / f'{role}-preview.png'
                            page.screenshot(path=str(path), full_page=True)
                            print(path)
                            page.goto(
                                f'https://analog.test/analog-search/passports/{passport_id}',
                                wait_until='networkidle')
                            expect(page.locator('#brief-tab')).to_be_visible()
                            assert 'МВт/Гкал/ч' in page.locator('#brief-tab').inner_text()
                            path = screenshot_dir / f'{role}-brief.png'
                            page.screenshot(path=str(path), full_page=True)
                            print(path)
                            page.locator('button[data-target="detail-tab"]').click()
                            expect(page.locator('#detail-tab')).to_be_visible()
                            detail = _expanded_text(page)
                            assert 'ГРАНД-Смета' in detail
                            assert 'Конструктивные решения.gsfx' in detail
                            assert '111.13' in detail
                            assert 'Оплата труда рабочих' in detail
                            assert 'WorkersSalary' not in detail
                            assert 'LocalEstimate' not in detail
                            assert 'Building' not in detail
                            assert original_version.source_hash not in detail
                            expect(page.get_by_text(
                                'Диагностика и происхождение', exact=True)).to_have_count(0)
                            assert 'Сырые байты сохранены' not in detail
                            if role == 'user':
                                assert 'Администрирование паспорта' not in detail
                            else:
                                assert 'Администрирование паспорта' in detail
                            path = screenshot_dir / f'{role}-expanded.png'
                            page.screenshot(path=str(path), full_page=True)
                            print(path)
                            page.locator('button[data-target="brief-tab"]').click()
                            expect(page.locator('#brief-tab')).to_be_visible()
                            expect(page.locator('#detail-tab')).to_be_hidden()
                            context.close()
                        assert not errors, errors
                    finally:
                        browser.close()
                print('PASS: isolated user/admin previews, immutable passport, '
                      'expanded details, original values and API machine keys')


if __name__ == '__main__':
    main()