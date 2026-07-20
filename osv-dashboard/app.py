# -*- coding: utf-8 -*-
"""ОСВ Дашборд — веб-приложение с PostgreSQL, авторизацией и мультипредприятием."""
import json
import os
import re
import time
import traceback

import bcrypt
from flask import (Flask, abort, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from database import Company, DailyRow, EmailRule, Setting, Upload, User, db
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

# Если задан подпуть — сообщаем Flask, где он «живёт»
if SCRIPT_NAME:
    app.config['APPLICATION_ROOT'] = SCRIPT_NAME
    # Доверяем заголовкам X-Forwarded-* от nginx
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# --- PostgreSQL —  DATABASE_URL из переменной окружения ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = None


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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
    return dict(
        id=company.id,
        name=company.name,
        last_period=last.period_label if last else None,
        files_total=files_total,
        closure=last.closure_status if last else None,
        profit_sign=last.profit_sign if last else 'neutral',
        last_upload=(last.uploaded_at.strftime('%d.%m.%Y') if last else None),
    )


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


def _process_files(paths, company_id, ts, back_url):
    """Парсит файлы, строит дашборд, возвращает (upload_record_kwargs, audit)."""
    data, audit = parse_many(paths)
    if not data:
        return None, audit

    dataset = build_dataset(data)
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
            return redirect(url_for('home'))
        error = 'Неверный логин или пароль.'
    return render_template('login.html', error=error, username=username)


@app.route('/logout')
@login_required
def logout():
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

    back_url = url_for('company_detail', company_id=company.id)
    try:
        record_kwargs, audit = _process_files(final_paths, company.id, ts, back_url)
        if record_kwargs is None:
            db.session.rollback()
            import shutil; shutil.rmtree(final_dir, ignore_errors=True)
            return render_template('company_new.html',
                                   error='Ни один файл не удалось разобрать как ОСВ 1С.',
                                   audit=audit)

        _upsert_daily_rows(company.id, final_paths)
        upload = Upload(company_id=company.id, uploaded_by=current_user.id,
                        **record_kwargs)
        db.session.add(upload)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return render_template('company_new.html',
                               error=f'Ошибка обработки: {e}')

    return redirect(url_for('company_detail', company_id=company.id,
                            _anchor='audit'))


# ---------------------------------------------------------------------------
# Страница предприятия
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>')
@login_required
def company_detail(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    return render_template('company.html', company=company, uploads=uploads,
                           audit=None, error=None,
                           daily_stats=_daily_stats(company_id))


# ---------------------------------------------------------------------------
# Единая загрузка файлов (.xls/.xlsx и/или .zip)
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>/upload-all', methods=['POST'])
@login_required
def company_upload_all(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    all_files = [f for f in request.files.getlist('files') if f.filename]

    def _err(msg, audit=None):
        ups = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=ups,
                               audit=audit, error=msg,
                               daily_stats=_daily_stats(company_id))

    if not all_files:
        return _err('Файлы не выбраны.')

    ts = time.strftime('%Y%m%d_%H%M%S')
    audit_out = []
    zip_dates = []   # datetime.date объекты из ZIP-файлов
    total_files = 0

    # — XLS/XLSX: сохраняем в обычную папку (не zip_*)
    xls_files = [f for f in all_files
                 if f.filename.lower().endswith(('.xls', '.xlsx'))]
    if xls_files:
        saved = _save_files(xls_files, company_id, ts)
        total_files += len(saved)
        _upsert_daily_rows(company_id, saved)
        db.session.commit()

    # — ZIP: сохраняем и парсим суточные данные
    zip_files = [f for f in all_files if f.filename.lower().endswith('.zip')]
    for zf in zip_files:
        zip_dir = os.path.join(UPLOADS, str(company_id), f'zip_{ts}')
        os.makedirs(zip_dir, exist_ok=True)
        zip_path = os.path.join(zip_dir, os.path.basename(zf.filename))
        zf.save(zip_path)
        total_files += 1

        successes, errors = parse_zip(zip_path)
        audit_out += [{'file': e['file'], 'acc': '?',
                       'status': f'ОШИБКА: {e["error"]}', 'ok': False}
                      for e in errors]

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
            zip_dates.append(dt)
        db.session.commit()

    if not xls_files and not zip_files:
        return _err('Среди загруженных нет файлов .xls, .xlsx или .zip.')

    # Пересобираем дашборд: месячные XLS + суточные из БД
    back_url = url_for('company_detail', company_id=company_id)
    dataset, rebuild_audit = _rebuild_dashboard_with_daily(company_id)
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
    )
    db.session.add(upload)
    db.session.commit()

    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    return render_template('company.html', company=company, uploads=uploads,
                           audit=audit_out, error=None,
                           daily_stats=_daily_stats(company_id))


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------
@app.route('/dashboard/<int:company_id>/<name>')
@login_required
def dashboard(company_id, name):
    name = os.path.basename(name)
    if not name.endswith('.html'):
        abort(404)
    out_dir = os.path.join(OUTPUT, str(company_id))
    return send_from_directory(out_dir, name)


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
        if ts_dir.startswith('zip_'):   # папки ZIP-загрузок пропускаем
            continue
        full = os.path.join(company_dir, ts_dir)
        if not os.path.isdir(full):
            continue
        for fname in os.listdir(full):
            if fname.lower().endswith(('.xls', '.xlsx')):
                paths.append(os.path.join(full, fname))
    return paths


def _rebuild_dashboard_with_daily(company_id):
    """
    Перестраивает дашборд, объединяя:
      - строки аналитики (rows) из месячных XLS-файлов
      - временно́й ряд (daily) из месячных файлов + DailyRow из БД

    Возвращает (dataset, audit_list) или (None, audit_list) при ошибке.
    """
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
            acc, month = dr.acc, dr.date.strftime('%Y-%m')
            if acc not in data:
                data[acc] = dict(acc=acc, period='', dims=[], rows=[], daily=[],
                                 warnings=[], check_ok=None,
                                 top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))
            data[acc]['daily'].append(dict(
                date=dr.date.isoformat(), month=month,
                path1='', path2='', path3='',
                obd=dr.obd, obk=dr.obk,
            ))
        try:
            dataset = build_dataset(data)
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
        acc = dr.acc
        month = dr.date.strftime('%Y-%m')
        # Пропускаем месяцы, которые есть в детальных месячных файлах
        if acc in monthly_months and month in monthly_months[acc]:
            continue
        if acc not in data:
            # Создаём заглушку счёта с нулевыми rows/top если нет месячных данных
            data[acc] = dict(acc=acc, period='', dims=[], rows=[], daily=[],
                             warnings=[], check_ok=None,
                             top=dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))
        data[acc]['daily'].append(dict(
            date=dr.date.isoformat(),
            month=month,
            path1='', path2='', path3='',
            obd=dr.obd, obk=dr.obk,
        ))

    try:
        dataset = build_dataset(data)
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
        daily_stats = _daily_stats(company_id)
        return render_template('company.html', company=company, uploads=uploads,
                               audit=audit, error=error, daily_stats=daily_stats)

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
    dataset, audit = _rebuild_dashboard_with_daily(company_id)

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
    )
    db.session.add(upload)
    db.session.commit()

    audit_out += [{'file': f'{item["date"]} сч.{item["parsed"]["acc"]}',
                   'acc': item['parsed']['acc'], 'status': 'добавлен', 'ok': True}
                  for item in successes]

    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    daily_stats = _daily_stats(company_id)
    return render_template('company.html', company=company, uploads=uploads,
                           audit=audit_out, error=None, daily_stats=daily_stats)


def _daily_stats(company_id):
    """Статистика суточных данных + временна́я линейка для страницы компании."""
    from datetime import date as _d, timedelta as _td
    today = _d.today()

    rows = DailyRow.query.filter_by(company_id=company_id).all()
    dates_with_data = {r.date for r in rows}
    accs = sorted({r.acc for r in rows})

    # Окно линейки: от первой даты с данными (или 1-го числа текущего месяца)
    # до today+7, но не длиннее 60 дней назад
    if dates_with_data:
        start = min(dates_with_data)
        min_start = today - _td(days=59)
        if start < min_start:
            start = min_start
    else:
        start = today.replace(day=1)
    end = today + _td(days=7)

    timeline = []
    d = start
    while d <= end:
        if d > today:
            status = 'future'
        elif d in dates_with_data:
            status = 'ok'
        else:
            status = 'missing'
        timeline.append(dict(
            date=d.strftime('%d'),
            label=d.strftime('%d.%m'),
            status=status,
            is_today=(d == today),
            new_month=(d.day == 1),
            mon_label=d.strftime('%b') if d.day == 1 else '',
        ))
        d += _td(days=1)

    if not dates_with_data:
        return dict(has_data=False, date_from=None, date_to=None,
                    days=0, accs=[], records=0, timeline=timeline)

    dates_sorted = sorted(dates_with_data)
    return dict(
        has_data=True,
        date_from=dates_sorted[0].strftime('%d.%m.%Y'),
        date_to=dates_sorted[-1].strftime('%d.%m.%Y'),
        days=len(dates_with_data),
        accs=accs,
        records=len(rows),
        timeline=timeline,
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
        company.name = new_name
        db.session.commit()
    return redirect(url_for('company_detail', company_id=company_id))


@app.route('/company/<int:company_id>/delete', methods=['POST'])
@login_required
def company_delete(company_id):
    import shutil
    company = db.session.get(Company, company_id) or abort(404)

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
@login_required
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
        with imaplib.IMAP4_SSL(host, port_int, ssl_context=ctx) as M:
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


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    companies = Company.query.order_by(Company.name).all()

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'save_imap':
            for key, _, _ in IMAP_KEYS:
                val = request.form.get(key, '').strip()
                Setting.set(key, val)
            db.session.commit()

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

        return redirect(url_for('settings'))

    imap_cfg = {key: Setting.get(key, default) for key, _, default in IMAP_KEYS}
    rules = EmailRule.query.order_by(EmailRule.id).all()
    return render_template('settings.html',
                           imap_keys=IMAP_KEYS,
                           imap_cfg=imap_cfg,
                           rules=rules,
                           companies=companies)


# ---------------------------------------------------------------------------
# Инициализация БД и первого пользователя
# ---------------------------------------------------------------------------
def init_db():
    with app.app_context():
        db.create_all()
        # Создаём admin, если пользователей нет
        if not User.query.first():
            admin = User(
                username='admin',
                password_hash=_hash_password('admin123'),
            )
            db.session.add(admin)
            db.session.commit()
            print('Создан пользователь admin / admin123 — смените пароль!')


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
