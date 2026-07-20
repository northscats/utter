# -*- coding: utf-8 -*-
"""ОСВ Дашборд — веб-приложение с PostgreSQL, авторизацией и мультипредприятием."""
import json
import os
import time
import traceback

import bcrypt
from flask import (Flask, abort, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)

from database import Company, Upload, User, db
from osvparser import extract_org_name, parse_many
from builder import build_dataset

# ---------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, 'uploads')
OUTPUT = os.path.join(BASE, 'output')
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-change-me')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 МБ

# --- PostgreSQL через DATABASE_URL (Replit предоставляет автоматически) ---
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


def _process_files(paths, company_id, ts, back_url):
    """Парсит файлы, строит дашборд, возвращает (upload_record_kwargs, audit)."""
    data, audit = parse_many(paths)
    if not data:
        return None, audit

    dataset = build_dataset(data)
    html = render_dashboard(dataset, back_url=back_url)

    out_dir = os.path.join(OUTPUT, str(company_id))
    os.makedirs(out_dir, exist_ok=True)
    out_name = f'dashboard_{ts}.html'
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

    files = [f for f in request.files.getlist('files') if f.filename]
    if not files:
        return render_template('company_new.html',
                               error='Выберите хотя бы один файл ОСВ.')

    # Попробуем извлечь название из первого файла — сначала сохраним во временный буфер
    ts = time.strftime('%Y%m%d_%H%M%S')
    # временная папка до создания компании
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

    if not tmp_paths:
        return render_template('company_new.html',
                               error='Среди загруженных нет .xls/.xlsx файлов.')

    # Название: из файла или из формы
    org_name = (request.form.get('org_name') or '').strip()
    if not org_name:
        org_name = extract_org_name(tmp_paths[0]) or ''
    if not org_name:
        # возвращаем форму с предложением ввести вручную
        return render_template('company_new.html',
                               ask_name=True,
                               tmp_key=ts,
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
    audit = request.args.get('audit')  # флаг после редиректа
    return render_template('company.html', company=company, uploads=uploads,
                           audit=None, error=None)


# ---------------------------------------------------------------------------
# Добавление новых файлов к существующему предприятию
# ---------------------------------------------------------------------------
@app.route('/company/<int:company_id>/upload', methods=['POST'])
@login_required
def company_upload(company_id):
    company = db.session.get(Company, company_id) or abort(404)
    files = [f for f in request.files.getlist('files') if f.filename]

    if not files:
        uploads = Upload.query.filter_by(company_id=company_id)\
                              .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=uploads,
                               audit=None, error='Файлы не выбраны.')

    ts = time.strftime('%Y%m%d_%H%M%S')
    saved = _save_files(files, company_id, ts)
    if not saved:
        uploads = Upload.query.filter_by(company_id=company_id)\
                              .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=uploads,
                               audit=None, error='Среди загруженных нет .xls/.xlsx файлов.')

    back_url = url_for('company_detail', company_id=company_id)
    try:
        record_kwargs, audit = _process_files(saved, company_id, ts, back_url)
        if record_kwargs is None:
            uploads = Upload.query.filter_by(company_id=company_id)\
                                  .order_by(Upload.uploaded_at.desc()).all()
            return render_template('company.html', company=company, uploads=uploads,
                                   audit=audit,
                                   error='Ни один файл не удалось разобрать как ОСВ 1С.')

        upload = Upload(company_id=company_id, uploaded_by=current_user.id,
                        **record_kwargs)
        db.session.add(upload)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        uploads = Upload.query.filter_by(company_id=company_id)\
                              .order_by(Upload.uploaded_at.desc()).all()
        return render_template('company.html', company=company, uploads=uploads,
                               audit=None, error=f'Ошибка обработки: {e}')

    uploads = Upload.query.filter_by(company_id=company_id)\
                          .order_by(Upload.uploaded_at.desc()).all()
    return render_template('company.html', company=company, uploads=uploads,
                           audit=audit, error=None)


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
