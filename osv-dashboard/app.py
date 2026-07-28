# -*- coding: utf-8 -*-
"""ОСВ Дашборд — веб-приложение с PostgreSQL, авторизацией и мультипредприятием."""
import json
import os
from datetime import datetime, timezone
import re
import time
import traceback

import threading

import bcrypt
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from functools import wraps

from jobs import create_job, get_job, finish_job, fail_job

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from database import (AiProvider, AiSummary, AuditLog, Company, DailyRow,
                       EmailRule, Setting, Upload, User, db)
from osvparser import extract_org_name, parse_many, parse_zip
from builder import build_dataset

# ---------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, 'uploads')
OUTPUT = os.path.join(BASE, 'output')
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

# Префикс пути, если приложение работает не в корне домена.
# Задаётся через переменную окружения: SCRIPT_NAME=/osv
# При развёртывании в корне оставьте переменную пустой или не задавайте её.
SCRIPT_NAME = os.environ.get('SCRIPT_NAME', '').rstrip('/')

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-change-me')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 МБ
app.config['WTF_CSRF_TIME_LIMIT'] = None   # токен живёт до истечения сессии

# Если задан подпуть — сообщаем Flask, где он «живёт»
if SCRIPT_NAME:
    app.config['APPLICATION_ROOT'] = SCRIPT_NAME
    app.config['SESSION_COOKIE_PATH'] = SCRIPT_NAME
    # Доверяем заголовкам X-Forwarded-* от nginx/Apache
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# --- PostgreSQL —  DATABASE_URL из переменной окружения ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# Шифрование значений в БД (Fernet / AES-128-CBC)
# ---------------------------------------------------------------------------
import sys as _sys
from cryptography.fernet import Fernet as _Fernet

_ENCRYPTION_KEY_RAW = os.environ.get('ENCRYPTION_KEY', '')
if not _ENCRYPTION_KEY_RAW:
    print(
        '\n[FATAL] Переменная окружения ENCRYPTION_KEY не задана.\n'
        'Сгенерируйте ключ:\n'
        '  python3 -c "import secrets,base64; '
        'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"\n'
        'и сохраните как Replit Secret с именем ENCRYPTION_KEY.\n',
        file=_sys.stderr, flush=True,
    )
    _sys.exit(1)
try:
    _FERNET = _Fernet(_ENCRYPTION_KEY_RAW.encode())
except Exception as _fernet_err:
    print(f'[FATAL] Неверный формат ENCRYPTION_KEY: {_fernet_err}',
          file=_sys.stderr, flush=True)
    _sys.exit(1)

# ---------------------------------------------------------------------------
# IMAP background thread state (single-worker gunicorn keeps this in one process)
# ---------------------------------------------------------------------------
_imap_stop = threading.Event()           # set() to request graceful shutdown
_imap_thread: threading.Thread | None = None
_imap_thread_started = False
_imap_thread_lock = threading.Lock()

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = None


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _ensure_imap_thread() -> None:
    """Start the IMAP background thread once per process (idempotent)."""
    global _imap_thread, _imap_thread_started
    if _imap_thread_started:
        return
    with _imap_thread_lock:
        if _imap_thread_started:
            return
        _imap_thread_started = True
    _imap_thread = threading.Thread(target=_imap_loop, daemon=True, name='imap-loop')
    _imap_thread.start()


@app.before_request
def _maybe_start_imap_thread():
    """Lazy IMAP thread starter — fires on the first HTTP request."""
    _ensure_imap_thread()


def admin_required(f):
    """Декоратор: доступ только для администраторов."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Jinja-фильтр для русского склонения
# ---------------------------------------------------------------------------
@app.template_filter('pluralize')
def pluralize_filter(n, one, few, many):
    n = abs(int(n))
    if 11 <= n % 100 <= 19:
        return many
    r = n % 10
    if r == 1:
        return one
    if 2 <= r <= 4:
        return few
    return many


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def render_dashboard(dataset, back_url='/'):
    tpl_path = os.path.join(BASE, 'templates', 'dashboard_template.html')
    with open(tpl_path, encoding='utf-8') as f:
        tpl = f.read()
    tpl = tpl.replace('/*__DATA__*/', json.dumps(dataset, ensure_ascii=False), 1)
    tpl = tpl.replace('/*__BACK_URL__*/', back_url, 1)
    return tpl


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _check_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _company_summary(company):
    """Агрегирует данные для карточки на главной странице."""
    uploads = company.uploads  # уже отсортированы по убыванию
    last = uploads[0] if uploads else None
    files_total = sum(u.files_count for u in uploads)
    dash_path = os.path.join(OUTPUT, str(company.id), 'dashboard_current.html')
    return dict(
        id=company.id,
        name=company.name,
        last_period=last.period_label if last else None,
        files_total=files_total,
        closure=last.closure_status if last else None,
        profit_sign=last.profit_sign if last else 'neutral',
        last_upload=(last.uploaded_at.strftime('%d.%m.%Y') if last else None),
        has_dashboard=os.path.isfile(dash_path),
    )


def _is_daily_xls(path):
    """True если XLS — суточная ОСВ (дата в шапке), False — периодическая (будет базой)."""
    from osvparser import extract_daily_date
    return extract_daily_date(path) is not None


def _compute_discrepancy(company_id, new_data):
    """Сравнивает СН новой ОСВ с расчётным СК из последних суточных записей.
    Возвращает список расхождений по счетам, отсортированный по убыванию дельты."""
    results = []
    for acc, d in new_data.items():
        new_snd = d['top']['snd']
        new_snk = d['top']['snk']
        if new_snd == 0.0 and new_snk == 0.0:
            continue  # нет начального сальдо — сравнивать нечего
        last_dr = DailyRow.query.filter_by(company_id=company_id, acc=acc)\
                                .order_by(DailyRow.date.desc()).first()
        if last_dr is None:
            continue
        delta_d = new_snd - last_dr.skd
        delta_k = new_snk - last_dr.skk
        if abs(delta_d) > 1.0 or abs(delta_k) > 1.0:
            results.append(dict(
                acc=acc,
                last_date=last_dr.date.strftime('%d.%m.%Y'),
                calc_skd=last_dr.skd,
                calc_skk=last_dr.skk,
                new_snd=new_snd,
                new_snk=new_snk,
                delta_d=delta_d,
                delta_k=delta_k,
            ))
    return sorted(results, key=lambda x: abs(x['delta_d']) + abs(x['delta_k']), reverse=True)


def _org_name_from_filename(path):
    """Пробует извлечь название орг. из имени суточного файла.

    Например: «ОСВ по счету 20 за 1 июля 2026 г. МУП КГХ.xls» → «МУП КГХ»
    """
    name = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r'\d{4}\s*г\.?\s+(.+)', name)
    if m:
        org = m.group(1).strip()
        if org:
            return org
    return None


def _save_files(paths_src, company_id, ts):
    """Сохраняет файлы в uploads/<company_id>/<ts>/."""
    batch_dir = os.path.join(UPLOADS, str(company_id), ts)
    os.makedirs(batch_dir, exist_ok=True)
    saved = []
    for f in paths_src:
        name = os.path.basename(f.filename)
        if not name.lower().endswith(('.xls', '.xlsx')):
            continue
        dest = os.path.join(batch_dir, name)
        f.save(dest)
        saved.append(dest)
    return saved


def _upsert_daily_rows(company_id, paths):
    """Если XLS-файл является суточным ОСВ (дата извлекается из шапки),
    записывает/обновляет строку DailyRow для этого дня и счёта.
    Возвращает количество обработанных файлов."""
    from osvparser import extract_daily_date, parse_osv
    count = 0
    for path in paths:
        dt = extract_daily_date(path)
        if dt is None:
            continue
        try:
            parsed = parse_osv(path)
        except Exception:
            continue
        acc = parsed['acc']
        top = parsed['top']
        row = DailyRow.query.filter_by(
            company_id=company_id, date=dt, acc=acc).first()
        if row is None:
            row = DailyRow(company_id=company_id, date=dt, acc=acc)
            db.session.add(row)
        row.snd = top['snd']; row.snk = top['snk']
        row.obd = top['obd']; row.obk = top['obk']
        row.skd = top['skd']; row.skk = top['skk']
        count += 1
    return count


def _process_files(paths, company_id, ts, back_url, company_name=''):
    """Парсит файлы, строит дашборд, возвращает (upload_record_kwargs, audit)."""
    data, audit = parse_many(paths)
    if not data:
        return None, audit

    dataset = build_dataset(data, company_name=company_name)
    html = render_dashboard(dataset, back_url=back_url)

    out_dir = os.path.join(OUTPUT, str(company_id))
    os.makedirs(out_dir, exist_ok=True)
    out_name = 'dashboard_current.html'
    with open(os.path.join(out_dir, out_name), 'w', encoding='utf-8') as f:
        f.write(html)

    # Выясняем статус закрытия из brief
    brief = dataset.get('brief', {})
    head = brief.get('head', '')
    closure_status = 'закрыт' in head.lower() if head else None

    # Финансовый результат
    lead = brief.get('lead', '')
    if 'убыток' in lead.lower() or 'убыточна' in lead.lower():
        profit_sign = 'loss'
    elif 'прибыль' in lead.lower() or 'прибыльна' in lead.lower():
        profit_sign = 'profit'
    else:
        profit_sign = 'neutral'

    want = ['20', '25', '26', '50', '51', '60', '62', '68', '69', '70', '76', '90', '91']
    missing = [a for a in want if a not in data]
    if missing:
        audit.append(dict(file='—', acc=', '.join(missing),
                          status='счета не загружены — соответствующие блоки будут пустыми',
                          ok=False))

    record_kwargs = dict(
        dashboard_file=out_name,
        period_label=dataset.get('period_label', ''),
        accounts_loaded=json.dumps(sorted(data.keys())),
        closure_status=closure_status,
        profit_sign=profit_sign,
        files_count=len(paths),
    )
    return record_kwargs, audit


# ---------------------------------------------------------------------------
# IMAP sync helper and background loop
# ---------------------------------------------------------------------------
def _get_admin_id() -> int:
    """Return the ID of the first admin user (used for automated uploads)."""
    u = User.query.filter_by(role='admin').order_by(User.id).first()
    return u.id if u else 1


def _do_imap_sync(uploader_id: int) -> dict:
    """Core IMAP sync logic. Must be called inside an app context.
    Returns dict(ok, msg, processed, updated).
    """
    import imaplib, email, ssl, fnmatch, tempfile, shutil as _shutil
    from email.header import decode_header as _dh

    def _decode(val):
        if val is None:
            return ''
        parts = []
        for chunk, charset in _dh(val):
            if isinstance(chunk, bytes):
                parts.append(chunk.decode(charset or 'utf-8', errors='replace'))
            else:
                parts.append(chunk)
        return ''.join(parts)

    host     = Setting.get('imap_host', '').strip()
    port_str = Setting.get('imap_port', '993').strip()
    login    = Setting.get('imap_login', '').strip()
    password = decrypt_value(Setting.get('imap_password', '').strip())
    folder   = Setting.get('imap_folder', 'INBOX').strip() or 'INBOX'

    if not host or not login or not password:
        return dict(ok=False, msg='IMAP не настроен: заполните хост, логин и пароль и сохраните.',
                    processed=0, updated=0)

    try:
        port_int = int(port_str)
    except ValueError:
        return dict(ok=False, msg=f'Неверный порт: {port_str}', processed=0, updated=0)

    rules = EmailRule.query.order_by(EmailRule.id).all()

    def _match_rule(rule, sender, subject):
        if rule.sender_pattern and not fnmatch.fnmatch(
                sender.lower(), rule.sender_pattern.lower()):
            return False
        if rule.subject_pattern and not fnmatch.fnmatch(
                subject.lower(), rule.subject_pattern.lower()):
            return False
        return True

    stats = dict(emails_checked=0, emails_matched=0,
                 files_processed=0, files_skipped=0,
                 companies_updated=set(), errors=[])

    tmp_root = tempfile.mkdtemp(prefix='imap_sync_')
    try:
        ctx = ssl.create_default_context()
        with imaplib.IMAP4_SSL(host, port_int, ssl_context=ctx, timeout=20) as M:
            M.login(login, password)
            status, _ = M.select(folder)
            if status != 'OK':
                return dict(ok=False, msg=f'Папка «{folder}» не найдена',
                            processed=0, updated=0)

            _, uid_list = M.search(None, 'UNSEEN')
            uids = uid_list[0].split() if uid_list and uid_list[0] else []
            stats['emails_checked'] = len(uids)

            for uid in uids:
                _, data = M.fetch(uid, '(RFC822)')
                raw = data[0][1] if data and data[0] else None
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                sender  = _decode(msg.get('From', ''))
                subject = _decode(msg.get('Subject', ''))

                matched_company_id = None
                for rule in rules:
                    if _match_rule(rule, sender, subject):
                        matched_company_id = rule.company_id
                        break

                if matched_company_id is None:
                    M.store(uid, '+FLAGS', '\\Seen')
                    continue

                stats['emails_matched'] += 1
                msg_tmp = os.path.join(tmp_root, uid.decode())
                os.makedirs(msg_tmp, exist_ok=True)

                attached_paths = []
                for part in msg.walk():
                    fname_raw = part.get_filename()
                    if not fname_raw:
                        continue
                    fname = _decode(fname_raw)
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in ('.xls', '.xlsx', '.zip'):
                        continue
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    dest = os.path.join(msg_tmp, fname)
                    with open(dest, 'wb') as fh:
                        fh.write(payload)
                    attached_paths.append(dest)

                if not attached_paths:
                    M.store(uid, '+FLAGS', '\\Seen')
                    continue

                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                company_id = matched_company_id
                for path in attached_paths:
                    ext = os.path.splitext(path)[1].lower()
                    try:
                        if ext == '.zip':
                            successes, errors_zip = parse_zip(path)
                            for e in errors_zip:
                                stats['errors'].append(f"{os.path.basename(path)}: {e['error']}")
                            for item in successes:
                                dt  = item['date']
                                acc = item['parsed']['acc']
                                top = item['parsed']['top']
                                row = DailyRow.query.filter_by(
                                    company_id=company_id, date=dt, acc=acc).first()
                                if row is None:
                                    row = DailyRow(company_id=company_id, date=dt, acc=acc)
                                    db.session.add(row)
                                row.snd = top['snd']; row.snk = top['snk']
                                row.obd = top['obd']; row.obk = top['obk']
                                row.skd = top['skd']; row.skk = top['skk']
                            db.session.commit()
                            if successes:
                                _co = db.session.get(Company, company_id)
                                _rebuild_dashboard_with_daily(
                                    company_id,
                                    company_name=_co.name if _co else '')
                                stats['companies_updated'].add(company_id)
                                _dates_z = sorted({it['date'] for it in successes})
                                _MRU_Z = {1:'янв',2:'фев',3:'мар',4:'апр',5:'май',6:'июн',
                                          7:'июл',8:'авг',9:'сен',10:'окт',11:'ноя',12:'дек'}
                                _d0, _d1 = _dates_z[0], _dates_z[-1]
                                _zlbl = (f'{_d0.day} {_MRU_Z[_d0.month]}'
                                         if _d0 == _d1
                                         else f'{_d0.strftime("%d.%m")}–{_d1.strftime("%d.%m")}')
                                _up = Upload(
                                    company_id=company_id,
                                    uploaded_by=uploader_id,
                                    period_label=_zlbl,
                                    accounts_loaded=json.dumps(
                                        sorted({it['parsed']['acc'] for it in successes})),
                                    closure_status=None, profit_sign='neutral',
                                    files_count=len(successes),
                                    dashboard_file='dashboard_current.html',
                                    source='auto_imap',
                                )
                                db.session.add(_up)
                                db.session.commit()
                            stats['files_processed'] += 1
                        else:  # XLS / XLSX
                            if _is_daily_xls(path):
                                save_dir = os.path.join(UPLOADS, str(company_id), f'daily_{ts}')
                            else:
                                save_dir = os.path.join(UPLOADS, str(company_id), ts)
                            os.makedirs(save_dir, exist_ok=True)
                            dest = os.path.join(save_dir, os.path.basename(path))
                            _shutil.copy2(path, dest)
                            _upsert_daily_rows(company_id, [dest])
                            db.session.commit()
                            _co = db.session.get(Company, company_id)
                            _rebuild_dashboard_with_daily(
                                company_id,
                                company_name=_co.name if _co else '')
                            stats['companies_updated'].add(company_id)
                            from osvparser import extract_daily_date as _edd
                            _xdt = _edd(dest)
                            _xlbl = _xdt.strftime('%d.%m.%Y') if _xdt else '—'
                            _up = Upload(
                                company_id=company_id,
                                uploaded_by=uploader_id,
                                period_label=_xlbl,
                                accounts_loaded='[]',
                                closure_status=None, profit_sign='neutral',
                                files_count=1,
                                dashboard_file='dashboard_current.html',
                                source='auto_imap',
                            )
                            db.session.add(_up)
                            db.session.commit()
                            stats['files_processed'] += 1
                    except Exception as exc:
                        stats['errors'].append(f"{os.path.basename(path)}: {exc}")
                        stats['files_skipped'] += 1

                M.store(uid, '+FLAGS', '\\Seen')

            M.logout()
    except imaplib.IMAP4.error as e:
        return dict(ok=False, msg=f'Ошибка IMAP-аутентификации: {e}',
                    processed=0, updated=0)
    except OSError as e:
        return dict(ok=False, msg=f'Сетевая ошибка: {e}', processed=0, updated=0)
    except Exception as e:
        return dict(ok=False, msg=f'Ошибка: {e}', processed=0, updated=0)
    finally:
        _shutil.rmtree(tmp_root, ignore_errors=True)

    upd = len(stats['companies_updated'])
    lines = []
    if stats['emails_checked'] == 0:
        lines.append('Непрочитанных писем нет')
    else:
        lines.append(f"Писем проверено: {stats['emails_checked']}, "
                     f"совпало с правилами: {stats['emails_matched']}")
    if stats['files_processed']:
        lines.append(f"Файлов обработано: {stats['files_processed']}, "
                     f"предприятий обновлено: {upd}")
    if stats['files_skipped']:
        lines.append(f"Пропущено с ошибкой: {stats['files_skipped']}")
    for err in stats['errors'][:3]:
        lines.append(f"  ⚠ {err}")
    if len(stats['errors']) > 3:
        lines.append(f"  … и ещё {len(stats['errors'])-3} ошибок")

    ok = not stats['errors'] or stats['files_processed'] > 0
    return dict(ok=ok, msg=' | '.join(lines),
                processed=stats['files_processed'], updated=upd)


def _imap_loop() -> None:
    """Daemon thread: poll IMAP inbox on a configurable interval."""
    while not _imap_stop.is_set():
        try:
            with app.app_context():
                interval_str = Setting.get('imap_interval', '30')
                try:
                    interval = max(5, int(interval_str)) * 60
                except ValueError:
                    interval = 30 * 60
                host = Setting.get('imap_host', '').strip()
                if host:
                    uid = _get_admin_id()
                    result = _do_imap_sync(uid)
                    if result['processed']:
                        print(f'[imap-loop] {result["msg"]}')
                else:
                    interval = 60  # check again in 1 min if not configured
        except Exception as exc:
            print(f'[imap-loop] unhandled error: {exc}')
            interval = 60
        _imap_stop.wait(interval)


# ---------------------------------------------------------------------------
# Background upload workers
# ---------------------------------------------------------------------------
def _bg_upload_all(job_id: str, company_id: int, company_name: str,
                   stage_dir: str, ts: str, uploader_id: int) -> None:
    """Background thread: classify + parse all staged files, rebuild dashboard."""
    import shutil as _sh
    with app.app_context():
        try:
            audit_out = []
            zip_dates = []
            total_files = 0

            all_staged = [os.path.join(stage_dir, f)
                          for f in os.listdir(stage_dir)
                          if os.path.isfile(os.path.join(stage_dir, f))]
            xls_staged = [p for p in all_staged
                          if os.path.splitext(p)[1].lower() in ('.xls', '.xlsx')]
            zip_staged = [p for p in all_staged if p.lower().endswith('.zip')]

            # — XLS: classify daily vs monthly, move to permanent dirs
            if xls_staged:
                total_files += len(xls_staged)
                ts_dir    = os.path.join(UPLOADS, str(company_id), ts)
                daily_dir = os.path.join(UPLOADS, str(company_id), f'daily_{ts}')
                os.makedirs(ts_dir, exist_ok=True)
                full_xls, daily_xls = [], []
                for path in xls_staged:
                    dest = os.path.join(ts_dir, os.path.basename(path))
                    _sh.move(path, dest)
                    if _is_daily_xls(dest):
                        os.makedirs(daily_dir, exist_ok=True)
                        final = os.path.join(daily_dir, os.path.basename(dest))
                        os.rename(dest, final)
                        daily_xls.append(final)
                    else:
                        full_xls.append(dest)

                _upsert_daily_rows(company_id, full_xls + daily_xls)
                db.session.commit()

                if full_xls:
                    data_check, _ = parse_many(full_xls)
                    if data_check:
                        for disc in _compute_discrepancy(company_id, data_check):
                            audit_out.append(dict(
                                file=f'сч.{disc["acc"]}',
                                acc=disc['acc'],
                                status=(
                                    f'РАСХОЖДЕНИЕ: расчётное СК на {disc["last_date"]} — '
                                    f'Дт {disc["calc_skd"]:,.0f} / Кт {disc["calc_skk"]:,.0f} | '
                                    f'новая ОСВ СН — Дт {disc["new_snd"]:,.0f} / Кт {disc["new_snk"]:,.0f} | '
                                    f'дельта Дт {disc["delta_d"]:+,.0f} / Кт {disc["delta_k"]:+,.0f}'
                                ),
                                ok='disc',
                            ))

            # — ZIP: move and parse
            for zp in zip_staged:
                total_files += 1
                zip_dir = os.path.join(UPLOADS, str(company_id), f'zip_{ts}')
                os.makedirs(zip_dir, exist_ok=True)
                dest = os.path.join(zip_dir, os.path.basename(zp))
                _sh.move(zp, dest)
                successes, errors = parse_zip(dest)
                audit_out += [{'file': e['file'], 'acc': '?',
                               'status': f'ОШИБКА: {e["error"]}', 'ok': False}
                              for e in errors]
                for item in successes:
                    dt, acc, top = item['date'], item['parsed']['acc'], item['parsed']['top']
                    row = DailyRow.query.filter_by(
                        company_id=company_id, date=dt, acc=acc).first()
                    if row is None:
                        row = DailyRow(company_id=company_id, date=dt, acc=acc)
                        db.session.add(row)
                    row.snd = top['snd']; row.snk = top['snk']
                    row.obd = top['obd']; row.obk = top['obk']
                    row.skd = top['skd']; row.skk = top['skk']
                    zip_dates.append(dt)
                db.session.commit()

            # Cleanup staging dir
            _sh.rmtree(stage_dir, ignore_errors=True)

            if not xls_staged and not zip_staged:
                fail_job(job_id, 'Среди загруженных нет файлов .xls, .xlsx или .zip.')
                return

            # Rebuild dashboard
            co = db.session.get(Company, company_id)
            back_url = f'/company/{company_id}'
            dataset, rebuild_audit = _rebuild_dashboard_with_daily(
                company_id, company_name=co.name if co else company_name)
            audit_out = rebuild_audit + audit_out

            if dataset is None:
                msg = rebuild_audit[-1]['status'] if rebuild_audit else 'нет данных'
                fail_job(job_id, f'Дашборд не построен: {msg}')
                return

            html_content = render_dashboard(dataset, back_url=back_url)
            out_dir = os.path.join(OUTPUT, str(company_id))
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, 'dashboard_current.html'),
                      'w', encoding='utf-8') as fh:
                fh.write(html_content)

            period_lbl = dataset.get('period_label', '')
            if zip_dates:
                _MRU = {1:'янв',2:'фев',3:'мар',4:'апр',5:'май',6:'июн',
                        7:'июл',8:'авг',9:'сен',10:'окт',11:'ноя',12:'дек'}
                zd = sorted(set(zip_dates))
                d0, d1 = zd[0], zd[-1]
                zl = (f'{d0.day} {_MRU[d0.month]}' if d0 == d1
                      else f'{d0.strftime("%d.%m")}–{d1.strftime("%d.%m")}')
                period_lbl = f'{period_lbl} + {zl}'.strip(' +')

            brief = dataset.get('brief', {})
            lead = brief.get('lead', '')
            profit_sign = ('loss'   if 'убыток'  in lead.lower() else
                           'profit' if 'прибыль' in lead.lower() else 'neutral')
            head = brief.get('head', '')
            closure_status = ('закрыт' in head.lower()) if head else None

            upload = Upload(
                company_id=company_id,
                uploaded_by=uploader_id,
                period_label=period_lbl,
                accounts_loaded=json.dumps(sorted(dataset.get('accounts', {}).keys())),
                closure_status=closure_status,
                profit_sign=profit_sign,
                files_count=total_files,
                dashboard_file='dashboard_current.html',
                source='manual',
            )
            db.session.add(upload)
            db.session.commit()

            discrepancy = [a for a in audit_out if a.get('ok') == 'disc']
            log_event('file_upload',
                      {'company_id': company_id, 'files': total_files, 'period': period_lbl},
                      user_id=uploader_id)
            finish_job(job_id, dict(
                audit=audit_out,
                discrepancy=discrepancy,
                period_label=period_lbl,
            ))
        except Exception as exc:
            traceback.print_exc()
            fail_job(job_id, str(exc))


def _bg_company_new(job_id: str, company_id: int, final_dir: str,
                    final_paths: list, ts: str, back_url: str,
                    company_name: str, uploader_id: int) -> None:
    """Background thread: process new company files and create first Upload."""
    import shutil as _sh
    with app.app_context():
        try:
            record_kwargs, audit = _process_files(
                final_paths, company_id, ts, back_url,
                company_name=company_name)
            if record_kwargs is None:
                fail_job(job_id, 'Ни один файл не удалось разобрать как ОСВ 1С')
                # Remove the empty company record so the user can retry
                _sh.rmtree(final_dir, ignore_errors=True)
                co = db.session.get(Company, company_id)
                if co:
                    DailyRow.query.filter_by(company_id=company_id).delete()
                    Upload.query.filter_by(company_id=company_id).delete()
                    db.session.delete(co)
                    db.session.commit()
                return

            _upsert_daily_rows(company_id, final_paths)
            upload = Upload(company_id=company_id, uploaded_by=uploader_id,
                            source='manual', **record_kwargs)
            db.session.add(upload)
            db.session.commit()

            log_event('company_created',
                      {'company_id': company_id, 'name': company_name},
                      user_id=uploader_id)
            finish_job(job_id, dict(
                audit=audit,
                discrepancy=[],
                period_label=record_kwargs.get('period_label', ''),
            ))
        except Exception as exc:
            traceback.print_exc()
            fail_job(job_id, str(exc))


# ---------------------------------------------------------------------------
# Шифрование / дешифрование (хранилище чувствительных настроек в БД)
# ---------------------------------------------------------------------------
_XLS_MAGIC = b'\xd0\xcf\x11\xe0'  # CFBF — Excel 97-2003
_ZIP_MAGIC  = b'PK'                # ZIP / OOXML (xlsx, zip)


def encrypt_value(plain: str) -> str:
    """Шифрует строку. Возвращает «enc:<ciphertext>»."""
    if not plain:
        return plain
    return 'enc:' + _FERNET.encrypt(plain.encode()).decode()


def decrypt_value(cipher: str) -> str:
    """Расшифровывает значение, зашифрованное через encrypt_value().
    Открытый текст (без префикса enc:) возвращается как есть — миграция без перезаписи."""
    if not cipher or not cipher.startswith('enc:'):
        return cipher
    try:
        return _FERNET.decrypt(cipher[4:].encode()).decode()
    except Exception:
        return ''


def log_event(action: str, details=None, user_id: int | None = None,
              ip: str | None = None) -> None:
    """Записывает событие безопасности в таблицу audit_log.
    Никогда не вызывает исключений — ошибки ведения журнала не должны ломать запросы."""
    from sqlalchemy.orm import Session as _AuditSession
    try:
        uid = user_id
        _ip = ip
        if uid is None:
            try:
                if current_user.is_authenticated:
                    uid = current_user.id
            except Exception:
                pass
        if _ip is None:
            try:
                _ip = request.remote_addr
            except Exception:
                pass
        entry = AuditLog(
            user_id=uid,
            action=action,
            details=json.dumps(details, ensure_ascii=False, default=str)
                    if details is not None else None,
            ip=_ip,
        )
        with _AuditSession(db.engine) as _sess:
            _sess.add(entry)
            _sess.commit()
    except Exception:
        pass


def _validate_upload(path: str, filename: str) -> tuple[bool, str]:
    """Проверяет magic bytes файла. Для ZIP проверяет zip-bomb и path traversal.
    Возвращает (ok, сообщение_об_ошибке)."""
    ext = os.path.splitext(filename)[1].lower()
    try:
        with open(path, 'rb') as fh:
            head = fh.read(8)
    except OSError as e:
        return False, f'Не удалось прочитать «{filename}»: {e}'

    if ext == '.xls':
        if not head.startswith(_XLS_MAGIC):
            return False, f'«{filename}» не является Excel 97-2003 (.xls) — неверная сигнатура файла'
    elif ext == '.xlsx':
        if not head.startswith(_ZIP_MAGIC):
            return False, f'«{filename}» не является Excel 2007+ (.xlsx) — неверная сигнатура файла'
    elif ext == '.zip':
        if not head.startswith(_ZIP_MAGIC):
            return False, f'«{filename}» не является ZIP-архивом — неверная сигнатура файла'
        ok, err = _check_zip_safety(path, filename)
        if not ok:
            return False, err

    return True, ''


def _check_zip_safety(path: str, filename: str) -> tuple[bool, str]:
    """Zip-bomb (>500 МБ распакованных данных) и path traversal."""
    import zipfile as _zf
    MAX_UNCOMPRESSED = 500 * 1024 * 1024
    try:
        with _zf.ZipFile(path, 'r') as zfile:
            total = sum(i.file_size for i in zfile.infolist())
            if total > MAX_UNCOMPRESSED:
                return False, (
                    f'«{filename}» отклонён: распакованный объём '
                    f'{total // 1024 // 1024} МБ превышает лимит 500 МБ (возможный zip-bomb)'
                )
            for info in zfile.infolist():
                name = info.filename
                parts = name.replace('\\', '/').split('/')
                if name.startswith('/') or '..' in parts:
                    return False, (
                        f'«{filename}» содержит опасный путь в архиве '
                        f'(path traversal): «{name}»'
                    )
    except _zf.BadZipFile as e:
        return False, f'«{filename}» — повреждённый ZIP-архив: {e}'
    return True, ''


# ---------------------------------------------------------------------------
# Обработчики ошибок
# ---------------------------------------------------------------------------
def _wants_json():
    """True если клиент ожидает JSON (AJAX-запрос)."""
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            'application/json' in request.headers.get('Accept', ''))


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if _wants_json():
        return jsonify(error='CSRF token invalid or expired'), 403
    return render_template('403.html'), 403


@app.errorhandler(403)
def forbidden(e):
    if _wants_json():
        return jsonify(error='Доступ запрещён'), 403
    return render_template('403.html'), 403


@app.errorhandler(500)
def internal_error(e):
    app.logger.error('500 Internal Server Error:\n%s', traceback.format_exc())
    if _wants_json():
        return jsonify(error='Внутренняя ошибка сервера'), 500
    return render_template('500.html'), 500


# ---------------------------------------------------------------------------
# Вспомогательный эндпоинт для JS/HTMX-клиентов: получить CSRF-токен
# ---------------------------------------------------------------------------
@app.route('/api/auth/csrf', methods=['GET'])
@login_required
def get_csrf_token():
    return jsonify(csrf_token=generate_csrf())


# ---------------------------------------------------------------------------
# Маршруты авторизации
# ---------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    error = None
    username = ''
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and _check_password(password, user.password_hash):
            login_user(user)
            log_event('login_ok', {'username': username})
            return redirect(url_for('home'))
        log_event('login_fail', {'username': username})
        error = 'Неверный логин или пароль.'
    return render_template('login.html', error=error, username=username)


@app.route('/logout')
@login_required
def logout():
    log_event('logout')
    logout_user()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Главная — список предприятий
# ---------------------------------------------------------------------------
@app.route('/')
@login_required
def home():
    companies = Company.query.order_by(Company.created_at.desc()).all()
    cards = [_company_summary(c) for c in companies]
    return render_template('home.html', companies=cards)


# ---------------------------------------------------------------------------
# Создание нового предприятия + первая загрузка
# ---------------------------------------------------------------------------
@app.route('/company/new', methods=['GET', 'POST'])
@login_required
def company_new():
    if request.method == 'GET':
        return render_template('company_new.html')

    ts = time.strftime('%Y%m%d_%H%M%S')

    # Если на второй POST пришёл tmp_key — читаем уже сохранённые файлы
    tmp_key = request.form.get('tmp_key', '').strip()
    if tmp_key:
        tmp_dir = os.path.join(UPLOADS, 'tmp', tmp_key)
        tmp_paths = sorted([
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.lower().endswith(('.xls', '.xlsx'))
        ]) if os.path.isdir(tmp_dir) else []
    else:
        # Первый POST — получаем файлы из запроса
        files = [f for f in request.files.getlist('files') if f.filename]
        if not files:
            return render_template('company_new.html',
                                   error='Выберите хотя бы один файл ОСВ.')

        tmp_dir = os.path.join(UPLOADS, 'tmp', ts)
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_paths = []
        for f in files:
            name = os.path.basename(f.filename)
            if not name.lower().endswith(('.xls', '.xlsx')):
                continue
            dest = os.path.join(tmp_dir, name)
            f.save(dest)
            ok, err = _validate_upload(dest, name)
            if not ok:
                import shutil as _sh_cn; _sh_cn.rmtree(tmp_dir, ignore_errors=True)
                return render_template('company_new.html', error=err)
            tmp_paths.append(dest)
        tmp_key = ts

    if not tmp_paths:
        return render_template('company_new.html',
                               error='Среди загруженных нет .xls/.xlsx файлов.')

    # Название: из формы → из шапки файла → из имени файла
    org_name = (request.form.get('org_name') or '').strip()
    if not org_name:
        org_name = extract_org_name(tmp_paths[0]) or ''
    if not org_name:
        org_name = _org_name_from_filename(tmp_paths[0]) or ''
    if not org_name:
        return render_template('company_new.html',
                               ask_name=True,
                               tmp_key=tmp_key,
                               error='Не удалось определить название организации из файла. '
                                     'Введите его вручную.')

    # Создаём компанию в БД
    company = Company(name=org_name, created_by=current_user.id)
    db.session.add(company)
    db.session.flush()  # получаем company.id

    # Перемещаем файлы в постоянное хранилище
    final_dir = os.path.join(UPLOADS, str(company.id), ts)
    os.makedirs(os.path.dirname(final_dir), exist_ok=True)
    import shutil
    shutil.move(tmp_dir, final_dir)
    final_paths = [os.path.join(final_dir, os.path.basename(p)) for p in tmp_paths]

    db.session.commit()  # commit company before starting thread
    log_event('company_new_started', {'name': org_name, 'company_id': company.id})

    # Запускаем обработку в фоне
    back_url = url_for('company_detail', company_id=company.id)
    job_id = create_job(company.id)
    threading.Thread(
        target=_bg_company_new,
        args=(job_id, company.id, final_dir, final_paths, ts,
              back_url, org_name, current_user.id),
        daemon=True,
    ).start()

    return redirect(url_for('company_detail', company_id=company.id, job=job_id))


# ---------------------------------------------------------------------------
# Страница предприятия
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>')
@login_required
def company_detail(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    year = request.args.get('year', None, type=int)
    ai_summary = AiSummary.query.filter_by(company_id=company_id).first()
    ai_provider_configured = AiProvider.query.filter_by(is_active=True).first() is not None
    return render_template('company.html', company=company, uploads=uploads,
                           audit=None, error=None, discrepancy=[],
                           annual_coverage=_annual_coverage(company_id, year),
                           ai_summary=ai_summary,
                           ai_provider_configured=ai_provider_configured)


@app.route('/company/<int:company_id>/job/<job_id>')
@login_required
def job_status(company_id, job_id):
    """Опрос статуса фонового задания (polling endpoint)."""
    job = get_job(job_id)
    if not job or job.get('company_id') != company_id:
        return jsonify(status='not_found'), 404
    return jsonify(
        status=job['status'],
        error=job.get('error'),
        result=job.get('result'),
    )


@app.route('/company/<int:company_id>/ai-summary', methods=['POST'])
@login_required
def company_ai_summary(company_id):
    """Формирует ИИ-заключение по финансовым данным предприятия."""
    from ai_client import call_ai_provider as _call_ai
    company = db.session.get(Company, company_id) or abort(404)

    # Выбираем провайдера: явно помеченный default → иначе первый активный
    provider = (AiProvider.query.filter_by(is_default=True, is_active=True).first()
                or AiProvider.query.filter_by(is_active=True).first())
    if not provider or not provider.base_url or not provider.model:
        return jsonify(ok=False, error='ИИ-провайдер не настроен'), 400

    api_key = decrypt_value(provider.api_key) if provider.api_key else ''

    # ── Сбор данных ──────────────────────────────────────────────────────────
    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    latest = uploads[0] if uploads else None

    # Последнее сальдо по ключевым счетам (из DailyRow)
    key_accs = {'51': 'Сч.51 Расчётный счёт',
                '62': 'Сч.62 Дебиторы (потребители)',
                '60': 'Сч.60 Кредиторы (поставщики)',
                '70': 'Сч.70 Расчёты с персоналом'}
    daily_facts = {}
    for acc in key_accs:
        row = (DailyRow.query
               .filter_by(company_id=company_id, acc=acc)
               .order_by(DailyRow.date.desc())
               .first())
        if row:
            daily_facts[acc] = {'date': row.date.strftime('%d.%m.%Y'),
                                'skd': row.skd, 'skk': row.skk}

    cov = _annual_coverage(company_id)

    # ── Формирование промпта ─────────────────────────────────────────────────
    lines = [f'Организация: {company.name}']

    if latest:
        lines.append(f'Период ОСВ: {latest.period_label or "не указан"}')
        cl = ('закрыт' if latest.closure_status is True
              else ('не закрыт' if latest.closure_status is False else 'неизвестно'))
        lines.append(f'Статус закрытия периода: {cl}')
        fin_map = {'profit': 'прибыль', 'loss': 'убыток', 'neutral': 'нейтральный'}
        lines.append(f'Финансовый результат: {fin_map.get(latest.profit_sign, "—")}')
        if latest.profit_value:
            v = f'{latest.profit_value:,.2f}'.replace(',', ' ')
            lines.append(f'Сумма финансового результата: {v} руб.')
        accs = latest.accounts_list
        if accs:
            lines.append(f'Загруженные счета ОСВ: {", ".join(accs)}')

    lines.append(f'Покрытие данными: {cov["total_months_ok"]}/{cov["total_months_past"]} '
                 f'мес. ({cov["months_coverage_pct"]}%)')

    if daily_facts:
        lines.append('\nОстатки по ключевым счетам (суточные данные, последняя дата):')
        for acc, info in daily_facts.items():
            saldo = info['skd'] - info['skk']
            v = f'{saldo:,.2f}'.replace(',', ' ')
            lines.append(f'  {key_accs[acc]} на {info["date"]}: сальдо {v} руб.')

    if len(uploads) > 1:
        lines.append(f'\nВсего пакетов загрузок: {len(uploads)}')

    system_msg = (
        'Ты финансовый аналитик, специализирующийся на анализе данных '
        'оборотно-сальдовых ведомостей (ОСВ) организаций жилищно-коммунального '
        'хозяйства России. Дай краткое профессиональное заключение (3–5 абзацев) '
        'по предоставленным данным. Укажи ключевые риски и сильные стороны. '
        'Отвечай строго на русском языке.'
    )
    user_msg = '\n'.join(lines)

    # ── Вызов ИИ ─────────────────────────────────────────────────────────────
    try:
        result = _call_ai(
            base_url=provider.base_url,
            api_key=api_key,
            model=provider.model,
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user',   'content': user_msg},
            ],
            temperature=0.3,
            max_tokens=1500,
            enable_thinking=False,
            timeout=90,
        )
    except Exception as e:
        app.logger.error('AI summary error for company %s: %s', company_id, e)
        return jsonify(ok=False, error=str(e)), 502

    content = result['content']
    usage   = result['usage']

    # ── Сохранение (upsert) ───────────────────────────────────────────────────
    summary = AiSummary.query.filter_by(company_id=company_id).first()
    if summary is None:
        summary = AiSummary(company_id=company_id)
        db.session.add(summary)
    summary.content           = content
    summary.provider_name     = provider.name
    summary.model_name        = provider.model
    summary.prompt_tokens     = usage.get('prompt_tokens', 0)
    summary.completion_tokens = usage.get('completion_tokens', 0)
    summary.generated_at      = datetime.now(timezone.utc)
    db.session.commit()

    log_event('ai_summary_generated', {
        'company_id': company_id,
        'tokens': usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0),
    })

    return jsonify(
        ok=True,
        content=content,
        meta=dict(
            provider=provider.name,
            model=provider.model,
            tokens=usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0),
            generated_at=summary.generated_at.strftime('%d.%m.%Y %H:%M'),
        ),
    )


# ---------------------------------------------------------------------------
# Единая загрузка файлов (.xls/.xlsx и/или .zip)
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>/upload-all', methods=['POST'])
@login_required
def company_upload_all(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    all_files = [f for f in request.files.getlist('files') if f.filename]

    def _err(msg):
        ups = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=ups,
                               audit=None, error=msg, discrepancy=[],
                               annual_coverage=_annual_coverage(company_id))

    if not all_files:
        return _err('Файлы не выбраны.')

    ts = time.strftime('%Y%m%d_%H%M%S')

    # Сохраняем все файлы в staging-папку синхронно (быстро: только дисковый ввод-вывод)
    stage_dir = os.path.join(UPLOADS, str(company_id), f'stage_{ts}')
    os.makedirs(stage_dir, exist_ok=True)

    saved = 0
    for f in all_files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ('.xls', '.xlsx', '.zip'):
            continue
        dest = os.path.join(stage_dir, os.path.basename(f.filename))
        f.save(dest)
        ok, err = _validate_upload(dest, f.filename)
        if not ok:
            import shutil as _sh_v; _sh_v.rmtree(stage_dir, ignore_errors=True)
            return _err(err)
        saved += 1

    if not saved:
        import shutil as _sh; _sh.rmtree(stage_dir, ignore_errors=True)
        return _err('Среди загруженных нет файлов .xls, .xlsx или .zip.')

    job_id = create_job(company_id)
    threading.Thread(
        target=_bg_upload_all,
        args=(job_id, company_id, company.name, stage_dir, ts, current_user.id),
        daemon=True,
    ).start()

    return redirect(url_for('company_detail', company_id=company_id, job=job_id))

    # Пересобираем дашборд: месячные XLS + суточные из БД
    back_url = url_for('company_detail', company_id=company_id)
    dataset, rebuild_audit = _rebuild_dashboard_with_daily(company_id,
                                                            company_name=company.name)
    audit_out = rebuild_audit + audit_out

    if dataset is None:
        db.session.rollback()
        msg = rebuild_audit[-1]['status'] if rebuild_audit else 'нет данных'
        return _err(f'Дашборд не построен: {msg}', audit_out)

    html_content = render_dashboard(dataset, back_url=back_url)
    out_dir = os.path.join(OUTPUT, str(company_id))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'dashboard_current.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    # Метаданные для записи в историю
    period_lbl = dataset.get('period_label', '')
    if zip_dates:
        _MRU = {1:'янв',2:'фев',3:'мар',4:'апр',5:'май',6:'июн',
                7:'июл',8:'авг',9:'сен',10:'окт',11:'ноя',12:'дек'}
        zd_sorted = sorted(set(zip_dates))
        d0, d1 = zd_sorted[0], zd_sorted[-1]
        zip_lbl = (f'{d0.day} {_MRU[d0.month]}' if d0 == d1
                   else f'{d0.strftime("%d.%m")}–{d1.strftime("%d.%m")}')
        period_lbl = f'{period_lbl} + {zip_lbl}'.strip(' +')

    brief = dataset.get('brief', {})
    lead  = brief.get('lead', '')
    if 'убыток' in lead.lower():
        profit_sign = 'loss'
    elif 'прибыль' in lead.lower():
        profit_sign = 'profit'
    else:
        profit_sign = 'neutral'
    head = brief.get('head', '')
    closure_status = ('закрыт' in head.lower()) if head else None

    upload = Upload(
        company_id=company_id,
        uploaded_by=current_user.id,
        period_label=period_lbl,
        accounts_loaded=json.dumps(sorted(dataset.get('accounts', {}).keys())),
        closure_status=closure_status,
        profit_sign=profit_sign,
        files_count=total_files,
        dashboard_file='dashboard_current.html',
        source='manual',
    )
    db.session.add(upload)
    db.session.commit()

    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    discrepancy = [a for a in audit_out if a.get('ok') == 'disc']
    return render_template('company.html', company=company, uploads=uploads,
                           audit=audit_out, error=None,
                           discrepancy=discrepancy,
                           annual_coverage=_annual_coverage(company_id))


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------
@app.route('/dashboard/<int:company_id>/<name>')
@login_required
def dashboard(company_id, name):
    name = os.path.basename(name)
    if not name.endswith('.html'):
        abort(404)
    fpath = os.path.join(OUTPUT, str(company_id), name)
    if not os.path.isfile(fpath):
        abort(404)
    with open(fpath, encoding='utf-8') as f:
        html = f.read()
    # Гарантируем наличие кнопки "Все предприятия" независимо от даты генерации файла
    if 'href="/"' not in html:
        html = html.replace(
            '<a class="hd-back"',
            '<a class="hd-back" href="/">☰ Все предприятия</a>'
            '\n    <a class="hd-back"',
            1,
        )
    # Вставляем CSRF-токен, company_id и флаг AI прямо в уже собранный HTML
    ai_ok = AiProvider.query.filter_by(is_active=True).first() is not None
    html = html.replace("'__CSRF__'",   f"'{generate_csrf()}'",    1)
    html = html.replace('__CID__',      str(company_id),            1)
    html = html.replace('__AI_OK__',    'true' if ai_ok else 'false', 1)
    html = html.replace('__AI_DISP__',  'inline-flex' if ai_ok else 'none', 1)
    return Response(html, mimetype='text/html')


# ---------------------------------------------------------------------------
# Загрузка суточного ZIP-архива
# ---------------------------------------------------------------------------
def _find_monthly_xls(company_id):
    """Возвращает пути ко всем XLS-файлам из месячных загрузок компании."""
    company_dir = os.path.join(UPLOADS, str(company_id))
    if not os.path.isdir(company_dir):
        return []
    paths = []
    for ts_dir in os.listdir(company_dir):
        if ts_dir.startswith(('zip_', 'daily_', 'tmp_')):  # не базовые папки
            continue
        full = os.path.join(company_dir, ts_dir)
        if not os.path.isdir(full):
            continue
        for fname in os.listdir(full):
            if fname.lower().endswith(('.xls', '.xlsx')):
                paths.append(os.path.join(full, fname))
    return paths


def _rebuild_dashboard_with_daily(company_id, company_name=''):
    """
    Перестраивает дашборд, объединяя:
      - строки аналитики (rows) из месячных XLS-файлов
      - временно́й ряд (daily) из месячных файлов + DailyRow из БД

    Возвращает (dataset, audit_list) или (None, audit_list) при ошибке.
    """
    def _dr_entry(dr):
        """Словарь daily-записи из DailyRow — включает sn/sk для суточных графиков."""
        return dict(
            date=dr.date.isoformat(),
            month=dr.date.strftime('%Y-%m'),
            path1='', path2='', path3='',
            snd=dr.snd, snk=dr.snk,
            obd=dr.obd, obk=dr.obk,
            skd=dr.skd, skk=dr.skk,
        )

    monthly_paths = _find_monthly_xls(company_id)
    if not monthly_paths:
        # Нет файлов на диске — строим только из суточных записей БД (если есть)
        daily_db = DailyRow.query.filter_by(company_id=company_id)\
                                 .order_by(DailyRow.date).all()
        if not daily_db:
            return None, [{'file': '—', 'acc': '—',
                           'status': 'Нет данных: загрузите ОСВ-файлы (.xls/.xlsx) '
                                     'или суточный ZIP-архив', 'ok': False}]
        data = {}
        for dr in daily_db:
            acc = dr.acc
            if acc not in data:
                data[acc] = dict(acc=acc, period='', dims=[], rows=[], daily=[],
                                 warnings=[], check_ok=None,
                                 top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))
            data[acc]['daily'].append(_dr_entry(dr))
        try:
            dataset = build_dataset(data, company_name=company_name)
        except ValueError as e:
            return None, [{'file': '—', 'acc': '—',
                           'status': f'Ошибка сборки дашборда: {e}', 'ok': False}]
        return dataset, []

    data, audit = parse_many(monthly_paths)
    if not data:
        return None, audit

    # Какие месяцы уже покрыты месячными файлами — чтобы не задваивать
    monthly_months: dict[str, set[str]] = {}   # acc -> {month, …}
    for acc, d in data.items():
        monthly_months[acc] = {e['month'] for e in d['daily']}

    # Добавляем суточные записи из БД для периодов вне месячных файлов
    daily_db = DailyRow.query.filter_by(company_id=company_id)\
                             .order_by(DailyRow.date).all()
    for dr in daily_db:
        acc   = dr.acc
        month = dr.date.strftime('%Y-%m')
        if acc in monthly_months and month in monthly_months[acc]:
            continue
        if acc not in data:
            data[acc] = dict(acc=acc, period='', dims=[], rows=[], daily=[],
                             warnings=[], check_ok=None,
                             top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))
        data[acc]['daily'].append(_dr_entry(dr))

    try:
        dataset = build_dataset(data, company_name=company_name)
    except ValueError as e:
        return None, audit + [{'file': '—', 'acc': '—',
                                'status': f'Ошибка сборки дашборда: {e}', 'ok': False}]
    return dataset, audit


@app.route('/company/<int:company_id>/upload-zip', methods=['POST'])
@login_required
def company_upload_zip(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    zfile = request.files.get('zipfile')

    def _back(error=None, audit=None):
        uploads = Upload.query.filter_by(company_id=company_id)\
                              .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=uploads,
                               audit=audit, error=error, discrepancy=[],
                               annual_coverage=_annual_coverage(company_id))

    if not zfile or not zfile.filename:
        return _back(error='Файл не выбран.')
    if not zfile.filename.lower().endswith('.zip'):
        return _back(error='Ожидается ZIP-архив (.zip).')

    # Сохраняем ZIP на диск
    ts = time.strftime('%Y%m%d_%H%M%S')
    zip_dir = os.path.join(UPLOADS, str(company_id), f'zip_{ts}')
    os.makedirs(zip_dir, exist_ok=True)
    zip_path = os.path.join(zip_dir, 'archive.zip')
    zfile.save(zip_path)

    # Парсим ZIP
    successes, errors = parse_zip(zip_path)

    if not successes:
        msgs = '; '.join(e['error'] for e in errors[:3])
        return _back(error=f'Не удалось разобрать ни одного файла из архива. {msgs}')

    # Upsert в DailyRow
    upserted = 0
    for item in successes:
        dt    = item['date']
        acc   = item['parsed']['acc']
        top   = item['parsed']['top']
        row = DailyRow.query.filter_by(
            company_id=company_id, date=dt, acc=acc).first()
        if row is None:
            row = DailyRow(company_id=company_id, date=dt, acc=acc)
            db.session.add(row)
        row.snd = top['snd']; row.snk = top['snk']
        row.obd = top['obd']; row.obk = top['obk']
        row.skd = top['skd']; row.skk = top['skk']
        upserted += 1
    db.session.commit()

    # Пересобираем дашборд (месячные данные + новые суточные)
    back_url = url_for('company_detail', company_id=company_id)
    dataset, audit = _rebuild_dashboard_with_daily(company_id,
                                                    company_name=company.name)

    audit_out = [{'file': e['file'], 'acc': '?',
                  'status': f'ОШИБКА: {e["error"]}', 'ok': False}
                 for e in errors]

    if dataset is None:
        db.session.rollback()
        return _back(error='Дашборд не построен: ' + (audit[-1]['status'] if audit else ''),
                     audit=audit_out)

    html = render_dashboard(dataset, back_url=back_url)
    out_dir = os.path.join(OUTPUT, str(company_id))
    os.makedirs(out_dir, exist_ok=True)
    out_name = 'dashboard_current.html'
    with open(os.path.join(out_dir, out_name), 'w', encoding='utf-8') as f:
        f.write(html)

    # Диапазон дат из ZIP
    _MRU = {1:'января',2:'февраля',3:'марта',4:'апреля',5:'мая',6:'июня',
            7:'июля',8:'августа',9:'сентября',10:'октября',11:'ноября',12:'декабря'}
    dates = sorted({item['date'] for item in successes})
    d_from, d_to = dates[0], dates[-1]
    if d_from == d_to:
        period_lbl = f'{d_from.day} {_MRU[d_from.month]} {d_from.year}'
    else:
        period_lbl = f'{d_from.strftime("%d.%m")}–{d_to.strftime("%d.%m.%Y")}'

    upload = Upload(
        company_id=company_id,
        uploaded_by=current_user.id,
        period_label=period_lbl,
        accounts_loaded=json.dumps(sorted({item['parsed']['acc'] for item in successes})),
        closure_status=None,
        profit_sign='neutral',
        files_count=upserted,
        dashboard_file=out_name,
        source='manual',
    )
    db.session.add(upload)
    db.session.commit()

    audit_out += [{'file': f'{item["date"]} сч.{item["parsed"]["acc"]}',
                   'acc': item['parsed']['acc'], 'status': 'добавлен', 'ok': True}
                  for item in successes]

    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    return render_template('company.html', company=company, uploads=uploads,
                           audit=audit_out, error=None, discrepancy=[],
                           annual_coverage=_annual_coverage(company_id))


def _annual_coverage(company_id, year=None):
    """Годовая карта покрытия: месячные ОСВ (Upload) + суточные (DailyRow)."""
    import calendar as _cal
    import re as _re
    from datetime import date as _d

    today = _d.today()
    if year is None:
        year = today.year

    # --- Все суточные строки ---
    all_rows = DailyRow.query.filter_by(company_id=company_id).all()
    all_dates = {r.date for r in all_rows}
    year_dates = {d for d in all_dates if d.year == year}
    data_start = min(all_dates) if all_dates else None

    # --- Все загрузки ---
    all_uploads = Upload.query.filter_by(company_id=company_id)\
                              .order_by(Upload.uploaded_at.asc()).all()

    # Привязка Upload к (year, month) через period_label или uploaded_at
    RU_M = {'янв':1,'фев':2,'мар':3,'апр':4,'май':5,'июн':6,
             'июл':7,'авг':8,'сен':9,'окт':10,'ноя':11,'дек':12}

    def _upl_ym(u):
        """Возвращает список (year, month) — один элемент или диапазон.
        'Январь–Июнь 2026' → [(2026,1),(2026,2),(2026,3),(2026,4),(2026,5),(2026,6)]
        'Июнь 2026'        → [(2026,6)]
        """
        lbl = (u.period_label or '').lower()
        hits = _re.findall(r'(янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек)', lbl)
        yr_hit = _re.search(r'\b(20\d{2})\b', lbl)
        yr = int(yr_hit.group(1)) if yr_hit else u.uploaded_at.year
        if hits:
            m_start = RU_M[hits[0]]
            m_end   = RU_M[hits[-1]]
            if m_start <= m_end:
                return [(yr, m) for m in range(m_start, m_end + 1)]
            # Диапазон через год (редко) — берём только конечный месяц
            return [(yr, m_end)]
        # DD.MM–DD.MM → берём последний MM
        m2 = _re.search(r'\d+\.(\d+)[\s–\-]+\d+\.(\d+)', lbl)
        if m2:
            return [(u.uploaded_at.year, int(m2.group(2)))]
        return [(u.uploaded_at.year, u.uploaded_at.month)]

    # (year, month) → лучший Upload (с dashboard_file предпочтительнее)
    upl_map = {}
    for u in all_uploads:
        for key in _upl_ym(u):
            if key not in upl_map or (u.dashboard_file and not upl_map[key].dashboard_file):
                upl_map[key] = u

    MON = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек']

    months = []
    for m in range(1, 13):
        days_in_month = _cal.monthrange(year, m)[1]
        first_wd = _d(year, m, 1).weekday()  # 0=Пн
        upload = upl_map.get((year, m))

        # Статус месячной ОСВ
        month_start = _d(year, m, 1)
        if month_start > today:
            upl_status = 'future'
        elif upload is None:
            upl_status = 'no_upload'
        elif upload.closure_status is True:
            upl_status = 'closed_profit' if upload.profit_sign == 'profit' else (
                         'closed_loss'   if upload.profit_sign == 'loss'   else 'closed')
        elif upload.closure_status is False:
            upl_status = 'open'
        else:
            upl_status = 'uploaded'

        # Суточная сетка (None = пустая ячейка выравнивания)
        days = [None] * first_wd
        for dn in range(1, days_in_month + 1):
            day = _d(year, m, dn)
            if day > today:
                st = 'future'
            elif day in year_dates:
                st = 'ok'         # есть суточные данные
            elif upload is not None:
                st = 'monthly'    # месяц загружен, но суточной детализации нет
            elif data_start and day >= data_start:
                st = 'missing'    # пропуск в суточном ряду
            else:
                st = 'nodata'     # до начала каких-либо данных
            days.append(dict(day=dn, date=day.strftime('%d.%m'),
                             status=st, is_today=(day == today)))

        past  = [d for d in days if d and d['status'] != 'future']
        ok    = [d for d in days if d and d['status'] == 'ok']
        miss  = [d for d in days if d and d['status'] == 'missing']

        month_start_d = _d(year, m, 1)
        months_past_1 = 1 if month_start_d <= today else 0
        months_ok_1   = 1 if (upload is not None and month_start_d <= today) else 0

        months.append(dict(
            num=m, name=MON[m - 1],
            days=days,
            upload=upload,
            upl_status=upl_status,
            ok_days=len(ok),
            miss_days=len(miss),
            past_days=len(past),
            coverage_pct=int(len(ok) * 100 / len(past)) if past else 0,
            months_past=months_past_1,
            months_ok=months_ok_1,
        ))

    # Навигация по годам
    data_years = sorted(
        {d.year for d in all_dates} |
        {u.uploaded_at.year for u in all_uploads} |
        {today.year}
    )
    prev_year = year - 1 if (year - 1) in data_years else None
    next_year = year + 1 if (year + 1) in data_years else None

    total_ok          = sum(m['ok_days']    for m in months)
    total_past        = sum(m['past_days']  for m in months)
    total_months_ok   = sum(m['months_ok']  for m in months)
    total_months_past = sum(m['months_past'] for m in months)
    year_accs  = sorted({r.acc for r in all_rows if r.date.year == year})

    return dict(
        year=year,
        months=months,
        prev_year=prev_year,
        next_year=next_year,
        data_years=data_years,
        total_ok=total_ok,
        total_past=total_past,
        coverage_pct=int(total_ok * 100 / total_past) if total_past else 0,
        total_months_ok=total_months_ok,
        total_months_past=total_months_past,
        months_coverage_pct=int(total_months_ok * 100 / total_months_past)
                            if total_months_past else 0,
        has_daily=bool(all_dates),
        accs=year_accs,
        has_any=bool(all_dates or all_uploads),
    )


# ---------------------------------------------------------------------------
# Переименование предприятия
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>/rename', methods=['POST'])
@login_required
def company_rename(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    new_name = request.form.get('name', '').strip()
    if new_name:
        old_name = company.name
        company.name = new_name
        db.session.commit()
        log_event('company_renamed', {'id': company_id, 'old': old_name, 'new': new_name})
    return redirect(url_for('company_detail', company_id=company_id))


@app.route('/company/<int:company_id>/delete', methods=['POST'])
@login_required
def company_delete(company_id):
    import shutil
    company = db.session.get(Company, company_id) or abort(404)
    log_event('company_deleted', {'id': company_id, 'name': company.name})

    # Удаляем все связанные записи
    DailyRow.query.filter_by(company_id=company_id).delete()
    Upload.query.filter_by(company_id=company_id).delete()
    EmailRule.query.filter_by(company_id=company_id).delete()
    db.session.delete(company)
    db.session.commit()

    # Удаляем файлы с диска
    company_uploads = os.path.join(UPLOADS, str(company_id))
    if os.path.isdir(company_uploads):
        shutil.rmtree(company_uploads, ignore_errors=True)
    dashboard_file = os.path.join(OUTPUT, f'dashboard_{company_id}_current.html')
    if os.path.isfile(dashboard_file):
        os.remove(dashboard_file)

    return redirect(url_for('home'))


# ---------------------------------------------------------------------------
# Настройки приложения
# ---------------------------------------------------------------------------
IMAP_KEYS = [
    ('imap_host',     'Хост IMAP',              'imap.yandex.ru'),
    ('imap_port',     'Порт',                   '993'),
    ('imap_login',    'Логин (e-mail)',          ''),
    ('imap_password', 'Пароль / пароль приложения', ''),
    ('imap_folder',   'Папка',                  'INBOX'),
    ('imap_interval', 'Интервал опроса (мин)',   '30'),
]


@app.route('/settings/test-imap', methods=['POST'])
@admin_required
def settings_test_imap():
    import imaplib
    import ssl
    from flask import jsonify

    host     = request.form.get('imap_host', '').strip()
    port     = request.form.get('imap_port', '993').strip()
    login    = request.form.get('imap_login', '').strip()
    password = request.form.get('imap_password', '').strip()
    folder   = request.form.get('imap_folder', 'INBOX').strip() or 'INBOX'

    if not host or not login or not password:
        return jsonify(ok=False, msg='Заполните хост, логин и пароль')

    try:
        port_int = int(port)
    except ValueError:
        return jsonify(ok=False, msg=f'Неверный порт: {port}')

    try:
        ctx = ssl.create_default_context()
        with imaplib.IMAP4_SSL(host, port_int, ssl_context=ctx,
                               timeout=15) as M:
            M.login(login, password)
            status, data = M.select(folder, readonly=True)
            if status != 'OK':
                return jsonify(ok=False, msg=f'Папка «{folder}» не найдена')
            count = int(data[0]) if data and data[0] else 0
            M.logout()
        return jsonify(ok=True, msg=f'Подключение успешно. В папке «{folder}»: {count} писем')
    except imaplib.IMAP4.error as e:
        return jsonify(ok=False, msg=f'Ошибка аутентификации: {e}')
    except OSError as e:
        return jsonify(ok=False, msg=f'Сетевая ошибка: {e}')
    except Exception as e:
        return jsonify(ok=False, msg=f'Ошибка: {e}')


@app.route('/settings/sync-imap', methods=['POST'])
@admin_required
def settings_sync_imap():
    """Ручная синхронизация — тонкая обёртка над _do_imap_sync()."""
    result = _do_imap_sync(current_user.id)
    return jsonify(**result)


@app.route('/ai/brief', methods=['POST'])
@login_required
def ai_brief():
    """Генерирует блок «Что происходит» через ИИ по данным из дашборда."""
    import json as _json
    import re as _re
    from ai_client import call_ai_provider as _call_ai

    req = request.get_json(force=True, silent=True) or {}

    provider = (AiProvider.query.filter_by(is_default=True, is_active=True).first()
                or AiProvider.query.filter_by(is_active=True).first())
    if not provider:
        return jsonify(ok=False, error='ИИ-провайдер не настроен'), 400

    api_key = decrypt_value(provider.api_key) if provider.api_key else ''

    def _mln(v):
        sign = '+' if v >= 0 else '−'
        return f'{sign}{abs(v)/1e6:.1f} млн ₽'.replace('.', ',')

    company_name = req.get('company_name', 'Предприятие')
    period       = req.get('period', '')
    months_list  = req.get('months', [])
    closing      = req.get('closing', [])
    pl           = req.get('pl', {})
    dz_tot       = req.get('dz_tot', {})
    kz_tot       = req.get('kz_tot', {})
    cash         = req.get('cash', {})
    kz_rows      = req.get('kz', [])
    hazard       = req.get('hazard', '')

    lines = [f'Организация: {company_name}']
    if period:
        lines.append(f'Отчётный период: {period}')
    if months_list:
        closed_m     = [m for m, s in zip(months_list, closing) if 'закрыт' in s.lower() and 'не закрыт' not in s.lower()]
        not_closed_m = [m for m, s in zip(months_list, closing) if 'не закрыт' in s.lower()]
        if closed_m:
            lines.append(f'Закрытые месяцы: {", ".join(closed_m)}')
        if not_closed_m:
            lines.append(f'⚠ Незакрытые (данные неполные): {", ".join(not_closed_m)}')

    if pl:
        rev   = pl.get('rev_total', 0)
        nds   = pl.get('nds_total', 0)
        cost  = pl.get('cost_total', 0)
        adm   = pl.get('adm_total', 0)
        oinc  = pl.get('oinc_total', 0)
        oexp  = pl.get('oexp_total', 0)
        revn  = rev - nds
        spend = cost + adm
        sales = revn - spend
        net   = sales + oinc - oexp
        lines.append('\nФинансовые результаты:')
        lines.append(f'  Выручка без НДС: {revn/1e6:.1f} млн ₽'.replace('.', ','))
        lines.append(f'  Расходы (производство + управление): {spend/1e6:.1f} млн ₽'.replace('.', ','))
        lines.append(f'  Результат от основной деятельности: {_mln(sales)}')
        if oinc or oexp:
            lines.append(f'  Прочие доходы: {oinc/1e6:.1f} млн ₽, прочие расходы: {oexp/1e6:.1f} млн ₽'.replace('.', ','))
            lines.append(f'  Итоговый результат: {_mln(net)}')
    else:
        sales = 0; net = 0

    if dz_tot:
        sn, sk = dz_tot.get('sn', 0), dz_tot.get('sk', 0)
        lines.append(f'\nДебиторская задолженность: начало {sn/1e6:.1f} млн ₽ → конец {sk/1e6:.1f} млн ₽ (изм. {_mln(sk-sn)})'.replace('.', ','))
    if kz_tot:
        sn, sk = kz_tot.get('sn', 0), kz_tot.get('sk', 0)
        lines.append(f'Кредиторская задолженность: начало {sn/1e6:.1f} млн ₽ → конец {sk/1e6:.1f} млн ₽ (изм. {_mln(sk-sn)})'.replace('.', ','))
        if kz_rows:
            top = kz_rows[0]
            lines.append(f'Крупнейший кредитор: {top[0]} — {top[2]/1e6:.1f} млн ₽'.replace('.', ','))
    if cash:
        cash_end  = cash.get('end', 0)
        cash_days = cash.get('days', 0)
        lines.append(f'Деньги на счетах и в кассе: {cash_end/1e6:.1f} млн ₽ (≈{max(cash_days,0):.0f} раб. дней платежей)'.replace('.', ','))
    if hazard:
        h_clean = _re.sub(r'<[^>]+>', '', hazard)
        lines.append(f'\n⚠ Качество данных: {h_clean}')

    is_loss = (sales < 0) if pl else False

    system_msg = (
        'Ты финансовый аналитик — специалист по ОСВ организаций ЖКХ России. '
        'По приведённым финансовым данным сформируй блок «Что происходит». '
        'Верни ТОЛЬКО валидный JSON без пояснений:\n'
        '{\n'
        '  "lead": "Одно предложение — главный вывод (чистый текст, без HTML)",\n'
        '  "points": [\n'
        '    "<b>Тема.</b> Факт с цифрами, используй <span class=\\"bad\\">−X млн ₽</span> и <span class=\\"ok\\">+X млн ₽</span> для выделения",\n'
        '    "..."\n'
        '  ],\n'
        '  "foot": "<b>Приоритеты:</b> пункт1; пункт2.",\n'
        '  "is_loss": true\n'
        '}\n'
        'Правила: 4–6 пунктов; каждый начинается с <b>Тема.</b>; '
        'is_loss=true если основная деятельность убыточна; только русский язык.'
    )

    try:
        result = _call_ai(
            base_url=provider.base_url,
            api_key=api_key,
            model=provider.model,
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user',   'content': '\n'.join(lines)},
            ],
            temperature=0.25,
            max_tokens=1200,
            enable_thinking=False,
            timeout=60,
        )
    except Exception as e:
        app.logger.error('AI brief error: %s', e)
        return jsonify(ok=False, error=str(e)), 502

    content = result['content']

    # Извлекаем JSON — убираем markdown-фенсы, ищем первый {...}
    json_str = content
    m = _re.search(r'```(?:json)?\s*([\s\S]*?)```', content, _re.IGNORECASE)
    if m:
        json_str = m.group(1).strip()
    else:
        m2 = _re.search(r'\{[\s\S]*\}', content)
        if m2:
            json_str = m2.group(0)

    try:
        brief_data = _json.loads(json_str)
        # Нормализуем обязательные поля
        brief_data.setdefault('lead', '')
        brief_data.setdefault('points', [])
        brief_data.setdefault('foot', '')
        brief_data.setdefault('is_loss', is_loss)
    except Exception:
        brief_data = {
            'lead': content[:200],
            'points': [f'<b>Заключение.</b> {content[:600]}'],
            'foot': '',
            'is_loss': is_loss,
        }

    return jsonify(ok=True, brief=brief_data)


@app.route('/settings/test-ai', methods=['POST'])
@admin_required
def settings_test_ai():
    """Проверка подключения к AI-провайдеру (AJAX)."""
    from ai_client import call_ai_provider as _call_ai
    base_url  = request.form.get('base_url', '').strip()
    api_key   = request.form.get('api_key', '').strip()
    model     = request.form.get('model', '').strip()

    if not base_url or not model:
        return jsonify(ok=False, msg='Укажите Base URL и модель')
    try:
        result = _call_ai(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{'role': 'user', 'content': 'Ответь одним словом: OK'}],
            max_tokens=16,
            timeout=20,
        )
        answer = result['content'][:120] or '(пустой ответ)'
        return jsonify(ok=True, msg=f'Подключение успешно. Ответ модели: {answer}')
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    companies = Company.query.order_by(Company.name).all()

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'save_imap':
            for key, _, _ in IMAP_KEYS:
                val = request.form.get(key, '').strip()
                if key == 'imap_password' and val:
                    val = encrypt_value(val)
                Setting.set(key, val)
            db.session.commit()
            log_event('imap_settings_saved')

        elif action == 'add_rule':
            company_id      = request.form.get('company_id', type=int)
            sender_pattern  = request.form.get('sender_pattern', '').strip()
            subject_pattern = request.form.get('subject_pattern', '').strip()
            note            = request.form.get('note', '').strip()
            if company_id and (sender_pattern or subject_pattern):
                db.session.add(EmailRule(
                    company_id=company_id,
                    sender_pattern=sender_pattern,
                    subject_pattern=subject_pattern,
                    note=note,
                ))
                db.session.commit()

        elif action == 'delete_rule':
            rule_id = request.form.get('rule_id', type=int)
            rule = db.session.get(EmailRule, rule_id)
            if rule:
                db.session.delete(rule)
                db.session.commit()

        elif action == 'add_ai_provider':
            ai_name    = request.form.get('ai_name', '').strip()
            ai_url     = request.form.get('ai_base_url', '').strip()
            ai_key_raw = request.form.get('ai_api_key', '').strip()
            ai_model   = request.form.get('ai_model', '').strip()
            ai_active  = request.form.get('ai_is_active') == '1'
            ai_default = request.form.get('ai_is_default') == '1'
            if ai_name and ai_url and ai_model:
                if ai_default:
                    AiProvider.query.update({'is_default': False})
                prov = AiProvider(
                    name=ai_name,
                    base_url=ai_url,
                    api_key=encrypt_value(ai_key_raw) if ai_key_raw else '',
                    model=ai_model,
                    is_active=ai_active,
                    is_default=ai_default,
                )
                db.session.add(prov)
                db.session.commit()
                log_event('ai_provider_added', {'name': ai_name})

        elif action == 'update_ai_provider':
            provider_id  = request.form.get('provider_id', type=int)
            prov = db.session.get(AiProvider, provider_id)
            if prov:
                ai_name    = request.form.get('ai_name', '').strip()
                ai_url     = request.form.get('ai_base_url', '').strip()
                ai_key_raw = request.form.get('ai_api_key', '').strip()
                ai_model   = request.form.get('ai_model', '').strip()
                ai_active  = request.form.get('ai_is_active') == '1'
                ai_default = request.form.get('ai_is_default') == '1'
                if ai_name:
                    prov.name = ai_name
                if ai_url:
                    prov.base_url = ai_url
                if ai_model:
                    prov.model = ai_model
                if ai_key_raw:                          # пустое поле → ключ не перезаписывается
                    prov.api_key = encrypt_value(ai_key_raw)
                prov.is_active = ai_active
                if ai_default and not prov.is_default:
                    AiProvider.query.update({'is_default': False})
                prov.is_default = ai_default
                db.session.commit()
                log_event('ai_provider_updated', {'name': prov.name})

        elif action == 'delete_ai_provider':
            provider_id = request.form.get('provider_id', type=int)
            prov = db.session.get(AiProvider, provider_id)
            if prov:
                pname = prov.name
                db.session.delete(prov)
                db.session.commit()
                log_event('ai_provider_deleted', {'name': pname})

        elif action == 'set_default_ai_provider':
            provider_id = request.form.get('provider_id', type=int)
            AiProvider.query.update({'is_default': False})
            prov = db.session.get(AiProvider, provider_id)
            if prov:
                prov.is_default = True
                db.session.commit()
                log_event('ai_provider_set_default', {'name': prov.name})

        return redirect(url_for('settings'))

    imap_cfg = {key: Setting.get(key, default) for key, _, default in IMAP_KEYS}
    # Расшифровываем пароль для отображения в форме (страница только для admin)
    if imap_cfg.get('imap_password'):
        imap_cfg['imap_password'] = decrypt_value(imap_cfg['imap_password'])
    rules = EmailRule.query.order_by(EmailRule.id).all()
    ai_providers = AiProvider.query.order_by(AiProvider.created_at).all()
    return render_template('settings.html',
                           imap_keys=IMAP_KEYS,
                           imap_cfg=imap_cfg,
                           rules=rules,
                           companies=companies,
                           ai_providers=ai_providers)


# ---------------------------------------------------------------------------
# Управление пользователями (только для администраторов)
# ---------------------------------------------------------------------------
@app.route('/users')
@admin_required
def users_list():
    users = User.query.order_by(User.created_at).all()
    return render_template('users.html', users=users)


@app.route('/users/create', methods=['POST'])
@admin_required
def users_create():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', 'user')
    if role not in ('admin', 'user'):
        role = 'user'
    error = None
    if not username:
        error = 'Укажите логин.'
    elif len(password) < 4:
        error = 'Пароль должен содержать не менее 4 символов.'
    elif User.query.filter_by(username=username).first():
        error = f'Пользователь «{username}» уже существует.'
    if error:
        users = User.query.order_by(User.created_at).all()
        return render_template('users.html', users=users, error=error)
    new_user = User(
        username=username,
        password_hash=_hash_password(password),
        role=role,
    )
    db.session.add(new_user)
    db.session.commit()
    log_event('user_created', {'username': username, 'role': role})
    return redirect(url_for('users_list'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def users_delete(user_id):
    user = db.session.get(User, user_id)
    if user and user.id != current_user.id:
        log_event('user_deleted', {'username': user.username})
        db.session.delete(user)
        db.session.commit()
    return redirect(url_for('users_list'))


@app.route('/users/<int:user_id>/set-password', methods=['POST'])
@admin_required
def users_set_password(user_id):
    user = db.session.get(User, user_id)
    password = request.form.get('password', '').strip()
    if user and len(password) >= 4:
        user.password_hash = _hash_password(password)
        db.session.commit()
        log_event('user_password_changed', {'username': user.username})
    return redirect(url_for('users_list'))


@app.route('/users/<int:user_id>/set-role', methods=['POST'])
@admin_required
def users_set_role(user_id):
    user = db.session.get(User, user_id)
    role = request.form.get('role', 'user')
    if role not in ('admin', 'user'):
        role = 'user'
    # Нельзя понизить самого себя
    if user and user.id != current_user.id:
        old_role = user.role
        user.role = role
        db.session.commit()
        log_event('user_role_changed',
                  {'username': user.username, 'old': old_role, 'new': role})
    return redirect(url_for('users_list'))


# ---------------------------------------------------------------------------
# Инициализация БД и первого пользователя
# ---------------------------------------------------------------------------
def init_db():
    with app.app_context():
        db.create_all()
        # Миграция: добавляем новые колонки если отсутствуют
        try:
            from sqlalchemy import text as _text
            with db.engine.connect() as _conn:
                _conn.execute(_text(
                    "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS "
                    "source VARCHAR(20) DEFAULT 'manual'"
                ))
                _conn.commit()
        except Exception:
            pass
        # Миграция: добавляем колонку role если отсутствует
        try:
            from sqlalchemy import text as _text2
            with db.engine.connect() as _conn2:
                _conn2.execute(_text2(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                    "role VARCHAR(20) NOT NULL DEFAULT 'user'"
                ))
                # Первый созданный пользователь (наименьший id) — всегда admin
                _conn2.execute(_text2(
                    "UPDATE users SET role = 'admin' "
                    "WHERE id = (SELECT MIN(id) FROM users)"
                ))
                _conn2.commit()
        except Exception:
            pass
        # Создаём admin, если пользователей нет
        if not User.query.first():
            admin = User(
                username='admin',
                password_hash=_hash_password('admin123'),
                role='admin',
            )
            db.session.add(admin)
            db.session.commit()
            print('Создан пользователь admin / admin123 — смените пароль!')


if __name__ == '__main__':
    # PostgreSQL в Replit иногда поднимается позже Flask.
    # Повторяем попытку подключения до 10 раз с паузой 2 с.
    for _attempt in range(10):
        try:
            with app.app_context():
                init_db()
            break
        except Exception as _e:
            print(f'[startup] БД недоступна (попытка {_attempt + 1}/10): {_e}')
            if _attempt == 9:
                raise
            time.sleep(2)
    _ensure_imap_thread()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)),
            debug=False, threaded=True)
