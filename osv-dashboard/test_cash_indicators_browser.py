"""Visual regression for the authorised cash indicators modal.

Run from osv-dashboard while the development workflow is available:
    python test_cash_indicators_browser.py --public-preview

To intentionally replace the committed references:
    python test_cash_indicators_browser.py --public-preview --update-snapshots

The test creates and removes its own empty company. All indicator values are
synthetic and are injected in the browser; no user or accounting data is read.
"""
import argparse
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import uuid

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parent.parent
REFERENCES = ROOT / 'screenshots'
SNAPSHOTS = (
    'cash-table-full.png',
    'cash-table-partial.png',
    'cash-modal-narrow.png',
)


def snapshot(date, value, accounts):
    missing = [account for account in ('50', '51', '52', '55')
               if account not in accounts]
    coverage = 'complete' if not missing else 'partial'
    return {
        'date': date,
        'label': date,
        'value': value,
        'debit_value': value + 3_000_000,
        'credit_value': value / 2,
        'accounts': accounts,
        'missing_accounts': missing,
        'coverage': coverage,
        'cumulative_accounts': accounts,
        'cumulative_missing_accounts': missing,
        'cumulative_coverage': coverage,
        'comparison': {
            'comparable': False,
            'previous_date': None,
            'date': date,
            'value': None,
            'reason': 'Первая точка синтетического периода',
        },
    }


def card(accounts):
    values = {
        '50': 1_250_000,
        '51': 12_500_000,
        '52': 3_750_000,
        '55': 7_500_000,
    }
    total = sum(values[account] for account in accounts)
    first = snapshot('2026-08-31', total - 1_000_000, accounts)
    latest = snapshot('2026-09-17', total, accounts)
    latest['comparison'] = {
        'comparable': True,
        'previous_date': first['date'],
        'date': latest['date'],
        'accounts': accounts,
        'value': 1_000_000,
        'reason': None,
    }
    return {
        **latest,
        'id': 'cash',
        'title': 'Денежные средства',
        'unit': 'RUB',
        'breakdown': [
            {'label': f'Счёт {account}', 'value': values[account]}
            for account in accounts
        ],
        'series': [first, latest],
        'previous_year_series': [
            snapshot('2025-08-31', total - 2_000_000, accounts),
            snapshot('2025-09-17', total - 1_500_000, accounts),
        ],
        'delta': {
            **latest['comparison'],
            'unit': 'RUB',
            'label': '2026-08-31 → 2026-09-17',
        },
    }


def assert_matches(actual, reference, update):
    if update:
        reference.parent.mkdir(exist_ok=True)
        shutil.copyfile(actual, reference)
        return
    if not reference.exists():
        raise AssertionError(
            f'Missing visual reference {reference}. '
            'Run once with --update-snapshots and review the images.')
    with Image.open(actual).convert('RGB') as actual_image, \
            Image.open(reference).convert('RGB') as reference_image:
        assert actual_image.size == reference_image.size, (
            f'{reference.name}: size changed from {reference_image.size} '
            f'to {actual_image.size}')
        difference = ImageChops.difference(actual_image, reference_image)
        pixels = (difference.get_flattened_data()
                  if hasattr(difference, 'get_flattened_data')
                  else difference.getdata())
        changed = sum(
            1 for pixel in pixels
            if max(pixel) > 20
        )
        ratio = changed / (actual_image.width * actual_image.height)
        assert ratio <= 0.002, (
            f'{reference.name}: {ratio:.2%} of pixels changed '
            '(allowed 0.20%); inspect {actual}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-preview', action='store_true', required=True)
    parser.add_argument('--update-snapshots', action='store_true')
    args = parser.parse_args()
    if os.environ.get('REPLIT_DEPLOYMENT'):
        raise RuntimeError('Development preview only')
    host = os.environ['REPLIT_DEV_DOMAIN']
    if not host.endswith('.replit.dev'):
        raise RuntimeError('Only a development preview domain is permitted')
    ca_file = ssl.get_default_verify_paths().cafile
    if ca_file:
        os.environ.setdefault('NODE_EXTRA_CA_CERTS', ca_file)

    from app import app
    from database import Company, User, db
    from playwright.sync_api import expect, sync_playwright

    marker = uuid.uuid4().hex
    user_id = company_id = None
    app.config['TESTING'] = True
    actual_dir = Path(tempfile.mkdtemp(prefix='cash_visual_'))
    succeeded = False
    try:
        with app.app_context():
            user = User(username='cash-visual-' + marker,
                        password_hash='not-a-login-password', role='admin')
            db.session.add(user)
            db.session.flush()
            user_id = user.id
            company = Company(name='Синтетическая проверка ДДС ' + marker[:6],
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
            browser = pw.chromium.launch(
                executable_path=shutil.which('chromium'),
                headless=True, args=['--no-sandbox'])
            context = browser.new_context(
                viewport={'width': 1400, 'height': 1100},
                device_scale_factor=1,
                reduced_motion='reduce',
            )
            context.add_cookies([{
                'name': 'session', 'value': session_value,
                'domain': host, 'path': '/', 'secure': True,
            }])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(
                f'https://{host}/company/{company_id}/key-indicators',
                wait_until='domcontentloaded')
            page.wait_for_function(
                '() => window.KI_App && window.KI_App.openModal')
            page.wait_for_function(
                "() => !document.querySelector('#ki-loading.active')")

            def open_cash(cash_card):
                page.evaluate("""cashCard => {
                    const app = window.KI_App;
                    window.KI_Layout = null;
                    app.state.mode = 'operational';
                    app.state.data = {
                        period: {label: 'Синтетический период'},
                        cards: [cashCard],
                    };
                    app.openModal('cash');
                }""", cash_card)
                expect(page.locator('#ki-modal')).to_be_visible()
                expect(page.locator('#ki-modal-title')).to_contain_text(
                    'Ликвидность и денежные средства')
                expect(page.locator('.cash-table tbody tr')).to_have_count(5)

            open_cash(card(['50', '51', '52', '55']))
            full_path = actual_dir / SNAPSHOTS[0]
            page.locator('.cash-table-scroll').screenshot(path=str(full_path))
            assert_matches(full_path, REFERENCES / SNAPSHOTS[0],
                           args.update_snapshots)

            page.evaluate('window.KI_App.closeModal(true)')
            open_cash(card(['51', '55']))
            expect(page.locator('.cash-coverage-note')).to_have_text(
                'Частичный охват')
            expect(page.locator('.cash-data-state.missing')).to_have_count(2)
            partial_path = actual_dir / SNAPSHOTS[1]
            page.locator('.cash-table-scroll').screenshot(path=str(partial_path))
            assert_matches(partial_path, REFERENCES / SNAPSHOTS[1],
                           args.update_snapshots)

            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('.cash-table-scroll').scroll_into_view_if_needed()
            page.locator('.cash-table-scroll').evaluate(
                'element => { element.scrollLeft = 180; }')
            dimensions = page.evaluate("""() => {
                const modal = document.querySelector('#ki-modal .modal');
                const table = document.querySelector('.cash-table-scroll');
                return {
                    viewport: document.documentElement.clientWidth,
                    modalLeft: modal.getBoundingClientRect().left,
                    modalRight: modal.getBoundingClientRect().right,
                    tableClient: table.clientWidth,
                    tableScroll: table.scrollWidth,
                    tableLeft: table.scrollLeft,
                };
            }""")
            assert dimensions['modalLeft'] >= 0
            assert dimensions['modalRight'] <= dimensions['viewport']
            assert dimensions['tableScroll'] > dimensions['tableClient']
            assert dimensions['tableLeft'] > 0
            narrow_path = actual_dir / SNAPSHOTS[2]
            page.locator('#ki-modal .modal').screenshot(path=str(narrow_path))
            assert_matches(narrow_path, REFERENCES / SNAPSHOTS[2],
                           args.update_snapshots)
            assert not errors, errors
            browser.close()
        print('PASS: authorised synthetic cash modal matches full, partial, '
              'and narrow scrolling visual references')
        succeeded = True
    finally:
        if succeeded:
            shutil.rmtree(actual_dir, ignore_errors=True)
        else:
            print(f'Actual visual snapshots kept in {actual_dir}')
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