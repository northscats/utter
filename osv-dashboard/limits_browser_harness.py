"""Opt-in isolated Task 301 browser server; never imports production data.

From the repository root:
  .pythonlibs/bin/python osv-dashboard/limits_browser_harness.py --port 8091

The printed directory contains synthetic XLSX fixtures and the disposable DB.
Only this entrypoint installs transport stubs; production modules are unchanged.
"""
import argparse
from datetime import date
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time


def fixtures(root):
    """Generate 68 real semantic columns, not a mocked parser response."""
    from openpyxl import Workbook
    from limits_parser import _BASIC_HEADERS, parse_xlsx

    headers = list(_BASIC_HEADERS) + [
        'Предельная стоимость объекта, тыс. руб.',
        'Плановые даты дата начала работ',
        'Плановые даты дата завершения работ',
        'Плановые даты дата открытия',
    ]
    while len(headers) < 68:
        year = 2026 + len(headers) - 20
        headers.append(
            f'Объем финансирования, тыс. руб. в {year} году средства федерального бюджета')
    directory = root / 'fixtures'
    directory.mkdir()
    for number, amount in ((1, 100), (2, 250), (3, 400)):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        sheet.append(list(range(1, 69)))  # GIS REB's second header row.
        row = []
        for header in headers:
            if header == 'уин':
                value = '301.001'
            elif header == 'наименование объекта из ап':
                value = f'Тестовая школа — снимок {number}'
            elif header == 'адрес':
                value = 'Синтетическая улица, 301'
            elif header.startswith('Плановые даты'):
                value = date(2026, 9, 1)
            elif header.startswith(('Предельная стоимость', 'Объем финансирования')):
                value = amount
            else:
                value = '1'
            row.append(value)
        sheet.append(row)
        target = directory / f'snapshot-{number}.xlsx'
        workbook.save(target)
        parsed = parse_xlsx(target)
        assert len(parsed['columns']) == 68 and len(parsed['rows']) == 1
    (directory / 'invalid.xlsx').write_bytes(b'Intentional invalid XLSX for Task 301')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--root', help='New, non-existent directory for disposable data')
    parser.add_argument('--fixtures-only', action='store_true')
    args = parser.parse_args()
    if os.environ.get('REPLIT_DEPLOYMENT'):
        raise RuntimeError('Browser harness is forbidden in deployment')
    if args.root:
        root = Path(args.root).resolve()
        root.mkdir(parents=True, exist_ok=False)
    else:
        root = Path(tempfile.mkdtemp(prefix='limits-browser-301-'))
    fixture_dir = fixtures(root)
    print(f'Synthetic fixtures: {fixture_dir}', flush=True)
    if args.fixtures_only:
        return

    # Must precede importing app/database; never use the inherited DB or secrets.
    from cryptography.fernet import Fernet
    os.environ['DATABASE_URL'] = 'sqlite:///' + str(root / 'browser.sqlite')
    os.environ['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
    os.environ['SESSION_SECRET'] = secrets.token_hex(32)
    os.environ['SCRIPT_NAME'] = ''
    for name in ('DURABLE_BACKUP_DIR', 'INITIAL_ADMIN_PASSWORD', 'ADMIN_INITIAL_PASSWORD',
                 'MAX_BOT_TOKEN', 'MAX_LIMITS_BOT_TOKEN', 'MAX_LIMITS_WEBHOOK_URL'):
        os.environ.pop(name, None)

    import app as application
    from database import db, User, Setting
    import max_bot_config
    import max_limits
    from flask import abort, request

    app = application.app
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True,
                      SESSION_COOKIE_SECURE=False)
    application.UPLOADS = str(root / 'uploads')
    application.OUTPUT = str(root / 'output')
    application.DOCS_STORAGE = str(root / 'uploads' / 'docs')
    application.CONTROL_SNAPSHOT_UPLOADS = application.UPLOADS
    for directory in (application.UPLOADS, application.OUTPUT, application.DOCS_STORAGE):
        Path(directory).mkdir(parents=True, exist_ok=True)
    max_bot_config.CONFIG = root / 'private' / 'max-bot.json'
    application._ensure_imap_thread = lambda: None
    max_limits.start_max_limits_worker = lambda *args, **kwargs: False
    max_limits.stop_max_limits_worker = lambda *args, **kwargs: None
    max_limits.status = lambda: 'stopped'

    # Any accidental outgoing network access fails visibly, even from a worker.
    def no_network(*args, **kwargs):
        raise RuntimeError('External transport disabled by isolated browser harness')
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network

    @app.before_request
    def harness_boundaries():
        if request.path.startswith('/limit-notifications/max/') and request.method == 'POST':
            abort(403, description='MAX operations disabled in isolated harness')

    # Preserve real background parsing, but leave queued state visible for polling.
    background = application._limit_parse_in_background
    def delayed_background(version_id):
        import threading
        def launch():
            time.sleep(2)
            background(version_id)
        threading.Thread(target=launch, daemon=True).start()
    application._limit_parse_in_background = delayed_background

    with app.app_context():
        db.create_all()
        db.session.add(User(
            username='limits-browser-admin', role='admin',
            password_hash=application._hash_password('Browser-only-301!Pass'),
            password_version=1, password_change_required=False))
        Setting.set('limits_history_ready', 'ready')
        db.session.commit()
        from max_dataset_scope import migrate_selected_version
        migrate_selected_version()
    application._limits_history_ready.set()
    print(f'Isolated DB/storage: {root}', flush=True)
    print(f'Login: http://127.0.0.1:{args.port}/login', flush=True)
    print('Disposable login: limits-browser-admin / Browser-only-301!Pass', flush=True)
    print('Upload snapshot-1.xlsx; select it in MAX; upload snapshot-2.xlsx; '
          'verify ready report/download and unchanged MAX selection; upload invalid.xlsx. '
          'No real MAX sends are possible.', flush=True)
    app.run(host='127.0.0.1', port=args.port, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()