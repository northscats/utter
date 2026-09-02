# -*- coding: utf-8 -*-
"""Сервисные операции универсального справочника предприятий."""
import hashlib
import json
import re
from datetime import date, datetime, timezone

from database import (Company, CompanyContact, CompanyDadataProfile,
                      CompanyDadataSnapshot, CompanyFinance, CompanyLicense,
                      CompanyOkved, utcnow, db)
from sqlalchemy import and_, or_


def clean_identifier(value, allowed_lengths=()):
    """Оставляет только цифры и проверяет длину ИНН/ОГРН."""
    value = re.sub(r'\D', '', str(value or ''))
    if allowed_lengths and value and len(value) not in allowed_lengths:
        return ''
    return value


def company_identity_key(inn='', ogrn='', kpp=''):
    """Ключ юрлица/филиала: ОГРН или ИНН, филиалы различаются по КПП."""
    inn = clean_identifier(inn, (10, 12))
    ogrn = clean_identifier(ogrn, (13, 15))
    kpp = clean_identifier(kpp, (9,))
    base = ('ogrn:' + ogrn) if ogrn else (('inn:' + inn) if inn else '')
    return (base + ':kpp:' + kpp) if base and kpp else (base or None)


def parse_dadata_date(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=timezone.utc).date()
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _json(value, default):
    try:
        return json.loads(value or '') if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _normalize_contacts(data):
    """Возвращает контакты, реально присутствующие в ответе DaData."""
    result = []
    seen = set()
    contact_types = (
        ('phones', 'phone', 'Телефон'),
        ('emails', 'email', 'E-mail'),
        ('sites', 'website', 'Сайт'),
    )
    for source_key, contact_type, default_label in contact_types:
        items = data.get(source_key) or []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if isinstance(item, str):
                item = {'value': item}
            if not isinstance(item, dict):
                continue
            details = item.get('data') or {}
            value = str(
                item.get('unrestricted_value') or item.get('value') or
                details.get('source') or ''
            ).strip()
            dedupe_key = (contact_type, value.casefold())
            if not value or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            label = str(details.get('type') or default_label).strip()
            result.append({
                'type': contact_type,
                'label': label[:120],
                'name': str(details.get('contact') or '').strip()[:255],
                'value': value[:500],
            })
    return result


def normalize_party(party):
    """Извлекает стабильные поля, не затирая внутренние контакты."""
    data = party.get('data') if isinstance(party, dict) else {}
    data = data if isinstance(data, dict) else {}
    name = data.get('name') or {}
    state = data.get('state') or {}
    address = data.get('address') or {}
    address_data = address.get('data') or {}
    management = data.get('management') or {}
    opf = data.get('opf') or {}
    okved = data.get('okved') or ''
    code_fields = (
        'okato', 'okfs', 'okogu', 'okpo', 'oktmo', 'okved', 'okved_type',
    )
    normalized = {
        'short_name': name.get('short_with_opf') or name.get('short') or '',
        'full_name': name.get('full_with_opf') or name.get('full') or '',
        'opf': opf.get('short') or opf.get('full') or '',
        'inn': data.get('inn') or '',
        'kpp': data.get('kpp') or '',
        'ogrn': data.get('ogrn') or '',
        'ogrn_date': data.get('ogrn_date'),
        'status': state.get('status') or '',
        'registration_date': (
            state.get('registration_date') or data.get('registration_date')),
        'actuality_date': (
            state.get('actuality_date') or data.get('actuality_date')),
        'liquidation_date': (
            state.get('liquidation_date') or data.get('liquidation_date')),
        'address': address.get('unrestricted_value') or address.get('value') or '',
        'address_quality': address_data.get('qc') or address.get('qc') or '',
        'address_ids': {
            key: address_data.get(key) for key in (
                'fias_id', 'fias_level', 'kladr_id', 'okato', 'oktmo',
                'postal_code', 'geo_lat', 'geo_lon', 'qc_complete',
                'qc_house', 'source'
            ) if address_data.get(key) not in (None, '')
        },
        'director_name': management.get('name') or '',
        'director_position': management.get('post') or '',
        'primary_okved': okved,
        'codes': {
            key: data.get(key) for key in code_fields
            if data.get(key) not in (None, '')
        },
        'branch_type': data.get('branch_type') or '',
        'branch_count': data.get('branch_count'),
        'contacts': _normalize_contacts(data),
    }
    return normalized


def duplicate_companies(inn='', ogrn='', kpp='', exclude_id=None):
    """Возвращает возможные дубли; филиалы с разным КПП не считаются дублем."""
    query = Company.query
    if exclude_id:
        query = query.filter(Company.id != exclude_id)
    candidates = query.filter(
        or_(
            and_(Company.inn == inn, Company.inn != ''),
            and_(Company.ogrn == ogrn, Company.ogrn != ''),
        )
    ).all() if inn or ogrn else []
    result = []
    for company in candidates:
        same_inn = bool(inn and company.inn == inn)
        same_ogrn = bool(ogrn and company.ogrn == ogrn)
        if same_ogrn or (same_inn and (not kpp or not company.kpp or company.kpp == kpp)):
            result.append(company)
    return result


def apply_normalized_to_company(company, normalized, *, increment=True):
    """Применяет только официальные поля; ручные контакты/классификации не меняет."""
    fields = {
        'full_name': normalized.get('full_name', ''),
        'inn': normalized.get('inn', ''),
        'ogrn': normalized.get('ogrn', ''),
        'kpp': normalized.get('kpp', ''),
        'opf_short': normalized.get('opf', ''),
        'legal_status': normalized.get('status', ''),
        'address': normalized.get('address', ''),
        'legal_address': normalized.get('address', ''),
        'address_quality': normalized.get('address_quality', ''),
        'address_ids_json': json.dumps(normalized.get('address_ids', {}), ensure_ascii=False),
        'director_name': normalized.get('director_name', ''),
        'director_position': normalized.get('director_position', ''),
        'primary_okved': normalized.get('primary_okved', ''),
        'registration_date': parse_dadata_date(normalized.get('registration_date')),
        'ogrn_date': parse_dadata_date(normalized.get('ogrn_date')),
        'actuality_date': parse_dadata_date(normalized.get('actuality_date')),
        'liquidation_date': parse_dadata_date(normalized.get('liquidation_date')),
        'dadata_actuality_date': parse_dadata_date(normalized.get('actuality_date')),
    }
    for field, value in fields.items():
        if value not in (None, ''):
            setattr(company, field, value)
    company.identity_key = company_identity_key(
        company.inn, company.ogrn, company.kpp)
    if increment:
        company.profile_version = (company.profile_version or 0) + 1
    return company


def save_snapshot(company, party, normalized, *, query_inn='', query_ogrn='',
                  requested_by=None, status='success', error_text=''):
    raw = party if isinstance(party, dict) else {}
    raw_text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    snapshot = CompanyDadataSnapshot(
        company_id=company.id,
        query_inn=query_inn,
        query_ogrn=query_ogrn,
        response_hash=hashlib.sha256(raw_text.encode('utf-8')).hexdigest(),
        status=status,
        normalized_json=json.dumps(normalized or {}, ensure_ascii=False),
        response_json=raw_text if status == 'success' else '{}',
        error_text=error_text[:1000] if error_text else '',
        requested_by=requested_by,
        actuality_date=parse_dadata_date(normalized.get('actuality_date'))
            if normalized else None,
    )
    db.session.add(snapshot)
    return snapshot


def accept_dadata(company, snapshot, user_id):
    raw = _json(snapshot.response_json, {})
    # Нормализуем сохранённый сырой ответ заново: набор поддерживаемых полей
    # развивается, а исторический normalized_json мог быть создан старой версией.
    normalized = normalize_party(raw) if raw else _json(
        snapshot.normalized_json, {})
    apply_normalized_to_company(company, normalized)
    profile = company.dadata_profile
    if profile is None:
        profile = CompanyDadataProfile(company_id=company.id)
        db.session.add(profile)
    profile.normalized_json = json.dumps(normalized, ensure_ascii=False)
    profile.extended_json = snapshot.response_json or '{}'
    profile.source = 'DaData'
    profile.received_at = snapshot.received_at
    profile.actuality_date = snapshot.actuality_date
    profile.accepted_at = utcnow()
    profile.accepted_by = user_id
    profile.version = (profile.version or 0) + 1
    data = raw.get('data', {}) if isinstance(raw, dict) else {}
    CompanyOkved.query.filter_by(company_id=company.id).delete()
    okveds = data.get('okveds') or []
    main_code = normalized.get('primary_okved') or ''
    if main_code:
        main_item = next((
            item for item in okveds
            if isinstance(item, dict) and str(item.get('code') or '') == main_code
        ), {})
        db.session.add(CompanyOkved(
            company_id=company.id, code=main_code,
            name=str(main_item.get('name') or '')[:500], is_main=True))
    for item in okveds:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip()
        if code and code != main_code:
            db.session.add(CompanyOkved(
                company_id=company.id, code=code,
                name=str(item.get('name') or '')[:500], is_main=False))
    CompanyLicense.query.filter_by(company_id=company.id).delete()
    for item in data.get('licenses') or []:
        if isinstance(item, dict):
            db.session.add(CompanyLicense(
                company_id=company.id,
                number=str(item.get('series') or item.get('number') or '')[:255],
                activity=json.dumps(item.get('activities') or [], ensure_ascii=False),
                status=str(item.get('status') or '')[:80],
                details_json=json.dumps(item, ensure_ascii=False)))
    CompanyContact.query.filter_by(
        company_id=company.id, source='DaData').delete()
    for item in normalized.get('contacts') or []:
        if not isinstance(item, dict) or not str(item.get('value') or '').strip():
            continue
        db.session.add(CompanyContact(
            company_id=company.id,
            contact_type=str(item.get('type') or 'other')[:40],
            label=str(item.get('label') or '')[:120],
            name=str(item.get('name') or '')[:255],
            value=str(item.get('value') or '')[:500],
            is_internal=False,
            source='DaData',
        ))
    CompanyFinance.query.filter_by(company_id=company.id).delete()
    finance_rows = {}
    for item in data.get('finance_history') or []:
        if not isinstance(item, dict) or not str(item.get('year') or '').isdigit():
            continue
        metrics = {
            str(metric.get('code')): metric.get('value')
            for metric in item.get('metrics') or []
            if isinstance(metric, dict) and metric.get('code')
        }
        revenue = metrics.get('2110')
        profit = (metrics.get('2500') if metrics.get('2500') is not None
                  else metrics.get('2300'))
        finance_rows[int(item['year'])] = {
            'year': item.get('year'),
            'revenue': revenue,
            'expense': None,
            'profit': profit,
        }
    finance = data.get('finance') or {}
    if isinstance(finance, dict) and str(finance.get('year') or '').isdigit():
        finance_rows[int(finance['year'])] = finance
    for item in finance_rows.values():
        if not isinstance(item, dict):
            continue
        year = item.get('year')
        if str(year).isdigit():
            income = item.get('income')
            expense = item.get('expense')
            profit = item.get('profit')
            if profit is None and income is not None and expense is not None:
                profit = income - expense
            db.session.add(CompanyFinance(
                company_id=company.id, year=int(year),
                revenue=item.get('revenue') or income,
                expense=expense,
                profit=profit,
                details_json=json.dumps(item, ensure_ascii=False)))
    company.dadata_received_at = snapshot.received_at
    company.dadata_source = 'DaData'
    company.dadata_sync_status = 'success'
    company.dadata_last_error = ''
    return profile