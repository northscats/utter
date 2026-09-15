# -*- coding: utf-8 -*-
"""Безопасный разбор заключения экспертизы и ССР 3.01.

Парсер не применяет XSLT, не разрешает сущности/сети и работает только с
переданными байтами.  Извлечённые данные являются preview: до подтверждения
они не попадают в паспорт.
"""
import base64
import hashlib
import io
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


MAX_XML_SIZE = 50 * 1024 * 1024
MAX_ZIP_SIZE = 100 * 1024 * 1024
MAX_ZIP_MEMBERS = 10000
MAX_XML_DEPTH = 100
PARSER_VERSION = 'analog-1'


class AnalogParseError(ValueError):
    def __init__(self, message, code='invalid_xml', path=''):
        super().__init__(message)
        self.code, self.path = code, path


def _bytes(value, label):
    if hasattr(value, 'read'):
        value = value.read(MAX_XML_SIZE + 1)
    if not isinstance(value, (bytes, bytearray)):
        value = str(value or '').encode('utf-8')
    value = bytes(value)
    if len(value) > MAX_XML_SIZE:
        raise AnalogParseError(f'{label}: файл больше допустимого размера',
                               'size_limit')
    return value


def _safe_xml(raw, label):
    raw = _bytes(raw, label)
    upper = raw.upper()
    # Internal harmless DOCTYPE is found in the real attached conclusion; any
    # entity declaration, external identifier or processing XSLT is forbidden.
    if (b'<!ENTITY' in upper
            or re.search(rb'<!DOCTYPE[^>]*(?:SYSTEM|PUBLIC)', upper, re.S)):
        raise AnalogParseError(f'{label}: XML-сущности запрещены', 'xxe')
    if b'<XSL:' in upper or b'XSLT' in upper:
        # The conclusion's HTML wrapper contains an innocuous stylesheet
        # declaration. It is never executed, but must not be interpreted.
        # Keep parsing the data tree; ET itself does not execute stylesheets.
        pass
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        raise AnalogParseError(f'{label}: повреждённый XML: {exc}',
                               'malformed_xml') from exc
    stack = [(root, 1)]
    while stack:
        node, level = stack.pop()
        if level > MAX_XML_DEPTH:
            raise AnalogParseError(f'{label}: превышена глубина XML',
                                   'depth_limit')
        stack.extend((child, level + 1) for child in list(node))
    return root, raw


def _text(node):
    return ' '.join(''.join(node.itertext()).split()) if node is not None else ''


def _find(node, name):
    if node is None:
        return None
    for child in node.iter():
        if child.tag.rsplit('}', 1)[-1] == name:
            return child
    return None


def _val(node, name, default=''):
    found = _find(node, name)
    return _text(found) or default


def _direct_val(node, name, default=''):
    if node is None:
        return default
    for child in list(node):
        if child.tag.rsplit('}', 1)[-1] == name:
            return _text(child) or default
    return default


def _decimal(value, default=None):
    if value in (None, ''):
        return default
    try:
        return Decimal(str(value).replace('\xa0', '').replace(' ', '')
                       .replace(',', '.'))
    except (InvalidOperation, ValueError):
        return default


def _date(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            return date.fromisoformat(value) if fmt == '%Y-%m-%d' \
                else date.strptime(value, fmt)  # pragma: no cover
        except (ValueError, AttributeError):
            continue
    # date.strptime is not available on older Python; keep this explicit.
    match = re.match(r'^(\d{2})[./](\d{2})[./](\d{4})$', value)
    return date(int(match.group(3)), int(match.group(2)), int(match.group(1))) \
        if match else None


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _norm_name(value):
    return re.sub(r'[^0-9а-яa-z]+', '', (value or '').lower())


def parse_conclusion(raw):
    """Return normalized positive expert-conclusion data."""
    root, raw = _safe_xml(raw, 'заключение')
    # The attached signed format contains a base64 ZIP with conclusion.xml.
    nested = _find(root, 'conclusionRaw')
    nested_xml = None
    if nested is not None and _text(nested):
        try:
            packed = base64.b64decode(_text(nested), validate=True)
            if len(packed) > MAX_ZIP_SIZE:
                raise AnalogParseError('заключение: вложенный архив слишком велик',
                                       'zip_size')
            with zipfile.ZipFile(io.BytesIO(packed)) as archive:
                members = archive.infolist()
                if len(members) > MAX_ZIP_MEMBERS:
                    raise AnalogParseError('заключение: слишком много файлов',
                                           'zip_members')
                if sum(member.file_size for member in members) > MAX_ZIP_SIZE:
                    raise AnalogParseError('заключение: распакованный архив слишком велик',
                                           'zip_size')
                for member in members:
                    name = member.filename.replace('\\', '/')
                    if name.startswith('/') or '..' in name.split('/'):
                        raise AnalogParseError('заключение: небезопасный путь',
                                               'zip_path')
                    if member.file_size > MAX_XML_SIZE:
                        raise AnalogParseError('заключение: вложенный файл велик',
                                               'zip_size')
                    if name.lower().endswith('.xml'):
                        nested_xml = archive.read(member)
                        break
        except (ValueError, zipfile.BadZipFile) as exc:
            raise AnalogParseError('заключение: повреждённый вложенный архив',
                                   'bad_archive') from exc
    if nested_xml:
        inner, _ = _safe_xml(nested_xml, 'вложенное заключение')
        data_root = inner
    else:
        data_root = root
    result_code = _val(data_root, 'ExaminationResult')
    result = 'positive' if result_code in ('1', 'positive', 'положительное') \
        else ('negative' if result_code in ('2', '0', 'negative', 'отрицательное')
              else '')
    # Fallback to visible body for older wrapper variants.
    body = _val(root, 'conclusionBody')
    if not result:
        result = 'negative' if re.search(r'отрицательн', body, re.I) else (
            'positive' if re.search(r'положительн', body, re.I) else 'unknown')
    name = _val(data_root, 'Name')
    if not name:
        match = re.search(r'Наименование объекта экспертизы:\s*(.*?)\s*(?:Вид работ:|$)',
                          body, re.I)
        name = match.group(1) if match else ''
    work = ''
    if body:
        # conclusionBody is CDATA HTML in the real signed container. Do not
        # use its markup as a field value.
        raw_body = _find(root, 'conclusionBody')
        raw_body = raw_body.text or '' if raw_body is not None else body
        match = re.search(r'Вид\s+работ:</[^>]+>\s*<[^>]+>(.*?)</',
                          raw_body, re.I | re.S)
        if not match:
            match = re.search(r'Вид\s+работ:\s*(.*?)(?:Объект\s+экспертизы:|$)',
                              body, re.I)
        work = re.sub(r'<[^>]+>', ' ', match.group(1)).strip() if match else ''
    if not work:
        work = {'1': 'строительство', '2': 'реконструкция',
                '3': 'капитальный ремонт'}.get(_val(data_root, 'ConstructionType'), '')
    roles = []
    for role, tag in (('expert', 'ExpertOrganization'), ('applicant', 'Declarant'),
                      ('developer', 'ProjectDocumentsDeveloper'),
                      ('customer', 'ProjectDocumentsTechnicalCustomer')):
        org = _find(data_root, tag)
        if org is not None:
            organization = _find(org, 'Organization') or org
            roles.append({'role': role, 'name': _val(organization, 'OrgFullName') or
                          _val(organization, 'Name'), 'ogrn': _val(organization, 'OrgOGRN'),
                          'inn': _val(organization, 'OrgINN'), 'kpp': _val(organization, 'OrgKPP'),
                          'email': _val(organization, 'Email'), 'address': _val(organization, 'Note')})
    docs = []
    docs_node = _find(data_root, 'Documents')
    if docs_node is not None:
        for item in list(docs_node):
            if item.tag.rsplit('}', 1)[-1] != 'Document':
                continue
            file_node = _find(item, 'File')
            sig = _find(file_node, 'SignFile') if file_node is not None else None
            ordinal = _val(item, 'DocType')
            docs.append({'ordinal': ordinal,
                         'parent_ordinal': ordinal.rsplit('.', 1)[0] if '.' in ordinal else '',
                         'name': _val(item, 'DocName'), 'note': _val(item, 'Note'),
                         'file_format': _val(file_node, 'FileFormat') if file_node is not None else '',
                         'checksum': _val(file_node, 'FileChecksum') if file_node is not None else '',
                         'signature_name': _val(sig, 'FileName') if sig is not None else '',
                         'signature_checksum': _val(sig, 'FileChecksum') if sig is not None else ''})
    # The signed conclusion stores table 3.2.1 as ordered cost vectors
    # (basic/current level and approved/previous value). Keep every component
    # rather than reducing it to one total.
    cost_rows = []
    estimate = _find(data_root, 'EstimatedCost')
    before = _text(_find(estimate, 'EstimatedComplexCostBefore')).split()
    after_node = _find(estimate, 'EstimatedComplexCostPost')
    after_text = _text(after_node)
    after = after_text.split()
    # The final vector is followed by free-text VAT/change justification.
    # Keep that text as a reason, never attach it to the first numeric cell.
    reason_text = ' '.join(after[13:]).strip()
    comment = _val(estimate, 'CostComment')
    vat_match = re.search(r'(\d+(?:[,.]\d+)?)\s*%', comment)
    vat_text = f'{vat_match.group(1)}%' if vat_match else ''
    price_level = _val(estimate, 'PriceLevel') or _val(data_root, 'PriceLevel')
    components = ('базисная стоимость', 'базисные работы', 'базисное оборудование',
                  'базисные прочие затраты', 'базисные проектные работы',
                  'базисный возврат', 'текущая стоимость', 'строительные работы',
                  'оборудование', 'прочие затраты', 'проектные работы',
                  'НДС', 'возврат')
    for index, component in enumerate(components):
        old, new = (_decimal(before[index]) if index < len(before) else None,
                    _decimal(after[index]) if index < len(after) else None)
        if old is not None or new is not None:
            cost_rows.append({'ordinal': str(index + 1), 'name': component,
                              'submitted_cost': old, 'approved_cost': new,
                              'change_cost': (new - old if old is not None and new is not None else None),
                               'price_level': price_level, 'vat': vat_text,
                               'footnote': comment,
                                'change_reason': comment or reason_text if index == 0 else '',
                              'provenance': 'conclusionRaw/EstimatedCost'})
    object_node = _find(data_root, 'Object')
    tep_rows = []
    if object_node is not None:
        for item in list(object_node):
            if item.tag.rsplit('}', 1)[-1] != 'TEI':
                continue
            parts = _text(item).split()
            if len(parts) >= 2:
                numeric = _decimal(parts[-1])
                normalized, normalized_unit = numeric, parts[-2] if len(parts) > 2 else ''
                if numeric is not None and normalized_unit.lower() in ('гкал', 'гкал/ч'):
                    normalized = numeric * Decimal('1162.222222222222')
                    normalized_unit = 'кВт'
                tep_rows.append({'name': ' '.join(parts[:-2]) if len(parts) > 2 else parts[0],
                                 'unit': parts[-2] if len(parts) > 2 else '',
                                 'value': numeric, 'normalized_value': normalized,
                                 'normalized_unit': normalized_unit, 'value_text': _text(item),
                                 'provenance': 'conclusionRaw/Object/TEI'})
    climate = (_val(data_root, 'ClimateConditions') or
               _val(data_root, 'ClimateDistrict') or
               (_val(object_node, 'ClimateDistrict') if object_node is not None else ''))
    cadastral = _val(data_root, 'CadastralNumber')
    construction_object = _val(data_root, 'Object') or _val(data_root, 'ExaminationObject')
    return {
        'conclusion_number': _val(data_root, 'ConclusionNumber') or
            _val(root, 'conclusionNumber') or _val(root, 'number'),
        'approval_date': _date(_val(data_root, 'ApprovalDate') or
                               _val(root, 'approvalDate') or
                               _val(root, 'expertiseConclusionDate')),
        'examination_result': result,
        'expert_object_name': name,
        'work_type': work,
        'address': _direct_val(object_node, 'Address') if object_node is not None else '',
        'climate_zone': _val(data_root, 'ClimateDistrict'),
        'climate_conditions': climate,
        'natural_conditions': climate,
        'cadastral_number': cadastral,
        'construction_object': construction_object,
        'extra_info': {
            'cadastral_number': cadastral,
            'climate_conditions': climate,
            'construction_object': construction_object,
            'source_size': len(raw),
            'parser_version': 'analog-parser-1',
        },
        'roles': roles, 'documents': docs, 'cost_rows': cost_rows, 'teps': tep_rows,
        'schema_version': (data_root.attrib.get('SchemaVersion', '') if data_root is not None else ''),
        'source_hash': _hash(raw), 'raw_size': len(raw),
    }


def _cost(node):
    return {key.lower(): _decimal(_val(node, key), Decimal('0'))
            for key in ('Building', 'Mounting', 'Equipment', 'Other', 'Total')}


def parse_gge(raw):
    """Parse SummaryEstimate-3_01 without float arithmetic."""
    root, raw = _safe_xml(raw, 'ССР')
    if root.tag.rsplit('}', 1)[-1] != 'Construction':
        raise AnalogParseError('ССР: ожидается элемент Construction', 'schema')
    meta = _find(root, 'Meta')
    file_meta = _find(meta, 'File') if meta is not None else None
    version = _val(file_meta, 'Version') if file_meta is not None else ''
    if version and version != '3.01':
        raise AnalogParseError(f'ССР: неподдерживаемая версия {version}', 'schema')
    summary = _find(root, 'Summary')
    summary = _cost(summary) if summary is not None else {}
    chapters, items = [], []
    for chapter in list(root):
        match = re.match(r'Chapter(\d+)$', chapter.tag.rsplit('}', 1)[-1])
        if not match:
            continue
        ch_summary = _find(chapter, 'SubSummary') or _find(chapter, 'Summary')
        values = _cost(ch_summary) if ch_summary is not None else _cost(chapter)
        chapters.append({'number': int(match.group(1)),
                         'name': _val(chapter, 'ConstantNameChapter'),
                         **values})
        for child in list(chapter):
            typ = child.tag.rsplit('}', 1)[-1]
            if typ not in ('LocalEstimate', 'Addition', 'Calculation'):
                continue
            values = _cost(child)
            items.append({'chapter_number': int(match.group(1)), 'item_type': typ,
                          'ordinal': _val(child, 'Num'), 'name': _val(child, 'Name'),
                          'reason': _val(child, 'Reason'), **values})
    signatures = []
    signatures_node = _find(root, 'Signatures')
    if signatures_node is not None:
        for item in list(signatures_node):
            signatures.append({'role': item.tag.rsplit('}', 1)[-1],
                               'name': _val(item, 'Name'),
                               'position': _val(item, 'Position'),
                               'department': _val(item, 'Department')})
    price = _find(root, 'PriceLevel')
    return {
        'name': _direct_val(root, 'Name'), 'customer': _direct_val(root, 'Customer'),
        'approved_date': _date(_direct_val(root, 'Approved')),
        'price_year': int(_val(price, 'Year')) if _val(price, 'Year').isdigit() else None,
        'price_quarter': int(_val(price, 'Quarter')) if _val(price, 'Quarter').isdigit() else None,
        'basic_price': _val(price, 'BasicPrice').lower() == 'true',
        'summary': summary, 'chapters': chapters, 'items': items,
        'signatures': signatures,
        'schema_version': version or '3.01', 'ssr_hash': _hash(raw),
        'raw_size': len(raw),
    }


def parse_pair(conclusion, ssr):
    """Validate both sources and return explicit mismatch diagnostics."""
    left, right = parse_conclusion(conclusion), parse_gge(ssr)
    level = f"{right.get('price_year') or ''}/Q{right.get('price_quarter') or ''}"
    if level != '/Q':
        for row in left.get('cost_rows', []):
            row['price_level'] = row.get('price_level') or level
    diagnostics = [{'severity': 'info', 'code': 'pair_validated',
                    'message': 'Пара источников прошла структурную проверку',
                    'path': ''}]
    for field, label in (('conclusion_number', 'номер заключения'),
                         ('approval_date', 'дата утверждения'),
                         ('work_type', 'вид работ')):
        if not left.get(field):
            diagnostics.append({'severity': 'error', 'code': 'required_missing',
                                'message': f'Не заполнено обязательное поле: {label}'})
    if left['examination_result'] != 'positive':
        diagnostics.append({'severity': 'error', 'code': 'negative_conclusion',
                            'message': 'Отрицательное заключение нельзя импортировать'})
    if left['expert_object_name'] and right['name']:
        a, b = left['expert_object_name'].lower(), right['name'].lower()
        if _norm_name(a) not in _norm_name(b) and _norm_name(b) not in _norm_name(a):
            diagnostics.append({'severity': 'error', 'code': 'name_mismatch',
                                'message': 'Название объекта в заключении и ССР не совпадает'})
    total = right.get('summary', {}).get('total')
    if total is not None and total <= 0:
        diagnostics.append({'severity': 'error', 'code': 'missing_total',
                            'message': 'В ССР отсутствует положительный итог'})
    approved = next((row.get('approved_cost') for row in left.get('cost_rows', [])
                     if row.get('ordinal') == '7'), None)
    if total is not None and approved is not None and abs(total - approved) > Decimal('0.01'):
        diagnostics.append({'severity': 'error', 'code': 'total_mismatch',
                            'message': f'Итог заключения {approved} не совпадает с ССР {total} '
                                       '(допуск 0,01)'})
    if not right.get('price_year') or not right.get('price_quarter'):
        diagnostics.append({'severity': 'warning', 'code': 'price_level_missing',
                            'message': 'В ССР не указан год или квартал уровня цен'})
    return {'conclusion': left, 'ssr': right, 'diagnostics': diagnostics,
            'can_confirm': not any(d['severity'] == 'error' for d in diagnostics)}


# Stable public names used by import jobs and external validation tests.
parse_conclusion_xml = parse_conclusion
parse_ssr = parse_gge
compare_pair = parse_pair
