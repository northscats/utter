# -*- coding: utf-8 -*-
"""Blueprint /docs/* — универсальный парсер документов.

Каталоги, правила маршрутизации/проверки, пакеты, статус обработки.
"""
import os
import threading
import time

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required

from database import (AnalysisTemplate, DocAnalysisRun, DocCatalog,
                       DocCatalogAnalysisRun, DocCheckRule, DocFile,
                       DocFileMark, DocFilePage, DocFinding,
                       DocFreeAnalysisResult, DocMarkButton, DocPackage,
                       DocRoutingRule, db, utcnow)
from doc_pipeline import (PackageValidationError, get_unrouted_catalog,
                          process_package, validate_zip)

docs_bp = Blueprint('docs', __name__, url_prefix='/docs')
_ANALYSIS_START_LOCK = threading.Lock()


@docs_bp.route('/')
@login_required
def docs_ui():
    """Страница проверки документов (минимальный интерфейс)."""
    from flask import render_template
    from flask_wtf.csrf import generate_csrf
    return render_template('docs_check.html', csrf_token=generate_csrf())

RULE_TYPES = ('filename', 'keyword', 'extension', 'has_artifact')


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


def _analysis_run_dict(run: DocAnalysisRun):
    """Безопасное представление запуска для владельца документа."""
    import json
    try:
        options = json.loads(run.options_json or '{}')
    except (TypeError, ValueError):
        options = {}
    try:
        result = json.loads(run.result_json or '{}')
    except (TypeError, ValueError):
        result = {}
    return dict(
        id=run.id,
        file_id=run.file_id,
        mode=run.mode,
        scope=run.scope,
        user_prompt=run.user_prompt or '',
        template_id=run.template_id,
        template_name=run.template_name or '',
        status=run.status,
        error_text=run.error_text or '',
        options=options,
        result=result,
        author=run.author.username if run.author else '—',
        created_at=run.created_at.strftime('%d.%m.%Y %H:%M'),
        started_at=(run.started_at.strftime('%d.%m.%Y %H:%M')
                    if run.started_at else None),
        completed_at=(run.completed_at.strftime('%d.%m.%Y %H:%M')
                      if run.completed_at else None),
        answer=(run.free_result.content
                if run.free_result is not None else None),
    )


def _catalog_analysis_run_dict(run: DocCatalogAnalysisRun,
                               include_children=True):
    """Представление версии анализа каталога и её фиксированного состава."""
    import json

    def parsed(value, fallback):
        try:
            return json.loads(value or '')
        except (TypeError, ValueError):
            return fallback

    data = dict(
        id=run.id,
        package_id=run.package_id,
        catalog_id=run.catalog_id,
        catalog_name=(run.catalog_name_snapshot
                      or (run.catalog.name if run.catalog else 'Удалённый каталог')),
        mode=run.mode,
        user_prompt=run.user_prompt or '',
        template_id=run.template_id,
        template_name=run.template_name or '',
        file_ids=parsed(run.file_ids_json, []),
        skipped_files=parsed(run.skipped_json, []),
        options=parsed(run.options_json, {}),
        summary=parsed(run.summary_json, {}),
        errors=parsed(run.errors_json, []),
        status=run.status,
        files_total=run.files_total,
        files_done=run.files_done,
        files_cancelled=run.files_cancelled or 0,
        error_text=run.error_text or '',
        author=run.author.username if run.author else '—',
        cancel_requested_by=(
            run.cancel_requester.username if run.cancel_requester else None),
        cancel_requested_at=(
            run.cancel_requested_at.strftime('%d.%m.%Y %H:%M')
            if run.cancel_requested_at else None),
        cancelled_at=(run.cancelled_at.strftime('%d.%m.%Y %H:%M')
                      if run.cancelled_at else None),
        created_at=run.created_at.strftime('%d.%m.%Y %H:%M'),
        completed_at=(run.completed_at.strftime('%d.%m.%Y %H:%M')
                      if run.completed_at else None),
    )
    if include_children:
        data['file_runs'] = [
            _analysis_run_dict(child)
            for child in sorted(run.file_runs, key=lambda x: x.id)
        ]
    return data


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
    # История запусков сохраняется независимо от жизненного цикла каталога.
    for run in DocCatalogAnalysisRun.query.filter_by(catalog_id=cat.id).all():
        if not run.catalog_name_snapshot:
            run.catalog_name_snapshot = cat.name
        run.catalog_id = None
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
            approved=bool(f.approved),
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
    runs = (DocAnalysisRun.query.filter_by(file_id=f.id)
            .order_by(DocAnalysisRun.created_at.desc(), DocAnalysisRun.id.desc())
            .limit(20).all())
    return jsonify(
        id=f.id, orig_name=f.orig_name, status=f.status,
        error_text=f.error_text or '', pages_count=f.pages_count,
        approved=bool(f.approved),
        analysis_status=f.analysis_status or 'none',
        catalog=dict(id=f.catalog.id, name=f.catalog.name) if f.catalog else None,
        pages=[dict(page_num=p.page_num, text=p.text or '',
                    has_image=bool(p.image_path), ocr_used=p.ocr_used)
               for p in pages],
        findings=[dict(id=fi.id, page=fi.page_num, type=fi.ftype,
                       quote=fi.snippet or '', description=fi.description or '')
                  for fi in sorted(f.findings, key=lambda x: (x.page_num, x.id))],
        marks=[dict(id=m.id, page=m.page_num, button_key=m.button_key)
               for m in sorted(f.marks, key=lambda x: (x.page_num, x.id))],
        analysis_runs=[_analysis_run_dict(run) for run in runs],
    )


@docs_bp.route('/files/<int:file_id>/approve', methods=['POST'])
@login_required
def file_approve(file_id):
    """Согласование / отмена согласования файла."""
    from database import utcnow
    f = _file_access(db.session.get(DocFile, file_id) or abort(404))
    data = request.get_json(silent=True) or {}
    approved = data.get('approved', True)
    if not isinstance(approved, bool):
        return jsonify(error='approved должен быть булевым значением'), 400
    f.approved = approved
    f.approved_at = utcnow() if approved else None
    db.session.commit()
    return jsonify(ok=True, approved=f.approved,
                   approved_at=f.approved_at.isoformat() if f.approved_at else None)


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
    """Запуск фоновой ИИ-проверки файлов пакета (всех или подмножества).

    Параметры (JSON, все необязательны): find_dates, find_signatures,
    find_seals (bool), skip_analyzed (bool), template_id (int),
    file_ids (list[int]) — только выбранные файлы.
    """
    from doc_analysis import ProviderNotLocalError, analyze_package, get_local_provider

    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    if pkg.status != 'done':
        return jsonify(error='Пакет ещё не обработан — дождитесь завершения'), 400
    data = request.get_json(silent=True) or {}
    options = {}
    for k in ('find_dates', 'find_signatures', 'find_seals', 'skip_analyzed'):
        if k in data:
            if not isinstance(data[k], bool):
                return jsonify(error=f'{k} должен быть булевым значением'), 400
            options[k] = data[k]
    if data.get('template_id') is not None:
        try:
            tid = int(data['template_id'])
        except (TypeError, ValueError):
            return jsonify(error='template_id должен быть числом'), 400
        if db.session.get(AnalysisTemplate, tid) is None:
            return jsonify(error='Шаблон анализа не найден'), 404
        options['template_id'] = tid
    if data.get('file_ids'):
        if not isinstance(data['file_ids'], list):
            return jsonify(error='file_ids должен быть списком'), 400
        try:
            ids = {int(x) for x in data['file_ids']}
        except (TypeError, ValueError):
            return jsonify(error='file_ids должен содержать числа'), 400
        own = {f.id for f in pkg.files}
        options['file_ids'] = list(ids & own)
        if not options['file_ids']:
            return jsonify(error='Указанные файлы не найдены в пакете'), 400

    # ИБ: проверяем локальность провайдера ДО запуска фоновой работы
    try:
        get_local_provider()
    except ProviderNotLocalError as pe:
        return jsonify(error=str(pe)), 400

    # Атомарно резервируем пакет: row lock защищает несколько процессов,
    # локальный lock — параллельные потоки и SQLite в тестах.
    with _ANALYSIS_START_LOCK:
        db.session.expire_all()
        pkg = (DocPackage.query.filter_by(id=pkg_id).with_for_update()
               .populate_existing().first() or abort(404))
        _package_access(pkg)
        if pkg.analysis_status == 'running':
            db.session.rollback()
            return jsonify(error='Проверка уже выполняется'), 409
        pkg.analysis_status = 'running'
        pkg.analysis_error = ''
        pkg.analysis_total = 0
        pkg.analysis_done = 0
        db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(target=analyze_package, args=(app_obj, pkg.id, options),
                     daemon=True).start()
    return jsonify(_package_dict(pkg)), 202


@docs_bp.route('/files/<int:file_id>/analyze', methods=['POST'])
@login_required
def current_file_analyze(file_id):
    """Запускает явный анализ открытого документа пользовательским промптом."""
    from doc_analysis import (ProviderNotLocalError, analyze_current_file,
                              get_local_provider)

    f = _file_access(db.session.get(DocFile, file_id) or abort(404))
    pkg = f.package
    if f.status != 'ready':
        return jsonify(error='Документ ещё не готов к анализу'), 400
    if pkg.status != 'done':
        return jsonify(error='Пакет ещё не обработан — дождитесь завершения'), 400
    if pkg.analysis_status == 'running':
        return jsonify(error='В этом пакете уже выполняется анализ'), 409

    data = request.get_json(silent=True) or {}
    mode = (data.get('mode') or '').strip()
    if mode not in ('supplement', 'free'):
        return jsonify(error='mode должен быть supplement или free'), 400
    user_prompt = (data.get('user_prompt') or '').strip()
    if not user_prompt:
        return jsonify(error='Введите пользовательский промпт'), 400
    if len(user_prompt) > 8000:
        return jsonify(error='Промпт не должен превышать 8000 символов'), 400

    options = {}
    for key in ('find_dates', 'find_signatures', 'find_seals'):
        if key in data:
            if not isinstance(data[key], bool):
                return jsonify(error=f'{key} должен быть булевым значением'), 400
            options[key] = data[key]

    template = None
    if data.get('template_id') not in (None, ''):
        try:
            template_id = int(data['template_id'])
        except (TypeError, ValueError):
            return jsonify(error='template_id должен быть числом'), 400
        template = db.session.get(AnalysisTemplate, template_id)
        if template is None:
            return jsonify(error='Шаблон анализа не найден'), 404
        options.setdefault('find_dates', bool(template.find_dates))
        options.setdefault('find_signatures', bool(template.find_signatures))
        options.setdefault('find_seals', bool(template.find_seals))

    try:
        get_local_provider()
    except ProviderNotLocalError as exc:
        return jsonify(error=str(exc)), 400

    import json
    rules_snapshot = [
        dict(id=rule.id, name=rule.name, prompt_text=rule.prompt_text)
        for rule in (f.catalog.check_rules if f.catalog else [])
        if rule.is_active
    ]
    option_snapshot = dict(
        find_dates=options.get('find_dates', True),
        find_signatures=options.get('find_signatures', True),
        find_seals=options.get('find_seals', True),
        rules=rules_snapshot,
    )

    # Повторная проверка под блокировкой обязательна: пока пользователь
    # заполнял форму, анализ мог стартовать из другой вкладки.
    with _ANALYSIS_START_LOCK:
        db.session.expire_all()
        pkg = (DocPackage.query.filter_by(id=f.package_id).with_for_update()
               .populate_existing().first() or abort(404))
        _package_access(pkg)
        if pkg.analysis_status == 'running':
            db.session.rollback()
            return jsonify(error='В этом пакете уже выполняется анализ'), 409
        run = DocAnalysisRun(
            package_id=pkg.id,
            file_id=f.id,
            created_by=current_user.id,
            mode=mode,
            scope='current_file',
            user_prompt=user_prompt,
            template_id=template.id if template else None,
            template_name=template.name if template else '',
            template_prompt=template.system_prompt if template else '',
            options_json=json.dumps(option_snapshot, ensure_ascii=False),
            status='queued',
        )
        db.session.add(run)
        pkg.analysis_status = 'running'
        pkg.analysis_error = ''
        pkg.analysis_total = 1
        pkg.analysis_done = 0
        db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(
        target=analyze_current_file,
        args=(app_obj, run.id, options),
        daemon=True,
        name=f'doc-analysis-run-{run.id}',
    ).start()
    return jsonify(_analysis_run_dict(run)), 202


@docs_bp.route('/packages/<int:pkg_id>/catalogs/<int:catalog_id>/analyze',
               methods=['POST'])
@login_required
def catalog_analyze(pkg_id, catalog_id):
    """Запускает версионный анализ снимка готовых файлов одного каталога."""
    import json
    from doc_analysis import (ProviderNotLocalError, analyze_catalog,
                              get_local_provider)

    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    catalog = db.session.get(DocCatalog, catalog_id) or abort(404)
    if pkg.status != 'done':
        return jsonify(error='Пакет ещё не обработан — дождитесь завершения'), 400

    all_files = [f for f in pkg.files if f.catalog_id == catalog.id]
    ready_files = sorted(
        (f for f in all_files if f.status == 'ready'),
        key=lambda f: f.id)
    if not all_files:
        return jsonify(error='Каталог пуст'), 400
    if not ready_files:
        return jsonify(error='В каталоге нет готовых документов'), 400

    data = request.get_json(silent=True) or {}
    mode = (data.get('mode') or '').strip()
    if mode not in ('supplement', 'free'):
        return jsonify(error='mode должен быть supplement или free'), 400
    user_prompt = (data.get('user_prompt') or '').strip()
    if not user_prompt:
        return jsonify(error='Введите пользовательский промпт'), 400
    if len(user_prompt) > 8000:
        return jsonify(error='Промпт не должен превышать 8000 символов'), 400

    options = {}
    for key in ('find_dates', 'find_signatures', 'find_seals'):
        if key in data:
            if not isinstance(data[key], bool):
                return jsonify(error=f'{key} должен быть булевым значением'), 400
            options[key] = data[key]
    template = None
    if data.get('template_id') not in (None, ''):
        try:
            template_id = int(data['template_id'])
        except (TypeError, ValueError):
            return jsonify(error='template_id должен быть числом'), 400
        template = db.session.get(AnalysisTemplate, template_id)
        if template is None:
            return jsonify(error='Шаблон анализа не найден'), 404
        options.setdefault('find_dates', bool(template.find_dates))
        options.setdefault('find_signatures', bool(template.find_signatures))
        options.setdefault('find_seals', bool(template.find_seals))

    try:
        get_local_provider()
    except ProviderNotLocalError as exc:
        return jsonify(error=str(exc)), 400

    rules_snapshot = [
        dict(id=rule.id, name=rule.name, prompt_text=rule.prompt_text)
        for rule in catalog.check_rules if rule.is_active
    ]
    option_snapshot = dict(
        find_dates=options.get('find_dates', True),
        find_signatures=options.get('find_signatures', True),
        find_seals=options.get('find_seals', True),
        rules=rules_snapshot,
    )
    ready_ids = [f.id for f in ready_files]
    skipped_snapshot = [
        dict(file_id=f.id, name=f.orig_name, status=f.status,
             error=f.error_text or 'Документ не готов к анализу', skipped=True)
        for f in all_files if f.status != 'ready'
    ]

    with _ANALYSIS_START_LOCK:
        db.session.expire_all()
        pkg = (DocPackage.query.filter_by(id=pkg_id).with_for_update()
               .populate_existing().first() or abort(404))
        _package_access(pkg)
        if pkg.analysis_status == 'running':
            db.session.rollback()
            return jsonify(error='В этом пакете уже выполняется анализ'), 409
        # Фиксируем состав повторным запросом внутри блокировки. После commit
        # дальнейшие перемещения файлов не меняют file_ids_json.
        locked_ready = (DocFile.query.filter(
            DocFile.package_id == pkg.id,
            DocFile.catalog_id == catalog.id,
            DocFile.status == 'ready',
            DocFile.id.in_(ready_ids)).order_by(DocFile.id).all())
        if not locked_ready:
            db.session.rollback()
            return jsonify(error='В каталоге нет готовых документов'), 400
        batch = DocCatalogAnalysisRun(
            package_id=pkg.id,
            catalog_id=catalog.id,
            catalog_name_snapshot=catalog.name,
            created_by=current_user.id,
            mode=mode,
            user_prompt=user_prompt,
            template_id=template.id if template else None,
            template_name=template.name if template else '',
            template_prompt=template.system_prompt if template else '',
            file_ids_json=json.dumps(
                [f.id for f in locked_ready], ensure_ascii=False),
            skipped_json=json.dumps(skipped_snapshot, ensure_ascii=False),
            options_json=json.dumps(option_snapshot, ensure_ascii=False),
            files_total=len(locked_ready),
            status='queued',
        )
        db.session.add(batch)
        db.session.flush()
        for f in locked_ready:
            db.session.add(DocAnalysisRun(
                package_id=pkg.id,
                catalog_run_id=batch.id,
                file_id=f.id,
                created_by=current_user.id,
                mode=mode,
                scope='selected_catalog',
                user_prompt=user_prompt,
                template_id=template.id if template else None,
                template_name=template.name if template else '',
                template_prompt=template.system_prompt if template else '',
                options_json=json.dumps(option_snapshot, ensure_ascii=False),
                status='queued',
            ))
        pkg.analysis_status = 'running'
        pkg.analysis_error = ''
        pkg.analysis_total = len(locked_ready)
        pkg.analysis_done = 0
        db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(
        target=analyze_catalog,
        args=(app_obj, batch.id),
        daemon=True,
        name=f'doc-catalog-analysis-{batch.id}',
    ).start()
    return jsonify(_catalog_analysis_run_dict(batch)), 202


@docs_bp.route('/catalog-analysis-runs/<int:run_id>')
@login_required
def catalog_analysis_run(run_id):
    run = db.session.get(DocCatalogAnalysisRun, run_id) or abort(404)
    _package_access(run.package)
    return jsonify(_catalog_analysis_run_dict(run))


@docs_bp.route('/catalog-analysis-runs/<int:run_id>/cancel', methods=['POST'])
@login_required
def catalog_analysis_cancel(run_id):
    """Запрашивает кооперативную остановку без завершения Python-потока."""
    with _ANALYSIS_START_LOCK:
        run = (DocCatalogAnalysisRun.query.filter_by(id=run_id)
               .with_for_update().populate_existing().first() or abort(404))
        _package_access(run.package)
        if run.status == 'cancel_requested':
            db.session.rollback()
            return jsonify(_catalog_analysis_run_dict(run)), 202
        if run.status not in ('queued', 'running'):
            db.session.rollback()
            return jsonify(error='Этот анализ уже завершён'), 409
        now = utcnow()
        run.status = 'cancel_requested'
        run.cancel_requested_by = current_user.id
        run.cancel_requested_at = now
        for child in run.file_runs:
            if child.status in ('queued', 'running'):
                child.status = 'cancel_requested'
        db.session.commit()
    return jsonify(_catalog_analysis_run_dict(run)), 202


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
        fcounts = {'date': 0, 'signature': 0, 'seal': 0}
        for fi in f.findings:
            if fi.ftype in fcounts:
                fcounts[fi.ftype] += 1
        latest_run = (DocAnalysisRun.query.filter_by(file_id=f.id)
                      .order_by(DocAnalysisRun.created_at.desc(),
                                DocAnalysisRun.id.desc()).first())
        out.append(dict(
            file_id=f.id, orig_name=f.orig_name, file_status=f.status,
            pages_count=f.pages_count, approved=bool(f.approved),
            analysis_status=f.analysis_status or 'none',
            catalog=dict(id=f.catalog.id, name=f.catalog.name) if f.catalog else None,
            results=rows,
            findings_count=fcounts,
            marks=[dict(page=m.page_num, button_key=m.button_key) for m in f.marks],
            latest_run=(_analysis_run_dict(latest_run) if latest_run else None),
        ))
    return out


@docs_bp.route('/packages/<int:pkg_id>/results')
@login_required
def package_results(pkg_id):
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    d = _package_dict(pkg)
    d['files'] = _results_for_package(pkg)
    runs = (DocCatalogAnalysisRun.query.filter_by(package_id=pkg.id)
            .order_by(DocCatalogAnalysisRun.created_at.desc(),
                      DocCatalogAnalysisRun.id.desc()).limit(20).all())
    d['catalog_runs'] = [
        _catalog_analysis_run_dict(run) for run in runs
    ]
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


# ---------------------------------------------------------------------------
# Кнопки ручных отметок
# ---------------------------------------------------------------------------
def _slug_key(label: str) -> str:
    """Ключ кнопки из названия: транслитерация + нормализация."""
    tr = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh',
          'з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o',
          'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'c',
          'ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu',
          'я':'ya'}
    out = []
    for ch in (label or '').lower():
        if ch in tr:
            out.append(tr[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append('_')
    key = '_'.join(x for x in ''.join(out).split('_') if x)
    return key[:80] or 'btn'


def _mark_button_dict(b):
    return dict(id=b.id, key=b.key, label=b.label,
                catalog=dict(id=b.catalog.id, name=b.catalog.name)
                if b.catalog else None)


@docs_bp.route('/mark-buttons')
@login_required
def mark_buttons_list():
    btns = DocMarkButton.query.order_by(DocMarkButton.id).all()
    return jsonify([_mark_button_dict(b) for b in btns])


@docs_bp.route('/mark-buttons', methods=['POST'])
@login_required
def mark_button_create():
    _admin_required()
    data = request.get_json(silent=True) or {}
    label = (data.get('label') or '').strip()
    if not label:
        return jsonify(error='Название кнопки обязательно'), 400
    catalog_id = None
    if data.get('catalog_id') is not None:
        cat = db.session.get(DocCatalog, int(data['catalog_id'] or 0))
        if cat is None:
            return jsonify(error='Каталог не найден'), 404
        catalog_id = cat.id
    key = base = _slug_key(label)
    n = 1
    while DocMarkButton.query.filter_by(key=key).first() is not None:
        n += 1
        key = f'{base}_{n}'
    btn = DocMarkButton(key=key, label=label[:255], catalog_id=catalog_id)
    db.session.add(btn)
    db.session.commit()
    return jsonify(_mark_button_dict(btn)), 201


@docs_bp.route('/mark-buttons/<int:btn_id>', methods=['DELETE'])
@login_required
def mark_button_delete(btn_id):
    _admin_required()
    btn = db.session.get(DocMarkButton, btn_id) or abort(404)
    # Отметки, сделанные этой кнопкой, удаляем вместе с ней
    DocFileMark.query.filter_by(button_key=btn.key).delete()
    db.session.delete(btn)
    db.session.commit()
    return jsonify(ok=True)


@docs_bp.route('/files/<int:file_id>/marks/toggle', methods=['POST'])
@login_required
def file_mark_toggle(file_id):
    """Поставить/снять ручную отметку на странице файла."""
    f = _file_access(db.session.get(DocFile, file_id) or abort(404))
    data = request.get_json(silent=True) or {}
    button_key = (data.get('button_key') or '').strip()
    if not button_key or DocMarkButton.query.filter_by(key=button_key).first() is None:
        return jsonify(error='Кнопка отметки не найдена'), 404
    try:
        page = int(data.get('page') or 0)
    except (TypeError, ValueError):
        return jsonify(error='page должен быть числом'), 400
    if page < 1 or (f.pages_count and page > f.pages_count):
        return jsonify(error='Недопустимый номер страницы'), 400
    existing = DocFileMark.query.filter_by(
        file_id=f.id, page_num=page, button_key=button_key).first()
    if existing is not None:
        db.session.delete(existing)
        db.session.commit()
        return jsonify(ok=True, active=False)
    db.session.add(DocFileMark(file_id=f.id, page_num=page,
                               button_key=button_key,
                               created_by=current_user.id))
    db.session.commit()
    return jsonify(ok=True, active=True)


# ---------------------------------------------------------------------------
# Шаблоны анализа
# ---------------------------------------------------------------------------
def _template_dict(t):
    return dict(id=t.id, name=t.name, system_prompt=t.system_prompt or '',
                find_dates=t.find_dates, find_signatures=t.find_signatures,
                find_seals=t.find_seals)


@docs_bp.route('/analysis-templates')
@login_required
def templates_list():
    tpls = AnalysisTemplate.query.order_by(AnalysisTemplate.id).all()
    return jsonify([_template_dict(t) for t in tpls])


@docs_bp.route('/analysis-templates', methods=['POST'])
@login_required
def template_create():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Название шаблона обязательно'), 400
    tpl = AnalysisTemplate(
        name=name[:255],
        system_prompt=(data.get('system_prompt') or '')[:10000],
        find_dates=bool(data.get('find_dates', True)),
        find_signatures=bool(data.get('find_signatures', True)),
        find_seals=bool(data.get('find_seals', True)))
    db.session.add(tpl)
    db.session.commit()
    return jsonify(_template_dict(tpl)), 201


@docs_bp.route('/analysis-templates/<int:tpl_id>', methods=['PUT'])
@login_required
def template_update(tpl_id):
    tpl = db.session.get(AnalysisTemplate, tpl_id) or abort(404)
    data = request.get_json(silent=True) or {}
    if 'name' in data and (data['name'] or '').strip():
        tpl.name = data['name'].strip()[:255]
    if 'system_prompt' in data:
        tpl.system_prompt = (data['system_prompt'] or '')[:10000]
    for k in ('find_dates', 'find_signatures', 'find_seals'):
        if k in data:
            setattr(tpl, k, bool(data[k]))
    db.session.commit()
    return jsonify(_template_dict(tpl))


@docs_bp.route('/analysis-templates/<int:tpl_id>', methods=['DELETE'])
@login_required
def template_delete(tpl_id):
    tpl = db.session.get(AnalysisTemplate, tpl_id) or abort(404)
    db.session.delete(tpl)
    db.session.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Дораспределение файлов по правилам
# ---------------------------------------------------------------------------
@docs_bp.route('/packages/<int:pkg_id>/route-all', methods=['POST'])
@login_required
def package_route_all(pkg_id):
    """Повторно распределяет файлы пакета по правилам маршрутизации.

    Работают и правила по тексту документа (has_artifact) — фраза ищется
    в извлечённом OCR-тексте страниц. Перемещаются файлы из системного
    каталога «Не распределено» и файлы вовсе без каталога."""
    from doc_pipeline import get_unrouted_catalog, route_file
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    unrouted = get_unrouted_catalog()
    routed = 0
    for f in pkg.files:
        if f.catalog_id not in (None, unrouted.id):
            continue
        cat = route_file(f.orig_name, f)
        if cat is not None:
            f.catalog_id = cat.id
            routed += 1
    db.session.commit()
    return jsonify(ok=True, routed=routed)


# ---------------------------------------------------------------------------
# Отчёт (JSON + XLSX) — по пакету, каталогу или файлу
# ---------------------------------------------------------------------------
def _report_rows(pkg, catalog_id=None, file_id=None):
    """Строки отчёта: файл, каталог, согласование и ручные отметки."""
    btn_labels = {b.key: b.label for b in DocMarkButton.query.all()}
    rows = []
    for f in sorted(pkg.files, key=lambda x: ((x.catalog.name if x.catalog else ''), x.orig_name)):
        if catalog_id is not None and f.catalog_id != catalog_id:
            continue
        if file_id is not None and f.id != file_id:
            continue
        rows.append(dict(
            file_id=f.id, filename=f.orig_name,
            catalog=f.catalog.name if f.catalog else '—',
            approved=bool(f.approved),
            approved_at=f.approved_at.isoformat() if f.approved_at else None,
            analysis_status=f.analysis_status or 'none',
            file_status=f.status,
            marks=[dict(page=m.page_num,
                        label=btn_labels.get(m.button_key, m.button_key))
                   for m in sorted(f.marks, key=lambda x: (x.page_num, x.id))],
        ))
    return rows


@docs_bp.route('/report')
@login_required
def report_json():
    pkg_id = request.args.get('package_id', type=int)
    if not pkg_id:
        return jsonify(error='package_id обязателен'), 400
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    rows = _report_rows(pkg,
                        catalog_id=request.args.get('catalog_id', type=int),
                        file_id=request.args.get('file_id', type=int))
    return jsonify(
        package=dict(id=pkg.id, title=pkg.title),
        total=len(rows),
        approved=sum(1 for r in rows if r['approved']),
        rows=rows)


_AN_FILE_LABELS = {'none': '—', 'processing': 'выполняется',
                   'done': 'завершён', 'error': 'ошибка'}


@docs_bp.route('/report/export')
@login_required
def report_export():
    """XLSX-отчёт: файлы, согласование, отметки и итоги по каталогам."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    pkg_id = request.args.get('package_id', type=int)
    if not pkg_id:
        abort(400)
    pkg = _package_access(db.session.get(DocPackage, pkg_id) or abort(404))
    rows = _report_rows(pkg,
                        catalog_id=request.args.get('catalog_id', type=int),
                        file_id=request.args.get('file_id', type=int))

    wb = Workbook()
    ws = wb.active
    ws.title = 'Отчёт'
    headers = ['№', 'Файл', 'Каталог', 'Согласован',
               'Отметки', 'Статус анализа']
    ws.append(headers)
    hdr_fill = PatternFill('solid', fgColor='1F4E79')
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = hdr_fill

    sec_fill = PatternFill('solid', fgColor='DCE6F1')
    # Группировка по каталогам с промежуточными итогами
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r['catalog'], []).append(r)
    n = 0
    for cat_name in sorted(by_cat):
        crows = by_cat[cat_name]
        ws.append([f'{cat_name} — файлов: {len(crows)}, '
                   f'согласовано: {sum(1 for x in crows if x["approved"])}'])
        ws.merge_cells(start_row=ws.max_row, start_column=1,
                       end_row=ws.max_row, end_column=len(headers))
        ws.cell(row=ws.max_row, column=1).fill = sec_fill
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for r in crows:
            n += 1
            marks_txt = '; '.join(f'{m["label"]} (стр. {m["page"]})'
                                  for m in r['marks'])
            ws.append([n, _xlsx_safe(r['filename']), _xlsx_safe(r['catalog']),
                       'Да' if r['approved'] else 'Нет',
                       _xlsx_safe(marks_txt),
                       _AN_FILE_LABELS.get(r['analysis_status'],
                                           r['analysis_status'])])
    ws.append([])
    ws.append(['Итого', f'файлов: {len(rows)}', '',
               f'{sum(1 for r in rows if r["approved"])}/{len(rows)}',
               '', ''])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)

    widths = [5, 45, 22, 12, 45, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        for row in ws.iter_rows(min_col=i, max_col=i):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=(i in (2, 8)))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_title = (pkg.title or f'package_{pkg.id}')[:60]
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'Отчёт_{safe_title}.xlsx')


# ---------------------------------------------------------------------------
# Чанковая загрузка больших ZIP (init → chunk → finalize)
# ---------------------------------------------------------------------------
MAX_UPLOAD_CHUNKS = 200          # 200 × 5 МБ = 1 ГБ максимум
CHUNK_MAX_BYTES = 8 * 1024 * 1024


def _upload_tmp_dir():
    d = os.path.join(current_app.config['DOCS_STORAGE'], '_uploads')
    os.makedirs(d, exist_ok=True)
    return d


@docs_bp.route('/uploads/init', methods=['POST'])
@login_required
def upload_init():
    import secrets
    token = secrets.token_urlsafe(24)
    path = os.path.join(_upload_tmp_dir(), f'{current_user.id}_{token}.part')
    open(path, 'wb').close()
    return jsonify(token=token)


def _upload_part_path(token: str) -> str:
    """Путь part-файла: токен валидируется, привязан к пользователю."""
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9_-]{16,64}', token or ''):
        abort(400)
    return os.path.join(_upload_tmp_dir(), f'{current_user.id}_{token}.part')


@docs_bp.route('/uploads/<token>/chunk', methods=['POST'])
@login_required
def upload_chunk(token):
    path = _upload_part_path(token)
    if not os.path.isfile(path):
        return jsonify(error='Загрузка не найдена или устарела'), 404
    chunk = request.get_data()
    if not chunk:
        return jsonify(error='Пустой фрагмент'), 400
    if len(chunk) > CHUNK_MAX_BYTES:
        return jsonify(error='Фрагмент больше 8 МБ'), 400
    if os.path.getsize(path) + len(chunk) > MAX_UPLOAD_CHUNKS * CHUNK_MAX_BYTES:
        os.remove(path)
        return jsonify(error='Превышен максимальный размер загрузки'), 400
    with open(path, 'ab') as fh:
        fh.write(chunk)
    return jsonify(ok=True, size=os.path.getsize(path))


@docs_bp.route('/uploads/<token>/finalize', methods=['POST'])
@login_required
def upload_finalize(token):
    """Завершает чанковую загрузку и создаёт пакет (как package_create)."""
    import shutil
    path = _upload_part_path(token)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return jsonify(error='Загрузка не найдена или пуста'), 404
    data = request.get_json(silent=True) or {}
    filename = os.path.basename((data.get('filename') or 'archive.zip'))
    if not filename.lower().endswith('.zip'):
        os.remove(path)
        return jsonify(error='Ожидается ZIP-архив (.zip)'), 400

    storage_root = current_app.config['DOCS_STORAGE']
    ts = time.strftime('%Y%m%d_%H%M%S')
    pkg = DocPackage(
        title=(data.get('title') or '').strip()[:255]
              or os.path.splitext(filename)[0][:255],
        uploaded_by=current_user.id,
        zip_filename=filename[:500],
        status='pending')
    db.session.add(pkg)
    db.session.commit()

    pkg_dir = os.path.join(storage_root, str(pkg.id))
    zip_path = os.path.join(pkg_dir, f'archive_{ts}.zip')
    try:
        os.makedirs(pkg_dir, exist_ok=True)
        shutil.move(path, zip_path)
        validate_zip(zip_path)
    except PackageValidationError as ve:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        pkg.status = 'error'
        pkg.error_text = str(ve)
        db.session.commit()
        return jsonify(_package_dict(pkg)), 400
    except Exception:
        shutil.rmtree(pkg_dir, ignore_errors=True)
        if os.path.isfile(path):
            os.remove(path)
        pkg.status = 'error'
        pkg.error_text = 'Не удалось сохранить архив'
        db.session.commit()
        return jsonify(error='Не удалось сохранить архив'), 500

    app_obj = current_app._get_current_object()
    threading.Thread(
        target=process_package,
        args=(app_obj, pkg.id, zip_path, storage_root),
        daemon=True, name=f'docpkg-{pkg.id}').start()
    return jsonify(_package_dict(pkg)), 201
