# -*- coding: utf-8 -*-
"""Pure, auditable calculations for the CFO key-indicator screen.

The module deliberately consumes a small snapshot rather than ``builder``'s
derived values.  This keeps the new screen independent from the legacy
dashboard formulas and rating.
"""
import calendar
import re
from collections import defaultdict
from datetime import date as Date, timedelta


_BALANCE_ACCOUNTS = {'50', '51', '52', '55', '60', '62', '66', '67', '70'}
_CARD_ORDER = ('cash', 'revenue', 'collection', 'ar', 'ap', 'cogs',
               'profit', 'credits', 'salary', 'capex')
_TITLES = {
    'cash': 'Денежные средства',
    'revenue': 'Выручка',
    'collection': 'Уровень собираемости',
    'ar': 'Дебиторская задолженность (62.01)',
    'ap': 'Кредиторская задолженность (60.01)',
    'cogs': 'Себестоимость продаж',
    'profit': 'Прибыль от продаж (до отдельных расходов)',
    'credits': 'Кредиты и займы',
    'salary': 'Расчёты по оплате труда',
    'capex': 'Капитальные вложения (оборот счёта 08)',
}
_RU_MONTH = {
    'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'ма': 5,
    'июн': 6, 'июл': 7, 'август': 8, 'сентябр': 9, 'октябр': 10,
    'ноябр': 11, 'декабр': 12,
}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(float(value), 2)


def _iso(value):
    try:
        return Date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _period_bounds(text):
    """Conservative extraction of original report boundaries."""
    text = str(text or '').lower()
    # Prefer explicit ranges; a year elsewhere in a header must not override
    # the actual endpoints.
    iso_dates = re.findall(r'(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)', text)
    if len(iso_dates) >= 2:
        try:
            values = [Date(int(y), int(m), int(d)) for y, m, d in iso_dates]
            return min(values), max(values)
        except ValueError:
            return None, None
    nums = re.findall(r'(?<!\d)(\d{1,2})[./](\d{1,2})[./](20\d{2})(?!\d)', text)
    if len(nums) >= 2:
        try:
            values = [Date(int(y), int(m), int(d)) for d, m, y in nums]
            return min(values), max(values)
        except ValueError:
            return None, None
    years = [int(x) for x in re.findall(r'(?<!\d)(20\d{2})(?!\d)', text)]
    if not years:
        return None, None
    year = years[-1]
    m = re.search(r'([1-4])\s*квартал', text)
    if m:
        q = int(m.group(1))
        return Date(year, 3 * q - 2, 1), Date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
    if 'полугод' in text:
        half_marker = re.search(r'(?:^|\D)([12])\s*(?:-?е\s+)?полугод|'
                                r'\b(ii|втор\w*)\s+полугод', text)
        half = 2 if half_marker and (
            (half_marker.group(1) == '2') or half_marker.group(2)
        ) else 1
        return (Date(year, 1 if half == 1 else 7, 1),
                Date(year, 6 if half == 1 else 12, 30 if half == 1 else 31))
    if re.search(r'\bгод\b|за\s+20\d{2}', text):
        return Date(year, 1, 1), Date(year, 12, 31)
    for stem, month in _RU_MONTH.items():
        if stem in text:
            day_match = re.search(r'(?<!\d)([0-3]?\d)\s+[а-яё]*' +
                                  re.escape(stem) + r'[а-яё]*\s+' +
                                  str(year), text)
            if day_match:
                try:
                    exact = Date(year, month, int(day_match.group(1)))
                    return exact, exact
                except ValueError:
                    return None, None
            return Date(year, month, 1), Date(year, month, calendar.monthrange(year, month)[1])
    return None, None


def build_indicator_source(parsed_data):
    """Return a JSON-safe versioned snapshot of authentic parsed ОСВ data."""
    if not isinstance(parsed_data, dict):
        raise ValueError('parsed_data must be a dictionary')
    accounts = {}
    all_dates = set()
    warnings = []
    for account, raw in sorted(parsed_data.items()):
        if not isinstance(raw, dict):
            continue
        account = str(account)
        daily = []
        seen = set()
        for item in raw.get('daily') or []:
            dt = str(item.get('date') or '')
            if not _iso(dt):
                continue
            row = {
                'date': dt,
                'month': str(item.get('month') or dt[:7]),
                'path1': str(item.get('path1') or ''),
                'path2': str(item.get('path2') or ''),
                'path3': str(item.get('path3') or ''),
                'obd': _number(item.get('obd')),
                'obk': _number(item.get('obk')),
            }
            for key in ('snd', 'snk', 'skd', 'skk'):
                if item.get(key) is not None:
                    row[key] = _number(item[key])
            marker = tuple(row.get(k) for k in
                           ('date', 'path1', 'path2', 'path3', 'obd', 'obk',
                            'snd', 'snk', 'skd', 'skk'))
            if marker not in seen:
                seen.add(marker)
                daily.append(row)
                all_dates.add(dt)
        rows = []
        for item in raw.get('rows') or []:
            if not isinstance(item, dict):
                continue
            rows.append({
                'lvl': int(item.get('lvl') or 0),
                'label': str(item.get('label') or ''),
                'p0': str(item.get('p0') or ''),
                'p1': str(item.get('p1') or ''),
                'p2': str(item.get('p2') or ''),
                'p3': str(item.get('p3') or ''),
                **{k: _number(item.get(k)) for k in
                   ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
            })
        top_raw = raw.get('top')
        top = None
        if isinstance(top_raw, dict):
            top = {k: _number(top_raw.get(k)) for k in
                   ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')}
        start, end = _period_bounds(raw.get('period'))
        balance_reports = []
        for report in raw.get('balance_reports') or []:
            if not isinstance(report, dict):
                continue
            report_start, report_end = _period_bounds(report.get('period'))
            report_top = report.get('top')
            if report_end is None or not isinstance(report_top, dict):
                continue
            report_rows = []
            for item in report.get('rows') or []:
                if not isinstance(item, dict):
                    continue
                report_rows.append({
                    'lvl': int(item.get('lvl') or 0),
                    'label': str(item.get('label') or ''),
                    **{k: _number(item.get(k)) for k in
                       ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
                })
            balance_reports.append({
                'period': str(report.get('period') or ''),
                'source_file': str(report.get('source_file') or ''),
                'report_start': report_start.isoformat() if report_start else None,
                'report_end': report_end.isoformat(),
                'top': {k: _number(report_top.get(k)) for k in
                        ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
                'rows': report_rows,
            })
        coverage = []
        coverage_items = list(raw.get('coverage_reports') or [])
        if raw.get('period'):
            coverage_items.append({'period': raw.get('period')})
        for item in coverage_items:
            period_text = item.get('period') if isinstance(item, dict) else item
            explicit_start = _iso(item.get('start')) if isinstance(item, dict) else None
            explicit_end = _iso(item.get('end')) if isinstance(item, dict) else None
            if explicit_start and explicit_end:
                coverage_start, coverage_end = explicit_start, explicit_end
            else:
                coverage_start, coverage_end = _period_bounds(period_text)
            if coverage_start and coverage_end:
                coverage.append({
                    'start': coverage_start.isoformat(),
                    'end': coverage_end.isoformat(),
                    'period': str(period_text or ''),
                    'source_file': str(item.get('source_file') or '')
                    if isinstance(item, dict) else '',
                })
        balance_observations = []
        for item in raw.get('balance_observations') or []:
            if not isinstance(item, dict) or not _iso(item.get('date')):
                continue
            observation = {
                'date': str(item['date']),
                'path1': str(item.get('path1') or ''),
                'path2': str(item.get('path2') or ''),
                'path3': str(item.get('path3') or ''),
            }
            for key in ('snd', 'snk', 'skd', 'skk'):
                if item.get(key) is not None:
                    observation[key] = _number(item[key])
            if any(key in observation for key in ('snd', 'snk', 'skd', 'skk')):
                balance_observations.append(observation)
                all_dates.add(observation['date'])
        acc_warnings = [str(x) for x in (raw.get('warnings') or [])]
        warnings.extend(acc_warnings)
        accounts[account] = {
            'period': str(raw.get('period') or ''),
            'report_start': start.isoformat() if start else None,
            'report_end': end.isoformat() if end else None,
            'check_ok': raw.get('check_ok') if isinstance(raw.get('check_ok'), bool) else None,
            'warnings': acc_warnings,
            'daily': sorted(daily, key=lambda x: (x['date'], x['path1'], x['path2'], x['path3'])),
            'top': top,
            'rows': rows,
            'source_file': str(raw.get('source_file') or ''),
            'balance_reports': balance_reports,
            'coverage': coverage,
            'balance_observations': balance_observations,
        }
        # Report boundaries are real selectable balance dates even when the
        # final dated turnover happened one or more days earlier.
        if end is not None and top is not None:
            all_dates.add(end.isoformat())
        for report in balance_reports:
            all_dates.add(report['report_end'])
        for interval in coverage:
            all_dates.add(interval['end'])
    return {
        'version': 1,
        'dates': sorted(all_dates),
        'accounts': accounts,
        'warnings': list(dict.fromkeys(warnings)),
    }


def _range(kind, endpoint):
    if kind == 'day':
        return endpoint, endpoint
    if kind == 'month':
        return endpoint.replace(day=1), endpoint
    if kind == 'quarter':
        month = ((endpoint.month - 1) // 3) * 3 + 1
        return endpoint.replace(month=month, day=1), endpoint
    return endpoint.replace(month=1, day=1), endpoint


def _option_key(kind, dt):
    if kind == 'day':
        return dt
    if kind == 'month':
        return dt.year, dt.month
    if kind == 'quarter':
        return dt.year, (dt.month - 1) // 3 + 1
    return (dt.year,)


def _options(dates, kind):
    grouped = {}
    for dt in dates:
        grouped[_option_key(kind, dt)] = max(dt, grouped.get(_option_key(kind, dt), dt))
    names = ('янв', 'фев', 'мар', 'апр', 'май', 'июн',
             'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')
    result = []
    for endpoint in sorted(grouped.values()):
        if kind == 'day':
            label = endpoint.strftime('%d.%m.%Y')
        elif kind == 'month':
            label = f'{names[endpoint.month - 1].capitalize()} {endpoint.year}'
        elif kind == 'quarter':
            label = f'{(endpoint.month - 1) // 3 + 1} квартал {endpoint.year}'
        else:
            label = str(endpoint.year)
        result.append({'value': endpoint.isoformat(), 'label': label})
    return result


def _daily(source, account, start, end):
    return [r for r in source['accounts'].get(account, {}).get('daily', [])
            if start <= _iso(r['date']) <= end]


def _leaf_rows(rows, predicate):
    """Select the deepest account code per date and analytic coordinate."""
    matched = [r for r in rows if predicate(r)]
    grouped = defaultdict(list)
    for row in matched:
        paths = [row.get(k, '') for k in ('path1', 'path2', 'path3')]
        analytics = tuple(x for x in paths
                          if x and not re.match(r'^\d{2}(?:\.\d+)+$', x))
        grouped[(row['date'], analytics)].append(row)
    result = []
    for candidates in grouped.values():
        depth = max(max((x.count('.') for x in
                         (r.get('path1', ''), r.get('path2', ''), r.get('path3', ''))
                         if re.match(r'^\d{2}(?:\.\d+)+$', x)), default=-1)
                    for r in candidates)
        result.extend(r for r in candidates
                      if max((x.count('.') for x in
                              (r.get('path1', ''), r.get('path2', ''), r.get('path3', ''))
                              if re.match(r'^\d{2}(?:\.\d+)+$', x)), default=-1) == depth)
    return result


def _has_prefix(row, prefix):
    return any(str(row.get(k) or '').startswith(prefix)
               for k in ('path1', 'path2', 'path3'))


def _analytic_label(row):
    values = [str(row.get(k) or '') for k in ('path1', 'path2', 'path3')]
    analytics = [x for x in values
                 if x and not re.match(r'^\d{2}(?:\.\d+)+$', x)]
    return analytics[0] if analytics else 'Без аналитики'


def _turnover_basis(rows):
    result = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row['date']].append(row)
    for dated in by_date.values():
        plain = [r for r in dated if not any(
            (r.get('path1'), r.get('path2'), r.get('path3')))]
        result.extend(plain if plain else _leaf_rows(dated, lambda r: True))
    return result


def _sum(rows, side):
    return round(sum(r.get(side, 0.0) for r in rows), 2)


def _account_covers(source, account, start, end):
    acc = source['accounts'].get(account)
    if not acc:
        return False
    spans = [(acc.get('report_start'), acc.get('report_end'))]
    spans += [(r.get('report_start'), r.get('report_end'))
              for r in acc.get('balance_reports') or []]
    spans += [(r.get('start'), r.get('end')) for r in acc.get('coverage') or []]
    return any(_iso(a) and _iso(b) and _iso(a) <= start and _iso(b) >= end
               for a, b in spans)


def _pl_facts(source, start, end):
    d90 = _daily(source, '90', start, end)
    rev_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.01'))
    vat_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.03'))
    cost_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.02'))
    gross = _sum(rev_rows, 'obk') if rev_rows else None
    vat = _sum(vat_rows, 'obd') if vat_rows else None
    revenue = round(gross - vat, 2) if gross is not None and vat is not None else gross
    cost = _sum(cost_rows, 'obd') if cost_rows else None
    # Gross revenue minus cost is not profit from sales.  VAT must be known.
    profit = round(revenue - cost, 2) if (gross is not None and vat is not None
                                          and cost is not None) else None
    return dict(rows=d90, rev_rows=rev_rows, vat_rows=vat_rows,
                cost_rows=cost_rows, gross=gross, vat=vat, revenue=revenue,
                cost=cost, profit=profit,
                vat_basis='net' if vat is not None else
                ('gross' if gross is not None else None))


def _sub_balance(source, account, sub, end, credit=False):
    acc = source['accounts'].get(account)
    if not acc:
        return None, f'Счёт {account} не загружен'
    # Explicit daily balances are valid only for that exact date.
    observations = (acc.get('balance_observations') or []) + acc['daily']
    explicit = [r for r in observations if r['date'] == end.isoformat()
                and any(k in r for k in ('skd', 'skk'))]
    specific = [r for r in explicit if sub in
                (r.get('path1'), r.get('path2'), r.get('path3'))]
    if specific:
        return round(sum(max(0.0, (r.get('skk', 0) - r.get('skd', 0))
                                 if credit else
                                 (r.get('skd', 0) - r.get('skk', 0)))
                         for r in specific), 2), None
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    report_rows = report.get('rows', []) if report else acc['rows']
    if report is None and acc.get('report_end') != end.isoformat():
        return None, 'Сальдо доступно только на исходную границу отчёта'
    row = next((r for r in report_rows if r['label'] == sub), None)
    if row is None:
        return None, f'Субсчёт {sub} отсутствует; значение не считается нулём'
    value = row['skk'] - row['skd'] if credit else row['skd'] - row['skk']
    return round(max(0.0, value), 2), None


def _top_balance(source, account, end, credit=False):
    acc = source['accounts'].get(account)
    if not acc:
        return None, f'Счёт {account} не загружен'
    observations = (acc.get('balance_observations') or []) + acc['daily']
    explicit = [r for r in observations if r['date'] == end.isoformat()
                and not any((r.get('path1'), r.get('path2'), r.get('path3')))
                and any(k in r for k in ('skd', 'skk'))]
    if explicit:
        row = explicit[-1]
        return round(max(0.0, row.get('skk', 0) - row.get('skd', 0)
                             if credit else row.get('skd', 0) - row.get('skk', 0)), 2), None
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    if report is not None:
        top = report['top']
    elif acc.get('report_end') == end.isoformat() and acc.get('top') is not None:
        top = acc['top']
    else:
        return None, 'Сальдо доступно только на исходную границу отчёта'
    return round(max(0.0, top['skk'] - top['skd']
                         if credit else top['skd'] - top['skk']), 2), None


def _balance_provenance(source, account, end):
    acc = source['accounts'].get(account, {})
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    if report:
        parts = [report.get('period'), report.get('source_file')]
    elif acc.get('report_end') == end.isoformat():
        parts = [acc.get('period'), acc.get('source_file')]
    elif any(r.get('date') == end.isoformat()
             for r in acc.get('balance_observations') or []):
        parts = [f'наблюдение на {end.isoformat()}', acc.get('source_file')]
    else:
        return None
    text = ' · '.join(str(x) for x in parts if x)
    return f'Источник сальдо счёта {account}: {text}.' if text else None


def _card(cid, value, unit, formula, source_label, reason=None, **extra):
    unavailable = value is None
    card = {
        'id': cid, 'title': _TITLES[cid], 'value': value, 'unit': unit,
        'status': 'unavailable' if unavailable else 'neutral',
        'status_label': 'Недоступно' if unavailable else 'Факт',
        'delta': None, 'formula': formula, 'source': source_label,
        'reason': reason, 'notes': [], 'breakdown': [], 'series': [],
        'details': [],
    }
    card.update(extra)
    return card


def build_key_indicators(source, period='month', date=None):
    """Calculate the strict JSON response contract from a source snapshot."""
    if not isinstance(source, dict) or source.get('version') != 1 \
            or not isinstance(source.get('accounts'), dict):
        raise ValueError('Unsupported indicator source')
    if period not in ('day', 'month', 'quarter', 'year'):
        raise ValueError('period must be day, month, quarter or year')
    dates = sorted(dt for dt in (_iso(x) for x in source.get('dates', [])) if dt)
    available = ['month', 'quarter', 'year']
    # Day is selectable only where at least turnover KPIs have real daily rows.
    if dates:
        available.append('day')
    options = _options(dates, period)
    if not options:
        fallback = _iso(date) if date is not None else Date(1970, 1, 1)
        if fallback is None:
            raise ValueError('date must be YYYY-MM-DD')
        options = _options([fallback], period)
    if date is None:
        endpoint = _iso(options[-1]['value'])
    else:
        endpoint = _iso(date)
        if endpoint is None or date not in {x['value'] for x in options}:
            raise ValueError('date is not an available option for selected period')
    start, end = _range(period, endpoint)
    label = next(x['label'] for x in options if x['value'] == endpoint.isoformat())
    warnings = list(source.get('warnings') or [])
    loaded_accounts = list(source['accounts'])
    partial = bool(loaded_accounts) and not any(
        _account_covers(source, account, start, end)
        for account in loaded_accounts)
    if period == 'month':
        partial = partial or end.day != calendar.monthrange(end.year, end.month)[1]
    elif period == 'quarter':
        partial = partial or end.month not in (3, 6, 9, 12) or \
            end.day != calendar.monthrange(end.year, end.month)[1]
    elif period == 'year':
        partial = partial or (end.month, end.day) != (12, 31)
    if partial:
        warnings.append('Покрытие выбранного периода неполное; обороты отражают только загруженные даты')
    for account, acc in source['accounts'].items():
        if acc.get('check_ok') is False:
            warnings.append(f'Контрольное равенство счёта {account} не сходится')

    pl = _pl_facts(source, start, end)
    d90, rev_rows, vat_rows, cost_rows = (
        pl['rows'], pl['rev_rows'], pl['vat_rows'], pl['cost_rows'])
    gross_rev, vat = pl['gross'], pl['vat']
    revenue, cogs, profit = pl['revenue'], pl['cost'], pl['profit']

    cash_parts, cash_reasons = [], []
    for account in ('50', '51', '52', '55'):
        value, why = _top_balance(source, account, end)
        if value is not None:
            cash_parts.append((account, value))
        elif account in source['accounts']:
            cash_reasons.append(f'{account}: {why}')
    cash = round(sum(v for _, v in cash_parts), 2) if cash_parts else None
    cash_reason = None if cash_parts else ('; '.join(cash_reasons) or 'Счета 50/51/52/55 не загружены')
    cash_missing = [a for a in ('50', '51', '52', '55')
                    if not any(part[0] == a for part in cash_parts)]
    cash_notes = []
    if cash_parts:
        cash_notes.append('Поддерживаемый охват: счета ' +
                          ', '.join(a for a, _ in cash_parts) + '.')
        if cash_missing:
            cash_notes.append('Частичный охват: нет поддерживаемого сальдо по счетам ' +
                              ', '.join(cash_missing) + '; они не приняты за ноль.')

    d62 = _daily(source, '62', start, end)
    collection = None
    collection_reason = ('ОСВ не связывает начисления с оплатами и не содержит '
                         'распределение платежей по срокам; собираемость недоступна')
    turnover_ratio = None
    if d62:
        # Account-level rows and analytic rows must never be added together.
        plain = [r for r in d62 if not any((r['path1'], r['path2'], r['path3']))]
        basis = plain if plain else _leaf_rows(d62, lambda r: True)
        charged, paid = _sum(basis, 'obd'), _sum(basis, 'obk')
        if charged:
            turnover_ratio = round(paid / charged * 100, 2)

    ar, ar_reason = _sub_balance(source, '62', '62.01', end)
    ap, ap_reason = _sub_balance(source, '60', '60.01', end, credit=True)
    credit_parts = []
    credit_reasons = []
    for account in ('66', '67'):
        value, why = _top_balance(source, account, end, credit=True)
        if value is not None:
            credit_parts.append((account, value))
        elif account in source['accounts']:
            credit_reasons.append(f'{account}: {why}')
    credits = round(sum(x[1] for x in credit_parts), 2) if credit_parts else None
    credits_reason = None if credit_parts else ('; '.join(credit_reasons) or 'Счета 66/67 не загружены')
    salary, salary_reason = _top_balance(source, '70', end, credit=True)
    d08 = _turnover_basis(_daily(source, '08', start, end))
    capex = _sum(d08, 'obd') if d08 else None
    capex_reason = None if d08 else 'Нет суточного дебетового оборота счёта 08'

    cards = [
        _card('cash', cash, 'RUB', 'СКд − СКк по счетам 50, 51, 52, 55',
              'Конечные сальдо ОСВ', cash_reason,
              breakdown=[{'label': f'Счёт {a}', 'value': v} for a, v in cash_parts],
              notes=cash_notes),
        _card('revenue', revenue, 'RUB',
              'ОбК(90.01*) − ОбД(90.03*)' if vat is not None else 'ОбК(90.01*), брутто',
              'Суточные листовые обороты счёта 90',
              None if revenue is not None else 'Нет строк точного префикса 90.01',
              details=[{'label': 'Выручка брутто', 'value': gross_rev, 'unit': 'RUB'},
                       {'label': 'НДС', 'value': vat, 'unit': 'RUB',
                        **({'reason': 'Строки 90.03 отсутствуют'} if vat is None else {})}]),
        _card('collection', collection, '%', 'Требуется распределение оплат по начислениям',
              'Суточные обороты счёта 62', collection_reason,
              details=[{'label': 'Соотношение оборотов, не собираемость',
                        'value': turnover_ratio, 'unit': '%',
                        **({} if turnover_ratio is not None else
                           {'reason': 'Нет ненулевой базы дебетового оборота счёта 62'})},
                       {'label': 'График/возраст долга', 'value': None, 'unit': 'RUB',
                        'reason': 'ОСВ не содержит сроков возникновения и погашения долга'}]),
        _card('ar', ar, 'RUB', 'max(СКд − СКк, 0) по 62.01',
              'Сальдо субсчёта 62.01', ar_reason,
              notes=['Прочие расчёты счёта 76 не включены: надёжное соответствие ДЗ не задано.']),
        _card('ap', ap, 'RUB', 'max(СКк − СКд, 0) по 60.01',
              'Сальдо субсчёта 60.01', ap_reason,
              notes=['Прочие расчёты счёта 76 не включены: надёжное соответствие КЗ не задано.']),
        _card('cogs', cogs, 'RUB', 'ОбД(90.02*)',
              'Суточные листовые обороты счёта 90',
              None if cogs is not None else 'Нет строк точного префикса 90.02'),
        _card('profit', profit, 'RUB', 'Выручка без НДС − ОбД(90.02*)',
              'Расчёт по субсчетам продаж',
              None if profit is not None else
              ('НДС 90.03 отсутствует: прибыль нельзя получить из выручки брутто'
               if gross_rev is not None and vat is None else
               'Недостаточно данных выручки или себестоимости'),
              notes=['Это прибыль от продаж до отдельных расходов, а не чистая прибыль.',
                     'Обороты счетов 20, 26 и 44 повторно не добавляются.']),
        _card('credits', credits, 'RUB', 'Кредитовое сальдо счетов 66 и 67',
              'Конечные сальдо ОСВ', credits_reason,
              breakdown=[{'label': f'Счёт {a}', 'value': v} for a, v in credit_parts],
              notes=(['Поддерживаемый охват: счета ' +
                      ', '.join(a for a, _ in credit_parts) + '.'] +
                     (['Частичный охват: отсутствует поддерживаемое сальдо по ' +
                       ', '.join(a for a in ('66', '67')
                                 if a not in {x[0] for x in credit_parts}) + '.']
                      if credit_parts and len(credit_parts) < 2 else []))),
        _card('salary', salary, 'RUB', 'max(СКк − СКд, 0) по счёту 70',
              'Конечное сальдо счёта 70', salary_reason,
              notes=['Кредитовое сальдо — расчётная задолженность работникам; ОСВ не подтверждает просрочку.']),
        _card('capex', capex, 'RUB', 'ОбД(08) за выбранный период',
              'Суточный оборот счёта 08', capex_reason,
              notes=['Показатель отражает капитализируемые вложения, но не план и не полный денежный поток инвестпрограммы.'],
              details=[{'label': 'План инвестпрограммы', 'value': None, 'unit': 'RUB',
                        'reason': 'План отсутствует в ОСВ'}]),
    ]
    card_map = {c['id']: c for c in cards}
    if vat is None and revenue is not None:
        card_map['revenue']['title'] = 'Выручка брутто (НДС недоступен)'
    for cid, account_list in (
            ('cash', ('50', '51', '52', '55')),
            ('ar', ('62',)), ('ap', ('60',)),
            ('credits', ('66', '67')), ('salary', ('70',))):
        for account in account_list:
            note = _balance_provenance(source, account, end)
            if note:
                card_map[cid]['notes'].append(note)
    if profit is not None and profit < 0:
        card_map['profit']['status'] = 'bad'
        card_map['profit']['status_label'] = 'Убыток от продаж'
    if cogs is not None and revenue is not None and cogs > revenue:
        card_map['cogs']['status'] = 'warning'
        card_map['cogs']['status_label'] = 'Выше выручки'

    # Authentic monthly dynamics.  Missing monthly facts remain null; report
    # coverage alone is not proof of an explicit zero.
    month_options = _options(dates, 'month')
    for option in month_options:
        month_end = _iso(option['value'])
        month_start, _ = _range('month', month_end)
        month_pl = _pl_facts(source, month_start, month_end)
        month_values = {
            'revenue': month_pl['revenue'],
            'cogs': month_pl['cost'],
            'profit': month_pl['profit'],
        }
        rows08 = _turnover_basis(_daily(source, '08', month_start, month_end))
        month_values['capex'] = _sum(rows08, 'obd') if rows08 else None
        month_values['collection'] = None
        cash_month = []
        for account in ('50', '51', '52', '55'):
            value, _ = _top_balance(source, account, month_end)
            if value is not None:
                cash_month.append(value)
        month_values['cash'] = round(sum(cash_month), 2) if cash_month else None
        month_values['ar'] = _sub_balance(
            source, '62', '62.01', month_end)[0]
        month_values['ap'] = _sub_balance(
            source, '60', '60.01', month_end, credit=True)[0]
        debt_month = []
        for account in ('66', '67'):
            value, _ = _top_balance(source, account, month_end, credit=True)
            if value is not None:
                debt_month.append(value)
        month_values['credits'] = round(sum(debt_month), 2) if debt_month else None
        month_values['salary'] = _top_balance(
            source, '70', month_end, credit=True)[0]
        for cid in _CARD_ORDER:
            card_map[cid]['series'].append({
                'label': option['label'], 'value': month_values[cid]})

    # Compare only an immediately preceding equivalent selection with a
    # declared complete report span and identical calculation basis.
    selected_idx = next((i for i, item in enumerate(options)
                         if item['value'] == endpoint.isoformat()), -1)
    if selected_idx > 0:
        prev_end = _iso(options[selected_idx - 1]['value'])
        prev_start, _ = _range(period, prev_end)
        current_pl_complete = _account_covers(source, '90', start, end)
        previous_pl_complete = _account_covers(source, '90', prev_start, prev_end)
        previous_pl = _pl_facts(source, prev_start, prev_end)
        if (current_pl_complete and previous_pl_complete and
                pl['vat_basis'] == previous_pl['vat_basis']):
            for cid, key in (('revenue', 'revenue'), ('cogs', 'cost'),
                             ('profit', 'profit')):
                current, previous = card_map[cid]['value'], previous_pl[key]
                if current is not None and previous is not None:
                    card_map[cid]['delta'] = {
                        'value': round(current - previous, 2), 'unit': 'RUB',
                        'label': 'к предыдущему сопоставимому периоду'}
        rows08_prev = _turnover_basis(_daily(source, '08', prev_start, prev_end))
        if (_account_covers(source, '08', start, end) and
                _account_covers(source, '08', prev_start, prev_end) and
                d08 and rows08_prev):
            card_map['capex']['delta'] = {
                'value': round(capex - _sum(rows08_prev, 'obd'), 2),
                'unit': 'RUB', 'label': 'к предыдущему сопоставимому периоду'}
        else:
            card_map['capex']['notes'].append(
                'Сравнение скрыто: нет двух полностью сопоставимых периодов.')
        previous_cash = []
        for account in ('50', '51', '52', '55'):
            value, _ = _top_balance(source, account, prev_end)
            if value is not None:
                previous_cash.append((account, value))
        if ({a for a, _ in previous_cash} == {a for a, _ in cash_parts}
                and cash_parts):
            card_map['cash']['delta'] = {
                'value': round(cash - sum(v for _, v in previous_cash), 2),
                'unit': 'RUB', 'label': 'к предыдущей сопоставимой дате'}
        previous_ar = _sub_balance(source, '62', '62.01', prev_end)[0]
        previous_ap = _sub_balance(source, '60', '60.01', prev_end, credit=True)[0]
        if ar is not None and previous_ar is not None:
            card_map['ar']['delta'] = {
                'value': round(ar - previous_ar, 2), 'unit': 'RUB',
                'label': 'к предыдущей сопоставимой дате'}
        if ap is not None and previous_ap is not None:
            card_map['ap']['delta'] = {
                'value': round(ap - previous_ap, 2), 'unit': 'RUB',
                'label': 'к предыдущей сопоставимой дате'}
        previous_debt = []
        for account in ('66', '67'):
            value, _ = _top_balance(source, account, prev_end, credit=True)
            if value is not None:
                previous_debt.append((account, value))
        if ({a for a, _ in previous_debt} == {a for a, _ in credit_parts}
                and credit_parts):
            card_map['credits']['delta'] = {
                'value': round(credits - sum(v for _, v in previous_debt), 2),
                'unit': 'RUB', 'label': 'к предыдущей сопоставимой дате'}
        previous_salary = _top_balance(source, '70', prev_end, credit=True)[0]
        if salary is not None and previous_salary is not None:
            card_map['salary']['delta'] = {
                'value': round(salary - previous_salary, 2), 'unit': 'RUB',
                'label': 'к предыдущей сопоставимой дате'}

    # Object table uses one and the same path1 dimension for both sides.
    rev_group, vat_group, cost_group = defaultdict(float), defaultdict(float), defaultdict(float)
    for row in rev_rows:
        rev_group[_analytic_label(row)] += row['obk']
    for row in vat_rows:
        vat_group[_analytic_label(row)] += row['obd']
    for row in cost_rows:
        cost_group[_analytic_label(row)] += row['obd']
    segments = sorted(set(rev_group) | set(cost_group))
    vat_allocable = bool(vat_rows) and set(vat_group).issubset(set(rev_group))
    object_rows = []
    for segment in segments:
        has_revenue = segment in rev_group
        has_cost = segment in cost_group
        rv = rev_group.get(segment) if has_revenue else None
        if vat_allocable and rv is not None:
            rv -= vat_group.get(segment, 0.0)
        co = cost_group.get(segment) if has_cost else None
        pr = rv - co if (rv is not None and co is not None and vat is not None
                         and vat_allocable) else None
        object_rows.append({
            'label': segment,
            'revenue': round(rv, 2) if rv is not None else None,
            'cost': round(co, 2) if co is not None else None,
            'profit': round(pr, 2) if pr is not None else None,
            'margin': round(pr / rv * 100, 2) if pr is not None and rv else None,
        })
    obj_rev = revenue
    obj_cost = cogs
    obj_notes = []
    obj_reason = None
    if vat_rows and not vat_allocable:
        obj_reason = 'НДС нельзя надёжно распределить по той же аналитике path1'
        obj_notes.append('Таблица объектов показывает выручку брутто; карточка выручки сверяется отдельно.')
    elif not vat_rows and gross_rev is not None:
        obj_reason = 'Строки НДС 90.03 отсутствуют; выручка показана брутто'
        obj_notes.append('Прибыль и рентабельность объектов недоступны без НДС.')
    objects = {
        'rows': object_rows,
        'totals': {'revenue': obj_rev, 'cost': obj_cost,
                   'profit': profit,
                   'margin': round(profit / obj_rev * 100, 2)
                   if profit is not None and obj_rev else None},
        'reason': obj_reason, 'notes': obj_notes,
    }
    recommendations = []
    if profit is not None and profit < 0:
        recommendations.append({
            'id': 'sales-loss', 'severity': 'bad',
            'title': 'Отрицательная прибыль от продаж',
            'description': 'Проверьте состав выручки и себестоимости по убыточным объектам учёта.',
            'metric_id': 'profit',
        })
    if cogs is not None and revenue is not None and cogs > revenue:
        recommendations.append({
            'id': 'cost-above-revenue', 'severity': 'warning',
            'title': 'Себестоимость выше выручки',
            'description': 'Проверьте полноту начисления выручки и статьи себестоимости продаж.',
            'metric_id': 'cogs',
        })
    loaded = sorted(source['accounts'])
    required = ['08', '50/51/52/55', '60.01', '62.01', '66/67', '70',
                '90.01', '90.02', '90.03']
    missing = []
    if not rev_rows:
        missing.append('90.01')
    if not cost_rows:
        missing.append('90.02')
    if not vat_rows:
        missing.append('90.03')
    for c in cards:
        if c['value'] is None and c['id'] not in ('revenue', 'cogs', 'profit'):
            missing.append(c['id'])
    quality = {
        'accounts': loaded,
        'missing_accounts': list(dict.fromkeys(missing)),
        'warnings': list(dict.fromkeys(warnings)),
        'source_label': 'Фактические данные загруженных ОСВ',
    }
    methodology = [
        {'title': 'Обороты и сальдо',
         'text': 'Оборотные показатели фильтруются по датам. Сальдо используется только на явно поддерживаемую дату или исходную границу отчёта.'},
        {'title': 'Продажи',
         'text': 'Выручка — кредит 90.01 минус дебет 90.03; себестоимость — дебет 90.02. Родительские и дочерние итоги не суммируются.'},
        {'title': 'Недоступные данные',
         'text': 'Отсутствие счёта, аналитики или периода возвращает null с причиной, а не искусственный ноль.'},
    ]
    return {
        'period': {'kind': period, 'date': endpoint.isoformat(), 'label': label,
                   'start': start.isoformat(), 'end': end.isoformat(),
                   'options': options, 'available_kinds': available,
                   'warnings': list(dict.fromkeys(warnings))},
        'cards': cards, 'objects': objects, 'recommendations': recommendations,
        'quality': quality, 'methodology': methodology,
    }
