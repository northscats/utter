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

from analog_parser import AnalogParseError, parse_gge, parse_pair
from database import (AnalogClimateReference, AnalogCostIndexReference,
                       AnalogCostRow, AnalogDiagnostic, AnalogDocument,
                      AnalogImport, AnalogNetworkAttribute, AnalogOrganizationRole,
                      AnalogPassport, AnalogPassportVersion, AnalogSource,
                      AnalogSSRChapter, AnalogSSRItem, AnalogSSRSignature, AnalogTep, AuditLog, db,
                      utcnow)


analog_bp = Blueprint('analog', __name__, url_prefix='/analog-search')
OBJECT_TYPES = ('котельная', 'БМК', 'ЦТП', 'ТП', 'тепловая сеть', 'Сети')
WORK_TYPES = ('строительство', 'капитальный ремонт', 'реконструкция')
CAPACITY_UNITS = {
    'mw': Decimal('1000'),
    'gcal_h': Decimal('1163'),
}
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

def _normalized_address(value):
    return ' '.join(str(value or '').lower().replace('ё', 'е').split())
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


def _duplicate_import_response(existing, pair=None):
    passport = existing.version.passport
    passport_id = passport.id
    message = 'Такая пара файлов уже импортирована'
    climate_updated = False
    if pair:
        conclusion = pair.get('conclusion', {})
        climate_details = conclusion.get('climate_details', {})
        if climate_details:
            extracted = {}
            if passport.extracted_json:
                try:
                    extracted = json.loads(passport.extracted_json)
                except (TypeError, ValueError):
                    extracted = {}
            extracted['climate_zone'] = conclusion.get('climate_zone', '')
            extracted['climate_details'] = climate_details
            passport.extracted_json = _json(extracted)
            passport.climate_zone = conclusion.get('climate_zone', '')
            passport.climate_subzone = ''
            passport.natural_conditions = conclusion.get(
                'natural_conditions', '')
            climate_updated = True
            db.session.add(AuditLog(
                user_id=current_user.id,
                action='analog_climate_reparsed',
                details=_json({'passport_id': passport_id}),
                ip=request.remote_addr))
    if passport.archived and current_user.is_admin:
        passport.archived = False
        db.session.add(AuditLog(
            user_id=current_user.id,
            action='analog_passport_restored_by_reimport',
            details=_json({'passport_id': passport_id,
                           'source_hash': existing.source_hash,
                           'ssr_hash': existing.ssr_hash}),
            ip=request.remote_addr))
        db.session.commit()
        message = 'Удалённый ранее объект восстановлен из загруженных файлов'
        if _html_import_request():
            flash(f'{message} и снова добавлен в общую таблицу аналогов.',
                  'success')
            return redirect(url_for('analog.simple_search'))
        return jsonify(status='restored', import_id=None,
                       passport_id=passport_id,
                       redirect_url=url_for('analog.simple_search'),
                       message=message)
    if passport.archived:
        if climate_updated:
            db.session.commit()
        message = ('Эта пара файлов относится к удалённому объекту. '
                   'Восстановить его может администратор')
        if _html_import_request():
            flash(message, 'warning')
            return redirect(url_for('analog.simple_search'))
        return jsonify(status='archived', import_id=None,
                       passport_id=passport_id, message=message), 409
    if _html_import_request():
        if climate_updated:
            db.session.commit()
        flash(f'{message}. Объект уже находится в общей таблице аналогов.', 'info')
        return redirect(url_for('analog.simple_search'))
    if climate_updated:
        db.session.commit()
    return jsonify(status='duplicate', import_id=None, passport_id=passport_id,
                   redirect_url=url_for('analog.passport_detail',
                                        passport_id=passport_id),
                   message=message)


def _capacity_display(passport):
    current_id = passport.current_version_id
    power = next((
        item for item in passport.teps
        if item.version_id == current_id and 'мощност' in (item.name or '').lower()
    ), None)
    if power is not None and power.value_text:
        text = power.value_text.strip()
        unit = (power.unit or '').strip()
        if (unit and unit.lower() not in ('не указано', 'нет', '-')
                and unit.lower() not in text.lower()):
            text = f'{text} {unit}'
        return text
    return f'{passport.capacity_kw} кВт' if passport.capacity_kw is not None else ''


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
            'climate_zone': passport.climate_zone,
            'capacity_kw': str(capacity) if capacity is not None else None,
            'capacity_display': _capacity_display(passport),
            'deviation_percent': float(deviation) if deviation is not None else None,
            'similarity_percent': float(score), 'good': deviation is None or deviation <= 10,
            'adjusted_cost': (str(next((v.adjusted_cost for v in passport.versions
                                        if v.id == passport.current_version_id), None) or '')
                              )}


def _capacity_kw_from_args(args):
    """Normalize the search value to kW while retaining old API compatibility."""
    raw = args.get('capacity_value', '').strip()
    if not raw:
        raw = args.get('capacity_kw', '').strip()
        unit = 'kw'
    else:
        unit = args.get('capacity_unit', 'mw').strip().lower()
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(',', '.'))
        if value <= 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        abort(400, description='Мощность должна быть положительным числом')
    if unit == 'kw':
        return value
    factor = CAPACITY_UNITS.get(unit)
    if factor is None:
        abort(400, description='Поддерживаются единицы мощности Гкал/ч и МВт')
    return value * factor


def _advanced_score(passport, criteria):
    """Apply all filters from the agreed 135/pr + 604/pr search form."""
    version = next((item for item in passport.versions
                    if item.id == passport.current_version_id), None)
    network = {
        item.key: (item.value_number if item.value_number is not None
                   else item.value)
        for item in passport.network_attributes
        if not passport.current_version_id
        or item.version_id == passport.current_version_id
    }
    extracted = {}
    if passport.extracted_json:
        try:
            extracted = json.loads(passport.extracted_json)
        except (TypeError, ValueError):
            extracted = {}
    climate = extracted.get('climate_details', {})
    zone = (passport.climate_zone or '').strip().upper()
    region = next((value for value in ('IV', 'III', 'II', 'I')
                   if zone.startswith(value)), '')
    subzone = zone[len(region):] if region else ''
    length = network.get('length_m')
    capacity_gcal_h = network.get('capacity_gcal_h')
    if capacity_gcal_h in (None, ''):
        capacity_tep = next((
            item for item in passport.teps
            if (not passport.current_version_id
                or item.version_id == passport.current_version_id)
            and ('пропуск' in (item.name or '').lower()
                 or 'гкал/ч' in (item.unit or '').lower())
        ), None)
        if capacity_tep is not None:
            capacity_gcal_h = (capacity_tep.normalized_value
                               if (capacity_tep.normalized_unit or '').lower()
                               == 'гкал/ч' else capacity_tep.value)
    total_cost = version.total_cost if version else None
    cost_per_m = None
    try:
        if total_cost is not None and length is not None and Decimal(str(length)):
            cost_per_m = Decimal(str(total_cost)) / Decimal(str(length))
    except (InvalidOperation, TypeError, ZeroDivisionError):
        pass
    actuals = {
        'price_year': version.price_year if version else None,
        'work_type': passport.work_type,
        'name': passport.title,
        'address': passport.address,
        'length_m': length,
        'capacity_gcal_h': capacity_gcal_h,
        'diameter_mm': network.get('diameter_mm'),
        'climate_region': region,
        'climate_subzone': subzone,
        'wind_region': climate.get('wind_region'),
        'snow_region': climate.get('snow_region'),
        'seismicity': climate.get('seismicity'),
        'geological_conditions': climate.get('geological_conditions'),
        'depth_m': network.get('depth_m'),
        'total_cost': total_cost,
        'cost_per_m': cost_per_m,
        'comparison_year': version.price_year if version else None,
    }
    actuals.update(network)
    rules = (
        ('price_year', 'Год сметного уровня цен', 2, 'exact', False),
        ('work_type', 'Тип объекта', 8, 'exact', True),
        ('name', 'Наименование / описание', 2, 'text', False),
        ('address', 'Адрес / местоположение', 2, 'text', False),
        ('length_m', 'Протяжённость трассы', 15, 'numeric10', True),
        ('capacity_gcal_h', 'Пропускная способность', 10, 'numeric10', True),
        ('diameter_mm', 'Диаметр сети', 10, 'numeric10', True),
        ('climate_region', 'Климатический район', 8, 'exact', True),
        ('climate_subzone', 'Подрайон', 7, 'exact', True),
        ('wind_region', 'Ветровой район', 4, 'exact', True),
        ('snow_region', 'Снеговой район', 4, 'exact', True),
        ('seismicity', 'Сейсмичность', 4, 'exact', True),
        ('geological_conditions', 'Инженерно-геологические условия', 4, 'exact', True),
        ('depth_m', 'Глубина залегания трассы', 3, 'numeric20', False),
        ('laying_method', 'Тип прокладки', 3, 'exact', False),
        ('pipe_system', 'Система трубопроводов', 3, 'exact', False),
        ('material', 'Материал трубопроводов', 3, 'set', False),
        ('insulation', 'Тип изоляции', 2, 'exact', False),
        ('supply_temp_c', 'Температура подачи', 2, 'numeric20', False),
        ('return_temp_c', 'Температура обратки', 2, 'numeric20', False),
        ('pressure_mpa', 'Давление теплоносителя', 2, 'numeric20', False),
        ('chamber_count', 'Количество тепловых камер', 2, 'numeric20', False),
        ('bypass', 'Наличие байпаса', 2, 'exact', False),
        ('bypass_diameter_mm', 'Диаметр байпаса', 1, 'numeric20', False),
        ('bypass_length_m', 'Протяжённость байпаса', 1, 'numeric20', False),
        ('channelless_length_m', 'Бесканальная прокладка', 2, 'numeric20', False),
        ('channel', 'Прокладка в сборных каналах', 2, 'exact', False),
        ('channel_length_m', 'Протяжённость каналов', 1, 'numeric20', False),
        ('channel_type', 'Тип канала', 1, 'exact', False),
        ('monolithic_channel_length_m', 'Монолитный канал', 1, 'numeric20', False),
        ('prefab_channel_length_m', 'Сборный канал', 1, 'numeric20', False),
        ('casing_length_m', 'Протяжённость футляров', 1, 'numeric20', False),
        ('valve_replacement', 'Замена запорной арматуры', 2, 'exact', False),
        ('valve_count', 'Количество арматуры', 1, 'numeric20', False),
        ('valve_note', 'Примечание по арматуре', 1, 'text', False),
        ('water_crossing', 'Наличие водяного перехода', 2, 'exact', False),
        ('communications_zone', 'Охранные зоны коммуникаций', 2, 'exact', False),
        ('zouit', 'Наличие ЗОУИТ', 2, 'exact', False),
        ('zouit_types', 'Типы ЗОУИТ', 2, 'set', False),
        ('total_cost', 'Полная стоимость', 2, 'numeric20', False),
        ('cost_per_m', 'Стоимость на 1 м', 2, 'numeric20', False),
        ('comparison_year', 'Год цен для сравнения', 1, 'exact', False),
    )
    details, total, weight = [], Decimal('0'), Decimal('0')
    for field, label, field_weight, kind, mandatory in rules:
        expected = criteria.get(field)
        if expected in (None, ''):
            continue
        actual = actuals.get(field)
        if kind.startswith('numeric'):
            tolerance = Decimal('10') if kind == 'numeric10' else Decimal('20')
            try:
                a = Decimal(str(actual))
                b = Decimal(str(expected).replace(',', '.'))
                delta = abs(a - b) / abs(b) * 100 if b else Decimal('100')
                matched = delta <= tolerance
                points = max(Decimal('0'),
                             Decimal(field_weight) * (1 - delta / tolerance))
                reason = (f'отклонение {delta.quantize(Decimal("0.01"))}% '
                          f'({"допустимо" if matched else "выше допуска"})')
            except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
                points, reason, matched = (
                    Decimal('0'), 'нет данных для сравнения', False)
        elif kind == 'text':
            matched = (str(expected).strip().lower() in
                       str(actual or '').strip().lower())
            points = Decimal(field_weight) if matched else Decimal('0')
            reason = 'содержит заданное значение' if matched else 'не совпадает'
        elif kind == 'set':
            wanted = {item.strip().lower() for item in str(expected).split(',')
                      if item.strip()}
            present = {item.strip().lower() for item in str(actual or '').split(',')
                       if item.strip()}
            overlap = len(wanted & present) / len(wanted) if wanted else 0
            matched = overlap == 1
            points = Decimal(str(field_weight * overlap))
            reason = ('все значения совпадают' if matched else
                      f'совпало {len(wanted & present)} из {len(wanted)}')
        else:
            matched = (str(actual or '').strip().lower() ==
                       str(expected).strip().lower())
            points = Decimal(field_weight) if matched else Decimal('0')
            reason = 'совпадает' if matched else 'не совпадает'
        total += points
        weight += Decimal(field_weight)
        details.append({'field': field, 'label': label,
                        'weight': field_weight,
                        'points': float(points), 'reason': reason,
                        'mandatory': mandatory, 'passed': matched,
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
    for field in ('object_type', 'work_type'):
        value = args.get(field, '').strip()
        if value:
            if field == 'object_type' and value.lower() in (
                    'сети', 'тепловая сеть'):
                query = query.filter(func.lower(AnalogPassport.object_type).in_(
                    ('сети', 'тепловая сеть')))
            else:
                query = query.filter(getattr(AnalogPassport, field) == value)
    exact_climate = args.get('climate_zone', '').strip().upper()
    climate_region = args.get('climate_region', '').strip().upper()
    if exact_climate:
        query = query.filter(func.upper(AnalogPassport.climate_zone) ==
                             exact_climate)
    elif climate_region in ('I', 'II', 'III', 'IV'):
        accepted = [climate_region] + [
            f'{climate_region}{suffix}' for suffix in ('А', 'Б', 'В', 'Г')]
        query = query.filter(func.upper(AnalogPassport.climate_zone).in_(
            accepted))
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
    return render_template('analog_mode_select.html')


@analog_bp.get('/simple')
@login_required
def simple_search():
    view = request.args.get('view', 'search')
    if view not in ('search', 'base'):
        view = 'search'
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = min(max(request.args.get('per_page', 25, type=int), 1), 100)
    capacity = _capacity_kw_from_args(request.args)
    query, group, deviation = _simple_query(request.args, capacity)
    title = request.args.get('title', '').strip()
    address = request.args.get('address', '').strip()
    if title:
        query = query.filter(AnalogPassport.title.ilike(f'%{title}%'))
    if address:
        query = query.filter(AnalogPassport.address.ilike(f'%{address}%'))
    if view == 'search' and deviation is not None:
        query = query.filter(deviation <= Decimal('0.10'))
    ordering = ((deviation.asc(), AnalogPassport.id.asc())
                if deviation is not None else
                (func.lower(AnalogPassport.title).asc(), AnalogPassport.id.asc()))
    pagination = query.order_by(*ordering).paginate(
                                    page=page, per_page=per_page, error_out=False)
    rows = [_result_row(p, capacity) for p in pagination.items]
    if request.args.get('format') == 'json':
        return jsonify(items=rows, page=page, per_page=per_page,
                       total=pagination.total, pages=pagination.pages,
                        good=pagination.total)
    return render_template('analog_search.html', rows=rows, good_rows=rows,
                            poor_rows=[], good_count=pagination.total,
                           pagination=pagination,
                            object_types=OBJECT_TYPES, work_types=WORK_TYPES,
                            current_view=view)


@analog_bp.get('/api/search')
@login_required
def search_api():
    # The same indexed query as the UI, with stable pagination and ranking.
    args = request.args
    capacity = _capacity_kw_from_args(args)
    page = max(args.get('page', 1, type=int), 1)
    per_page = min(max(args.get('per_page', 25, type=int), 1), 100)
    query, group, deviation = _simple_query(args, capacity)
    if capacity:
        query = query.order_by(group.asc(), deviation.asc(),
                               AnalogPassport.id.asc())
    else:
        query = query.order_by(AnalogPassport.id.asc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = [_result_row(p, capacity) for p in pagination.items]
    return jsonify(items=rows, page=page, per_page=per_page,
                   total=pagination.total,
                   good=sum(1 for row in rows if row['good']))


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
        pair = parse_pair(conclusion, ssr)
        existing = AnalogSource.query.filter_by(
            source_hash=source_hash, ssr_hash=ssr_hash).first()
        if existing:
            return _duplicate_import_response(existing, pair)
        conclusion_number = pair.get('conclusion', {}).get('conclusion_number')
        if not requested_target and conclusion_number:
            existing_identity = AnalogPassportVersion.query.filter_by(
                conclusion_number=conclusion_number).order_by(
                    AnalogPassportVersion.id.desc()).first()
            if existing_identity:
                requested_target = existing_identity.passport_id
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
                return _duplicate_import_response(existing, pair)
            raise
        if _html_import_request():
            return redirect(url_for('analog.import_preview_detail',
                                    import_id=row.id), code=303)
        return jsonify(status='preview', import_id=row.id, preview=row.preview,
                       diagnostics=pair['diagnostics'],
                       can_confirm=pair['can_confirm'])
    except AnalogParseError as exc:
        db.session.rollback()
        return jsonify(status='error', diagnostics=_diagnostics([exc]),
                       can_confirm=False), 422

@analog_bp.get('/imports/<int:import_id>/preview')
@login_required
def import_preview_detail(import_id):
    row = db.session.get(AnalogImport, import_id) or abort(404)
    if not current_user.is_admin and row.created_by != current_user.id:
        abort(403, description='Предпросмотр доступен только его автору или администратору')
    if row.status != 'preview':
        if row.status == 'confirmed':
            version = AnalogPassportVersion.query.filter_by(
                source_hash=row.source_hash, ssr_hash=row.ssr_hash).first()
            if version:
                flash('Объект уже загружен и находится в общей таблице аналогов.',
                      'info')
                return redirect(url_for('analog.simple_search'))
        abort(410, description='Предпросмотр больше не доступен')
    duplicate_passport = (
        db.session.get(AnalogPassport, row.target_passport_id)
        if row.target_passport_id else None
    )
    return render_template('analog_preview.html', pair=row.preview,
                           import_id=row.id, diagnostics=row.diagnostics,
                           duplicate_passport=duplicate_passport)
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
        if not request.is_json and version:
            return redirect(url_for('analog.simple_search', view='base'))
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
                climate_subzone='',
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
            schema_version=ssr.get('schema_version') or conclusion.get('schema_version'),
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
            'climate_details': conclusion.get('climate_details', {}),
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
        power_tep = None
        for item in conclusion.get('teps', []):
            db.session.add(AnalogTep(passport_id=passport.id, version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key) for key in
                ('name', 'unit', 'value', 'normalized_value', 'normalized_unit',
                 'value_text', 'provenance')}))
            if ('мощност' in item.get('name', '').lower()
                    and item.get('value') is not None):
                power_tep = item
        if power_tep is not None:
            passport.capacity_kw = power_tep.get('normalized_value') \
                if power_tep.get('normalized_unit') == 'кВт' else None
        for item in ssr.get('chapters', []):
            db.session.add(AnalogSSRChapter(version_id=version.id,
                extracted_json=_json(item), effective_json=_json(item), **{
                key: item.get(key, 0) for key in
                ('number', 'name', 'building', 'mounting', 'equipment', 'other', 'total')}))
        for item in ssr.get('items', []):
            db.session.add(AnalogSSRItem(version_id=version.id,
                details_json=_json({'details': item.get('details', {}),
                                    'link': item.get('link', ''),
                                    'costs': {key: item.get(key) for key in
                                              ('building', 'mounting',
                                               'equipment', 'other')}}),
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
        if not request.is_json:
            return redirect(url_for('analog.simple_search', view='base'))
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
    gge = {}
    if version and version.payload_json:
        try:
            gge = json.loads(version.payload_json).get('ssr', {})
        except (TypeError, ValueError):
            gge = {}
    if version and version.source and version.source.raw_ssr_path:
        source_path = os.path.realpath(os.path.join(
            os.path.dirname(__file__), version.source.raw_ssr_path))
        storage_root = os.path.realpath(ANALOG_STORAGE)
        if (source_path.startswith(storage_root + os.sep)
                and os.path.isfile(source_path)):
            try:
                with open(source_path, 'rb') as stream:
                    gge = parse_gge(stream.read())
            except (OSError, AnalogParseError):
                pass
    gge_items = {}
    for item in gge.get('items', []):
        gge_items.setdefault(item.get('chapter_number'), []).append(item)
    gge_chapters = [{
        **chapter, 'items': gge_items.get(chapter.get('number'), [])
    } for chapter in gge.get('chapters', [])]
    climate_details = {}
    if passport.extracted_json:
        try:
            climate_details = json.loads(passport.extracted_json).get(
                'climate_details', {})
        except (TypeError, ValueError):
            climate_details = {}
    data = {'id': passport.id, 'title': passport.title, 'address': passport.address,
            'object_type': passport.object_type, 'climate_zone': passport.climate_zone,
            'work_type': passport.work_type, 'capacity_kw': str(passport.capacity_kw or ''),
            'capacity_display': _capacity_display(passport),
            'archived': passport.archived, 'versions': [
                {'id': item.id, 'version_no': item.version_no,
                 'conclusion_number': item.conclusion_number,
                 'total_cost': str(item.total_cost or ''),
                 'adjusted_cost': str(item.adjusted_cost or ''),
                  'adjustment_provenance': item.adjustment_provenance,
                  'adjustment_reference_version': item.adjustment_reference_version,
                  'adjustment_reference_date': item.adjustment_reference_date,
                  'adjustment_source': item.adjustment_source,
                  'adjustment_price_level': item.adjustment_price_level}
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
        climate_details=climate_details,
        capacity_display=_capacity_display(passport),
        organizations=[x for x in passport.organizations
                       if x.version_id == version_id],
        teps=[x for x in passport.teps if x.version_id == version_id],
        documents=[x for x in passport.documents if x.version_id == version_id],
        costs=[x for x in passport.costs if x.version_id == version_id],
        diagnostics=[x for x in (version.diagnostics if version else [])],
        signatures=[x for x in (version.signatures if version else [])],
        source=(version.source if version else None),
        gge=gge, gge_chapters=gge_chapters)


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
    return (jsonify(status='archived') if request.is_json else
            redirect(url_for('analog.simple_search')))


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

@analog_bp.get('/api/climate-suggestion')
@login_required
def climate_suggestion():
    address = (request.args.get('address') or '').strip()
    item = _climate_suggestion(address)
    if item is None:
        return jsonify(status='missing', message=(
            'В согласованном справочнике нет значения для этого адреса; '
            'климатический район не подставлен.'))
    return jsonify(status='suggested', climate_zone=item.climate_zone,
                   climate_subzone=item.climate_subzone, source=item.source,
                   reference_version=item.reference_version,
                   reference_date=item.reference_date.isoformat())
@analog_bp.post('/advanced/search')
@analog_bp.post('/regulatory/search')
@login_required
def advanced_search():
    criteria = request.get_json(silent=True) or request.form.to_dict()
    if not request.is_json:
        for field in ('material', 'zouit_types'):
            criteria[field] = ','.join(request.form.getlist(field))
    query = AnalogPassport.query.filter_by(archived=False)
    if criteria.get('object_type') and str(criteria.get('object_type_required', '')).lower() in (
            '1', 'true', 'yes', 'on'):
        query = query.filter_by(object_type=criteria['object_type'])
    exact_climate = str(criteria.get('climate_zone', '')).strip().upper()
    climate_region = str(criteria.get('climate_region', '')).strip().upper()
    if exact_climate:
        climate_match = (
            func.upper(AnalogPassport.climate_zone) == exact_climate)
    elif climate_region in ('I', 'II', 'III', 'IV'):
        accepted = [climate_region] + [
            f'{climate_region}{suffix}' for suffix in ('А', 'Б', 'В', 'Г')]
        climate_match = func.upper(AnalogPassport.climate_zone).in_(accepted)
    else:
        climate_match = None
    for key in ('diameter_mm', 'pipe_diameter_mm', 'laying_method', 'pipe_system'):
        if criteria.get(key) and str(criteria.get(f'{key}_required', '')).lower() in (
                '1', 'true', 'yes', 'on'):
            stored_key = 'diameter_mm' if key == 'pipe_diameter_mm' else key
            query = query.filter(exists().where(
                (AnalogNetworkAttribute.passport_id == AnalogPassport.id)
                & (AnalogNetworkAttribute.version_id == AnalogPassport.current_version_id)
                & (AnalogNetworkAttribute.key == stored_key)
                & (AnalogNetworkAttribute.value == str(criteria[key]))))
    page = max(request.args.get('page', 1, type=int), 1)
    per_page = min(max(request.args.get('per_page', 25, type=int), 1), 100)
    rows = []
    for passport in query.all():
        similarity, details = _advanced_score(passport, criteria)
        mandatory = [item for item in details if item['mandatory']]
        suitable = bool(mandatory) and all(item['passed'] for item in mandatory)
        suitability_reasons = [
            f"{item['label']}: {item['reason']}" for item in mandatory
            if not item['passed']]
        rows.append({'id': passport.id, 'title': passport.title,
                     'similarity_percent': similarity,
                     'details': details,
                     'suitable': suitable,
                     'suitability_reasons': suitability_reasons,
                     'algorithm_version': '135pr-604pr-v1'})
    rows.sort(key=lambda item: (-item['similarity_percent'], item['id']))
    total = len(rows)
    start = (page - 1) * per_page
    rows = rows[start:start + per_page]
    if request.accept_mimetypes.best == 'text/html':
        return render_template('analog_advanced.html', rows=rows,
                               criteria=criteria, page=page,
                               total=total)
    return jsonify(items=rows, page=page, per_page=per_page,
                   total=total,
                   algorithm_version='135pr-604pr-v1')


@analog_bp.get('/advanced')
@analog_bp.get('/regulatory')
@login_required
def advanced_page():
    return render_template('analog_advanced.html', rows=[], criteria={}, total=0)


@analog_bp.get('/advanced/criteria')
@login_required
def advanced_criteria():
    return jsonify(groups=[
        {'key': 'identity', 'title': 'Идентификация',
         'fields': [{'name': name} for name in
                    ('price_year', 'work_type', 'name', 'address')]},
        {'key': 'mandatory', 'title': 'Обязательные критерии — 135/пр',
         'fields': [{'name': name, 'required': True} for name in
                    ('length_m', 'capacity_gcal_h', 'diameter_mm',
                     'climate_region', 'climate_subzone', 'wind_region',
                     'snow_region', 'seismicity',
                     'geological_conditions')]},
        {'key': 'construction', 'title': 'Конструктивные решения — 604/пр',
         'fields': [{'name': name} for name in
                    ('depth_m', 'laying_method', 'pipe_system', 'material',
                     'insulation', 'supply_temp_c', 'return_temp_c',
                     'pressure_mpa', 'chamber_count', 'bypass',
                     'bypass_diameter_mm', 'bypass_length_m',
                     'channelless_length_m', 'channel', 'channel_length_m',
                     'channel_type', 'monolithic_channel_length_m',
                     'prefab_channel_length_m', 'casing_length_m',
                     'valve_replacement', 'valve_count', 'valve_note',
                     'water_crossing', 'communications_zone', 'zouit',
                     'zouit_types')]},
        {'key': 'cost', 'title': 'Стоимостные показатели',
         'fields': [{'name': name} for name in
                    ('total_cost', 'cost_per_m', 'comparison_year')]},
    ], algorithm_version='135pr-604pr-v1')


@analog_bp.post('/passports/<int:passport_id>/versions/<int:version_id>/adjust')
@_admin_only
def adjust_cost(passport_id, version_id):
    version = db.session.get(AnalogPassportVersion, version_id) or abort(404)
    if version.passport_id != passport_id:
        abort(404)
    values = request.form.to_dict() or (request.get_json(silent=True) or {})
    suggestion = None
    if values.get('reference_version'):
        calculation_date = _db_date(values.get('calculation_date'))
        suggestion = _cost_index_suggestion(values.get('price_level', '').strip(),
                                            calculation_date)
        if str(values.get('confirmed', '')).lower() not in ('1', 'true', 'yes', 'on'):
            abort(400, description='Предложенный коэффициент нужно явно подтвердить')
        if suggestion is None or suggestion.reference_version != values.get('reference_version'):
            abort(409, description='Предложение изменилось; запросите его заново')
    try:
        factor = suggestion.factor if suggestion else Decimal(str(values.get('factor')))
    except (InvalidOperation, TypeError, ValueError):
        abort(400, description='Коэффициент должен быть десятичным числом')
    if factor <= 0 or factor > Decimal('1000'):
        abort(400, description='Недопустимый коэффициент')
    version.adjustment_factor = factor
    version.adjusted_cost = (Decimal(str(version.total_cost)) * factor
                             if version.total_cost is not None else None)
    version.adjustment_provenance = (suggestion.source if suggestion else
                                     values.get('provenance') or
                                     'Коэффициент задан администратором')
    version.adjustment_reference_version = suggestion.reference_version if suggestion else ''
    version.adjustment_reference_date = suggestion.reference_date if suggestion else None
    version.adjustment_source = suggestion.source if suggestion else version.adjustment_provenance
    version.adjustment_price_level = suggestion.price_level if suggestion else (
        values.get('price_level') or '')
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

@analog_bp.get('/api/cost-index-suggestion')
@login_required
def cost_index_suggestion():
    price_level = (request.args.get('price_level') or '').strip()
    calculation_date = _db_date(request.args.get('date'))
    if not calculation_date:
        abort(400, description='Укажите дату расчёта')
    item = _cost_index_suggestion(price_level, calculation_date)
    if item is None:
        return jsonify(status='missing', message=(
            'В версионном справочнике нет коэффициента для этой даты и уровня цен; '
            'стоимость не рассчитана.'))
    return jsonify(status='suggested', factor=str(item.factor),
                   price_level=item.price_level, effective_date=item.effective_date.isoformat(),
                   source=item.source, reference_version=item.reference_version,
                   reference_date=item.reference_date.isoformat())

def _climate_suggestion(address):
    normalized = _normalized_address(address)
    if not normalized:
        return None
    candidates = AnalogClimateReference.query.filter_by(active=True).all()
    matches = [item for item in candidates
               if _normalized_address(item.address_pattern) in normalized]
    if not matches:
        return None
    return max(matches, key=lambda item: (
        item.reference_date, len(_normalized_address(item.address_pattern)), item.id))

@analog_bp.post('/admin/cost-index-references')
@_admin_only
def create_cost_index_reference():
    values = request.get_json(silent=True) or request.form
    effective_date = _db_date(values.get('effective_date'))
    reference_date = _db_date(values.get('reference_date'))
    try:
        factor = Decimal(str(values.get('factor')))
    except (InvalidOperation, TypeError, ValueError):
        abort(400, description='Коэффициент должен быть числом')
    required = ('price_level', 'source', 'reference_version')
    if (not effective_date or not reference_date or factor <= 0 or
            any(not str(values.get(key, '')).strip() for key in required)):
        abort(400, description='Заполните даты, уровень цен, коэффициент, источник и версию')
    item = AnalogCostIndexReference(
        effective_date=effective_date, price_level=str(values['price_level']).strip(),
        factor=factor, source=str(values['source']).strip(),
        reference_version=str(values['reference_version']).strip(),
        reference_date=reference_date)
    db.session.add(item)
    db.session.commit()
    return jsonify(status='created', id=item.id), 201

@analog_bp.post('/admin/climate-references')
@_admin_only
def create_climate_reference():
    values = request.get_json(silent=True) or request.form
    reference_date = _db_date(values.get('reference_date'))
    required = ('address_pattern', 'climate_zone', 'source', 'reference_version')
    if not reference_date or any(not str(values.get(key, '')).strip() for key in required):
        abort(400, description='Заполните правило, район, источник, версию и дату')
    item = AnalogClimateReference(
        address_pattern=str(values['address_pattern']).strip(),
        climate_zone=str(values['climate_zone']).strip(),
        climate_subzone=str(values.get('climate_subzone', '')).strip(),
        source=str(values['source']).strip(),
        reference_version=str(values['reference_version']).strip(),
        reference_date=reference_date)
    db.session.add(item)
    db.session.commit()
    return jsonify(status='created', id=item.id), 201

def _cost_index_suggestion(price_level, calculation_date):
    if not price_level or not calculation_date:
        return None
    return (AnalogCostIndexReference.query
            .filter_by(active=True, price_level=price_level)
            .filter(AnalogCostIndexReference.effective_date <= calculation_date)
            .order_by(AnalogCostIndexReference.effective_date.desc(),
                      AnalogCostIndexReference.reference_date.desc(),
                      AnalogCostIndexReference.id.desc()).first())

@analog_bp.post('/passports/<int:passport_id>/climate/confirm')
@_admin_only
def confirm_climate(passport_id):
    passport = db.session.get(AnalogPassport, passport_id) or abort(404)
    values = request.get_json(silent=True) or request.form
    if str(values.get('confirmed', '')).lower() not in ('1', 'true', 'yes', 'on'):
        abort(400, description='Предложение нужно явно подтвердить')
    item = _climate_suggestion(passport.address)
    if item is None:
        abort(409, description='Для адреса больше нет актуального предложения')
    if values.get('reference_version') != item.reference_version:
        abort(409, description='Версия предложения изменилась; запросите его заново')
    passport.climate_zone = item.climate_zone
    passport.climate_subzone = item.climate_subzone
    passport.climate_source = item.source
    passport.climate_reference_version = item.reference_version
    passport.climate_reference_date = item.reference_date
    db.session.add(AuditLog(user_id=current_user.id, action='analog_climate_confirmed',
                            details=_json({'passport_id': passport.id,
                                           'reference_version': item.reference_version}),
                            ip=request.remote_addr))
    db.session.commit()
    return jsonify(status='confirmed', climate_zone=item.climate_zone,
                   climate_subzone=item.climate_subzone)
