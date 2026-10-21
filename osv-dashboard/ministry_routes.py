# -*- coding: utf-8 -*-
"""Ministry cabinet routes, independent from the legacy ОСВ analytics."""
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode

from flask import (
    Blueprint, abort, current_app, jsonify, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from database import (
    AuditLog, Company, CompanyDadataSnapshot, UzterReportImport,
    UzterReportRow, db, utcnow,
)
from ministry_geo import (
    MUNICIPALITIES, SOURCE as GEO_SOURCE, municipality_for_territory,
    territory_aliases_for,
)
from uzter_imports import MAX_WORKBOOK_BYTES, ReportImportError, parse_supplier_workbook


ministry_bp = Blueprint('ministry', __name__)


def _cabinet_required(function):
    @wraps(function)
    @login_required
    def wrapped(*args, **kwargs):
        if not (current_user.is_admin
                or current_user.has_module_access('ministry_cabinet')):
            abort(403, description='Нет доступа к кабинету Министерства')
        return function(*args, **kwargs)
    return wrapped


def _json_error(message, status):
    return jsonify(error=message), status


def _stage_directory():
    return os.path.join(current_app.config.get('UPLOADS', os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'uploads')), 'uzter-staging')


def _source_directory():
    return os.path.join(current_app.config.get('UPLOADS', os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'uploads')), 'uzter')


def _preview_serializer():
    return URLSafeTimedSerializer(
        current_app.secret_key, salt='ministry-import-preview-v1')


def _source_display_name(filename):
    value = os.path.basename(str(filename or '').replace('\\', '/'))
    value = re.sub(r'[\x00-\x1f\x7f]', '', value).strip()
    return value[:255] or 'report.xlsx'


def _month_value(year, month):
    return f'{int(year):04d}-{int(month):02d}'


def _period_from_value(value):
    match = re.fullmatch(r'(\d{4})-(0[1-9]|1[0-2])', str(value or ''))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _latest_import_query():
    latest_ids = (
        db.session.query(func.max(UzterReportImport.id).label('id'))
        .filter(UzterReportImport.status == 'accepted')
        .group_by(
            UzterReportImport.provider_company_id,
            UzterReportImport.report_year,
            UzterReportImport.report_month,
            UzterReportImport.resource_type,
        ).subquery()
    )
    return UzterReportImport.query.filter(
        UzterReportImport.id.in_(db.session.query(latest_ids.c.id)))


def _history_import_query():
    return UzterReportImport.query.filter(
        UzterReportImport.status == 'accepted')


def _available_territories():
    values = (
        db.session.query(UzterReportRow.territory)
        .join(UzterReportImport, UzterReportImport.id == UzterReportRow.import_id)
        .filter(
            UzterReportImport.id.in_(
                db.session.query(_latest_import_query().with_entities(
                    UzterReportImport.id).subquery().c.id)),
            UzterReportRow.is_data_row.is_(True),
            UzterReportRow.territory != '',
        ).distinct().all()
    )
    return [row[0] for row in values if row[0]]


def _parse_debt_bound(value, label):
    value = (value or '').strip()
    if not value:
        return None
    if len(value) > 40:
        raise ValueError(f'Граница «{label}» слишком длинная.')
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f'Граница «{label}» должна быть числом.') from exc
    if not math.isfinite(parsed):
        raise ValueError(f'Граница «{label}» должна быть конечным числом.')
    return parsed


def _filter_rows(query, *, year, month, resources, districts, search,
                 min_debt=None, max_debt=None):
    if year and month:
        query = query.filter(
            UzterReportRow.report_year == year,
            UzterReportRow.report_month == month)
    if resources:
        query = query.filter(UzterReportRow.resource_type.in_(resources))
    if districts:
        observed = _available_territories()
        if districts == ['unassigned']:
            assigned = [
                value for value in observed
                if municipality_for_territory(value) is not None
            ]
            query = query.filter(or_(
                UzterReportRow.territory == '',
                ~UzterReportRow.territory.in_(assigned) if assigned else True,
            ))
        else:
            aliases = set()
            for district in districts:
                aliases.update(territory_aliases_for(district, observed))
            query = query.filter(UzterReportRow.territory.in_(aliases)) \
                if aliases else query.filter(UzterReportRow.id == -1)
    if search:
        query = query.filter(or_(
            UzterReportRow.debtor_name.contains(search, autoescape=True),
            UzterReportRow.inn.contains(search, autoescape=True),
            Company.name.contains(search, autoescape=True),
        ))
    if min_debt is not None:
        query = query.filter(UzterReportRow.debt_total >= min_debt)
    if max_debt is not None:
        query = query.filter(UzterReportRow.debt_total <= max_debt)
    return query


def _row_query():
    return (
        UzterReportRow.query
        .join(UzterReportImport, UzterReportImport.id == UzterReportRow.import_id)
        .join(Company, Company.id == UzterReportImport.provider_company_id)
        .filter(
            UzterReportRow.is_data_row.is_(True),
            UzterReportImport.id.in_(
                db.session.query(_latest_import_query().with_entities(
                    UzterReportImport.id).subquery().c.id)),
        )
    )


def _import_payload(item, *, is_latest=False):
    return {
        'id': item.id,
        'owner': item.provider.name if item.provider else '(организация удалена)',
        'ownerId': item.provider_company_id,
        'sourceSupplier': getattr(item, 'source_supplier_name', '') or '',
        'uploader': item.uploader.username if item.uploader else '',
        'file': item.source_filename,
        'version': item.version,
        'year': item.report_year,
        'month': item.report_month,
        'resource': item.resource_type,
        'rowCount': item.row_count,
        'dataRowCount': item.data_row_count,
        'createdAt': item.created_at.isoformat() if item.created_at else '',
        'warningCount': item.warning_count,
        'status': item.status,
        'isLatest': bool(is_latest),
    }


def _safe_json_array(value):
    try:
        result = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []


def _safe_json_object(value):
    try:
        result = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _import_source_path(stored_filename):
    if not re.fullmatch(r'[a-f0-9]{32}\.xlsx', str(stored_filename or '')):
        return None
    root = os.path.realpath(_source_directory())
    path = os.path.realpath(os.path.join(root, stored_filename))
    try:
        if os.path.commonpath((root, path)) != root:
            return None
    except ValueError:
        return None
    return path


def _finish_ministry_import_file_cleanup(audit):
    details = _safe_json_object(audit.details)
    try:
        attempts = max(0, int(details.get('file_cleanup_attempts') or 0)) + 1
    except (TypeError, ValueError):
        attempts = 1
    stored_filename = details.get('stored_filename')
    path = _import_source_path(stored_filename)
    cleanup_error = None
    try:
        if path is None:
            raise ValueError('Некорректный идентификатор исходного файла.')
        os.remove(path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        cleanup_error = f'{type(exc).__name__}: {exc}'[:500]

    complete = cleanup_error is None
    details['file_cleanup_status'] = 'complete' if complete else 'pending'
    details['file_cleanup_attempts'] = attempts
    if cleanup_error:
        details['file_cleanup_error'] = cleanup_error
    else:
        details.pop('file_cleanup_error', None)
    audit.action = (
        'ministry_report_import_deleted'
        if complete else 'ministry_import_delete_cleanup_pending'
    )
    audit.details = json.dumps(details, ensure_ascii=False, default=str)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            'could not finalize ministry report source cleanup')
        return False
    return complete


def _retry_pending_ministry_import_file_cleanup(limit=20):
    pending = (
        AuditLog.query
        .filter_by(action='ministry_import_delete_cleanup_pending')
        .order_by(AuditLog.id.asc())
        .limit(limit)
        .all()
    )
    for audit in pending:
        try:
            _finish_ministry_import_file_cleanup(audit)
        except Exception:
            db.session.rollback()
            current_app.logger.warning(
                'could not retry ministry report source cleanup',
                exc_info=True)


def _row_base_payload(row, report):
    company = report.provider
    return {
        'id': row.id,
        'owner': company.name if company else '(организация удалена)',
        'ownerId': report.provider_company_id,
        'uploader': report.uploader.username if report.uploader else '',
        'file': report.source_filename,
        'version': report.version,
        'year': row.report_year,
        'month': row.report_month,
        'resource': row.resource_type,
        'sheet': row.sheet_name,
        'sheetKind': row.sheet_kind,
        'name': row.debtor_name,
        'inn': row.inn,
        'kpp': row.kpp,
        'territory': row.territory,
        'district': municipality_for_territory(row.territory),
        'detail': row.consumer_detail,
        'status': row.status_text,
        'debt': row.debt_total,
        'periods': row.overdue_periods,
        'currentPeriodAccrual': row.current_period_accrual,
        'yearToDateAccrual': row.year_to_date_accrual,
        'sourceRow': row.source_row_number,
        'isGroupRow': row.is_group_row,
        'importId': report.id,
    }


def _comparison_map(rows):
    if not rows:
        return {}
    imports = {row.report_import.id: row.report_import for row in rows}
    current_ids = list(imports)
    inns = sorted({row.inn for row in rows if row.inn})
    counts = {}
    previous_imports = {}
    latest_imports = _latest_import_query().all()
    for report in imports.values():
        candidates = [
            item for item in latest_imports
            if item.provider_company_id == report.provider_company_id
            and item.resource_type == report.resource_type
            and (item.report_year, item.report_month)
            < (report.report_year, report.report_month)
        ]
        if candidates:
            previous_imports[report.id] = max(
                candidates, key=lambda item: (item.report_year, item.report_month))

    all_import_ids = set(current_ids) | {
        previous.id for previous in previous_imports.values()
    }
    if inns and all_import_ids:
        grouped = (
            db.session.query(
                UzterReportRow.import_id,
                UzterReportRow.inn,
                func.count(UzterReportRow.id),
                func.sum(UzterReportRow.debt_total),
                func.count(UzterReportRow.debt_total),
            )
            .filter(
                UzterReportRow.import_id.in_(all_import_ids),
                UzterReportRow.is_data_row.is_(True),
                UzterReportRow.inn.in_(inns),
            )
            .group_by(UzterReportRow.import_id, UzterReportRow.inn).all()
        )
        for import_id, inn, count, total, debt_count in grouped:
            counts[(import_id, inn)] = {
                'count': int(count),
                'debt': total,
                'debt_count': int(debt_count),
            }

    result = {}
    for row in rows:
        report = row.report_import
        key = (report.id, row.inn)
        current = counts.get(key, {})
        previous = previous_imports.get(report.id)
        previous_key = (previous.id, row.inn) if previous and row.inn else None
        previous_data = counts.get(previous_key, {}) if previous_key else {}
        comparison = {
            'available': False, 'delta': None,
            'previousMonth': _month_value(previous.report_year, previous.report_month)
                if previous else None,
            'reason': '',
        }
        if not row.inn:
            comparison['reason'] = 'Нет ИНН: строка не сопоставляется с организацией.'
        elif current.get('count', 0) != 1:
            comparison['reason'] = 'Повторяющийся ИНН: изменение неоднозначно.'
        elif not previous:
            comparison['reason'] = 'Нет более раннего отчёта этого поставщика по ресурсу.'
        elif previous_data.get('count', 0) == 0:
            comparison['reason'] = 'В более раннем отчёте нет сопоставимой строки.'
        elif previous_data.get('count', 0) != 1:
            comparison['reason'] = 'В более раннем отчёте ИНН встречается несколько раз.'
        elif (row.debt_total is None
              or previous_data.get('debt_count') != 1
              or previous_data.get('debt') is None):
            comparison['reason'] = 'Одно из значений задолженности отсутствует.'
        else:
            comparison['available'] = True
            comparison['delta'] = float(row.debt_total) - float(previous_data['debt'])
        result[row.id] = comparison
    return result


def _month_choices():
    items = (
        _latest_import_query()
        .order_by(UzterReportImport.report_year.desc(),
                  UzterReportImport.report_month.desc())
        .all()
    )
    unique = {}
    for item in items:
        value = _month_value(item.report_year, item.report_month)
        unique[value] = value
    labels = []
    names = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
             'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
    for value in sorted(unique, reverse=True):
        year, month = _period_from_value(value)
        labels.append({'value': value, 'label': f'{names[month - 1]} {year}'})
    return labels


def _map_payload():
    return {
        'available': True,
        'geometryUrl': url_for('ministry.map_geometry'),
        'source': 'Файл границ МО 2026, предоставленный пользователем',
        'sourceUrl': None,
        'sourceUpdated': None,
        'sourceAccessed': None,
        'termsUrl': None,
        'sourceNote': (
            'В файле не указаны источник и лицензия геометрии. '
            'Официальный перечень используется только для подписей территорий.'
        ),
        'note': (
            'Показаны полигоны из предоставленного файла. Выбор территории '
            'фильтрует строки справа по точному совпадению с полем '
            '«Административная принадлежность». Проценты не выводятся: '
            'показатель и источник для них ещё не согласованы.'
        ),
        'municipalities': [
            {'name': name, 'type': kind} for name, kind in MUNICIPALITIES
        ],
    }


@ministry_bp.get('/ministry/api/map-geometry')
@_cabinet_required
def map_geometry():
    path = os.path.join(
        current_app.static_folder, 'ministry_municipalities.geojson.gz')
    if not os.path.isfile(path):
        abort(404, description='Геометрия карты не подготовлена.')
    response = send_file(path, mimetype='application/geo+json')
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Vary'] = 'Accept-Encoding'
    response.headers['Cache-Control'] = 'private, max-age=86400'
    return response


@ministry_bp.get('/ministry')
@_cabinet_required
def dashboard():
    return redirect(url_for('ministry.dashboard_desktop'))


@ministry_bp.get('/ministry/desktop')
@_cabinet_required
def dashboard_desktop():
    providers = Company.query.filter(
        Company.status != 'archived').order_by(Company.name).all()
    return render_template('ministry_dashboard.html', providers=providers)


@ministry_bp.get('/ministry/analytics')
@_cabinet_required
def analytics_page():
    return render_template('ministry_analytics.html')


@ministry_bp.get('/ministry/uploads')
@_cabinet_required
def uploads_page():
    return render_template('ministry_uploads.html')


@ministry_bp.get('/ministry/api/analytics')
@_cabinet_required
def analytics_data():
    """Aggregate only the latest accepted supplier report versions."""
    latest = _latest_import_query()
    imports = latest.order_by(
        UzterReportImport.report_year, UzterReportImport.report_month,
        UzterReportImport.id).all()
    periods = sorted({
        _month_value(item.report_year, item.report_month) for item in imports
    })
    resources = sorted({item.resource_type for item in imports if item.resource_type})
    suppliers_by_id = {}
    for item in imports:
        suppliers_by_id[item.provider_company_id] = (
            item.provider.name if item.provider else '(организация удалена)')
    suppliers = [
        {'id': company_id, 'name': name}
        for company_id, name in sorted(
            suppliers_by_id.items(), key=lambda pair: pair[1].casefold())
    ]
    observed_territories = _available_territories()
    district_options = [
        {'name': name, 'label': f'{kind.capitalize()} {name}'}
        for name, kind in MUNICIPALITIES
        if any(municipality_for_territory(value) == name
               for value in observed_territories)
    ]
    if any(municipality_for_territory(value) is None
           for value in observed_territories):
        district_options.append({
            'name': 'unassigned', 'label': 'Не распределено / не распознано'})

    selected_month = request.args.get('month', '')
    if selected_month and (
        not _period_from_value(selected_month) or selected_month not in periods
    ):
        return _json_error('Нет принятого отчёта за выбранный период.', 400)
    if not selected_month and periods:
        selected_month = periods[-1]

    selected_resources = list(dict.fromkeys(
        value.strip()[:120] for value in request.args.getlist('resource')
        if value and value.strip() and value.strip() != 'all'
    ))
    if any(value not in resources for value in selected_resources):
        return _json_error('Неизвестный вид ресурса.', 400)

    raw_suppliers = request.args.getlist('supplier')
    try:
        selected_suppliers = list(dict.fromkeys(
            int(value) for value in raw_suppliers if value.strip()))
    except (TypeError, ValueError):
        return _json_error('Некорректный поставщик.', 400)
    available_supplier_ids = set(suppliers_by_id)
    if any(value not in available_supplier_ids for value in selected_suppliers):
        return _json_error('Неизвестный поставщик.', 400)

    selected_districts = list(dict.fromkeys(
        value.strip()[:120] for value in request.args.getlist('district')
        if value and value.strip() and value.strip() != 'all'
    ))
    known_districts = {name for name, _kind in MUNICIPALITIES}
    if ('unassigned' in selected_districts and len(selected_districts) > 1
            or any(value != 'unassigned' and value not in known_districts
                   for value in selected_districts)):
        return _json_error('Неизвестная территория.', 400)

    selected_period = _period_from_value(selected_month) if selected_month else None
    base_query = _row_query()
    if selected_resources:
        base_query = base_query.filter(
            UzterReportRow.resource_type.in_(selected_resources))
    if selected_suppliers:
        base_query = base_query.filter(
            UzterReportImport.provider_company_id.in_(selected_suppliers))
    all_rows = base_query.order_by(
        UzterReportRow.report_year, UzterReportRow.report_month,
        UzterReportImport.id, UzterReportRow.source_row_number).all()

    def district_key(row):
        return municipality_for_territory(row.territory) or 'unassigned'

    def allowed_district(row):
        return not selected_districts or district_key(row) in selected_districts

    # Dashboard deep links use its existing source-row table and filter syntax.
    def source_url(month, resource=None, district=None):
        params = [('month', month), ('resource', resource or 'all')]
        if district:
            params.append(('district', district))
        if len(selected_suppliers) == 1:
            supplier_name = suppliers_by_id[selected_suppliers[0]]
            if supplier_name and supplier_name != '(организация удалена)':
                params.append(('q', supplier_name))
        return url_for('ministry.dashboard_desktop') + '?' + urlencode(params)

    period_rows = [
        row for row in all_rows
        if selected_period
        and (row.report_year, row.report_month) == selected_period
        and allowed_district(row)
    ]

    def aggregate_rows(rows, key_for):
        buckets = {}
        inn_counts = {}
        for row in rows:
            report = row.report_import
            identity = (report.id, row.inn)
            if row.inn:
                inn_counts[identity] = inn_counts.get(identity, 0) + 1
        for row in rows:
            key = key_for(row)
            item = buckets.setdefault(key, {
                'rows': 0, 'debtCount': 0, 'debt': 0.0,
                'missingDebt': 0, 'groupRows': 0, 'duplicateRows': 0,
                'warningImports': set(), 'unknownTerritories': set(),
            })
            item['rows'] += 1
            if row.debt_total is None:
                item['missingDebt'] += 1
            else:
                item['debtCount'] += 1
                item['debt'] += float(row.debt_total)
            if row.is_group_row:
                item['groupRows'] += 1
            if row.inn and inn_counts.get((row.import_id, row.inn), 0) > 1:
                item['duplicateRows'] += 1
            if row.report_import.warning_count:
                item['warningImports'].add(row.import_id)
            if not municipality_for_territory(row.territory):
                item['unknownTerritories'].add(row.territory or '(пустое значение)')
        for item in buckets.values():
            item['debt'] = item['debt'] if item['debtCount'] else None
            item['warningImports'] = len(item['warningImports'])
            item['unknownTerritories'] = len(item['unknownTerritories'])
        return buckets

    distribution_data = aggregate_rows(period_rows, lambda row: row.resource_type)
    distribution = []
    for resource in sorted(set(resources) | set(distribution_data)):
        item = distribution_data.get(resource, {
            'rows': 0, 'debtCount': 0, 'debt': None, 'missingDebt': 0,
            'groupRows': 0, 'duplicateRows': 0, 'warningImports': 0,
            'unknownTerritories': 0,
        })
        distribution.append({
            'resource': resource,
            **item,
            'sourceUrl': source_url(selected_month, resource),
        })

    timeline_rows = [row for row in all_rows if allowed_district(row)]
    timeline_data = aggregate_rows(
        timeline_rows, lambda row: (
            _month_value(row.report_year, row.report_month), district_key(row)))
    districts_with_data = sorted({
        district_key(row) for row in timeline_rows
    }, key=lambda value: (value == 'unassigned', value.casefold()))
    chosen_districts = selected_districts or districts_with_data[:8]
    series = []
    for district in chosen_districts:
        points = []
        for period in periods:
            item = timeline_data.get((period, district))
            if item is None:
                points.append({
                    'month': period, 'debt': None, 'rows': 0,
                    'debtCount': 0, 'missingDebt': 0,
                    'groupRows': 0, 'duplicateRows': 0,
                    'sourceUrl': source_url(
                        period, selected_resources[0]
                        if len(selected_resources) == 1 else None, district),
                })
            else:
                points.append({
                    'month': period, 'debt': item['debt'],
                    'rows': item['rows'], 'debtCount': item['debtCount'],
                    'missingDebt': item['missingDebt'],
                    'groupRows': item['groupRows'],
                    'duplicateRows': item['duplicateRows'],
                    'sourceUrl': source_url(
                        period, selected_resources[0]
                        if len(selected_resources) == 1 else None, district),
                })
        series.append({
            'district': (
                'Не распределено / не распознано'
                if district == 'unassigned' else district),
            'districtValue': district,
            'points': points,
        })

    quality = aggregate_rows(period_rows, lambda _row: 'all').get('all', {
        'rows': 0, 'debtCount': 0, 'debt': None, 'missingDebt': 0,
        'groupRows': 0, 'duplicateRows': 0, 'warningImports': 0,
        'unknownTerritories': 0,
    })
    unknown_labels = {}
    for row in period_rows:
        if municipality_for_territory(row.territory) is None:
            label = row.territory or '(пустое значение)'
            unknown_labels[label] = unknown_labels.get(label, 0) + 1
    selected_imports = [
        item for item in imports
        if selected_period
        and (item.report_year, item.report_month) == selected_period
        and (not selected_resources or item.resource_type in selected_resources)
        and (not selected_suppliers
             or item.provider_company_id in selected_suppliers)
    ]
    warning_count = sum(item.warning_count for item in selected_imports)
    source_warnings = []
    for item in selected_imports:
        for warning in _safe_json_array(item.warnings_json):
            source_warnings.append({
                'file': item.source_filename,
                'supplier': (
                    item.provider.name if item.provider
                    else '(организация удалена)'),
                'resource': item.resource_type,
                'message': str(warning),
            })
    warning_limit = 100
    comparison_note = (
        'Для сравнения выберите не менее двух территорий.'
        if len(chosen_districts) < 2 else
        'Линии сопоставляют суммы заполненных строк; пропуски и неоднозначность не скрыты.'
    )
    return jsonify({
        'filters': {
            'periods': [
                {'value': value, 'label': _month_choices_label(value)}
                for value in reversed(periods)
            ],
            'resources': resources,
            'suppliers': suppliers,
            'districts': district_options,
            'selected': {
                'month': selected_month,
                'resources': selected_resources,
                'suppliers': selected_suppliers,
                'districts': selected_districts,
            },
        },
        'distribution': distribution,
        'timeline': {
            'periods': periods,
            'series': series,
            'comparisonNote': comparison_note,
        },
        'quality': {
            'rows': quality['rows'],
            'debtRows': quality['debtCount'],
            'missingDebt': quality['missingDebt'],
            'groupRows': quality['groupRows'],
            'duplicateRows': quality['duplicateRows'],
            'warningImports': warning_count,
            'unknownTerritories': quality['unknownTerritories'],
            'unknownTerritoryLabels': [
                {'label': label, 'rows': count}
                for label, count in sorted(
                    unknown_labels.items(), key=lambda pair: pair[0].casefold()
                )[:25]
            ],
            'unknownTerritoryLabelsOmitted': max(0, len(unknown_labels) - 25),
            'imports': len(selected_imports),
            'verifiedRegionalTotal': False,
            'warnings': source_warnings[:warning_limit],
            'warningsOmitted': max(0, len(source_warnings) - warning_limit),
        },
        'empty': not bool(period_rows),
    })


def _month_choices_label(value):
    year, month = _period_from_value(value)
    names = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
             'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
    return f'{names[month - 1]} {year}'


@ministry_bp.get('/ministry/api/data')
@_cabinet_required
def dashboard_data():
    _retry_pending_ministry_import_file_cleanup()
    latest = _latest_import_query()
    months = _month_choices()
    available_resources = [
        item[0] for item in latest.with_entities(
            UzterReportImport.resource_type).distinct()
        .order_by(UzterReportImport.resource_type).all()
    ]
    selected_month = request.args.get('month', '')
    if selected_month and not _period_from_value(selected_month):
        return _json_error('Некорректный отчётный месяц.', 400)
    if not selected_month and months:
        selected_month = months[0]['value']
    period = _period_from_value(selected_month)
    year, month = period if period else (None, None)
    requested_resources = list(dict.fromkeys(
        value.strip()[:120] for value in request.args.getlist('resource')
        if value and value.strip()
    ))
    if 'all' in requested_resources and any(
        value != 'all' for value in requested_resources
    ):
        return _json_error(
            'Нельзя сочетать все ресурсы с отдельными видами ресурса.', 400)
    selected_resources = [
        value for value in requested_resources if value != 'all'
    ]
    if any(value not in available_resources for value in selected_resources):
        return _json_error('Неизвестный вид ресурса.', 400)
    requested_districts = request.args.getlist('district')
    if not requested_districts:
        requested_districts = request.args.getlist('districts')
    districts = list(dict.fromkeys(
        value.strip()[:120] for value in requested_districts
        if value and value.strip() and value.strip() != 'all'
    ))
    municipality_names = {name for name, _kind in MUNICIPALITIES}
    if len(districts) > len(MUNICIPALITIES):
        return _json_error('Выбрано слишком много территорий.', 400)
    if ('unassigned' in districts and len(districts) > 1) or any(
        value != 'unassigned' and value not in municipality_names
        for value in districts
    ):
        return _json_error('Неизвестная территория.', 400)
    search = request.args.get('q', '').strip()[:120]
    try:
        min_debt = _parse_debt_bound(request.args.get('debt_min'), 'От')
        max_debt = _parse_debt_bound(request.args.get('debt_max'), 'До')
    except ValueError as exc:
        return _json_error(str(exc), 400)
    if min_debt is not None and max_debt is not None and min_debt > max_debt:
        return _json_error('Минимум задолженности не может превышать максимум.', 400)
    page = min(max(request.args.get('page', 1, type=int) or 1, 1), 100_000)
    per_page = min(max(request.args.get('per_page', 50, type=int) or 50, 1), 100)
    sort = request.args.get('sort', 'name')
    direction = request.args.get('direction', 'asc')
    if sort not in ('debt', 'name', 'inn', 'territory'):
        sort = 'name'
    if direction not in ('asc', 'desc'):
        direction = 'asc'

    query = _filter_rows(
        _row_query(), year=year, month=month, resources=selected_resources,
        districts=districts, search=search,
        min_debt=min_debt, max_debt=max_debt)
    data_rows = query.with_entities(func.count(UzterReportRow.id)).scalar() or 0
    debt_row_count = query.filter(UzterReportRow.debt_total.isnot(None)).count()
    debt_sum = query.with_entities(func.sum(UzterReportRow.debt_total)).scalar()
    missing_inn = query.filter(
        UzterReportRow.inn == '',
        UzterReportRow.sheet_kind == 'enterprise',
        ~UzterReportRow.is_group_row,
    ).count()
    group_rows = query.filter(
        UzterReportRow.inn == '', UzterReportRow.is_group_row.is_(True)).count()
    duplicate_groups = (
        query.with_entities(
            UzterReportRow.import_id, UzterReportRow.inn,
            func.count(UzterReportRow.id).label('n'))
        .filter(UzterReportRow.inn != '')
        .group_by(UzterReportRow.import_id, UzterReportRow.inn)
        .having(func.count(UzterReportRow.id) > 1).count()
    )
    source_rows_query = (
        UzterReportImport.query
        .filter(UzterReportImport.id.in_(
            db.session.query(_latest_import_query().with_entities(
                UzterReportImport.id).subquery().c.id)))
    )
    if year and month:
        source_rows_query = source_rows_query.filter(
            UzterReportImport.report_year == year,
            UzterReportImport.report_month == month)
    if selected_resources:
        source_rows_query = source_rows_query.filter(
            UzterReportImport.resource_type.in_(selected_resources))
    period_imports = source_rows_query.all()
    source_rows = sum(item.row_count for item in period_imports)
    warning_count = sum(item.warning_count for item in period_imports)
    coverage = round(100 * debt_row_count / data_rows, 1) if data_rows else None

    sort_fields = {
        'debt': UzterReportRow.debt_total,
        'name': UzterReportRow.debtor_name,
        'inn': UzterReportRow.inn,
        'territory': UzterReportRow.territory,
    }
    sort_column = sort_fields[sort]
    ordered = sort_column.desc().nullslast() if direction == 'desc' \
        else sort_column.asc().nullslast()
    ordered_query = query.order_by(
        ordered, UzterReportImport.id.desc(), UzterReportRow.source_row_number.asc())
    total = int(data_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    selected_rows = (
        ordered_query
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    compare = _comparison_map(selected_rows)
    serialized_rows = []
    for row in selected_rows:
        payload = _row_base_payload(row, row.report_import)
        payload['comparison'] = compare.get(row.id, {
            'available': False, 'delta': None, 'previousMonth': None,
            'reason': 'Сравнение недоступно.',
        })
        serialized_rows.append(payload)

    observed_territories = _available_territories()
    district_options = [
        {'name': name, 'label': f'{kind.capitalize()} {name}',
         'type': kind, 'hasData': any(
            municipality_for_territory(value) == name
            for value in observed_territories)}
        for name, kind in MUNICIPALITIES
    ]
    return jsonify({
        'filters': {
            'months': months,
            'resources': available_resources,
            'districts': district_options,
            'selected': {
                'month': selected_month,
                'resource': selected_resources[0]
                if len(selected_resources) == 1 else 'all',
                'resources': selected_resources,
                'district': districts[0] if len(districts) == 1 else 'all',
                'districts': districts,
                'q': search,
                'debtMin': request.args.get('debt_min', '').strip(),
                'debtMax': request.args.get('debt_max', '').strip(),
            },
        },
        'summary': {
            'imports': len(period_imports),
            'sourceRows': source_rows,
            'dataRows': data_rows,
            'matchedDebts': debt_row_count,
            'debtTotal': float(debt_sum) if debt_sum is not None else None,
            'missingInn': missing_inn,
            'duplicateInn': duplicate_groups,
            'groupRows': group_rows,
            'coverage': coverage,
            'warningCount': warning_count,
            'verifiedRegionalTotal': False,
        },
        'rows': serialized_rows,
        'pagination': {
            'page': page, 'perPage': per_page, 'total': total,
            'totalPages': total_pages,
        },
        'map': _map_payload(),
    })


@ministry_bp.get('/ministry/api/imports')
@_cabinet_required
def import_history_data():
    _retry_pending_ministry_import_file_cleanup()
    history_query = _history_import_query()
    total = history_query.count()
    selected_imports = (
        history_query
        .options(
            joinedload(UzterReportImport.provider),
            joinedload(UzterReportImport.uploader),
        )
        .order_by(UzterReportImport.created_at.desc(),
                  UzterReportImport.id.desc())
        .limit(100)
        .all()
    )
    selected_ids = [item.id for item in selected_imports]
    latest_ids = {
        item.id for item in _latest_import_query().with_entities(
            UzterReportImport.id)
        .filter(UzterReportImport.id.in_(selected_ids))
        .all()
    } if selected_ids else set()
    history = [
        _import_payload(item, is_latest=item.id in latest_ids)
        for item in selected_imports
    ]
    return jsonify({
        'imports': history,
        'total': total,
        'limit': 100,
        'permissions': {'canDeleteImports': bool(current_user.is_admin)},
    })


@ministry_bp.get('/ministry/api/debtors/<inn>')
@_cabinet_required
def debtor_detail(inn):
    inn = re.sub(r'\D', '', inn or '')
    if len(inn) not in (10, 12):
        return _json_error('Укажите ИНН из 10 или 12 цифр.', 400)
    resource = request.args.get('resource', 'all')[:120]
    month_value = request.args.get('month', '')
    period = _period_from_value(month_value) if month_value else None
    if month_value and not period:
        return _json_error('Некорректный отчётный месяц.', 400)
    query = _row_query().filter(UzterReportRow.inn == inn)
    if resource != 'all':
        query = query.filter(UzterReportRow.resource_type == resource)
    if period:
        query = query.filter(
            UzterReportRow.report_year == period[0],
            UzterReportRow.report_month == period[1])
    matches = (
        query.order_by(
            UzterReportRow.report_year.desc(),
            UzterReportRow.report_month.desc(),
            UzterReportRow.resource_type,
            UzterReportImport.provider_company_id,
            UzterReportRow.source_row_number)
        .limit(501).all()
    )
    truncated = len(matches) > 500
    matches = matches[:500]
    companies = Company.query.filter(Company.inn == inn).all()
    linked_company = companies[0] if len(companies) == 1 else None
    profile = linked_company.dadata_profile if linked_company else None
    latest_snapshot = (
        CompanyDadataSnapshot.query.filter_by(company_id=linked_company.id)
        .order_by(CompanyDadataSnapshot.received_at.desc())
        .first()
    ) if linked_company else None

    groups = {}
    for row in matches:
        report = row.report_import
        key = (row.report_year, row.report_month, row.resource_type,
               report.provider_company_id, report.id)
        item = groups.setdefault(key, {
            'month': _month_value(row.report_year, row.report_month),
            'resource': row.resource_type,
            'owner': report.provider.name if report.provider else '',
            'ownerId': report.provider_company_id,
            'version': report.version,
            'rows': 0,
            'debtValues': [],
            'missingDebt': 0,
        })
        item['rows'] += 1
        if row.debt_total is None:
            item['missingDebt'] += 1
        else:
            item['debtValues'].append(float(row.debt_total))
    history = []
    for item in groups.values():
        values = item.pop('debtValues')
        if item['rows'] == 1 and not item['missingDebt'] and len(values) == 1:
            item['debt'] = values[0]
            item['reason'] = ''
        else:
            item['debt'] = None
            item['reason'] = (
                'Повторяющиеся строки не объединены.' if item['rows'] > 1 else
                'Значение долга в исходной строке отсутствует.'
            )
        item['coverage'] = f'{len(values)} из {item["rows"]}'
        history.append(item)

    return jsonify({
        'inn': inn,
        'company': ({
            'id': linked_company.id,
            'name': linked_company.name,
            'fullName': linked_company.full_name or '',
            'inn': linked_company.inn or '',
            'kpp': linked_company.kpp or '',
            'address': linked_company.address or '',
            'source': 'общий справочник',
            'dadataSource': (
                profile.source if profile else
                ('DaData' if latest_snapshot and latest_snapshot.status == 'success' else None)
            ),
            'dadataReceivedAt': (
                profile.received_at.isoformat()
                if profile and profile.received_at else
                (latest_snapshot.received_at.isoformat()
                 if latest_snapshot and latest_snapshot.received_at else None)
            ),
            'dadataSnapshot': (
                _safe_json_object(latest_snapshot.normalized_json)
                if latest_snapshot and latest_snapshot.status == 'success' else None
            ),
        } if linked_company else None),
        'directoryMatchCount': len(companies),
        'directoryAmbiguous': len(companies) > 1,
        'rows': [_row_base_payload(row, row.report_import) for row in matches],
        'history': history,
        'hasMore': truncated,
    })


@ministry_bp.post('/ministry/import/preview')
@_cabinet_required
def import_preview():
    upload = request.files.get('file')
    provider_id = request.form.get('provider_id', type=int)
    if upload is None or not upload.filename:
        return _json_error('Выберите XLSX-файл.', 400)
    if not upload.filename.casefold().endswith('.xlsx'):
        return _json_error('Поддерживаются только книги XLSX.', 400)
    if request.content_length and request.content_length > MAX_WORKBOOK_BYTES + 1024 * 1024:
        return _json_error('Размер загрузки превышает допустимые 25 МБ.', 413)
    provider = db.session.get(Company, provider_id) if provider_id else None
    if provider is None or provider.status == 'archived':
        return _json_error('Выберите действующую организацию из справочника.', 400)
    raw = upload.stream.read(MAX_WORKBOOK_BYTES + 1)
    try:
        parsed = parse_supplier_workbook(raw)
    except ReportImportError as exc:
        return _json_error(str(exc), 422)
    warnings = list(parsed['warnings'])
    if parsed['source_supplier_inn'] and provider.inn \
            and parsed['source_supplier_inn'] != provider.inn:
        warnings.append(
            'ИНН поставщика в книге не совпадает с выбранным владельцем данных. '
            'Проверьте выбор вручную; исходная строка заголовка сохранится отдельно.')
    elif parsed['source_supplier_name'] and provider.name.casefold() \
            not in parsed['source_supplier_name'].casefold():
        warnings.append(
            'Название поставщика в книге отличается от выбранного владельца данных. '
            'Проверьте выбор вручную; файл не будет автоматически переименован.')

    token_id = uuid.uuid4().hex
    stage_dir = _stage_directory()
    os.makedirs(stage_dir, mode=0o700, exist_ok=True)
    stage_path = os.path.join(stage_dir, token_id + '.xlsx')
    with open(stage_path, 'xb') as stream:
        os.chmod(stage_path, 0o600)
        stream.write(raw)
    signed = _preview_serializer().dumps({
        'id': token_id, 'user_id': current_user.id,
        'sha256': parsed['file_sha256'], 'provider_id': provider.id,
        'source_filename': _source_display_name(upload.filename),
    })
    return jsonify({
        'previewToken': signed,
        'owner': provider.name,
        'ownerId': provider.id,
        'sourceSupplier': parsed['source_supplier_name'],
        'sourceSupplierInn': parsed['source_supplier_inn'],
        'file': _source_display_name(upload.filename),
        'period': _month_value(parsed['year'], parsed['month']),
        'year': parsed['year'],
        'month': parsed['month'],
        'resource': parsed['resource'],
        'rowCount': parsed['row_count'],
        'dataRows': parsed['data_row_count'],
        'warningCount': len(warnings),
        'warnings': warnings,
        'rows': [{
            'sheet': row['sheet_name'],
            'sourceRow': row['source_row_number'],
            'name': row['debtor_name'],
            'inn': row['inn'],
            'territory': row['territory'],
            'debt': row['debt_total'],
        } for row in parsed['rows'][:12]],
    })


@ministry_bp.post('/ministry/import/apply')
@_cabinet_required
def import_apply():
    token = request.form.get('preview_token', '')
    try:
        claims = _preview_serializer().loads(token, max_age=3600)
    except SignatureExpired:
        return _json_error('Предпросмотр истёк. Загрузите файл повторно.', 410)
    except BadSignature:
        return _json_error('Не удалось проверить предпросмотр файла.', 400)
    if claims.get('user_id') != current_user.id:
        return _json_error('Этот предпросмотр создан другим пользователем.', 403)
    stage_id = claims.get('id', '')
    if not re.fullmatch(r'[a-f0-9]{32}', str(stage_id)):
        return _json_error('Некорректный идентификатор предпросмотра.', 400)
    stage_path = os.path.join(_stage_directory(), stage_id + '.xlsx')
    try:
        with open(stage_path, 'rb') as stream:
            raw = stream.read(MAX_WORKBOOK_BYTES + 1)
    except OSError:
        return _json_error('Файл предпросмотра не найден. Загрузите его повторно.', 410)
    if hashlib.sha256(raw).hexdigest() != claims.get('sha256'):
        return _json_error('Файл предпросмотра изменился. Загрузите его повторно.', 409)
    provider = db.session.get(Company, claims.get('provider_id'))
    if provider is None or provider.status == 'archived':
        return _json_error('Организация-владелец больше недоступна.', 409)
    try:
        parsed = parse_supplier_workbook(raw)
    except ReportImportError as exc:
        return _json_error(str(exc), 422)
    if parsed['file_sha256'] != claims.get('sha256'):
        return _json_error('Контрольная сумма файла не совпала.', 409)
    duplicate = UzterReportImport.query.filter_by(
        provider_company_id=provider.id,
        file_sha256=parsed['file_sha256']).first()
    if duplicate:
        return jsonify({
            'ok': False,
            'message': (
                f'Этот файл уже импортирован как версия {duplicate.version} '
                f'за {_month_value(duplicate.report_year, duplicate.report_month)}. '
                'Повторная копия не создала новую запись и не изменила данные.'),
        }), 409

    latest_version = db.session.query(func.max(UzterReportImport.version)).filter_by(
        provider_company_id=provider.id,
        report_year=parsed['year'],
        report_month=parsed['month'],
        resource_type=parsed['resource'],
    ).scalar() or 0
    warnings = list(parsed['warnings'])
    if parsed['source_supplier_inn'] and provider.inn \
            and parsed['source_supplier_inn'] != provider.inn:
        warnings.append(
            'ИНН поставщика в книге отличается от владельца, выбранного сотрудником.')
    elif parsed['source_supplier_name'] and provider.name.casefold() \
            not in parsed['source_supplier_name'].casefold():
        warnings.append(
            'Название поставщика в книге отличается от владельца, выбранного сотрудником.')

    source_dir = _source_directory()
    os.makedirs(source_dir, mode=0o700, exist_ok=True)
    stored_filename = f'{uuid.uuid4().hex}.xlsx'
    stored_path = os.path.join(source_dir, stored_filename)
    with open(stored_path, 'xb') as stream:
        os.chmod(stored_path, 0o600)
        stream.write(raw)
    report = UzterReportImport(
        provider_company_id=provider.id,
        uploaded_by=current_user.id,
        source_filename=_source_display_name(claims.get('source_filename')),
        stored_filename=stored_filename,
        file_sha256=parsed['file_sha256'],
        report_year=parsed['year'],
        report_month=parsed['month'],
        resource_type=parsed['resource'],
        version=int(latest_version) + 1,
        status='accepted',
        row_count=parsed['row_count'],
        data_row_count=parsed['data_row_count'],
        warning_count=len(warnings),
        warnings_json=json.dumps(warnings, ensure_ascii=False),
    )
    # Keep the embedded supplier label without treating it as the selected owner.
    report.source_supplier_name = parsed['source_supplier_name']
    report.source_supplier_inn = parsed['source_supplier_inn']
    try:
        db.session.add(report)
        db.session.flush()
        db.session.add_all([
            UzterReportRow(
                import_id=report.id,
                report_year=parsed['year'],
                report_month=parsed['month'],
                resource_type=parsed['resource'],
                sheet_kind=row['sheet_kind'],
                sheet_name=row['sheet_name'],
                source_row_number=row['source_row_number'],
                inn=row['inn'],
                kpp=row['kpp'],
                debtor_name=row['debtor_name'],
                territory=row['territory'],
                consumer_detail=row['consumer_detail'],
                status_text=row['status_text'],
                debt_total=row['debt_total'],
                overdue_periods=row['overdue_periods'],
                current_period_accrual=row['current_period_accrual'],
                year_to_date_accrual=row['year_to_date_accrual'],
                source_values_json=json.dumps(
                    row['source_values'], ensure_ascii=False, separators=(',', ':')),
                is_group_row=row['is_group_row'],
                is_data_row=row['is_data_row'],
            ) for row in parsed['rows']
        ])
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        try:
            os.remove(stored_path)
        except OSError:
            pass
        duplicate = UzterReportImport.query.filter_by(
            provider_company_id=provider.id,
            file_sha256=parsed['file_sha256']).first()
        if duplicate:
            return jsonify({
                'ok': False,
                'message': 'Такая копия файла уже сохранена; прежняя версия не изменена.',
            }), 409
        current_app.logger.exception('ministry import database constraint failed')
        return _json_error('Не удалось сохранить импорт. Прежние версии не изменены.', 409)
    except Exception:
        db.session.rollback()
        try:
            os.remove(stored_path)
        except OSError:
            pass
        current_app.logger.exception('ministry import failed')
        return _json_error('Не удалось применить импорт. Прежние версии не изменены.', 500)
    try:
        os.remove(stage_path)
    except OSError:
        pass
    try:
        from app import log_event
        log_event('ministry_report_imported', {
            'import_id': report.id, 'provider_company_id': provider.id,
            'version': report.version, 'year': report.report_year,
            'month': report.report_month, 'resource': report.resource_type,
            'row_count': report.data_row_count,
        })
    except Exception:
        current_app.logger.warning('could not write ministry import audit event',
                                   exc_info=True)
    return jsonify({
        'ok': True,
        'importId': report.id,
        'message': (
            f'Импорт применён как версия {report.version}. '
            'Предыдущие версии и исходный файл сохранены.'),
    })


@ministry_bp.get('/ministry/import/<int:import_id>/file')
@_cabinet_required
def import_source_file(import_id):
    report = db.session.get(UzterReportImport, import_id) or abort(404)
    if not re.fullmatch(r'[a-f0-9]{32}\.xlsx', report.stored_filename or ''):
        abort(404)
    path = os.path.join(_source_directory(), report.stored_filename)
    if not os.path.isfile(path):
        abort(404, description='Исходный файл недоступен.')
    filename = report.source_filename or 'supplier-report.xlsx'
    return send_file(
        path, as_attachment=True, download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        conditional=True,
    )


@ministry_bp.post('/ministry/import/<int:import_id>/delete')
@_cabinet_required
def delete_report_import(import_id):
    if not current_user.is_admin:
        return _json_error('Удалять загрузки может только администратор.', 403)

    report = (
        UzterReportImport.query
        .filter_by(id=import_id, status='accepted')
        .with_for_update()
        .first()
    )
    if report is None:
        return _json_error('Загрузка не найдена или уже удалена.', 404)

    source_filename = report.stored_filename or ''
    details = {
        'import_id': report.id,
        'provider_company_id': report.provider_company_id,
        'provider': report.provider.name if report.provider else '',
        'file': report.source_filename,
        'period': _month_value(report.report_year, report.report_month),
        'resource': report.resource_type,
        'version': report.version,
        'stored_filename': source_filename,
        'file_cleanup_status': 'pending',
        'file_cleanup_attempts': 0,
    }
    deleted_rows = UzterReportRow.query.filter_by(
        import_id=report.id).delete(synchronize_session=False)
    details['deleted_rows'] = deleted_rows
    audit = AuditLog(
        user_id=current_user.id,
        action='ministry_import_delete_cleanup_pending',
        details=json.dumps(details, ensure_ascii=False, default=str),
        ip=request.remote_addr,
    )
    db.session.add(audit)
    db.session.delete(report)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('ministry report deletion failed')
        return _json_error(
            'Не удалось удалить загрузку. Данные не были изменены.', 500)

    file_removed = _finish_ministry_import_file_cleanup(audit)
    message = (
        f'Загрузка удалена вместе с {deleted_rows} строками отчёта и исходным файлом.'
        if file_removed else
        'Данные загрузки удалены. Очистка исходного файла не завершилась; '
        'система повторит её автоматически.'
    )
    return jsonify({
        'ok': True,
        'importId': import_id,
        'deletedRows': deleted_rows,
        'fileCleanupPending': not file_removed,
        'message': message,
    })


@ministry_bp.post('/ministry/api/dadata/lookup')
@_cabinet_required
def dadata_lookup():
    body = request.get_json(silent=True) or {}
    inn = re.sub(r'\D', '', str(body.get('inn') or ''))
    if len(inn) not in (10, 12):
        return _json_error('Укажите корректный ИНН организации.', 400)
    if current_app.config.get('TESTING') and body.get('_test_no_network'):
        return jsonify({'ok': False, 'message': 'DaData не вызывалась в этом тесте.'})
    if current_app.config.get('DADATA_DISABLED'):
        return _json_error('Интеграция DaData отключена.', 409)
    from app import Setting, _dadata_token, _find_dadata_party
    if Setting.get('dadata_enabled', '0') != '1':
        return _json_error('Интеграция DaData выключена в настройках.', 409)
    companies = Company.query.filter_by(inn=inn).all()
    if len(companies) != 1:
        return jsonify({
            'ok': False,
            'directoryMatchCount': len(companies),
            'directoryAmbiguous': len(companies) > 1,
            'message': (
                'В справочнике нет однозначной записи по ИНН; '
                'сведения DaData не связывались и карточка не создавалась.'),
        }), 409
    company = companies[0]
    try:
        if not _dadata_token():
            return _json_error('Токен DaData не настроен.', 409)
        party, normalized = _find_dadata_party(inn=inn)
    except Exception as exc:
        from dadata_client import DaDataError
        if isinstance(exc, DaDataError):
            return _json_error(str(exc), 502)
        current_app.logger.exception('ministry DaData lookup failed')
        return _json_error('Не удалось получить сведения DaData.', 502)

    if not party:
        return _json_error('DaData не нашла организацию по указанному ИНН.', 404)
    from directory_service import save_snapshot
    snapshot = save_snapshot(
        company, party, normalized, query_inn=inn,
        requested_by=current_user.id, status='success')
    db.session.commit()
    return jsonify({
        'ok': True,
        'company': {'id': company.id, 'name': company.name, 'inn': company.inn},
        'snapshot': {
            'id': snapshot.id,
            'source': 'DaData',
            'receivedAt': snapshot.received_at.isoformat()
                if snapshot.received_at else utcnow().isoformat(),
            'actualityDate': snapshot.actuality_date.isoformat()
                if snapshot.actuality_date else None,
            'normalized': normalized,
        },
        'message': (
            'Снимок DaData связан с существующей записью справочника. '
            'Исходные строки и реквизиты организации не изменены.'),
    })


@ministry_bp.errorhandler(ReportImportError)
def import_validation_error(error):
    return _json_error(str(error), 422)
