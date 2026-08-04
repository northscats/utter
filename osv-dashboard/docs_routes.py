# -*- coding: utf-8 -*-
"""Blueprint /docs/* — универсальный парсер документов.

Каталоги, правила маршрутизации/проверки, пакеты, статус обработки.
"""
import os
import threading
import time

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required

from database import (DocCatalog, DocCheckRule, DocFile, DocFilePage,
                      DocPackage, DocRoutingRule, db)
from doc_pipeline import (PackageValidationError, get_unrouted_catalog,
                          process_package, validate_zip)

docs_bp = Blueprint('docs', __name__, url_prefix='/docs')


@docs_bp.route('/')
@login_required
def docs_ui():
    """Страница проверки документов (минимальный интерфейс)."""
    from flask import render_template
    from flask_wtf.csrf import generate_csrf
    return render_template('docs_check.html', csrf_token=generate_csrf())

RULE_TYPES = ('filename', 'keyword', 'extension')


def _admin_required():
    if not current_user.is_admin:
        abort(403)


def _package_access(pkg: DocPackage) -> DocPackage:
    """Доступ к пакету: владелец или администратор. Иначе 403."""
    if pkg.uploaded_by != current_user.id and not current_user.is_admin:
        abort(403)
    return pkg


def _file_access(f: DocFile) -> DocFile:
    """Доступ к файлу — через владельца его пакета."""
    _package_access(f.package)
    return f


# ---------------------------------------------------------------------------
# Каталоги
# ---------------------------------------------------------------------------
def _catalog_dict(c, with_rules=False):
    d = dict(id=c.id, name=c.name, description=c.description or '',
             sort_order=c.sort_order, is_system=c.is_system,
             files_count=len(c.files))
    if with_rules:
        d['routing_rules'] = [dict(id=r.id, rule_type=r.rule_type,
                                   pattern=r.pattern, priority=r.priority)
                              for r in sorted(c.routing_rules,
                                              key=lambda x: (x.priority, x.id))]
        d['check_rules'] = [dict(id=r.id, name=r.name,
                                 prompt_text=r.prompt_text, is_active=r.is_active)
                            for r in c.check_rules]
    return d


@docs_bp.route('/catalogs')
@login_required
def catalogs_list():
    get_unrouted_catalog()  # гарантируем существование системного каталога
    cats = DocCatalog.query.order_by(DocCatalog.sort_order, DocCatalog.id).all()
    return jsonify([_catalog_dict(c, with_rules=True) for c in cats])


@docs_bp.route('/catalogs', methods=['POST'])
@login_required
def catalog_create():
    _admin_required()
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Название каталога обязательно'), 400
    cat = DocCatalog(name=name[:255],
                     description=(data.get('description') or '')[:5000],
                     sort_order=int(data.get('sort_order') or 0))
    db.session.add(cat)
    db.session.commit()
    return jsonify(_catalog_dict(cat, with_rules=True)), 201


@docs_bp.route('/catalogs/<int:cat_id>', methods=['PUT'])
@login_required
def catalog_update(cat_id):
    _admin_required()
    cat = db.session.get(DocCatalog, cat_id) or abort(404)
    if cat.is_system:
        return jsonify(error='Системный каталог нельзя изменять'), 400
    data = request.get_json(silent=True) or {}
    if 'name' in data:
        name = (data['name'] or '').strip()
        if not name:
            return jsonify(error='Название не может быть пустым'), 400
        cat.name = name[:255]
    if 'description' in data:
        cat.description = (data['description'] or '')[:5000]
    if 'sort_order' in data:
        cat.sort_order = int(data['sort_order'] or 0)
    db.session.commit()
    return jsonify(_catalog_dict(cat, with_rules=True))


@docs_bp.route('/catalogs/<int:cat_id>', methods=['DELETE'])
@login_required
def catalog_delete(cat_id):
    _admin_required()
    cat = db.session.get(DocCatalog, cat_id) or abort(404)
    if cat.is_system:
        return jsonify(error='Системный каталог нельзя удалить'), 400
    if cat.files:
        # Файлы переезжают в «Не распределено», история не теряется
        unrouted = get_unrouted_catalog()
        for f in cat.files:
            f.catalog_id = unrouted.id
    db.session.delete(cat)
    db.session.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Правила маршрутизации
# ---------------------------------------------------------------------------
@docs_bp.route('/catalogs/<int:cat_id>/routing-rules', methods=['POST'])
@login_required
def routing_rule_create(cat_id):
    _admin_required()
    cat = db.session.get(DocCatalog, cat_id) or abort(404)
    if cat.is_system:
        return jsonify(error='Для системного каталога правила не задаются'), 400
    data = request.get_json(silent=True) or {}
    rtype = data.get('rule_type')
    pattern = (data.get('pattern') or '').strip()
    if rtype not in RULE_TYPES:
        return jsonify(error=f'rule_type должен быть одним из {RULE_TYPES}'), 400
    if not pattern:
        return jsonify(error='pattern обязателен'), 400
    rule = DocRoutingRule(catalog_id=cat.id, rule_type=rtype,
                          pattern=pattern[:500],
                          priority=int(data.get('priority') or 0))
    db.session.add(rule)
    db.session.commit()
    return jsonify(id=rule.id, rule_type=rule.rule_type,
                   pattern=rule.pattern, priority=rule.priority), 201


@docs_bp.route('/routing-rules/<int:rule_id>', methods=['DELETE'])
@login_required
def routing_rule_delete(rule_id):
    _admin_required()
    rule = db.session.get(DocRoutingRule, rule_id) or abort(404)
    db.session.delete(rule)
    db.session.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Правила проверки
# ---------------------------------------------------------------------------
@docs_bp.route('/catalogs/<int:cat_id>/check-rules', methods=['POST'])
@login_required
def check_rule_create(cat_id):
    _admin_required()
    cat = db.session.get(DocCatalog, cat_id) or abort(404)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    prompt = (data.get('prompt_text') or '').strip()
    if not name or not prompt:
        return jsonify(error='name и prompt_text обязательны'), 400
    rule = DocCheckRule(catalog_id=cat.id, name=name[:255], prompt_text=prompt)
    db.session.add(rule)
    db.session.commit()
    return jsonify(id=rule.id, name=rule.name, prompt_text=rule.prompt_text,
                   is_active=rule.is_active), 201


@docs_bp.route('/check-rules/<int:rule_id>', methods=['PUT'])
@login_required
def check_rule_update(rule_id):
    _admin_required()
    rule = db.session.get(DocCheckRule, rule_id) or abort(404)
    data = request.get_json(silent=True) or {}
    if 'name' in data and (data['name'] or '').strip():
        rule.name = data['name'].strip()[:255]
    if 'prompt_text' in data and (data['prompt_text'] or '').strip():
        rule.prompt_text = data['prompt_text'].strip()
    if 'is_active' in data:
        rule.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify(id=rule.id, name=rule.name, prompt_text=rule.prompt_text,
                   is_active=rule.is_active)


@docs_bp.route('/check-rules/<int:rule_id>', methods=['DELETE'])
@login_required
def check_rule_delete(rule_id):
    _admin_required()
    rule = db.session.get(DocCheckRule, rule_id) or abort(404)
    db.session.delete(rule)
    db.session.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Пакеты
# ---------------------------------------------------------------------------
def _package_dict(p):
    return dict(id=p.id, title=p.title, status=p.status,
                error_text=p.error_text or '',
                files_total=p.files_total, files_done=p.files_done,
                created_at=p.created_at.strftime('%d.%m.%Y %H:%M'),
                zip_filename=p.zip_filename,
                analysis_status=p.analysis_status or 'none',
                analysis_error=p.analysis_error or '',
                analysis_total=p.analysis_total or 0,
                analysis_done=p.analysis_done or 0)


@docs_bp.route('/packages')
@login_required
def packages_list():
    q = DocPackage.query
    if not current_user.is_admin:
        q = q.filter_by(uploaded_by=current_user.id)
    pkgs = q.order_by(DocPackage.created_at.desc()).limit(100).all()
    return jsonify([_package_dict(p) for p in pkgs])


@docs_bp.route('/packages', methods=['POST'])
@login_required
def package_create():
    zfile = request.files.get('zipfile')
    if not zfile or not zfile.filename:
        return jsonify(error='Файл не выбран'), 400
    if not zfile.filename.lower().endswith('.zip'):
        return jsonify(error='Ожидается ZIP-архив (.zip)'), 400

    storage_root = current_app.config['DOCS_STORAGE']
    ts = time.strftime('%Y%m%d_%H%M%S')

    pkg = DocPackage(
        title=(request.form.get('title') or '').strip()[:255]
              or os.path.splitext(zfile.filename)[0][:255],
        uploaded_by=current_user.id,
        zip_filename=os.path.basename(zfile.filename)[:500],
        status='pending',
    )
    db.session.add(pkg)
    db.session.commit()

    pkg_dir = os.path.join(storage_root, str(pkg.id))
    zip_path = os.path.join(pkg_dir, f'archive_{ts}.zip')

    # Сохранение + предварительная валидация. При любой ошибке каталог пакета
    # удаляется целиком — отклонённые загрузки не занимают диск.
    import shutil
    try:
        os.makedirs(pkg_dir, exist_ok=True)
        zfile.save(zip_path)
        validate_zip(zip_path)
    except PackageValidationError as ve:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        pkg.status = 'error'
        pkg.error_text = str(ve)
        db.session.commit()
        return jsonify(_package_dict(pkg)), 400
    except Exception as exc:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        pkg.status = 'error'
        pkg.error_text = f'Не удалось сохранить архив: {exc}'
        db.session.commit()
        return jsonify(error='Не удалось сохранить архив'), 500

    app_obj = current_app._get_current_object()
    threading.Thread(
        target=process_package,
        args=(app_obj, pkg.id, zip_path, storage_root),
        daemon=True, name=f'docpkg-{pkg.id}',
    ).start()

    return jsonify(_package_dict(pkg)), 201


@docs_bp.route('/packages/<int:pkg_id>/status')
@login_required
def package_status(pkg_id):
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    return jsonify(_package_dict(pkg))


@docs_bp.route('/packages/<int:pkg_id>')
@login_required
def package_detail(pkg_id):
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    files = []
    for f in pkg.files:
        files.append(dict(
            id=f.id, orig_name=f.orig_name, status=f.status,
            error_text=f.error_text or '', pages_count=f.pages_count,
            catalog=dict(id=f.catalog.id, name=f.catalog.name) if f.catalog else None,
        ))
    d = _package_dict(pkg)
    d['files'] = files
    return jsonify(d)


@docs_bp.route('/packages/<int:pkg_id>', methods=['DELETE'])
@login_required
def package_delete(pkg_id):
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    if pkg.status == 'processing':
        return jsonify(error='Пакет обрабатывается — дождитесь завершения'), 400
    import shutil
    pkg_dir = os.path.join(current_app.config['DOCS_STORAGE'], str(pkg.id))
    db.session.delete(pkg)
    db.session.commit()
    shutil.rmtree(pkg_dir, ignore_errors=True)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Файлы и страницы
# ---------------------------------------------------------------------------
@docs_bp.route('/files/<int:file_id>')
@login_required
def file_detail(file_id):
    f = _file_access(db.session.get(DocFile, file_id) or abort(404))
    pages = (DocFilePage.query.filter_by(file_id=f.id)
             .order_by(DocFilePage.page_num).all())
    return jsonify(
        id=f.id, orig_name=f.orig_name, status=f.status,
        error_text=f.error_text or '', pages_count=f.pages_count,
        catalog=dict(id=f.catalog.id, name=f.catalog.name) if f.catalog else None,
        pages=[dict(page_num=p.page_num, text=p.text or '',
                    has_image=bool(p.image_path), ocr_used=p.ocr_used)
               for p in pages],
    )


@docs_bp.route('/files/<int:file_id>/set-catalog', methods=['POST'])
@login_required
def file_set_catalog(file_id):
    """Ручное перемещение файла в другой каталог."""
    f = _file_access(db.session.get(DocFile, file_id) or abort(404))
    data = request.get_json(silent=True) or {}
    cat = db.session.get(DocCatalog, int(data.get('catalog_id') or 0))
    if cat is None:
        return jsonify(error='Каталог не найден'), 404
    f.catalog_id = cat.id
    db.session.commit()
    return jsonify(ok=True, catalog=dict(id=cat.id, name=cat.name))


@docs_bp.route('/files/<int:file_id>/pages/<int:page_num>/image')
@login_required
def page_image(file_id, page_num):
    _file_access(db.session.get(DocFile, file_id) or abort(404))
    page = DocFilePage.query.filter_by(file_id=file_id, page_num=page_num).first()
    if page is None or not page.image_path:
        abort(404)
    # Путь из БД, записанный пайплайном — проверяем что внутри хранилища
    storage_root = os.path.realpath(current_app.config['DOCS_STORAGE'])
    real = os.path.realpath(page.image_path)
    if not real.startswith(storage_root + os.sep) or not os.path.isfile(real):
        abort(404)
    return send_file(real, mimetype='image/png')


# ---------------------------------------------------------------------------
# ИИ-проверка по правилам каталогов
# ---------------------------------------------------------------------------
@docs_bp.route('/packages/<int:pkg_id>/analyze', methods=['POST'])
@login_required
def package_analyze(pkg_id):
    """Запуск фоновой ИИ-проверки всех готовых файлов пакета."""
    from doc_analysis import ProviderNotLocalError, analyze_package, get_local_provider

    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    if pkg.status != 'done':
        return jsonify(error='Пакет ещё не обработан — дождитесь завершения'), 400
    if pkg.analysis_status == 'running':
        return jsonify(error='Проверка уже выполняется'), 409

    # ИБ: проверяем локальность провайдера ДО запуска фоновой работы
    try:
        get_local_provider()
    except ProviderNotLocalError as pe:
        return jsonify(error=str(pe)), 400

    pkg.analysis_status = 'running'
    pkg.analysis_error = ''
    pkg.analysis_total = 0
    pkg.analysis_done = 0
    db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(target=analyze_package, args=(app_obj, pkg.id),
                     daemon=True).start()
    return jsonify(_package_dict(pkg)), 202


def _results_for_package(pkg):
    """Сводка результатов проверки: файл → каталог → правила → статусы."""
    import json as _json
    from database import DocCheckResult

    out = []
    for f in pkg.files:
        rows = []
        for r in f.check_results:
            try:
                det = _json.loads(r.details or '{}')
            except ValueError:
                det = {'raw': r.details}
            rows.append(dict(
                rule_id=r.check_rule_id,
                rule_name=r.check_rule.name if r.check_rule else '—',
                status=r.status,
                details=det,
            ))
        out.append(dict(
            file_id=f.id, orig_name=f.orig_name, file_status=f.status,
            pages_count=f.pages_count,
            catalog=dict(id=f.catalog.id, name=f.catalog.name) if f.catalog else None,
            results=rows,
        ))
    return out


@docs_bp.route('/packages/<int:pkg_id>/results')
@login_required
def package_results(pkg_id):
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    d = _package_dict(pkg)
    d['files'] = _results_for_package(pkg)
    return jsonify(d)


_STATUS_LABELS = {
    'ok': 'ОК',
    'violation': 'Нарушение',
    'not_found': 'Не найдено',
    'error': 'Ошибка проверки',
}


def _xlsx_safe(value):
    """Защита от formula injection: строки, начинающиеся с =, +, -, @,
    Excel трактует как формулы — префиксируем апострофом."""
    s = str(value if value is not None else '')
    if s[:1] in ('=', '+', '-', '@'):
        return "'" + s
    return s


@docs_bp.route('/packages/<int:pkg_id>/export')
@login_required
def package_export(pkg_id):
    """Экспорт результатов проверки пакета в XLSX."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    rows = _results_for_package(pkg)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Результаты проверки'

    headers = ['Файл', 'Каталог', 'Правило проверки', 'Статус', 'Детали']
    ws.append(headers)
    hdr_fill = PatternFill('solid', fgColor='1F4E79')
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = hdr_fill

    status_fills = {
        'ok':        PatternFill('solid', fgColor='C6EFCE'),
        'violation': PatternFill('solid', fgColor='FFC7CE'),
        'not_found': PatternFill('solid', fgColor='FFEB9C'),
        'error':     PatternFill('solid', fgColor='D9D9D9'),
    }

    for f in rows:
        cat_name = f['catalog']['name'] if f['catalog'] else '—'
        if not f['results']:
            ws.append([_xlsx_safe(f['orig_name']), _xlsx_safe(cat_name), '—', '—',
                       'Правила проверки для каталога не настроены'
                       if f['file_status'] == 'ready' else
                       f'Файл не обработан ({f["file_status"]})'])
            continue
        for r in f['results']:
            det = r['details']
            det_text = '; '.join(det.get('notes') or []) or det.get('error', '') \
                       or det.get('raw', '') or ''
            ws.append([_xlsx_safe(f['orig_name']), _xlsx_safe(cat_name),
                       _xlsx_safe(r['rule_name']),
                       _STATUS_LABELS.get(r['status'], r['status']),
                       _xlsx_safe(det_text)])
            fill = status_fills.get(r['status'])
            if fill:
                ws.cell(row=ws.max_row, column=4).fill = fill

    widths = [45, 25, 35, 18, 80]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        for row in ws.iter_rows(min_col=i, max_col=i):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=(i == 5))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_title = (pkg.title or f'package_{pkg.id}')[:60]
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Проверка_{safe_title}.xlsx',
    )
