# -*- coding: utf-8 -*-
"""Controlled import of the heat-network prototype JSON v4."""
import json
from decimal import Decimal, InvalidOperation

from database import (AnalogCostRow, AnalogNetworkAttribute, AnalogPassport,
                      AnalogPassportVersion, AnalogTep, db)


NETWORK_FIELDS = {
    'len': ('length_m', 'м'), 'diam': ('diameter_mm', 'мм'),
    'wind': ('wind_region', ''), 'snow': ('snow_region', ''),
    'seis': ('seismicity', ''), 'geo': ('geology', ''),
    'depth': ('depth_m', 'м'), 'lay': ('laying_method', ''),
    'psys': ('pipe_system', ''), 'mat': ('pipe_material', ''),
    'insul': ('insulation', ''), 'tsup': ('supply_temperature_c', '°C'),
    'tret': ('return_temperature_c', '°C'), 'pres': ('pressure_mpa', 'МПа'),
    'cham': ('chambers', 'шт'), 'bypass': ('bypass', ''),
    'byd': ('bypass_diameter_mm', 'мм'), 'byl': ('bypass_length_m', 'м'),
}
KNOWN_FIELDS = set(NETWORK_FIELDS) | {
    'id', 'num', 'otype', 'name', 'addr', 'conc', 'pyear', 'cap', 'czone', 'csub'
}


def _number(value):
    try:
        return Decimal(str(value).replace(',', '.')) if value not in ('', None) else None
    except (InvalidOperation, ValueError):
        return None


def import_json_v4(payload, user_id=None, commit=True):
    """Import records without silently dropping conditional network fields.

    Returns transformed/skipped/conflict counts suitable for an audit report.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = json.loads(payload.decode('utf-8'))
    if not isinstance(payload, dict) or payload.get('v') != 4:
        raise ValueError('Ожидается JSON объектов-аналогов версии 4')
    report = {'transformed': 0, 'skipped': 0, 'conflicts': 0, 'warnings': []}
    for record in payload.get('records', []):
        if not isinstance(record, dict) or not record.get('name'):
            report['skipped'] += 1
            report['warnings'].append('Запись без name пропущена')
            continue
        existing = AnalogPassport.query.filter_by(
            title=record['name'], address=record.get('addr', '')).first()
        if existing:
            report['conflicts'] += 1
            continue
        work = record.get('otype', '')
        passport = AnalogPassport(
            title=record['name'], address=record.get('addr', ''),
            object_type='тепловая сеть' if any(record.get(k) for k in ('len', 'diam', 'lay'))
            else 'котельная',
            climate_zone=record.get('czone', ''),
            climate_subzone=record.get('csub', ''),
            work_type=work.lower(), created_by=user_id)
        passport.legacy_json = json.dumps(record, ensure_ascii=False,
                                          default=str)
        passport.capacity_kw = _number(record.get('cap'))
        db.session.add(passport)
        db.session.flush()
        version = AnalogPassportVersion(
            passport_id=passport.id, version_no=1,
            conclusion_number=record.get('conc') or f'json-v4:{record.get("id")}',
            examination_result='positive', expert_object_name=record['name'],
            source_hash=f'json-v4:{record.get("id")}'[:64],
            ssr_hash=f'json-v4:{record.get("id")}'[:64],
            price_year=int(record['pyear']) if str(record.get('pyear', '')).isdigit() else None,
            total_cost=_number(record.get('ctot')),
            imported_by=user_id, payload_json=json.dumps(record, ensure_ascii=False))
        db.session.add(version)
        db.session.flush()
        passport.current_version_id = version.id
        for key, (normalized, unit) in NETWORK_FIELDS.items():
            if key not in record or record[key] in ('', None):
                continue
            db.session.add(AnalogNetworkAttribute(
                passport_id=passport.id, version_id=version.id, key=normalized, value=str(record[key]),
                value_number=_number(record[key]), unit=unit, provenance='JSON v4'))
        # Preserve prototype fields even when they are conditional fields not
        # shown by the first version of the form.
        for key, value in record.items():
            if key in KNOWN_FIELDS or value in ('', None):
                continue
            db.session.add(AnalogNetworkAttribute(
                passport_id=passport.id, version_id=version.id, key=f'v4:{key}', value=str(value),
                value_number=_number(value), provenance='JSON v4'))
        cost_labels = {'ctot': 'total', 'cpm': 'construction',
                       'csmr': 'maintenance', 'ceq': 'equipment',
                       'bcl': 'base'}
        for key, label in cost_labels.items():
            if record.get(key) not in ('', None):
                db.session.add(AnalogCostRow(
                    passport_id=passport.id, version_id=version.id, ordinal=key, name=label,
                    approved_cost=_number(record[key]),
                    extracted_json=json.dumps({key: record[key]}, ensure_ascii=False),
                    effective_json=json.dumps({key: record[key]}, ensure_ascii=False),
                    provenance='JSON v4'))
        report['fields_preserved'] = report.get('fields_preserved', 0) + len(record)
        report['costs_preserved'] = report.get('costs_preserved', 0) + sum(
            key in record and record[key] not in ('', None) for key in cost_labels)
        for key, label, unit in (('conc', 'Номер заключения', ''),
                                 ('pyear', 'Год уровня цен', 'год')):
            if record.get(key) not in ('', None):
                db.session.add(AnalogTep(passport_id=passport.id, version_id=version.id, name=label,
                                         unit=unit, value_text=str(record[key]),
                                         value=_number(record[key]),
                                         provenance='JSON v4'))
        report['transformed'] += 1
    if commit:
        db.session.commit()
    return report