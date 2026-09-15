# -*- coding: utf-8 -*-
"""HTTP API and responsive UI for the analogue-object module."""
import hashlib
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, exists, func, or_
from sqlalchemy.exc import IntegrityError

from analog_parser import AnalogParseError, parse_pair
from database import (AnalogCostRow, AnalogDiagnostic, AnalogDocument,
                      AnalogImport, AnalogNetworkAttribute, AnalogOrganizationRole,
                      AnalogPassport, AnalogPassportVersion, AnalogSource,
                      AnalogSSRChapter, AnalogSSRItem, AnalogSSRSignature, AnalogTep, AuditLog, db,
                      utcnow)


analog_bp = Blueprint('analog', __name__, url_prefix='/analog-search')
OBJECT_TYPES = ('котельная', 'БМК', 'ЦТП', 'ТП', 'тепловая сеть')
WORK_TYPES = ('строительство', 'капитальный ремонт', 'реконструкция')
ANALOG_STORAGE = os.path.join(os.path.dirname(__file__), 'uploads', '0',
                              'analog_sources')


def _admin_only(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _db_date(value):
    if not value or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _hash_upload(upload):
    if upload is None or not upload.filename:
        raise AnalogParseError('Необходимо передать оба XML-файла', 'missing_file')
    data = upload.read()
    upload.stream.seek(0)
    return data, hashlib.sha256(data).hexdigest()


def _persist_raw(source_hash, suffix, data):
    """Persist immutable source bytes below a durable numeric upload root."""
    directory = os.path.join(ANALOG_STORAGE, source_hash)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, suffix)
    if os.path.exists(path):
        with open(path, 'rb') as stream:
            if hashlib.sha256(stream.read()).hexdigest() != hashlib.sha256(data).hexdigest():
                raise AnalogParseError('Контрольная сумма источника уже занята',
                                       'hash_collision')
    else:
        with open(path, 'xb') as stream:
            stream.write(data)
        os.chmod(path, 0o600)
    return os.path.relpath(path, os.path.dirname(__file__))


def _files():
    conclusion = (request.files.get('conclusion')
                  or request.files.get('conclusion_xml')
                  or request.files.get('xml'))
    ssr = request.files.get('ssr') or request.files.get('gge')
    return conclusion, ssr


def _diagnostics(errors):
    return [{'severity': getattr(e, 'severity', 'error'),
             'code': getattr(e, 'code', 'parse_error'),
             'message': str(e), 'path': getattr(e, 'path', '')}
            for e in errors]


def _html_import_request():
    return request.accept_mimetypes.best == 'text/html'


def _duplicate_import_response(existing):
    passport_id = existing.version.passport_id
    message = 'Такая пара файлов уже импортирована'
    if _html_import_request():
        flash(f'{message}. Открыт существующий паспорт.', 'info')
        return redirect(url_for('analog.passport_detail', passport_id=passport_id))
    return jsonify(status='duplicate', import_id=None, passport_id=passport_id,
                   redirect_url=url_for('analog.passport_detail',
                                        passport_id=passport_id),
                   message=message)


def _result_row(passport, query_capacity=None):
    capacity = Decimal(str(passport.capacity_kw)) if passport.capacity_kw is not None else None
    deviation = None
    if query_capacity is not None and capacity and Decimal(str(query_capacity)) != 0:
        deviation = abs((capacity - Decimal(str(query_capacity))) /
                        Decimal(str(query_capacity)) * 100)
    score = Decimal('0')
    if deviation is not None:
        score = max(Decimal('0'), Decimal('100') - deviation)
    return {'id': passport.id, 'title': passport.title, 'address': passport.address,
            'object_type': passport.object_type, 'work_type': passport.work_type,
            'capacity_kw': str(capacity) if capacity is not None else None,
            'deviation_percent': float(deviation) if deviation is not None else None,
            'similarity_percent': float(score), 'good': deviation is None or deviation <= 10,
            'adjusted_cost': (str(next((v.adjusted_cost for v in passport.versions
                                        if v.id == passport.current_version_id), None) or '')
                              )}


def _advanced_score(passport, criteria):
    """Weighted, deterministic matching; details explain every contribution."""
    rules = (('object_type', 30, 'exact'), ('climate_zone', 20, 'exact'),
             ('work_type', 20, 'exact'), ('capacity_kw', 30, 'numeric'),
             ('diameter_mm', 10, 'network'),
             ('pipe_diameter_mm', 10, 'network'),
             ('laying_method', 10, 'network'),
             ('pipe_system', 10, 'network'))
    details, total, weight = [], Decimal('0'), Decimal('0')
    for field, field_weight, kind in rules:
        expected = criteria.get(field)
        if expected in (None, ''):
            continue
        actual = getattr(passport, field, None)
        if kind == 'network':
            lookup = 'diameter_mm' if field == 'pipe_diameter_mm' else field
            actual = next((item.value for item in passport.network_attributes
                           if item.key == lookup and (
                               not passport.current_version_id or
                               item.version_id == passport.current_version_id)), None)
            points = Decimal(field_weight) if str(actual).lower() == str(expected).lower() else Decimal('0')
            reason = 'точное совпадение' if points else 'значения различаются'
        elif kind == 'exact':
            points = Decimal(field_weight) if str(actual).lower() == str(expected).lower() else Decimal('0')
            reason = 'точное совпадение' if points else 'значения различаются'
        else:
            try:
                a, b = Decimal(str(actual)), Decimal(str(expected))
                delta = abs(a - b) / abs(b) * 100 if b else Decimal('100')
                points = max(Decimal('0'), Decimal(field_weight) * (1 - delta / 100))
                reason = f'отклонение {delta.quantize(Decimal("0.01"))}%'
            except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
                points, reason = Decimal('0'), 'нет числового значения'
        total += points
        weight += Decimal(field_weight)
        details.append({'field': field, 'weight': field_weight,
                        'points': float(points), 'reason': reason,
                        'actual': str(actual) if actual is not None else None,
                        'expected': str(expected)})
    percent = total / weight * 100 if weight else Decimal('0')
    return float(percent), details


def _suitability(passport, criteria):
    """Evaluate explicit required constraints independently of similarity."""
    required = []
    for key, value in criteria.items():
        if not key.endswith('_required') or str(value).lower() not in (
                '1', 'true', 'yes', 'on'):
            continue
        required.append(key[:-9])
    declared = criteria.get('required_fields')
    if isinstance(declared, (list, tuple)):
        required.extend(str(item) for item in declared)
    reasons = []
    for field in dict.fromkeys(required):
        expected = criteria.get(field)
        actual = getattr(passport, field, None)
        if field in ('diameter_mm', 'pipe_diameter_mm', 'laying_method', 'pipe_system'):
            lookup = 'diameter_mm' if field == 'pipe_diameter_mm' else field
            actual = next((item.value for item in passport.network_attributes
                           if item.key == lookup and item.version_id == passport.current_version_id), None)
        passed = False
        if field == 'capacity_kw':
            try:
                passed = (actual is not None and expected and
                          abs((Decimal(str(actual)) - Decimal(str(expected))) /
                              Decimal(str(expected))) <= Decimal('0.10'))
            except (InvalidOperation, ZeroDivisionError, TypeError):
                passed = False
        else:
            passed = str(actual).lower() == str(expected).lower()
        if not passed:
            reasons.append(f'{field}: требуется {expected}, получено {actual}')
    return not reasons, reasons


def _simple_query(args, capacity=None):
    """Build the one indexed query shared by HTML and JSON search."""
    query = AnalogPassport.query.filter_by(archived=False)
    for field in ('object_type', 'climate_zone', 'work_type'):
        value = args.get(field, '').strip()
        if value:
            query = query.filter(getattr(AnalogPassport, field) == value)
    text = args.get('q', '').strip()
    if text:
        query = query.filter(or_(AnalogPassport.title.ilike(f'%{text}%'),
                                 AnalogPassport.address.ilike(f'%{text}%')))
    if capacity:
        wanted = Decimal(str(capacity))
        deviation = case(
            (AnalogPassport.capacity_kw.is_(None), None),
            else_=func.abs((AnalogPassport.capacity_kw - wanted) / wanted))
        group = case((deviation <= Decimal('0.10'), 0), else_=1)
        return query, group, deviation
    return query, None, None


@analog_bp.get('')
@login_required
def index():
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = min(max(request.args.get('per_page', 25, type=int), 1), 100)
    capacity = request.args.get('capacity_kw', type=str)
    if capacity:
        try:
            wanted = Decimal(str(capacity))
            if wanted <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            abort(400, description='Мощность должна быть положительным числом')
    query, group, deviation = _simple_query(request.args, capacity)
    ordering = ((group.asc(), deviation.asc(), AnalogPassport.id.asc())
                if group is not None else (AnalogPassport.id.asc(),))
    pagination = query.order_by(*ordering).paginate(
                                    page=page, per_page=per_page, error_out=False)
    rows = [_result_row(p, capacity) for p in pagination.items]
    if request.args.get('format') == 'json':
        return jsonify(items=rows, page=page, per_page=per_page,
                       total=pagination.total, pages=pagination.pages,
                       good=(query.filter(deviation <= Decimal('0.10')).count()
                             if deviation is not None else pagination.total))
    return render_template('analog_search.html', rows=rows, good_rows=[r for r in rows if r['good']],
                           poor_rows=[r for r in rows if not r['good']],
                           good_count=(query.filter(deviation <= Decimal('0.10')).count()
                                       if deviation is not None else pagination.total),
                           pagination=pagination,
                           object_types=OBJECT_TYPES, work_types=WORK_TYPES)


@analog_bp.get('/api/search')
@login_required
def search_api():
    # The same indexed query as the UI, with stable pagination and ranking.
    args = request.args
    capacity = args.get('capacity_kw', type=str)
    page = max(args.get('page', 1, type=int), 1)
    per_page = min(max(args.get('per_page', 25, type=int), 1), 100)
    if capacity:
        try:
            wanted = Decimal(str(capacity))
            if wanted <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            abort(400, description='Мощность должна быть положительным числом')
    query, group, deviation = _simple_query(args, capacity)
    if capacity:
        query = query.order_by(group.asc(), deviation.asc(), AnalogPassport.id.asc())
    else:
        query = query.order_by(AnalogPassport.id.asc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = [_result_row(p, capacity) for p in pagination.items]
    return jsonify(items=rows, page=page, per_page=per_page,
                   total=pagination.total,
                   good=query.filter(deviation <= Decimal('0.10')).count()
                   if deviation is not None else pagination.total)


@analog_bp.post('/import/preview')
@analog_bp.post('/import')
@analog_bp.post('/api/import/preview')
@login_required
def import_preview():
    conclusion_upload, ssr_upload = _files()
    try:
        conclusion, source_hash = _hash_upload(conclusion_upload)
        ssr, ssr_hash = _hash_upload(ssr_upload)
        requested_target = (request.args.get('passport_id', type=int)
                            or request.form.get('passport_id', type=int))
        if requested_target:
            target = db.session.get(AnalogPassport, requested_target) or abort(404)
            if not current_user.is_admin and target.created_by != current_user.id:
                abort(403)
        existing = AnalogSource.query.filter_by(
            source_hash=source_hash, ssr_hash=ssr_hash).first()
        if existing:
            return _duplicate_import_response(existing)
        pair = parse_pair(conclusion, ssr)
        conclusion_path = _persist_raw(source_hash, 'conclusion.xml', conclusion)
        ssr_path = _persist_raw(ssr_hash, 'summary.gge', ssr)
        row = AnalogImport(source_hash=source_hash, ssr_hash=ssr_hash,
                           source_filename=conclusion_upload.filename[:500],
                           ssr_filename=ssr_upload.filename[:500],
                           raw_conclusion_path=conclusion_path,
                           raw_ssr_path=ssr_path,
                           target_passport_id=requested_target,
                           preview_json=_json(pair), diagnostics_json=_json(pair['diagnostics']),
                           created_by=current_user.id)
        db.session.add(row)
        db.session.flush()
        for item in pair['diagnostics']:
            db.session.add(AnalogDiagnostic(
                import_id=row.id, severity=item.get('severity', 'warning'),
                code=item.get('code', 'diagnostic'),
                message=item.get('message', ''), path=item.get('path', '')))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = AnalogSource.query.filter_by(
                source_hash=source_hash, ssr_hash=ssr_hash).first()
            if existing:
                return _duplicate_import_response(existing)
            raise
        if _html_import_request():
            return render_template('analog_preview.html', pair=pair,
                                   import_id=row.id,
                                   diagnostics=pair['diagnostics'])
        return jsonify(status='preview', import_id=row.id, preview=row.preview,
                       diagnostics=pair['diagnostics'],
                       can_confirm=pair['can_confirm'])
    except AnalogParseError as exc:
        db.session.rollback()
        return jsonify(status='error', diagnostics=_diagnostics([exc]),
                       can_confirm=False), 422


@analog_bp.post('/import/<int:import_id>/confirm')
@analog_bp.post('/imports/<int:import_id>/confirm')
@analog_bp.post('/api/import/<int:import_id>/confirm')
@login_required
def import_confirm(import_id):
    row = db.session.get(AnalogImport, import_id) or abort(404)
    if not current_user.is_admin and row.created_by != current_user.id:
        abort(403, description='Подтвердить импорт может только его автор или администратор')
    if row.status == 'confirmed':
        version = AnalogPassportVersion.query.filter_by(
            source_hash=row.source_hash, ssr_hash=row.ssr_hash).first()
        if request.accept_mimetypes.best == 'text/html' and version:
            return redirect(url_for('analog.passport_detail',
                                    passport_id=version.passport_id))
        return jsonify(status='duplicate', passport_id=version.passport_id if version else None)
    preview = row.preview
    diagnostics = row.diagnostics
    if any(item.get('severity') == 'error' for item in diagnostics):
        return jsonify(status='rejected', diagnostics=diagnostics), 422
    conclusion, ssr = preview['conclusion'], preview['ssr']
    if conclusion.get('examination_result') != 'positive':
        return jsonify(status='rejected', diagnostics=[{
            'code': 'negative_conclusion',
            'message': 'Отрицательное заключение нельзя импортировать',
            'severity': 'error'}]), 422
    try:
        values = request.form.to_dict() or (request.get_json(silent=True) or {})
        target_id = row.target_passport_id
        passport = db.session.get(AnalogPassport, target_id) if target_id else None
        if target_id and passport is None:
            abort(404, description='Целевой паспорт не найден')
        if passport is None:
            identity = None
            if conclusion.get('conclusion_number'):
                identity = AnalogPassportVersion.query.filter_by(
                    conclusion_number=conclusion['conclusion_number']).first()
            passport = identity.passport if identity else None
        if passport is None:
            passport = AnalogPassport(
                title=conclusion.get('expert_object_name') or ssr.get('name') or 'Без названия',
                address=conclusion.get('address', ''),
                object_type=values.get('object_type', 'котельная'),
                work_type=(values.get('work_type') or conclusion.get('work_type', '')).lower(),
                climate_zone=conclusion.get('climate_zone', ''),
                climate_subzone=conclusion.get('climate_conditions', ''),
                cadastral_number=conclusion.get('cadastral_number', ''),
                natural_conditions=conclusion.get('natural_conditions', ''),
                construction_object=conclusion.get('construction_object', ''),
                created_by=current_user.id)
            db.session.add(passport)
            db.session.flush()
        else:
            values = request.form.to_dict() or (request.get_json(silent=True) or {})
            for field in ('title', 'address', 'object_type', 'work_type',
                          'climate_zone', 'climate_subzone',
                          'natural_conditions', 'cadastral_number'):
                if values.get(field) not in (None, ''):
                    setattr(passport, field, values[field])
            passport.cadastral_number = values.get(
                'cadastral_number') or conclusion.get('cadastral_number', passport.cadastral_number)
            passport.natural_conditions = values.get(
                'natural_conditions') or conclusion.get('natural_conditions', passport.natural_conditions)
            passport.construction_object = conclusion.get('construction_object', passport.construction_object)
        version_no = (max((v.version_no for v in passport.versions), default=0) + 1)
        summary = ssr.get('summary') or {}
        version = AnalogPassportVersion(
            passport_id=passport.id, version_no=version_no,
            conclusion_number=conclusion.get('conclusion_number') or f'import-{row.id}',
            approval_date=_db_date(conclusion.get('approval_date')),
            examination_result='positive',
            expert_object_name=conclusion.get('expert_object_name', ''),
            source_hash=row.source_hash, ssr_hash=row.ssr_hash,
            schema_version=conclusion.get('schema_version') or ssr.get('schema_version'),
            cadastral_number=conclusion.get('cadastral_number', ''),
            natural_conditions=conclusion.get('natural_conditions', ''),
            construction_object=conclusion.get('construction_object', ''),
            price_year=ssr.get('price_year'), price_quarter=ssr.get('price_quarter'),
            total_cost=summary.get('total'), payload_json=_json(preview),
            imported_by=current_user.id)
        db.session.add(version)
        db.session.flush()
        for diagnostic in AnalogDiagnostic.query.filter_by(import_id=row.id).all():
            diagnostic.version_id = version.id
        db.session.add(AnalogSource(
            version_id=version.id, source_hash=row.source_hash, ssr_hash=row.ssr_hash,
            source_filename=row.source_filename, ssr_filename=row.ssr_filename,
            source_size=conclusion.get('raw_size', 0), ssr_size=ssr.get('raw_size', 0),
            raw_conclusion_path=row.raw_conclusion_path, raw_ssr_path=row.raw_ssr_path))
        passport.extracted_json = _json({
            'title': conclusion.get('expert_object_name', ''),
            'address': conclusion.get('address', ''), 'work_type': conclusion.get('work_type', ''),
            'climate_zone': conclusion.get('climate_zone', ''),
            'teps': conclusion.get('teps', [])})
        passport.provenance_json = _json({'source_hash': row.source_hash,
                                          'ssr_hash': row.ssr_hash})
        for item in conclusion.get('roles', []):
            if item.get('role') in ('applicant', 'developer', 'customer'):
                db.session.add(AnalogOrganizationRole(passport_id=passport.id,
                    version_id=version.id, extracted_json=_json(item),
                    effective_json=_json(item), **{
                    key: item.get(key, '') for key in ('role', 'name', 'ogrn', 'inn', 'kpp', 'email', 'address')}))
        document_items = list(conclusion.get('documents', []))
        known_ordinals = {item.get('ordinal', '') for item in document_items}
        missing_sections = sorted({
            item.get('parent_ordinal', '') for item in document_items
            if item.get('parent_ordinal') and item.get('parent_ordinal') not in known_ordinals
        })
        document_items = [
            {'ordinal': ordinal, 'parent_ordinal': ordinal.rsplit('.', 1)[0]
             if '.' in ordinal else '', 'name': f'Раздел {ordinal}',
             'node_kind': 'section'}
            for ordinal in missing_sections
        ] + document_items
        document_rows = []
        for item in document_items:
            document_rows.append(AnalogDocument(
                passport_id=passport.id, version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key, '') for key in
                ('ordinal', 'parent_ordinal', 'name', 'file_format', 'checksum', 'note',
                 'signature_name', 'signature_checksum')},
                node_kind=item.get('node_kind', 'document')))
        db.session.add_all(document_rows)
        db.session.flush()
        document_by_ordinal = {item.ordinal: item for item in document_rows}
        for item in document_rows:
            parent = document_by_ordinal.get(item.parent_ordinal)
            if parent is not None and parent.id != item.id:
                item.parent_id = parent.id
        cost_price_level = ''
        if summary.get('total') is not None and (
                ssr.get('price_year') or ssr.get('price_quarter')):
            cost_price_level = f"{ssr.get('price_year') or ''}/Q{ssr.get('price_quarter') or ''}"
        for item in conclusion.get('cost_rows', []):
            if not item.get('price_level'):
                item['price_level'] = cost_price_level
            db.session.add(AnalogCostRow(passport_id=passport.id, version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key) for key in
                ('ordinal', 'name', 'submitted_cost', 'approved_cost', 'change_cost',
                 'price_level', 'vat', 'footnote', 'change_reason', 'provenance')}))
        for item in conclusion.get('teps', []):
            db.session.add(AnalogTep(passport_id=passport.id, version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key) for key in
                ('name', 'unit', 'value', 'normalized_value', 'normalized_unit',
                 'value_text', 'provenance')}))
            if 'мощност' in item.get('name', '').lower() and item.get('value') is not None:
                passport.capacity_kw = item.get('normalized_value') \
                    if item.get('normalized_unit') == 'кВт' else None
        for item in ssr.get('chapters', []):
            db.session.add(AnalogSSRChapter(version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key, 0) for key in
                ('number', 'name', 'building', 'mounting', 'equipment', 'other', 'total')}))
        for item in ssr.get('items', []):
            db.session.add(AnalogSSRItem(version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key, '') for key in
                ('chapter_number', 'item_type', 'ordinal', 'name', 'reason', 'total')}))
        for item in ssr.get('signatures', []):
            db.session.add(AnalogSSRSignature(version_id=version.id, **{
                key: item.get(key, '') for key in
                ('role', 'name', 'position', 'department')}))
        passport.current_version_id = version.id
        row.status, row.confirmed_at = 'confirmed', utcnow()
        db.session.add(AuditLog(user_id=current_user.id, action='analog_import_confirmed',
                                details=_json({'import_id': row.id, 'passport_id': passport.id}),
                                ip=request.remote_addr))
        db.session.commit()
        if request.accept_mimetypes.best == 'text/html':
            return redirect(url_for('analog.passport_detail',
                                    passport_id=passport.id))
        return jsonify(status='confirmed', passport_id=passport.id,
                       version_id=version.id)
    except Exception:
        db.session.rollback()
        raise


@analog_bp.get('/passports/<int:passport_id>')
@login_required
def passport_detail(passport_id):
    passport = db.session.get(AnalogPassport, passport_id) or abort(404)
    requested_version = request.args.get('version', type=int)
    version = next((item for item in passport.versions
                    if item.id == requested_version), None) if requested_version else None
    version = version or next((item for item in passport.versions
                               if item.id == passport.current_version_id), None)
    if version is None and passport.versions:
        version = passport.versions[-1]
    version_id = version.id if version else None
    data = {'id': passport.id, 'title': passport.title, 'address': passport.address,
            'object_type': passport.object_type, 'climate_zone': passport.climate_zone,
            'work_type': passport.work_type, 'capacity_kw': str(passport.capacity_kw or ''),
            'archived': passport.archived, 'versions': [
                {'id': item.id, 'version_no': item.version_no,
                 'conclusion_number': item.conclusion_number,
                 'total_cost': str(item.total_cost or ''),
                 'adjusted_cost': str(item.adjusted_cost or ''),
                 'adjustment_provenance': item.adjustment_provenance}
                for item in passport.versions],
            'current_version': {
                'organizations': [x.name for x in passport.organizations
                                  if x.version_id == version_id],
                'documents': [x.name for x in passport.documents
                              if x.version_id == version_id],
                'costs': [str(x.approved_cost or '') for x in passport.costs
                          if x.version_id == version_id],
                'diagnostics': [x.code for x in (version.diagnostics if version else [])],
                'source': {'source_hash': version.source.source_hash,
                           'ssr_hash': version.source.ssr_hash}
                if version and version.source else None}}
    if request.args.get('format') == 'json':
        return jsonify(data)
    return render_template(
        'analog_passport.html', passport=passport, version=version,
        organizations=[x for x in passport.organizations
                       if x.version_id == version_id],
        teps=[x for x in passport.teps if x.version_id == version_id],
        documents=[x for x in passport.documents if x.version_id == version_id],
        costs=[x for x in passport.costs if x.version_id == version_id],
        diagnostics=[x for x in (version.diagnostics if version else [])],
        signatures=[x for x in (version.signatures if version else [])],
        source=(version.source if version else None))


@analog_bp.post('/passports')
@_admin_only
def create_passport():
    values = request.get_json(silent=True) or request.form
    title = (values.get('title') or '').strip()
    if not title:
        abort(400, description='Наименование объекта обязательно')
    object_type = (values.get('object_type') or 'котельная').strip()
    work_type = (values.get('work_type') or '').strip()
    if object_type != 'тепловая сеть' and (not work_type or not values.get('capacity_kw')):
        abort(400, description='Для объектов площадного типа обязательны вид работ и мощность')
    if object_type == 'тепловая сеть' and not (
            values.get('network_length_m') or values.get('network_diameter_mm')):
        abort(400, description='Для тепловой сети укажите длину или диаметр')
    passport = AnalogPassport(
        title=title, address=(values.get('address') or '').strip(),
        object_type=object_type,
        climate_zone=(values.get('climate_zone') or '').strip(),
        climate_subzone=(values.get('climate_subzone') or '').strip(),
        work_type=work_type,
        created_by=current_user.id)
    db.session.add(passport)
    if values.get('capacity_kw'):
        try:
            passport.capacity_kw = Decimal(str(values['capacity_kw']).replace(',', '.'))
        except (InvalidOperation, ValueError):
            abort(400, description='Мощность должна быть числом')
    if object_type == 'тепловая сеть':
        # A manually created network has no version yet; keep its core typed
        # values in the passport extraction envelope until first import.
        passport.extracted_json = _json({
            'network_length_m': values.get('network_length_m', ''),
            'network_diameter_mm': values.get('network_diameter_mm', '')})
    db.session.flush()
    if object_type == 'тепловая сеть':
        version = AnalogPassportVersion(
            passport_id=passport.id, version_no=1,
            conclusion_number=f'manual-{passport.id}',
            examination_result='positive', source_hash=f'manual:{passport.id}',
            ssr_hash=f'manual:{passport.id}', imported_by=current_user.id)
        db.session.add(version)
        db.session.flush()
        for key, raw, unit in (
                ('length_m', values.get('network_length_m', ''), 'м'),
                ('diameter_mm', values.get('network_diameter_mm', ''), 'мм')):
            if raw:
                db.session.add(AnalogNetworkAttribute(
                    passport_id=passport.id, version_id=version.id, key=key,
                    value=str(raw), value_number=Decimal(str(raw).replace(',', '.')),
                    unit=unit, provenance='admin input'))
        passport.current_version_id = version.id
    db.session.add(passport)
    db.session.add(AuditLog(user_id=current_user.id, action='analog_passport_created',
                            details=_json({'title': title}), ip=request.remote_addr))
    db.session.commit()
    if request.accept_mimetypes.best == 'text/html':
        return redirect(url_for('analog.passport_detail', passport_id=passport.id))
    return jsonify(status='created', passport_id=passport.id), 201


@analog_bp.post('/passports/<int:passport_id>/archive')
@_admin_only
def archive(passport_id):
    passport = db.session.get(AnalogPassport, passport_id) or abort(404)
    passport.archived = True
    db.session.add(AuditLog(user_id=current_user.id, action='analog_passport_archived',
                            details=_json({'passport_id': passport.id}), ip=request.remote_addr))
    db.session.commit()
    return jsonify(status='archived') if request.is_json else redirect(url_for('analog.index'))


@analog_bp.post('/passports/<int:passport_id>/reimport')
@_admin_only
def reimport(passport_id):
    if not db.session.get(AnalogPassport, passport_id):
        abort(404)
    # Keep the exact same parser/preview transaction and preserve the target
    # passport title through the normal versioned import path.
    response = import_preview()
    return response


@analog_bp.post('/passports/<int:passport_id>/edit')
@_admin_only
def edit(passport_id):
    passport = db.session.get(AnalogPassport, passport_id) or abort(404)
    values = request.form.to_dict() or (request.get_json(silent=True) or {})
    object_type = values.get('object_type', passport.object_type).strip()
    work_type = values.get('work_type', passport.work_type).strip()
    capacity_value = values.get('capacity_kw', '')
    if object_type != 'тепловая сеть' and (
            not capacity_value and passport.capacity_kw is None):
        abort(400, description='Для объектов площадного типа обязательна мощность')
    if object_type != 'тепловая сеть' and not work_type:
        abort(400, description='Для объектов площадного типа обязателен вид работ')
    network_length = values.get('network_length_m', '').strip()
    network_diameter = values.get('network_diameter_mm', '').strip()
    if object_type == 'тепловая сеть' and not passport.current_version_id:
        db.session.add(passport)
        db.session.flush()
        version = AnalogPassportVersion(
            passport_id=passport.id, version_no=1,
            conclusion_number=f'manual-{passport.id}',
            examination_result='positive', source_hash=f'manual:{passport.id}',
            ssr_hash=f'manual:{passport.id}', imported_by=current_user.id)
        db.session.add(version)
        db.session.flush()
        passport.current_version_id = version.id
    if object_type == 'тепловая сеть' and not (
            network_length or network_diameter or any(
                item.key in ('length_m', 'diameter_mm') and
                item.version_id == passport.current_version_id
                for item in passport.network_attributes)):
        abort(400, description='Для тепловой сети укажите длину или диаметр')
    for field in ('title', 'address', 'object_type', 'climate_zone',
                  'climate_subzone', 'work_type', 'cadastral_number',
                  'natural_conditions', 'construction_object'):
        if field in request.form:
            setattr(passport, field, values[field].strip())
    if capacity_value:
        try:
            passport.capacity_kw = Decimal(capacity_value.replace(',', '.'))
        except (InvalidOperation, ValueError):
            abort(400, description='Мощность должна быть числом')
    if passport.current_version_id:
        for key, raw in (('length_m', network_length),
                         ('diameter_mm', network_diameter)):
            if raw:
                item = next((x for x in passport.network_attributes
                             if x.key == key and x.version_id == passport.current_version_id), None)
                if item is None:
                    item = AnalogNetworkAttribute(
                        passport_id=passport.id, version_id=passport.current_version_id,
                        key=key, unit='м' if key == 'length_m' else 'мм',
                        provenance='admin override')
                    db.session.add(item)
                item.value, item.value_number = raw, Decimal(raw.replace(',', '.'))
    db.session.add(AuditLog(user_id=current_user.id, action='analog_passport_edited',
                            details=_json({'passport_id': passport.id}), ip=request.remote_addr))
    db.session.commit()
    return jsonify(status='updated', passport_id=passport.id) if request.is_json else redirect(
        url_for('analog.passport_detail', passport_id=passport.id))


@analog_bp.post('/advanced/search')
@login_required
def advanced_search():
    criteria = request.get_json(silent=True) or request.form.to_dict()
    query = AnalogPassport.query.filter_by(archived=False)
    if criteria.get('object_type') and str(criteria.get('object_type_required', '')).lower() in (
            '1', 'true', 'yes', 'on'):
        query = query.filter_by(object_type=criteria['object_type'])
    for key in ('diameter_mm', 'pipe_diameter_mm', 'laying_method', 'pipe_system'):
        if criteria.get(key) and str(criteria.get(f'{key}_required', '')).lower() in (
                '1', 'true', 'yes', 'on'):
            stored_key = 'diameter_mm' if key == 'pipe_diameter_mm' else key
            query = query.filter(exists().where(
                (AnalogNetworkAttribute.passport_id == AnalogPassport.id)
                & (AnalogNetworkAttribute.version_id == AnalogPassport.current_version_id)
                & (AnalogNetworkAttribute.key == stored_key)
                & (AnalogNetworkAttribute.value == str(criteria[key]))))
    score_terms = []
    for field, field_weight in (('climate_zone', 20), ('work_type', 20)):
        expected = criteria.get(field)
        if expected:
            score_terms.append(case((getattr(AnalogPassport, field) == expected,
                                     field_weight), else_=0))
    if criteria.get('object_type'):
        score_terms.append(case((AnalogPassport.object_type == criteria['object_type'],
                                 30), else_=0))
    for key, field_weight in (('diameter_mm', 10), ('pipe_diameter_mm', 10),
                              ('laying_method', 10), ('pipe_system', 10)):
        if criteria.get(key):
            stored_key = 'diameter_mm' if key == 'pipe_diameter_mm' else key
            score_terms.append(case((exists().where(
                (AnalogNetworkAttribute.passport_id == AnalogPassport.id)
                & (AnalogNetworkAttribute.version_id == AnalogPassport.current_version_id)
                & (AnalogNetworkAttribute.key == stored_key)
                & (AnalogNetworkAttribute.value == str(criteria[key]))),
                field_weight), else_=0))
    capacity = criteria.get('capacity_kw')
    if capacity:
        try:
            wanted = Decimal(str(capacity))
            if wanted <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            abort(400, description='Мощность должна быть положительным числом')
        delta = func.abs(AnalogPassport.capacity_kw - wanted) / wanted
        score_terms.append(case((AnalogPassport.capacity_kw.is_(None), 0),
                                (delta >= 1, 0),
                                else_=30 * (1 - delta)))
    score = sum(score_terms) if score_terms else 0
    query = query.order_by(score.desc(), AnalogPassport.id.asc())
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = min(max(request.args.get('per_page', 25, type=int), 1), 100)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = []
    for passport in pagination.items:
        similarity, details = _advanced_score(passport, criteria)
        suitable, suitability_reasons = _suitability(passport, criteria)
        suitability_reasons = [
            f"{item['field']}: {item['reason']}" for item in details
            if item['points'] < item['weight']]
        rows.append({'id': passport.id, 'title': passport.title,
                     'similarity_percent': similarity,
                     'details': details,
                     'suitable': suitable,
                     'suitability_reasons': suitability_reasons,
                     'algorithm_version': 'weighted-v1'})
    if request.accept_mimetypes.best == 'text/html':
        return render_template('analog_advanced.html', rows=rows,
                               criteria=criteria, page=page,
                               total=pagination.total)
    return jsonify(items=rows, page=page, per_page=per_page,
                   total=pagination.total, algorithm_version='weighted-v1')


@analog_bp.get('/advanced')
@login_required
def advanced_page():
    return render_template('analog_advanced.html', rows=[], criteria={}, total=0)


@analog_bp.get('/advanced/criteria')
@login_required
def advanced_criteria():
    return jsonify(groups=[
        {'key': 'identity', 'title': 'Идентификация',
         'fields': [{'name': 'object_type', 'required': True},
                    {'name': 'work_type', 'required': False}]},
        {'key': 'environment', 'title': 'Природные условия',
         'fields': [{'name': 'climate_zone', 'required': False},
                    {'name': 'climate_subzone', 'required': False}]},
        {'key': 'capacity', 'title': 'Мощность',
         'fields': [{'name': 'capacity_kw', 'required': False, 'unit': 'кВт'}]},
        {'key': 'network', 'title': 'Тепловые сети',
         'fields': [{'name': 'pipe_diameter_mm', 'required': False},
                    {'name': 'laying_method', 'required': False},
                    {'name': 'pipe_system', 'required': False}]},
    ], algorithm_version='weighted-v1')


@analog_bp.post('/passports/<int:passport_id>/versions/<int:version_id>/adjust')
@_admin_only
def adjust_cost(passport_id, version_id):
    version = db.session.get(AnalogPassportVersion, version_id) or abort(404)
    if version.passport_id != passport_id:
        abort(404)
    try:
        factor = Decimal(str(request.form.get('factor') or
                             (request.get_json(silent=True) or {}).get('factor')))
    except (InvalidOperation, TypeError, ValueError):
        abort(400, description='Коэффициент должен быть десятичным числом')
    if factor <= 0 or factor > Decimal('1000'):
        abort(400, description='Недопустимый коэффициент')
    version.adjustment_factor = factor
    version.adjusted_cost = (Decimal(str(version.total_cost)) * factor
                             if version.total_cost is not None else None)
    version.adjustment_provenance = request.form.get('provenance') or (
        (request.get_json(silent=True) or {}).get('provenance') or
        'Коэффициент задан администратором')
    db.session.add(AuditLog(user_id=current_user.id, action='analog_cost_adjusted',
                            details=_json({'passport_id': passport_id,
                                           'version_id': version_id,
                                           'factor': str(factor)}),
                            ip=request.remote_addr))
    db.session.commit()
    return jsonify(status='updated', adjusted_cost=str(version.adjusted_cost or ''),
                   provenance=version.adjustment_provenance)


@analog_bp.post('/admin/migrate-json-v4')
@_admin_only
def migrate_json_v4():
    upload = request.files.get('file') or request.files.get('json')
    if not upload:
        abort(400, description='Необходим JSON v4')
    try:
        from analog_migration import import_json_v4
        report = import_json_v4(upload.read(), user_id=current_user.id)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if request.accept_mimetypes.best == 'text/html':
            return render_template('analog_migration_report.html',
                                   report={'error': str(exc)}), 422
        return jsonify(status='error', message=str(exc)), 422
    if request.accept_mimetypes.best == 'text/html':
        return render_template('analog_migration_report.html', report=report)
    return jsonify(status='ok', report=report)