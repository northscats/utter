# -*- coding: utf-8 -*-
"""Pure, auditable calculations for the CFO key-indicator screen.

The module deliberately consumes a small snapshot rather than ``builder``'s
derived values.  This keeps the new screen independent from the legacy
dashboard formulas and rating.
"""
import calendar
import hashlib
import json
import re
from collections import defaultdict
from datetime import date as Date, timedelta


_BALANCE_ACCOUNTS = {'50', '51', '52', '55', '60', '62', '66', '67', '70'}
_CASH_ACCOUNTS = ('50', '51', '52', '55')
_CARD_ORDER = ('cash', 'revenue', 'collection', 'ar', 'ap', 'cogs',
               'profit', 'credits', 'salary', 'capex')
_TITLES = {
    'cash': 'Денежные средства',
    'revenue': 'Выручка',
    'collection': 'Собираемость (прокси по ОСВ)',
    'ar': 'Подтверждённая дебиторская задолженность',
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
REVENUE_CATEGORIES = (
    ('population', 'Население'),
    ('uk', 'УК/ТСЖ'),
    ('budget', 'Бюджет'),
    ('legal', 'Прочие юридические лица'),
    ('subsidy', 'Субсидии'),
)

COST_CATEGORIES = (
    ('fuel', 'Топливо'),
    ('electricity', 'Электроэнергия'),
    ('payroll', 'ФОТ'),
    ('depreciation', 'Амортизация'),
    ('repair', 'Ремонт'),
    ('reagents', 'Реагенты'),
    ('other', 'Прочее'),
)


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
    # 1C uses this form for a report covering the beginning of a year:
    # ``за 9 месяцев 2026 г.``.  It is a period total, not an unsupported
    # free-form header.  Keep the end at the last day of the stated month;
    # do not turn it into a set of fabricated daily observations.
    months_match = re.search(
        r'(?<!\d)(\d{1,2})\s*месяц(?:а|ев)?\b', text)
    if months_match:
        months = int(months_match.group(1))
        if 1 <= months <= 12:
            return Date(year, 1, 1), Date(
                year, months, calendar.monthrange(year, months)[1])
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
                'obd_known': isinstance(item.get('obd'), (int, float))
                and not isinstance(item.get('obd'), bool),
                'obk_known': isinstance(item.get('obk'), (int, float))
                and not isinstance(item.get('obk'), bool),
            }
            for key in ('snd', 'snk', 'skd', 'skk'):
                if item.get(key) is not None:
                    row[key] = _number(item[key])
            marker = tuple(row.get(k) for k in
                           ('date', 'path1', 'path2', 'path3', 'obd', 'obk',
                            'obd_known', 'obk_known',
                            'snd', 'snk', 'skd', 'skk'))
            if marker not in seen:
                seen.add(marker)
                daily.append(row)
                all_dates.add(dt)
        def hierarchy_rows(items):
            result = []
            stack = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                level = int(item.get('lvl') or 0)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                label = str(item.get('label') or '')
                hierarchy = [value for _, value in stack if value]
                if label:
                    hierarchy.append(label)
                result.append({
                    'lvl': level,
                    'label': label,
                    'hierarchy_path': hierarchy,
                    'p0': str(item.get('p0') or ''),
                    'p1': str(item.get('p1') or ''),
                    'p2': str(item.get('p2') or ''),
                    'p3': str(item.get('p3') or ''),
                    **{k: _number(item.get(k)) for k in
                       ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
                    **{f'{k}_known': (
                        isinstance(item.get(k), (int, float))
                        and not isinstance(item.get(k), bool))
                       for k in ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')},
                })
                stack.append((level, label))
            return result

        rows = hierarchy_rows(raw.get('rows') or [])
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
            report_rows = hierarchy_rows(report.get('rows') or [])
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


def _group_daily_by_date(account):
    """Build a date lookup without changing the source snapshot."""
    grouped = defaultdict(list)
    if not isinstance(account, dict):
        return grouped
    for row in account.get('daily') or []:
        date_text = str(row.get('date') or '')
        normalized = _iso(date_text)
        if normalized:
            grouped[normalized.isoformat()].append(row)
    return grouped


def _group_balance_by_date(account):
    """Index exact balance observations for this calculation only."""
    grouped = defaultdict(list)
    if not isinstance(account, dict):
        return grouped
    observations = (account.get('balance_observations') or []) + \
        (account.get('daily') or [])
    for row in observations:
        normalized = _iso(row.get('date'))
        if normalized:
            grouped[normalized.isoformat()].append(row)
    return grouped


def _daily(source, account, start, end):
    # ``build_key_indicators`` installs a per-request date index.  A year
    # chart has one slot per calendar day, so repeatedly walking every raw
    # row for every slot quickly becomes needlessly expensive.  The cache is
    # deliberately local to that build (the caller makes a shallow copy of
    # the immutable source snapshot); a shared source is never mutated.
    cache = source.get('_daily_cache')
    key = (str(account), start.isoformat(), end.isoformat())
    if cache is not None and key in cache:
        return cache[key]
    index = source.get('_daily_index')
    if index is not None:
        dated = index.get(str(account), {})
        if start == end:
            result = dated.get(start.isoformat(), [])
        else:
            result = [
                row for dt, rows in dated.items()
                if start.isoformat() <= dt <= end.isoformat()
                for row in rows
            ]
    else:
        result = [
            r for r in source['accounts'].get(account, {}).get('daily', [])
            if start <= _iso(r['date']) <= end
        ]
    if cache is not None:
        cache[key] = result
    return result


def _report_bounds(report):
    """Return normalized bounds for one accepted period report."""
    start = _iso(report.get('report_start'))
    end = _iso(report.get('report_end'))
    if start and end:
        return start, end
    return _period_bounds(report.get('period'))


def _report_precedence(report, index):
    """Sort key where a later source revision wins an equal interval."""
    updated = str(report.get('source_updated_at') or '')
    return updated, index


def _period_report_selection(source, account, start, end):
    """Select an exact report or a complete, disjoint report tiling.

    Reports are period totals and may not be prorated.  An exact report wins
    over a tiling (for example a 9-month report wins over nine monthly
    reports); duplicate intervals use the latest accepted source revision.
    Returning ``None`` is intentional: callers must keep using daily data or
    report an unavailable/partial result rather than silently summing a
    fragment of the selected period.
    """
    acc = source.get('accounts', {}).get(str(account))
    if not acc:
        return None
    prepared = []
    for index, report in enumerate(acc.get('balance_reports') or []):
        if not isinstance(report, dict):
            continue
        report_start, report_end = _report_bounds(report)
        if not report_start or not report_end:
            continue
        if report_start < start or report_end > end or report_start > report_end:
            continue
        prepared.append((index, report_start, report_end, report))
    if not prepared:
        return None

    exact = [
        item for item in prepared
        if item[1] == start and item[2] == end
    ]
    if exact:
        return [max(exact, key=lambda item: _report_precedence(item[3], item[0]))[3]]

    # Keep only the latest revision of each interval.  Different intervals
    # can still overlap; the search below accepts only a disjoint tiling.
    latest_by_span = {}
    for item in prepared:
        span = (item[1], item[2])
        previous = latest_by_span.get(span)
        if previous is None or _report_precedence(
                item[3], item[0]) > _report_precedence(previous[3], previous[0]):
            latest_by_span[span] = item
    by_start = defaultdict(list)
    for item in latest_by_span.values():
        by_start[item[1]].append(item)
    for candidates in by_start.values():
        candidates.sort(
            key=lambda item: (item[2], _report_precedence(item[3], item[0])),
            reverse=True)

    memo = {}

    def tile(cursor):
        if cursor == end + timedelta(days=1):
            return []
        if cursor in memo:
            return memo[cursor]
        for item in by_start.get(cursor, []):
            next_cursor = item[2] + timedelta(days=1)
            if next_cursor > end + timedelta(days=1):
                continue
            tail = tile(next_cursor)
            if tail is not None:
                memo[cursor] = [item] + tail
                return memo[cursor]
        memo[cursor] = None
        return None

    tiling = tile(start)
    return [item[3] for item in tiling] if tiling is not None else None


def _period_rows(source, account, start, end):
    """Return rows from an exact/complete period report, if one is usable."""
    reports = _period_report_selection(source, account, start, end)
    if reports is None:
        return None
    rows = []
    for report in reports:
        report_start, report_end = _report_bounds(report)
        report_rows = report.get('rows') or []
        if not report_rows and isinstance(report.get('top'), dict):
            # Empty ОСВ is still an authentic zero total.  It must not become
            # a fabricated daily movement, but it can satisfy an exact report
            # total for account-level details.
            report_rows = [{
                'lvl': 0,
                'label': str(account),
                'p0': str(account), 'p1': '', 'p2': '', 'p3': '',
                **report['top'],
            }]
        for row in report_rows:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item['date'] = report_end.isoformat()
            item['month'] = report_end.strftime('%Y-%m')
            item['_period_aggregate'] = True
            item['_period_start'] = report_start.isoformat()
            item['_period_end'] = report_end.isoformat()
            item['_period_source_file'] = str(report.get('source_file') or '')
            rows.append(item)
    return rows


def _movement_rows(source, account, start, end):
    """Use report totals when they cover the whole interval, else daily rows."""
    report_rows = _period_rows(source, account, start, end)
    return report_rows if report_rows is not None else _daily(
        source, account, start, end)


def _period_provenance(source, account, start, end):
    reports = _period_report_selection(source, account, start, end)
    if reports is None:
        return None
    intervals = []
    files = []
    for report in reports:
        report_start, report_end = _report_bounds(report)
        intervals.append({
            'start': report_start.isoformat(),
            'end': report_end.isoformat(),
        })
        if report.get('source_file'):
            files.append(str(report['source_file']))
    return {
        'kind': 'period_aggregate',
        'basis': 'ОСВ: периодные итоги',
        'intervals': intervals,
        'source_files': files,
        'note': 'Суточные движения не синтезируются и месяцы не распределяются.',
    }


def _combined_period_provenance(source, accounts, start, end):
    """Return aggregate provenance only when every contributor is period data."""
    accounts = list(dict.fromkeys(str(account) for account in accounts))
    if not accounts:
        return None
    evidence = [
        (account, _period_provenance(source, account, start, end))
        for account in accounts
    ]
    if any(provenance is None for _, provenance in evidence):
        return None
    intervals = []
    files = []
    for _, provenance in evidence:
        intervals.extend(provenance['intervals'])
        files.extend(provenance['source_files'])
    return {
        'kind': 'period_aggregate',
        'basis': 'ОСВ: периодные итоги',
        'accounts': accounts,
        'intervals': intervals,
        'source_files': list(dict.fromkeys(files)),
        'note': 'Суточные движения не синтезируются и месяцы не распределяются.',
    }


def _aggregate_series_slots(source, start, end):
    """Pick the most granular complete report tiling for chart slots."""
    candidates = []
    for account in source.get('accounts', {}):
        reports = _period_report_selection(source, account, start, end)
        if not reports:
            continue
        spans = [_report_bounds(report) for report in reports]
        if all(span[0] and span[1] for span in spans):
            candidates.append(spans)
    if not candidates:
        return None
    return max(candidates, key=len)


def _exact_balance_rows(source, account, end):
    """Return balance observations for one exact date in O(1) with an index."""
    indexed = source.get('_balance_index')
    if indexed is not None:
        return indexed.get(str(account), {}).get(end.isoformat(), ())
    acc = source['accounts'].get(account, {})
    observations = (acc.get('balance_observations') or []) + \
        (acc.get('daily') or [])
    return [
        row for row in observations
        if _iso(row.get('date')) == end
    ]


def _leaf_rows(rows, predicate):
    """Select the deepest account code per date and analytic coordinate."""
    matched = [r for r in rows if predicate(r)]
    # Period reports carry the hierarchy in p0..p3, while daily rows carry
    # account coordinates in path1..path3. Select only terminal rows for a
    # report subtotal without changing the long-standing daily-row behaviour.
    if any(r.get('_period_aggregate') for r in matched):
        def hierarchy_depth(row):
            depth = int(row.get('lvl') or 0)
            for index, key in enumerate(('p0', 'p1', 'p2', 'p3')):
                if row.get(key):
                    depth = max(depth, index)
            return depth

        # A report subtotal is only a parent of rows in that same report.
        # Without this partition, a January 90.01 subtotal can be discarded
        # because a deeper February 90.01 child happens to be present in the
        # selected report tiling.
        partitions = defaultdict(list)
        for row in matched:
            partition = (
                row.get('_period_start'),
                row.get('_period_end'),
                row.get('_period_source_file'),
            )
            partitions[partition].append(row)
        result = []
        for partition_rows in partitions.values():
            for row in partition_rows:
                row_level = hierarchy_depth(row)
                is_parent = False
                for child in partition_rows:
                    child_level = hierarchy_depth(child)
                    if child is row or child_level <= row_level:
                        continue
                    if all(
                        not row.get(key) or child.get(key) == row.get(key)
                        for key in ('p0', 'p1', 'p2', 'p3')
                    ):
                        is_parent = True
                        break
                if not is_parent:
                    result.append(row)
        return result
    grouped = defaultdict(list)
    for row in matched:
        paths = [row.get(k, '') for k in
                 ('p0', 'p1', 'p2', 'p3', 'path1', 'path2', 'path3')]
        analytics = tuple(x for x in paths
                          if x and not re.match(r'^\d{2}(?:\.\d+)*$', x))
        grouped[(row['date'], analytics)].append(row)
    result = []
    for candidates in grouped.values():
        depth = max(max((x.count('.') for x in
                         (r.get('p0', ''), r.get('p1', ''), r.get('p2', ''),
                          r.get('p3', ''), r.get('path1', ''),
                          r.get('path2', ''), r.get('path3', ''))
                         if re.match(r'^\d{2}(?:\.\d+)*$', x)), default=-1)
                    for r in candidates)
        result.extend(r for r in candidates
                      if max((x.count('.') for x in
                              (r.get('p0', ''), r.get('p1', ''), r.get('p2', ''),
                               r.get('p3', ''), r.get('path1', ''),
                               r.get('path2', ''), r.get('path3', ''))
                              if re.match(r'^\d{2}(?:\.\d+)*$', x)), default=-1) == depth)
    return result


def _has_prefix(row, prefix):
    return any(str(row.get(k) or '').startswith(prefix)
               for k in ('p0', 'p1', 'p2', 'p3',
                         'path1', 'path2', 'path3'))


def _analytic_label(row):
    values = [str(row.get(k) or '') for k in ('path1', 'path2', 'path3')]
    analytics = [x for x in values
                 if x and not re.match(r'^\d{2}(?:\.\d+)*$', x)]
    return analytics[0] if analytics else 'Без аналитики'


def _analytic_parts(row):
    keys = ('p0', 'p1', 'p2', 'p3') if any(
        row.get(k) for k in ('p0', 'p1', 'p2', 'p3')) else (
        'path1', 'path2', 'path3')
    values = [str(row.get(k) or '').strip() for k in keys]
    return [value for value in values
            if value and not re.match(r'^\d{2}(?:\.\d+)*$', value)]


def revenue_analytic_key(parts):
    encoded = json.dumps(
        list(parts), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _analytic_identity(row):
    parts = _analytic_parts(row)
    return revenue_analytic_key(parts), (
        ' / '.join(f'«{part}»' for part in parts)
        if parts else 'Без аналитики')


def _known_sum(rows, side):
    if any(row.get(f'{side}_known') is False for row in rows):
        return None
    return _sum(rows, side)


def _turnover_basis(rows):
    result = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row['date']].append(row)
    for dated in by_date.values():
        plain = [r for r in dated if not _analytic_parts(r)]
        result.extend(plain if plain else _leaf_rows(dated, lambda r: True))
    return result


def _sum(rows, side):
    return round(sum(r.get(side, 0.0) for r in rows), 2)


def _account_covers(source, account, start, end):
    acc = source['accounts'].get(account)
    if not acc:
        return False
    if _period_report_selection(source, account, start, end) is not None:
        return True
    spans = [(acc.get('report_start'), acc.get('report_end'))]
    spans += [(r.get('report_start'), r.get('report_end'))
              for r in acc.get('balance_reports') or []]
    spans += [(r.get('start'), r.get('end')) for r in acc.get('coverage') or []]
    return any(_iso(a) and _iso(b) and _iso(a) <= start and _iso(b) >= end
               for a, b in spans)


def _pl_facts(source, start, end):
    d90 = _movement_rows(source, '90', start, end)
    rev_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.01'))
    vat_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.03'))
    cost_rows = _leaf_rows(d90, lambda r: _has_prefix(r, '90.02'))
    gross = _known_sum(rev_rows, 'obk') if rev_rows else None
    vat = _known_sum(vat_rows, 'obd') if vat_rows else None
    revenue = round(gross - vat, 2) if gross is not None and vat is not None else gross
    cost = _known_sum(cost_rows, 'obd') if cost_rows else None
    # Gross revenue minus cost is not profit from sales.  VAT must be known.
    profit = round(revenue - cost, 2) if (gross is not None and vat is not None
                                          and cost is not None) else None
    return dict(rows=d90, rev_rows=rev_rows, vat_rows=vat_rows,
                cost_rows=cost_rows, gross=gross, vat=vat, revenue=revenue,
                cost=cost, profit=profit,
                vat_basis='net' if vat is not None else
                ('gross' if gross is not None else None))


def _report_scope_row(report, account, sub):
    """Find a report row for an explicitly exported subaccount."""
    rows = report.get('rows') or []
    matches = [
        row for row in rows
        if str(row.get('label') or '') == sub
        or sub in {str(row.get(key) or '') for key in
                   ('p0', 'p1', 'p2', 'p3')}
    ]
    if matches:
        # A numeric subaccount subtotal is preferred over its analytic leaves.
        return min(matches, key=lambda row: int(row.get('lvl') or 0))
    return None


def _sub_balance(source, account, sub, end, credit=False):
    acc = source['accounts'].get(account)
    if not acc:
        return None, f'Счёт {account} не загружен'
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    # A report at the exact endpoint is the latest accepted source for that
    # interval. Never add or prefer an overlapping older daily observation.
    if report is None:
        explicit = [r for r in _exact_balance_rows(source, account, end)
                    if any(k in r for k in ('skd', 'skk'))]
        specific = [r for r in explicit if sub in
                    (r.get('path1'), r.get('path2'), r.get('path3'))]
        if specific:
            specific = _leaf_rows(specific, lambda row: True)
            return round(sum(max(0.0, (r.get('skk', 0) - r.get('skd', 0))
                                     if credit else
                                     (r.get('skd', 0) - r.get('skk', 0)))
                             for r in specific), 2), None
    report_rows = report.get('rows', []) if report else acc['rows']
    if report is None and acc.get('report_end') != end.isoformat():
        return None, 'Сальдо доступно только на исходную границу отчёта'
    row = _report_scope_row(
        {'rows': report_rows}, account, sub) if report_rows else None
    if row is None:
        return None, f'Субсчёт {sub} отсутствует; значение не считается нулём'
    value = row['skk'] - row['skd'] if credit else row['skd'] - row['skk']
    return round(max(0.0, value), 2), None


def _sub_opening_balance(source, account, sub, start, credit=False):
    """Return a subaccount opening balance only from an exact report boundary."""
    acc = source['accounts'].get(account)
    if not acc:
        return None
    reports = list(acc.get('balance_reports') or [])
    if acc.get('report_start') == start.isoformat():
        reports.append({
            'report_start': acc.get('report_start'),
            'rows': acc.get('rows') or [],
        })
    report = next((item for item in reversed(reports)
                   if item.get('report_start') == start.isoformat()), None)
    row = _report_scope_row(
        {'rows': report.get('rows') if report else []}, account, sub)
    if not row:
        return None
    value = row.get('snk', 0) - row.get('snd', 0) if credit else \
        row.get('snd', 0) - row.get('snk', 0)
    return round(max(0.0, value), 2)

def _row_subaccount(row, root):
    codes = [str(row.get(key) or '') for key in
             ('p0', 'p1', 'p2', 'p3', 'path1', 'path2', 'path3')]
    matched = [code for code in codes
               if re.fullmatch(re.escape(root) + r'(?:\.\d+)+', code)]
    return max(matched, key=lambda value: value.count('.')) if matched else None
def _sub_balance_analytics(source, account, sub, end, credit=False):
    """Return non-duplicated analytic balances at an exact report end."""
    acc = source['accounts'].get(account)
    if not acc:
        return []
    observations = _exact_balance_rows(source, account, end)
    debtor_scope = sub == '62.01' or sub == '76'
    if not debtor_scope:
        report_at_end = next((
            item for item in reversed(acc.get('balance_reports') or [])
            if item.get('report_end') == end.isoformat()), None)
        explicit = [] if report_at_end is not None else [
            row for row in observations
            if _has_prefix(row, sub)
            and _analytic_parts(row)
            and any(key in row for key in ('skd', 'skk'))
        ]
        # Period reports do not enter the dated observation index. Read their
        # exact-end rows as balances too, but only for an explicit subaccount.
        if not explicit:
            report = report_at_end
            report_rows = report.get('rows') if report else (
                acc.get('rows') if acc.get('report_end') == end.isoformat()
                else None)
            if report_rows:
                explicit = [
                    row for row in report_rows
                    if _has_prefix(row, sub)
                    and _analytic_parts(row)
                    and any(key in row for key in ('skd', 'skk'))
                ]
                if not explicit:
                    # Keep authentic root-level analytic leaves visible as
                    # detail, but never use their net as the requested
                    # 60.01/62.01 balance or turnover.
                    root_present = any(
                        str(row.get('label') or '') == str(account)
                        and int(row.get('lvl') or 0) == 0
                        for row in report_rows)
                    if root_present:
                        explicit = [
                            row for row in report_rows
                            if _analytic_parts(row)
                            and any(key in row for key in ('skd', 'skk'))
                        ]
                explicit = [
                    {
                        **row,
                        'date': end.isoformat(),
                        '_period_aggregate': True,
                    }
                    for row in explicit
                ]
        basis = _leaf_rows(explicit, lambda row: True)
        grouped = defaultdict(list)
        labels = {}
        for row in basis:
            key, label = _analytic_identity(row)
            grouped[key].append(row)
            labels[key] = label
        result = []
        for key, rows in sorted(grouped.items(), key=lambda item: labels[item[0]]):
            value = round(sum(max(
                0.0,
                row.get('skk', 0) - row.get('skd', 0) if credit else
                row.get('skd', 0) - row.get('skk', 0))
                              for row in rows), 2)
            result.append({
                'analytic_key': key,
                'label': labels[key],
                'value': value,
            })
        return result
    report = next((item for item in reversed(acc.get('balance_reports') or [])
                   if item.get('report_end') == end.isoformat()), None)
    report_rows = None
    if report is not None:
        report_rows = report.get('rows') or []
    elif acc.get('report_end') == end.isoformat():
        report_rows = acc.get('rows') or []
    report_analytics = _debtor_report_analytics(report_rows, sub) \
        if report_rows is not None else []
    if report_analytics:
        return report_analytics
    explicit = [
        row for row in observations
        if (_has_prefix(row, sub) or any(
            str(row.get(key) or '').startswith(sub)
            for key in ('p0', 'p1', 'p2', 'p3')))
        and _analytic_parts(row)
        and any(key in row for key in ('skd', 'skk'))
    ]
    prepared = []
    for row in explicit:
        row_account = ('62.01' if sub == '62.01'
                       else _row_subaccount(row, '76'))
        if row_account:
            prepared.append((row, row_account, tuple(_analytic_parts(row))))
    basis = [
        (row, row_account, parts)
        for row, row_account, parts in prepared
        if not any(
            row_account == other_account
            and len(other_parts) > len(parts)
            and other_parts[:len(parts)] == parts
            for _, other_account, other_parts in prepared)
    ]
    grouped = defaultdict(list)
    labels = {}
    seen_balances = set()
    for row, subaccount, _ in basis:
        key, label = _analytic_identity(row)
        marker = (subaccount, key, row.get('skd'), row.get('skk'))
        if marker in seen_balances:
            continue
        seen_balances.add(marker)
        grouped[(subaccount, key)].append(row)
        labels[(subaccount, key)] = label
    result = []
    for (subaccount, key), rows in sorted(
            grouped.items(), key=lambda item: (item[0][0], labels[item[0]])):
        value = round(sum(max(0.0, row.get('skd', 0) - row.get('skk', 0))
                          for row in rows), 2)
        result.append({
            'account': subaccount,
            'analytic_key': key,
            'label': labels[(subaccount, key)],
            'value': value,
        })
    return result


def _sub_turnover(source, account, sub, start, end, side):
    """Return turnover for an exact subaccount without parent/child duplication."""
    rows = _movement_rows(source, account, start, end)
    basis = _leaf_rows(rows, lambda row: _has_prefix(row, sub))
    return _known_sum(basis, side) if basis else None


def _explicit_subaccount_rows(rows, sub):
    """Return terminal rows belonging to an explicit subaccount.

    A root 62 row is deliberately not a match for either 62.01 or 62.02.
    ОСВ exports may contain the subaccount subtotal beside its analytical
    children, so remove a numeric parent whenever a deeper row is present.
    This is kept separate from the generic turnover basis because the
    collection proxy has a strict, named four-component contract.
    """
    sub = str(sub)
    matched = []
    for row in rows:
        codes = [
            str(row.get(key) or '').strip()
            for key in ('p0', 'p1', 'p2', 'p3', 'path1', 'path2', 'path3')
        ]
        if any(code == sub or code.startswith(sub + '.') for code in codes):
            matched.append(row)
    if not matched:
        return []

    def numeric_codes(row):
        return [
            code for code in (
                str(row.get(key) or '').strip()
                for key in ('p0', 'p1', 'p2', 'p3', 'path1', 'path2', 'path3')
            )
            if re.fullmatch(r'\d{2}(?:\.\d+)+', code)
        ]

    partitions = defaultdict(list)
    for row in matched:
        partitions[(
            row.get('date'),
            row.get('_period_start'),
            row.get('_period_end'),
            row.get('_period_source_file'),
        )].append(row)
    result = []
    for partition_rows in partitions.values():
        codes_by_row = {id(row): numeric_codes(row) for row in partition_rows}
        analytics_by_row = {
            id(row): tuple(_analytic_parts(row)) for row in partition_rows
        }
        for row in partition_rows:
            row_codes = codes_by_row[id(row)]
            row_subcodes = [code for code in row_codes
                            if code == sub or code.startswith(sub + '.')]
            if not row_subcodes:
                continue
            row_code = max(row_subcodes, key=lambda code: code.count('.'))
            row_analytics = analytics_by_row[id(row)]
            has_child = False
            for child in partition_rows:
                if child is row:
                    continue
                child_codes = codes_by_row[id(child)]
                child_subcodes = [code for code in child_codes
                                  if code == sub or code.startswith(sub + '.')]
                if not child_subcodes:
                    continue
                child_code = max(child_subcodes, key=lambda code: code.count('.'))
                child_analytics = analytics_by_row[id(child)]
                if child_code.startswith(row_code + '.') or (
                        child_code == row_code and
                        ((not row_analytics and child_analytics) or
                         (row_analytics and
                          len(child_analytics) > len(row_analytics) and
                          child_analytics[:len(row_analytics)] ==
                          row_analytics))):
                    has_child = True
                    break
            if not has_child:
                result.append(row)
    return result


def _explicit_subaccount_turnover(source, sub, start, end):
    """Return both known sides of one explicit collection subaccount."""
    rows = _explicit_subaccount_rows(
        _movement_rows(source, '62', start, end), sub)
    return {
        'debit': _known_sum(rows, 'obd') if rows else None,
        'credit': _known_sum(rows, 'obk') if rows else None,
        'rows': rows,
    }


def _collection_components(source, start, end):
    """Read the four strict ОСВ components used by collection proxy."""
    values = {
        sub: _explicit_subaccount_turnover(source, sub, start, end)
        for sub in ('62.01', '62.02')
    }
    debit_01 = values['62.01']['debit']
    credit_01 = values['62.01']['credit']
    debit_02 = values['62.02']['debit']
    credit_02 = values['62.02']['credit']
    components = [
        {'account': '62.01', 'label': 'Дебетовый оборот 62.01',
         'side': 'debit', 'value': debit_01},
        {'account': '62.01', 'label': 'Кредитовый оборот 62.01',
         'side': 'credit', 'value': credit_01},
        {'account': '62.02', 'label': 'Дебетовый оборот 62.02',
         'side': 'debit', 'value': debit_02},
        {'account': '62.02', 'label': 'Кредитовый оборот 62.02',
         'side': 'credit', 'value': credit_02},
    ]
    missing = [item['label'] for item in components
               if item['value'] is None]
    value = None
    reason = None
    if missing:
        reason = 'Нет обязательных компонентов: ' + ', '.join(missing)
        for item in components:
            if item['value'] is None:
                item['reason'] = 'Обязательный компонент отсутствует или неизвестен'
    elif debit_01 == 0:
        reason = 'Дебетовый оборот 62.01 равен нулю; процент не рассчитывается'
        for item in components:
            if item['account'] == '62.01' and item['side'] == 'debit':
                item['reason'] = reason
    else:
        value = round(
            (credit_01 + credit_02 - debit_02) / debit_01 * 100, 2)
    return {
        'value': value,
        'reason': reason,
        'components': components,
        'accounts': values,
    }


def _top_balance(source, account, end, credit=False):
    acc = source['accounts'].get(account)
    if not acc:
        return None, f'Счёт {account} не загружен'
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    if report is None:
        top_index = source.get('_top_balance_index')
        if top_index is not None:
            row = top_index.get(str(account), {}).get(end.isoformat())
        else:
            explicit = [r for r in _exact_balance_rows(source, account, end)
                        if not any((r.get('path1'), r.get('path2'),
                                    r.get('path3')))
                        and any(k in r for k in ('skd', 'skk'))]
            row = explicit[-1] if explicit else None
        if row is not None:
            return round(max(0.0, row.get('skk', 0) - row.get('skd', 0)
                                 if credit else
                                 row.get('skd', 0) - row.get('skk', 0)), 2), None
    if report is not None:
        top = report['top']
    elif acc.get('report_end') == end.isoformat() and acc.get('top') is not None:
        top = acc['top']
    else:
        return None, 'Сальдо доступно только на исходную границу отчёта'
    return round(max(0.0, top['skk'] - top['skd']
                         if credit else top['skd'] - top['skk']), 2), None


def _cash_snapshot(source, end):
    """Return one exact-date cash snapshot with its account coverage.

    A balance from another date is not a valid component of this snapshot.
    Keeping the account set alongside the value makes it possible to avoid
    comparing totals whose composition changed between dates.
    """
    parts, reasons = [], []
    for account in _CASH_ACCOUNTS:
        value, reason = _top_balance(source, account, end)
        if value is not None:
            parts.append((account, value))
        elif account in source['accounts']:
            reasons.append(f'{account}: {reason}')
    accounts = [account for account, _ in parts]
    missing_accounts = [account for account in _CASH_ACCOUNTS
                        if account not in accounts]
    value = round(sum(amount for _, amount in parts), 2) if parts else None
    coverage = ('complete' if len(accounts) == len(_CASH_ACCOUNTS)
                else 'partial' if accounts else 'missing')
    return {
        'date': end.isoformat(),
        'value': value,
        'accounts': accounts,
        'missing_accounts': missing_accounts,
        'coverage': coverage,
        'parts': parts,
        'reasons': reasons,
    }


def _cash_observation_snapshot(source, end):
    """Return cash only from exact daily/balance observations.

    Unlike ``_cash_snapshot``, this deliberately does not consult
    ``balance_reports`` or an account's report-level ``top`` value.  It is
    used for daily chart slots so a monthly/period report cannot masquerade
    as a daily observation at its endpoint.
    """
    parts, reasons = [], []
    date_text = end.isoformat()
    index = source.get('_top_balance_index') or {}
    for account in _CASH_ACCOUNTS:
        row = index.get(account, {}).get(date_text)
        if row is not None:
            value = round(max(0.0, row.get('skd', 0) - row.get('skk', 0)), 2)
            parts.append((account, value))
        elif account in source['accounts']:
            reasons.append(f'{account}: Нет точного суточного остатка')
    accounts = [account for account, _ in parts]
    missing_accounts = [account for account in _CASH_ACCOUNTS
                        if account not in accounts]
    value = round(sum(amount for _, amount in parts), 2) if parts else None
    coverage = ('complete' if len(accounts) == len(_CASH_ACCOUNTS)
                else 'partial' if accounts else 'missing')
    return {
        'date': date_text,
        'value': value,
        'accounts': accounts,
        'missing_accounts': missing_accounts,
        'coverage': coverage,
        'parts': parts,
        'reasons': reasons,
    }


def _previous_cash_endpoint(period, end):
    """Return the exact calendar endpoint immediately before ``end``."""
    if period == 'day':
        return end - timedelta(days=1)
    if period == 'month':
        return end.replace(day=1) - timedelta(days=1)
    if period == 'quarter':
        quarter_start_month = ((end.month - 1) // 3) * 3 + 1
        return end.replace(month=quarter_start_month, day=1) - timedelta(days=1)
    if period == 'year':
        return end.replace(year=end.year - 1, month=12, day=31)
    return None


def _cash_comparison(current, previous=None, reason=None):
    """Compare two cash snapshots without bridging unavailable dates."""
    current_date = current['date']
    if previous is None:
        return {
            'comparable': False,
            'previous_date': None,
            'date': current_date,
            'accounts': list(current['accounts']),
            'value': None,
            'reason': reason or 'Нет предыдущей точки',
        }
    previous_date = previous['date']
    if reason is not None:
        return {
            'comparable': False,
            'previous_date': previous_date,
            'date': current_date,
            'accounts': list(current['accounts']),
            'value': None,
            'reason': reason,
        }
    if current['value'] is None or previous['value'] is None:
        reason = reason or 'Нет точного остатка'
    elif not current['accounts'] or not previous['accounts']:
        reason = reason or 'Нет точного остатка'
    elif current['accounts'] != previous['accounts']:
        reason = reason or 'Изменился состав счетов'
    else:
        return {
            'comparable': True,
            'previous_date': previous_date,
            'date': current_date,
            'accounts': list(current['accounts']),
            'value': round(current['value'] - previous['value'], 2),
            'reason': None,
        }
    return {
        'comparable': False,
        'previous_date': previous_date,
        'date': current_date,
        'accounts': list(current['accounts']),
        'value': None,
        'reason': reason,
    }


def _account_balance_analytics(source, account, end, credit=True):
    """Return leaf analytic balances from an exact account report boundary."""
    acc = source['accounts'].get(account)
    if not acc:
        return []
    observations = _exact_balance_rows(source, account, end)
    report = next((item for item in reversed(acc.get('balance_reports') or [])
                   if item.get('report_end') == end.isoformat()), None)
    explicit = [] if report is not None else [
        row for row in observations
        if any((row.get('path1'), row.get('path2'), row.get('path3')))
        and any(key in row for key in ('skd', 'skk'))
    ]
    if explicit:
        basis = _leaf_rows(explicit, lambda row: True)
        return [{
            'account': account,
            'label': _analytic_identity(row)[1],
            'value': round(max(0.0, row.get('skk', 0) - row.get('skd', 0)
                               if credit else
                               row.get('skd', 0) - row.get('skk', 0)), 2),
            'credit_balance': round(max(0.0, row.get('skk', 0) - row.get('skd', 0)), 2),
            'debit_balance': round(max(0.0, row.get('skd', 0) - row.get('skk', 0)), 2),
            'debit_turnover': row.get('obd') if row.get('obd_known') is not False else None,
            'credit_turnover': row.get('obk') if row.get('obk_known') is not False else None,
        } for row in basis]
    rows = report.get('rows', []) if report else (
        acc.get('rows', []) if acc.get('report_end') == end.isoformat() else [])
    if report is not None:
        report_basis = [
            {
                **row,
                'date': end.isoformat(),
                '_period_aggregate': True,
            }
            for row in rows if _analytic_parts(row)
        ]
        if report_basis:
            rows = _leaf_rows(report_basis, lambda row: True)
            return [{
                'account': account,
                'label': _analytic_identity(row)[1],
                'value': round(max(
                    0.0, row.get('skk', 0) - row.get('skd', 0)
                    if credit else row.get('skd', 0) - row.get('skk', 0)), 2),
                'credit_balance': round(max(
                    0.0, row.get('skk', 0) - row.get('skd', 0)), 2),
                'debit_balance': round(max(
                    0.0, row.get('skd', 0) - row.get('skk', 0)), 2),
                'debit_turnover': row.get('obd'),
                'credit_turnover': row.get('obk'),
            } for row in rows]
    result = []
    for index, row in enumerate(rows):
        level = row.get('lvl', 0)
        next_level = rows[index + 1].get('lvl', 0) if index + 1 < len(rows) else -1
        if level <= 0 or next_level > level:
            continue
        parts = [row.get(key) for key in ('p1', 'p2', 'p3') if row.get(key)]
        label = ' › '.join(parts) if parts else row.get('label') or 'Без аналитики'
        result.append({
            'account': account,
            'label': label,
            'value': round(max(0.0, row.get('skk', 0) - row.get('skd', 0)
                               if credit else
                               row.get('skd', 0) - row.get('skk', 0)), 2),
            'credit_balance': round(max(0.0, row.get('skk', 0) - row.get('skd', 0)), 2),
            'debit_balance': round(max(0.0, row.get('skd', 0) - row.get('skk', 0)), 2),
            'debit_turnover': row.get('obd'),
            'credit_turnover': row.get('obk'),
        })
    return result


def _top_opening_balance(source, account, start, credit=False):
    """Return an account opening balance only from an exact report boundary."""
    acc = source['accounts'].get(account)
    if not acc:
        return None
    reports = list(acc.get('balance_reports') or [])
    if acc.get('report_start') == start.isoformat() and acc.get('top') is not None:
        reports.append({
            'report_start': acc.get('report_start'),
            'top': acc.get('top'),
        })
    report = next((item for item in reversed(reports)
                   if item.get('report_start') == start.isoformat()
                   and item.get('top') is not None), None)
    if not report:
        return None
    top = report['top']
    value = top.get('snk', 0) - top.get('snd', 0) if credit else \
        top.get('snd', 0) - top.get('snk', 0)
    return round(max(0.0, value), 2)


def _account_turnovers(source, account, start, end):
    rows = _movement_rows(source, account, start, end)
    if any(row.get('_period_aggregate') for row in rows):
        # Report hierarchies keep account/subaccount codes in p0..p3.  The
        # terminal row is the only movement amount to add; summing the root,
        # subaccount, and analytic leaf would double count a period total.
        basis = _leaf_rows(rows, lambda row: True)
    else:
        plain = [row for row in rows if not _analytic_parts(row)]
        basis = plain if plain else _turnover_basis(rows)
    return {
        'debit': _known_sum(basis, 'obd') if basis else None,
        'credit': _known_sum(basis, 'obk') if basis else None,
    }


def _balance_provenance(source, account, end):
    acc = source['accounts'].get(account, {})
    report = next((r for r in reversed(acc.get('balance_reports') or [])
                   if r.get('report_end') == end.isoformat()), None)
    if report:
        parts = [report.get('period'), report.get('source_file')]
    elif acc.get('report_end') == end.isoformat():
        parts = [acc.get('period'), acc.get('source_file')]
    elif _exact_balance_rows(source, account, end):
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


def build_key_indicators(source, period='month', date=None, date_from=None,
                         revenue_mappings=None, cost_mappings=None,
                         debtor_mappings=None):
    """Calculate the strict JSON response contract from a source snapshot."""
    if not isinstance(source, dict) or source.get('version') != 1 \
            or not isinstance(source.get('accounts'), dict):
        raise ValueError('Unsupported indicator source')
    # Keep the public snapshot reusable and JSON-safe while giving this
    # calculation its own small lookup cache.  Temporal charts can contain
    # 365 daily slots even for a yearly selection; without the index every
    # slot rescans all rows in every account.
    source = dict(source)
    source['_daily_index'] = {
        str(account): {
            date_text: list(rows)
            for date_text, rows in _group_daily_by_date(account_data).items()
        }
        for account, account_data in source['accounts'].items()
    }
    source['_balance_index'] = {}
    source['_top_balance_index'] = {}
    for account, account_data in source['accounts'].items():
        balance_groups = _group_balance_by_date(account_data)
        source['_balance_index'][str(account)] = {
            date_text: list(rows)
            for date_text, rows in balance_groups.items()
        }
        source['_top_balance_index'][str(account)] = {
            date_text: next((
                row for row in reversed(rows)
                if not any((row.get('path1'), row.get('path2'),
                            row.get('path3')))
                and any(key in row for key in ('skd', 'skk'))
            ), None)
            for date_text, rows in balance_groups.items()
        }
    source['_daily_cache'] = {}
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
    range_start_endpoint = endpoint
    if period in ('day', 'month', 'quarter') and date_from is not None:
        range_start_endpoint = _iso(date_from)
        option_values = {x['value'] for x in options}
        if range_start_endpoint is None or date_from not in option_values:
            raise ValueError('date_from is not an available option')
        if range_start_endpoint > endpoint:
            raise ValueError('date_from must not be after date')
        start, _ = _range(period, range_start_endpoint)
        start_label = next(
            x['label'] for x in options if x['value'] == date_from)
        label = (
            label if range_start_endpoint == endpoint
            else f'{start_label} — {label}')
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
    revenue, cogs, gross_profit = pl['revenue'], pl['cost'], pl['profit']
    cogs_analytics = []
    cogs_groups = defaultdict(list)
    cogs_labels = {}
    for row in cost_rows:
        key, analytic_label = _analytic_identity(row)
        cogs_groups[key].append(row)
        cogs_labels[key] = analytic_label
    for key, rows in sorted(
            cogs_groups.items(), key=lambda item: cogs_labels[item[0]]):
        cogs_analytics.append({
            'analytic_key': key,
            'label': cogs_labels[key],
            'value': _known_sum(rows, 'obd'),
        })
    cost_movements = []
    for account, movement_label in (
            ('20', 'Основное производство'),
            ('25', 'Общепроизводственные затраты'),
            ('26', 'Общехозяйственные затраты'),
            ('44', 'Расходы на продажу')):
        movement_rows = _turnover_basis(
            _movement_rows(source, account, start, end))
        cost_movements.append({
            'account': account,
            'label': movement_label,
            'value': _known_sum(movement_rows, 'obd') if movement_rows else None,
        })
    recognized_costs = [
        {
            'account': '90.02',
            'label': 'Себестоимость продаж',
            'value': cogs,
        },
        {
            'account': '90.07',
            'label': 'Признанные расходы на продажу',
            'value': _sub_turnover(
                source, '90', '90.07', start, end, 'obd'),
        },
        {
            'account': '90.08',
            'label': 'Признанные управленческие расходы',
            'value': _sub_turnover(
                source, '90', '90.08', start, end, 'obd'),
        },
    ]
    recognized_by_account = {
        item['account']: item['value'] for item in recognized_costs}
    applicable_cost_mappings = defaultdict(list)
    for item in cost_mappings or []:
        if not isinstance(item, dict):
            continue
        account = str(item.get('account') or '')
        analytic_key = str(item.get('analytic_key') or '')
        category = str(item.get('category') or '')
        target_account = str(item.get('target_account') or '')
        valid_from = _iso(item.get('valid_from'))
        valid_to = _iso(item.get('valid_to'))
        if (account in ('20', '25', '26', '44') and analytic_key
                and category in {key for key, _ in COST_CATEGORIES}
                and target_account in ('90.02', '90.07', '90.08')
                and valid_from and valid_to
                and valid_from <= end and valid_to >= start):
            applicable_cost_mappings[(account, analytic_key)].append({
                'category': category,
                'target_account': target_account,
                'valid_from': valid_from,
                'valid_to': valid_to,
            })

    forming_groups = defaultdict(list)
    forming_meta = {}
    for account in ('20', '25', '26', '44'):
        rows = _leaf_rows(
            _movement_rows(source, account, start, end),
            lambda row: bool(_analytic_parts(row)))
        for row in rows:
            analytic_key, analytic_label = _analytic_identity(row)
            identity = (account, analytic_key)
            row_date = _iso(row.get('date'))
            aggregate_mapping = bool(row.get('_period_aggregate'))
            rules = [
                rule for rule in applicable_cost_mappings.get(identity, [])
                if (
                    (aggregate_mapping and
                     rule['valid_from'] <= start and
                     rule['valid_to'] >= end)
                    or
                    (not aggregate_mapping and row_date and
                     rule['valid_from'] <= row_date <= rule['valid_to'])
                )]
            choices = {
                (rule['category'], rule['target_account']) for rule in rules}
            overlapping_rules = [
                rule for rule in applicable_cost_mappings.get(identity, [])
                if rule['valid_from'] <= end and rule['valid_to'] >= start
            ]
            temporal_conflict = (
                aggregate_mapping and bool(overlapping_rules) and
                any(not (rule['valid_from'] <= start and
                         rule['valid_to'] >= end)
                    for rule in overlapping_rules))
            conflict = len(choices) > 1 or temporal_conflict
            rule = None
            if len(choices) == 1 and not conflict:
                rule = max(
                    rules, key=lambda item: (item['valid_from'], item['valid_to']))
            segment = (
                account, analytic_key,
                rule['category'] if rule else '',
                rule['target_account'] if rule else '',
                rule['valid_from'].isoformat() if rule else '',
                rule['valid_to'].isoformat() if rule else '',
                conflict,
            )
            forming_groups[segment].append(row)
            forming_meta[segment] = {
                'identity': identity,
                'analytic_label': analytic_label,
                'rule': rule,
                'conflict': conflict,
                'choices': choices,
            }

    cost_mapping_conflicts = []
    forming_analytics = []
    category_amounts = defaultdict(float)
    selected_targets = set()
    for segment, rows in sorted(
            forming_groups.items(),
            key=lambda item: (item[0][0],
                              forming_meta[item[0]]['analytic_label'],
                              min(row['date'] for row in item[1]))):
        meta = forming_meta[segment]
        identity = meta['identity']
        rule = meta['rule']
        conflict = meta['conflict']
        segment_dates = sorted(row['date'] for row in rows)
        if conflict:
            cost_mapping_conflicts.append({
                'account': identity[0],
                'analytic_key': identity[1],
                'analytic_label': meta['analytic_label'],
                'message': (
                    'На выбранный период действуют противоречивые или '
                    'неполные правила: '
                    + ', '.join('/'.join(choice)
                                for choice in sorted(meta['choices']))),
            })
        value = _known_sum(rows, 'obd')
        if rule and value is not None:
            category_amounts[rule['category']] += value
            selected_targets.add(rule['target_account'])
        forming_analytics.append({
            'account': identity[0],
            'analytic_key': identity[1],
            'analytic_label': meta['analytic_label'],
            'value': value,
            'amount_known': value is not None,
            'category': rule['category'] if rule else None,
            'target_account': rule['target_account'] if rule else None,
            'valid_from': (
                rule['valid_from'].isoformat() if rule else segment_dates[0]),
            'valid_to': (
                rule['valid_to'].isoformat() if rule else segment_dates[-1]),
            'conflict': conflict,
        })
    unclassified_costs = [
        item for item in forming_analytics
        if not item['category'] and not item['conflict']]
    unknown_cost_amounts = [
        item for item in forming_analytics if not item['amount_known']]
    recognized_target_values = [
        recognized_by_account[target] for target in selected_targets]
    selected_recognized_total = (
        round(sum(recognized_target_values), 2)
        if selected_targets and all(value is not None
                                    for value in recognized_target_values)
        else None)
    classified_cost_total = round(sum(category_amounts.values()), 2)
    exceeds_total = (
        selected_recognized_total is not None
        and classified_cost_total > selected_recognized_total + 0.01)
    if exceeds_total:
        cost_mapping_conflicts.append({
            'message': (
                'Сумма классифицированных формирующих затрат превышает '
                'выбранный признанный итог'),
            'classified_total': classified_cost_total,
            'recognized_total': selected_recognized_total,
        })
    cost_structure_valid = (
        bool(forming_analytics) and not unclassified_costs
        and not unknown_cost_amounts and not cost_mapping_conflicts
        and selected_recognized_total is not None)
    cost_categories = [{
        'id': category,
        'label': title,
        'value': round(category_amounts[category], 2)
        if category in category_amounts else 0.0,
        'share': (
            round(category_amounts[category] / selected_recognized_total * 100, 2)
            if cost_structure_valid and selected_recognized_total else None),
    } for category, title in COST_CATEGORIES]
    cost_structure = {
        'categories': cost_categories,
        'analytics': forming_analytics,
        'unclassified': unclassified_costs,
        'unknown_amounts': unknown_cost_amounts,
        'conflicts': cost_mapping_conflicts,
        'selected_targets': sorted(selected_targets),
        'classified_total': classified_cost_total,
        'recognized_total': selected_recognized_total,
        'difference': (
            round(selected_recognized_total - classified_cost_total, 2)
            if selected_recognized_total is not None else None),
        'reconciled': (
            cost_structure_valid
            and abs(selected_recognized_total - classified_cost_total) <= 0.01),
        'structure_valid': cost_structure_valid,
    }
    selling_expense = recognized_costs[1]['value']
    admin_expense = recognized_costs[2]['value']
    profit_from_sales = (
        round(gross_profit - selling_expense - admin_expense, 2)
        if gross_profit is not None
        and selling_expense is not None
        and admin_expense is not None else None)
    other_income = _sub_turnover(
        source, '91', '91.01', start, end, 'obk')
    other_expense = _sub_turnover(
        source, '91', '91.02', start, end, 'obd')
    other_result = (
        round(other_income - other_expense, 2)
        if other_income is not None and other_expense is not None else None)
    profit_before_tax = (
        round(profit_from_sales + other_result, 2)
        if profit_from_sales is not None and other_result is not None else None)
    profit_display = (
        profit_from_sales if profit_from_sales is not None else gross_profit)

    other_analytics_map = {}
    for sub, side, kind in (
            ('91.01', 'obk', 'income'),
            ('91.02', 'obd', 'expense')):
        rows = _leaf_rows(
            _movement_rows(source, '91', start, end),
            lambda row, prefix=sub: _has_prefix(row, prefix))
        grouped = defaultdict(list)
        labels = {}
        for row in rows:
            key, label = _analytic_identity(row)
            grouped[key].append(row)
            labels[key] = label
        for key, group in grouped.items():
            item = other_analytics_map.setdefault(
                key, {'analytic_key': key, 'label': labels[key],
                      'income': None, 'expense': None})
            item[kind] = _known_sum(group, side)
    other_analytics = sorted(
        other_analytics_map.values(), key=lambda item: item['label'])

    cash_snapshot = _cash_snapshot(source, end)
    cash_parts = cash_snapshot['parts']
    cash = cash_snapshot['value']
    cash_reason = (
        None if cash_parts else
        ('; '.join(cash_snapshot['reasons']) or
         'Счета 50/51/52/55 не загружены'))
    cash_missing = cash_snapshot['missing_accounts']
    cash_notes = []
    if cash_parts:
        cash_notes.append('Поддерживаемый охват: счета ' +
                          ', '.join(a for a, _ in cash_parts) + '.')
        if cash_missing:
            cash_notes.append('Частичный охват: нет поддерживаемого сальдо по счетам ' +
                              ', '.join(cash_missing) + '; они не приняты за ноль.')

    d62 = _movement_rows(source, '62', start, end)
    collection_data = _collection_components(source, start, end)
    collection = collection_data['value']
    collection_reason = collection_data['reason']
    collection_components = collection_data['components']
    # Keep the account-level component rows as the auditable analytics.  They
    # are intentionally not inferred from root 62 or from payment registers.
    collection_analytics = []
    for sub in ('62.01', '62.02'):
        item = collection_data['accounts'][sub]
        collection_analytics.append({
            'account': sub,
            'analytic_key': None,
            'label': f'Субсчёт {sub}',
            'debit': item['debit'],
            'credit': item['credit'],
            'turnover_ratio': (
                round(item['credit'] / item['debit'] * 100, 2)
                if item['debit'] not in (None, 0)
                and item['credit'] is not None else None),
        })
    turnover_ratio = collection

    ar_62, ar_reason = _sub_balance(source, '62', '62.01', end)
    ar_opening = _sub_opening_balance(source, '62', '62.01', start)
    ar_62_analytics = _sub_balance_analytics(source, '62', '62.01', end)
    ar_76_analytics = _sub_balance_analytics(source, '76', '76', end)
    active_debtor_mappings = defaultdict(set)
    known_debtor_mapping_keys = set()
    overlapping_debtor_mappings = defaultdict(list)
    for item in debtor_mappings or []:
        if not isinstance(item, dict):
            continue
        account = str(item.get('account') or '')
        analytic_key = str(item.get('analytic_key') or '')
        category = str(item.get('category') or '')
        effective_from = _iso(item.get('effective_from'))
        effective_to = _iso(item.get('effective_to'))
        if ((account == '62.01' or re.fullmatch(r'76(?:\.\d+)+', account))
                and analytic_key
                and category in {key for key, _ in REVENUE_CATEGORIES}
                and effective_from and effective_to):
            mapping_key = (account, analytic_key)
            known_debtor_mapping_keys.add(mapping_key)
            if effective_from <= end and effective_to >= start:
                overlapping_debtor_mappings[mapping_key].append({
                    'category': category,
                    'covers_interval': (
                        effective_from <= start and effective_to >= end),
                })
            # A period aggregate must have one unambiguous mapping for its
            # whole interval. Endpoint-only validity would classify an
            # aggregate report using a mid-period rule.
            if effective_from <= start and effective_to >= end:
                active_debtor_mappings[mapping_key].add(category)
    ar_analytics = []
    debtor_conflicts = []
    debtor_new = []
    debtor_unmapped = []
    debtor_categories = defaultdict(float)
    allowed_76 = 0.0
    active_mapping_records = []
    for item in debtor_mappings or []:
        effective_from = _iso(item.get('effective_from'))
        effective_to = _iso(item.get('effective_to'))
        if effective_from and effective_to and \
                effective_from <= start and effective_to >= end:
            active_mapping_records.append({
                'id': item.get('id'),
                'account': str(item.get('account') or ''),
                'analytic_key': str(item.get('analytic_key') or ''),
                'analytic_label': str(item.get('analytic_label') or ''),
                'category': str(item.get('category') or ''),
                'effective_from': effective_from.isoformat(),
                'effective_to': effective_to.isoformat(),
            })
    for account_root, analytics in (
            ('62.01', ar_62_analytics), ('76', ar_76_analytics)):
        for item in analytics:
            account = item.get('account') or account_root
            categories = active_debtor_mappings.get(
                (account, item['analytic_key']), set())
            mapping_key = (account, item['analytic_key'])
            overlapping = overlapping_debtor_mappings.get(mapping_key, [])
            overlap_categories = {
                record['category'] for record in overlapping}
            temporal_conflict = (
                bool(overlapping) and
                any(not record['covers_interval']
                    for record in overlapping))
            conflict = temporal_conflict or len(overlap_categories) > 1
            category = (
                next(iter(categories))
                if len(categories) == 1 and not conflict else None)
            classified = {
                **item, 'account': account, 'category': category,
                'included_in_total': account == '62.01' or category is not None,
            }
            ar_analytics.append(classified)
            if conflict:
                conflict = {
                    **classified, 'categories': sorted(categories),
                    'message': (
                        'Для аналитики действуют конфликтующие или '
                        'не покрывающие весь выбранный период правила'),
                }
                debtor_conflicts.append(conflict)
            if category is None:
                if (account, item['analytic_key']) in known_debtor_mapping_keys:
                    debtor_unmapped.append(classified)
                else:
                    debtor_new.append(classified)
            else:
                debtor_categories[category] += item['value']
                if account.startswith('76.'):
                    allowed_76 += item['value']
    if ar_62 is not None:
        detailed_62 = round(sum(
            item['value'] for item in ar_62_analytics), 2)
        residual_62 = round(ar_62 - detailed_62, 2)
        if abs(residual_62) > 0.01:
            debtor_unmapped.append({
                'account': '62.01',
                'analytic_key': None,
                'label': 'Остаток 62.01 без доступной аналитической детализации',
                'value': residual_62,
                'category': None,
                'included_in_total': True,
                'synthetic_residual': True,
            })
    ar = (round(ar_62 + allowed_76, 2)
          if ar_62 is not None else None)
    classified_ar = round(sum(debtor_categories.values()), 2)
    debtor_category_rows = [{
        'id': key, 'label': title,
        'value': round(debtor_categories[key], 2)
        if key in debtor_categories else None,
    } for key, title in REVENUE_CATEGORIES]
    debtor_difference = (
        round(ar - classified_ar, 2) if ar is not None else None)
    debtor_segments = {
        'categories': debtor_category_rows,
        'analytics': ar_analytics,
        'mapping_records': active_mapping_records,
        'new': debtor_new,
        'unmapped': debtor_unmapped,
        'conflicts': debtor_conflicts,
        'base_62_01': ar_62,
        'included_76': round(allowed_76, 2),
        'classified_total': classified_ar,
        'confirmed_total': ar,
        'reconciliation_difference': debtor_difference,
        'reconciled': (debtor_difference is not None
                       and abs(debtor_difference) <= 0.01
                       and not debtor_conflicts),
        'effective_from': start.isoformat(),
        'effective_to': end.isoformat(),
    }
    ap, ap_reason = _sub_balance(source, '60', '60.01', end, credit=True)
    ap_opening = _sub_opening_balance(
        source, '60', '60.01', start, credit=True)
    ap_analytics = _sub_balance_analytics(
        source, '60', '60.01', end, credit=True)
    ap_credit_turnover = _sub_turnover(
        source, '60', '60.01', start, end, 'obk')
    ap_components = [{
        'account': '60.01',
        'label': 'Поставщики и подрядчики',
        'value': ap,
        'reason': ap_reason,
    }]
    for account, label in (
            ('68', 'Налоги и сборы'),
            ('69', 'Страховые взносы'),
            ('70', 'Расчёты с персоналом')):
        value, reason = _top_balance(source, account, end, credit=True)
        ap_components.append({
            'account': account,
            'label': label,
            'value': value,
            'reason': reason,
        })
    known_ap_values = [
        item['value'] for item in ap_components if item['value'] is not None]
    known_ap_total = (
        round(sum(known_ap_values), 2) if known_ap_values else None)
    credit_parts = []
    credit_reasons = []
    credit_accounts = []
    credit_analytics = []
    for account in ('66', '67'):
        value, why = _top_balance(source, account, end, credit=True)
        account_daily = _movement_rows(source, account, start, end)
        plain_turnovers = [
            row for row in account_daily
            if not _analytic_parts(row)
        ]
        turnover_rows = (
            plain_turnovers if plain_turnovers else
            _turnover_basis(account_daily))
        debit_turnover = (
            _known_sum(turnover_rows, 'obd') if turnover_rows else None)
        credit_turnover = (
            _known_sum(turnover_rows, 'obk') if turnover_rows else None)
        if value is not None:
            credit_parts.append((account, value))
        elif account in source['accounts']:
            credit_reasons.append(f'{account}: {why}')
        credit_accounts.append({
            'account': account,
            'label': ('Краткосрочные кредиты и займы' if account == '66'
                      else 'Долгосрочные кредиты и займы'),
            'value': value,
            'reason': why,
            'debit_turnover': debit_turnover,
            'credit_turnover': credit_turnover,
        })
        credit_analytics.extend(
            _account_balance_analytics(source, account, end))
    credits = round(sum(x[1] for x in credit_parts), 2) if credit_parts else None
    for item in credit_accounts:
        item['share'] = (
            round(item['value'] / credits * 100, 2)
            if item['value'] is not None and credits else None)
    credits_reason = None if credit_parts else ('; '.join(credit_reasons) or 'Счета 66/67 не загружены')
    salary, salary_reason = _top_balance(source, '70', end, credit=True)
    salary_debit = _top_balance(source, '70', end)[0]
    salary_turnovers = _account_turnovers(source, '70', start, end)
    salary_analytics = _account_balance_analytics(source, '70', end)
    insurance_credit = _top_balance(source, '69', end, credit=True)[0]
    insurance_debit = _top_balance(source, '69', end)[0]
    insurance_turnovers = _account_turnovers(source, '69', start, end)
    insurance_analytics = _account_balance_analytics(source, '69', end)
    insurance_ratio = (
        round(insurance_turnovers['credit'] / salary_turnovers['credit'] * 100, 2)
        if insurance_turnovers['credit'] is not None
        and salary_turnovers['credit'] not in (None, 0) else None)
    capex_turnovers = _account_turnovers(source, '08', start, end)
    capex = capex_turnovers['debit']
    capex_reason = (
        None if capex is not None else
        'Нет суточного дебетового оборота счёта 08')
    capex_opening = _top_opening_balance(source, '08', start)
    capex_closing = _top_balance(source, '08', end)[0]
    capex_credit_balance = _top_balance(source, '08', end, credit=True)[0]
    capex_analytics = _account_balance_analytics(
        source, '08', end, credit=False)
    for item in capex_analytics:
        item['turnover_share'] = (
            round(item['debit_turnover'] / capex_turnovers['debit'] * 100, 2)
            if item['debit_turnover'] is not None
            and capex_turnovers['debit'] not in (None, 0) else None)
        item['balance_share'] = (
            round(item['debit_balance'] / capex_closing * 100, 2)
            if capex_closing not in (None, 0) else None)

    cards = [
        _card('cash', cash, 'RUB', 'СКд − СКк по счетам 50, 51, 52, 55',
              'Конечные сальдо ОСВ', cash_reason,
              # Keep all supported accounts selectable.  A missing account is
              # ``None`` (never an invented zero), while the headline still
              # sums only the authentic parts below.
              breakdown=[{
                  'label': f'Счёт {account}',
                  'value': dict(cash_parts).get(account),
              } for account in _CASH_ACCOUNTS],
              date=cash_snapshot['date'],
              accounts=list(cash_snapshot['accounts']),
              missing_accounts=list(cash_snapshot['missing_accounts']),
              coverage=cash_snapshot['coverage'],
              comparison=None,
              notes=cash_notes),
        _card('revenue', revenue, 'RUB',
              'ОбК(90.01*) − ОбД(90.03*)' if vat is not None else 'ОбК(90.01*), брутто',
              'Суточные листовые обороты счёта 90',
              None if revenue is not None else 'Нет строк точного префикса 90.01',
              details=[{'label': 'Выручка брутто', 'value': gross_rev, 'unit': 'RUB'},
                       {'label': 'НДС', 'value': vat, 'unit': 'RUB',
                        **({'reason': 'Строки 90.03 отсутствуют'} if vat is None else {})}]),
        _card('collection', collection, '%',
              '(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%',
              'Явные обороты ОСВ 62.01 и 62.02',
              collection_reason,
               measurement_basis='accounting_collection_proxy',
               linked_payments_available=False,
               accounting_proxy={
                   'value': collection,
                   'formula': '(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%',
                   'source': 'Явные обороты ОСВ 62.01 и 62.02',
               },
               collection_components=collection_components,
               components=collection_components,
               turnover_ratio=turnover_ratio,
               turnover_series=[],
               analytics=collection_analytics,
               details=collection_components + [
                   {'label': 'Связанные оплаты по начислениям',
                    'value': None, 'unit': '%',
                    'reason': ('ОСВ не содержит связи платежа с начислением; '
                               'показатель является бухгалтерским прокси')},
                   {'label': 'График/возраст долга', 'value': None, 'unit': 'RUB',
                    'reason': 'ОСВ не содержит сроков возникновения и погашения долга'}]),
        _card('ar', ar, 'RUB',
              'max(СКд − СКк, 0) по 62.01 + явно сопоставленный дебет 76',
              'Сальдо 62.01 и разрешённые аналитики 76', ar_reason,
               opening_value=ar_opening if not allowed_76 else None,
               analytics=ar_analytics,
               debtor_segments=debtor_segments,
               notes=['Счёт 76 включён только по явно сопоставленным аналитикам выбранного периода.']),
        _card('ap', ap, 'RUB', 'max(СКк − СКд, 0) по 60.01',
              'Сальдо субсчёта 60.01', ap_reason,
              opening_value=ap_opening,
              analytics=ap_analytics,
              credit_turnover=ap_credit_turnover,
              known_total=known_ap_total,
              components=ap_components,
              notes=['Прочие расчёты счёта 76 не включены: надёжное соответствие КЗ не задано.']),
        _card('cogs', cogs, 'RUB', 'ОбД(90.02*)',
              'Суточные листовые обороты счёта 90',
              None if cogs is not None else 'Нет строк точного префикса 90.02',
              analytics=cogs_analytics,
              cost_movements=cost_movements,
              recognized_costs=recognized_costs,
              cost_structure=cost_structure,
              notes=['Счета 20, 25, 26 и 44 не прибавляются к 90.02: '
                     'их суммы могут уже быть включены после закрытия периода.']),
        _card('profit', profit_display, 'RUB',
              ('Выручка без НДС − ОбД(90.02*) − ОбД(90.07*) − ОбД(90.08*)'
               if profit_from_sales is not None else
               'Выручка без НДС − ОбД(90.02*)'),
              'Расчёт по субсчетам 90 и 91',
              None if profit_display is not None else
              ('НДС 90.03 отсутствует: прибыль нельзя получить из выручки брутто'
               if gross_rev is not None and vat is None else
               'Недостаточно данных выручки или себестоимости'),
              gross_profit=gross_profit,
              selling_expense=selling_expense,
              admin_expense=admin_expense,
              profit_from_sales=profit_from_sales,
              other_income=other_income,
              other_expense=other_expense,
              other_result=other_result,
              profit_before_tax=profit_before_tax,
              other_analytics=other_analytics,
              gross_series=[],
              sales_profit_series=[],
              other_result_series=[],
              pretax_series=[],
              notes=[
                  ('Основная карточка показывает прибыль от продаж по 90.02, '
                   '90.07 и 90.08.' if profit_from_sales is not None else
                   'Основная карточка показывает валовую прибыль: 90.07 и/или '
                   '90.08 отсутствуют и не приняты за ноль.'),
                  'Обороты счетов 20, 26 и 44 повторно не добавляются.',
                  'Чистая прибыль, EBIT, EBITDA и ROA не выводятся без '
                  'дополнительной классификации и источников.']),
        _card('credits', credits, 'RUB', 'Кредитовое сальдо счетов 66 и 67',
              'Конечные сальдо ОСВ', credits_reason,
              breakdown=[{'label': f'Счёт {a}', 'value': v} for a, v in credit_parts],
              accounts=credit_accounts,
              analytics=credit_analytics,
              coverage={
                  'loaded': [item['account'] for item in credit_accounts
                             if item['value'] is not None],
                  'missing': [item['account'] for item in credit_accounts
                              if item['value'] is None],
                  'complete': all(item['value'] is not None
                                  for item in credit_accounts),
              },
              notes=(['Поддерживаемый охват: счета ' +
                      ', '.join(a for a, _ in credit_parts) + '.'] +
                     (['Частичный охват: отсутствует поддерживаемое сальдо по ' +
                       ', '.join(a for a in ('66', '67')
                                 if a not in {x[0] for x in credit_parts}) + '.']
                      if credit_parts and len(credit_parts) < 2 else []))),
        _card('salary', salary, 'RUB', 'max(СКк − СКд, 0) по счёту 70',
              'Конечное сальдо счёта 70', salary_reason,
              debit_balance=salary_debit,
              debit_turnover=salary_turnovers['debit'],
              credit_turnover=salary_turnovers['credit'],
              analytics=salary_analytics,
              insurance={
                  'credit_balance': insurance_credit,
                  'debit_balance': insurance_debit,
                  'debit_turnover': insurance_turnovers['debit'],
                  'credit_turnover': insurance_turnovers['credit'],
                  'turnover_ratio': insurance_ratio,
                  'analytics': insurance_analytics,
              },
              accrual_series=[],
              insurance_series=[],
              notes=[
                  'Кредитовое сальдо — расчётная задолженность работникам; ОСВ не подтверждает просрочку.',
                  'Кредитовый оборот 70 показывается как начисления работникам, а не как подтверждённый ФОТ.',
                  'Обороты 69 не называются начисленными или уплаченными взносами без классификации операций.']),
        _card('capex', capex, 'RUB', 'ОбД(08) за выбранный период',
              'Суточный оборот счёта 08', capex_reason,
              opening_balance=capex_opening,
              closing_balance=capex_closing,
              credit_balance=capex_credit_balance,
              debit_turnover=capex_turnovers['debit'],
              credit_turnover=capex_turnovers['credit'],
              analytics=capex_analytics,
              credit_series=[],
              balance_series=[],
              notes=[
                  'Дебетовый оборот 08 отражает формирование капитальных вложений, но не полный денежный поток.',
                  'Дебетовое сальдо 08 — бухгалтерская незавершёнка; оно не подтверждает возраст или просрочку объекта.',
                  'Кредитовый оборот 08 не называется вводом без корреспонденции со счётом 01 и документа принятия к учёту.'],
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
    if profit_display is not None and profit_display < 0:
        card_map['profit']['status'] = 'bad'
        card_map['profit']['status_label'] = 'Убыток от продаж'
    if profit_from_sales is None and gross_profit is not None:
        card_map['profit']['title'] = (
            'Валовая прибыль (отдельные расходы недоступны)')
        card_map['profit']['status_label'] = (
            'Валовая прибыль' if gross_profit >= 0 else 'Валовой убыток')
    if cogs is not None and revenue is not None and cogs > revenue:
        card_map['cogs']['status'] = 'warning'
        card_map['cogs']['status_label'] = 'Выше выручки'

    card_source_accounts = {
        'cash': ('50', '51', '52', '55'),
        'revenue': ('90',),
        'collection': ('62',),
        'ar': ('62', '76'),
        'ap': ('60',),
        'cogs': ('90', '20', '25', '26', '44'),
        'profit': ('90', '91'),
        'credits': ('66', '67'),
        'salary': ('70', '69'),
        'capex': ('08',),
    }
    # Provenance belongs to the accounts that actually form a value. For
    # example, COGS is the 90.02 value and must not inherit period provenance
    # merely because an unrelated 20 account has monthly reports.
    value_accounts = {
        'cash': tuple(account for account, _ in cash_parts),
        'revenue': ('90',),
        'collection': ('62',),
        'ar': tuple(
            ['62'] + sorted({
                str(item.get('account'))
                for item in ar_analytics
                if str(item.get('account') or '').startswith('76.')
                and item.get('included_in_total')
            })),
        'ap': ('60',),
        'cogs': ('90',),
        'profit': ('90',),
        'credits': tuple(account for account, _ in credit_parts),
        'salary': ('70',),
        'capex': ('08',),
    }
    period_card_provenance = {}
    for card_id, accounts_for_value in value_accounts.items():
        if card_map[card_id]['value'] is None:
            continue
        provenance = _combined_period_provenance(
            source, accounts_for_value, start, end)
        if provenance is not None:
            period_card_provenance[card_id] = provenance
            card_map[card_id]['source'] = (
                'Периодные итоги ОСВ (точные отчётные интервалы)')
            card_map[card_id]['period_provenance'] = provenance

    def add_period_detail_provenance(item, account):
        if _period_provenance(source, str(account), start, end):
            item['provenance'] = 'period_aggregate'
            item['provenance_accounts'] = [str(account)]

    for detail in card_map['revenue'].get('details') or []:
        add_period_detail_provenance(detail, '90')
    for detail in card_map['collection'].get('details') or []:
        if detail.get('account') in ('62.01', '62.02'):
            add_period_detail_provenance(detail, '62')
    for item in card_map['collection'].get('components') or []:
        add_period_detail_provenance(item, '62')
    for item in card_map['collection'].get('analytics') or []:
        add_period_detail_provenance(item, '62')
    for item in card_map['cogs'].get('cost_movements') or []:
        add_period_detail_provenance(item, item.get('account'))
    for item in card_map['cogs'].get('recognized_costs') or []:
        add_period_detail_provenance(
            item, str(item.get('account') or '').split('.')[0])
    for item in card_map['cogs'].get('cost_structure', {}).get(
            'analytics', []) or []:
        add_period_detail_provenance(item, item.get('account'))
    for item in card_map['ap'].get('components') or []:
        add_period_detail_provenance(
            item, str(item.get('account') or '').split('.')[0])
    for item in card_map['ap'].get('analytics') or []:
        add_period_detail_provenance(item, '60')
    for item in card_map['credits'].get('accounts') or []:
        add_period_detail_provenance(item, item.get('account'))
    for item in card_map['ar'].get('analytics') or []:
        add_period_detail_provenance(
            item, str(item.get('account') or '').split('.')[0])

    # Daily sources retain their daily chart (with null gaps).  A complete
    # report tiling uses one point per authentic report interval instead:
    # placing a monthly total on every day, or even on month-end, would
    # fabricate daily movements.  The card value remains the exact aggregate.
    aggregate_slots = _aggregate_series_slots(source, start, end)
    if aggregate_slots:
        monthly_slots = all(
            slot_start == slot_end.replace(day=1)
            and slot_end.day == calendar.monthrange(
                slot_end.year, slot_end.month)[1]
            for slot_start, slot_end in aggregate_slots
        )
        series_kind = 'month' if monthly_slots else 'period'
        month_options = []
        for slot_start, slot_end in aggregate_slots:
            if monthly_slots:
                label = _options([slot_end], 'month')[0]['label']
            else:
                label = f'{slot_start.isoformat()} — {slot_end.isoformat()}'
            month_options.append({
                'value': slot_end.isoformat(),
                'label': label,
                '_start': slot_start.isoformat(),
            })
    else:
        series_kind = 'day'
        series_dates = []
        cursor = start
        while cursor <= end:
            series_dates.append(cursor)
            cursor += timedelta(days=1)
        month_options = _options(series_dates, series_kind)
    for option in month_options:
        month_end = _iso(option['value'])
        month_start = _iso(option.get('_start')) if option.get('_start') \
            else _range(series_kind, month_end)[0]
        month_pl = _pl_facts(source, month_start, month_end)
        month_selling = _sub_turnover(
            source, '90', '90.07', month_start, month_end, 'obd')
        month_admin = _sub_turnover(
            source, '90', '90.08', month_start, month_end, 'obd')
        month_sales_profit = (
            round(month_pl['profit'] - month_selling - month_admin, 2)
            if month_pl['profit'] is not None
            and month_selling is not None and month_admin is not None else None)
        month_other_income = _sub_turnover(
            source, '91', '91.01', month_start, month_end, 'obk')
        month_other_expense = _sub_turnover(
            source, '91', '91.02', month_start, month_end, 'obd')
        month_other_result = (
            round(month_other_income - month_other_expense, 2)
            if month_other_income is not None
            and month_other_expense is not None else None)
        month_pretax = (
            round(month_sales_profit + month_other_result, 2)
            if month_sales_profit is not None
            and month_other_result is not None else None)
        month_values = {
            'revenue': month_pl['revenue'],
            'cogs': month_pl['cost'],
            'profit': (month_sales_profit if month_sales_profit is not None
                       else month_pl['profit']),
        }
        point_date = option['value']
        card_map['profit']['gross_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_pl['profit']})
        card_map['profit']['sales_profit_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_sales_profit})
        card_map['profit']['other_result_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_other_result})
        card_map['profit']['pretax_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_pretax})
        month_capex_turnovers = _account_turnovers(
            source, '08', month_start, month_end)
        month_values['capex'] = month_capex_turnovers['debit']
        card_map['capex']['credit_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_capex_turnovers['credit']})
        card_map['capex']['balance_series'].append({
            'date': point_date, 'label': option['label'],
            'value': _top_balance(source, '08', month_end)[0]})
        month_collection = _collection_components(
            source, month_start, month_end)
        month_values['collection'] = month_collection['value']
        month_components = {
            item['label']: item['value']
            for item in month_collection['components']
        }
        card_map['collection']['turnover_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_collection['value'],
            'components': month_collection['components'],
            'reason': month_collection['reason'],
            'debit_6201': month_components.get('Дебетовый оборот 62.01'),
            'credit_6201': month_components.get('Кредитовый оборот 62.01'),
            'debit_6202': month_components.get('Дебетовый оборот 62.02'),
            'credit_6202': month_components.get('Кредитовый оборот 62.02')})
        cash_month = _cash_snapshot(source, month_end)
        month_values['cash'] = cash_month['value']
        card_map['cash']['series'].append({
            'label': option['label'],
            'date': cash_month['date'],
            'value': cash_month['value'],
            'accounts': list(cash_month['accounts']),
            'missing_accounts': list(cash_month['missing_accounts']),
            'coverage': cash_month['coverage'],
        })
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
        month_salary_turnovers = _account_turnovers(
            source, '70', month_start, month_end)
        month_insurance_turnovers = _account_turnovers(
            source, '69', month_start, month_end)
        card_map['salary']['accrual_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_salary_turnovers['credit']})
        card_map['salary']['insurance_series'].append({
            'date': point_date, 'label': option['label'],
            'value': month_insurance_turnovers['credit']})
        for cid in _CARD_ORDER:
            if cid == 'cash':
                continue
            card_map[cid]['series'].append({
                'date': point_date, 'label': option['label'],
                'value': month_values[cid]})

        # Keep every selectable accounting component on the same authentic
        # timeline as its parent card.  A point is intentionally ``None`` when
        # the source has no exact observation for that slot; this is not a
        # place to carry a balance forward or prorate a period report.
        def append_component_series(items, values, unit=None):
            if not items:
                return
            for item in items:
                label = item.get('label')
                if isinstance(values, dict) and label not in values:
                    continue
                value = values.get(label) if isinstance(values, dict) else None
                item.setdefault('series', []).append({
                    'date': point_date,
                    'label': option['label'],
                    'value': value,
                    'unit': unit or item.get('unit'),
                    'granularity': series_kind,
                    'synthetic': False,
                })

        cash_values = {}
        for account in _CASH_ACCOUNTS:
            account_value, _ = _top_balance(source, account, month_end)
            cash_values[f'Счёт {account}'] = account_value
        append_component_series(card_map['cash'].get('breakdown'), cash_values, 'RUB')

        append_component_series(
            card_map['revenue'].get('details'),
            {'Выручка брутто': month_pl['gross'],
             'НДС': month_pl['vat']},
            'RUB')
        collection_values = {
            item['label']: item['value']
            for item in month_collection['components']
        }
        append_component_series(
            card_map['collection'].get('details'), collection_values, 'RUB')

        # Balance analytics are selectable details in the AR/AP windows.  Use
        # the exact point-end snapshot (or the authentic period report at
        # that endpoint), never the selected-period headline as a proxy for a
        # missing analytic row.
        ar_point = _sub_balance_analytics(
            source, '62', '62.01', month_end)
        ar_point.extend(_sub_balance_analytics(source, '76', '76', month_end))
        append_component_series(
            card_map['ar'].get('analytics'),
            {item.get('label'): item.get('value') for item in ar_point},
            'RUB')
        for item in card_map['ar'].get('analytics') or []:
            key = (item.get('account'), item.get('analytic_key'))
            if item.get('series'):
                item['series'][-1]['value'] = next(
                    (row.get('value') for row in ar_point
                     if (row.get('account'), row.get('analytic_key')) == key),
                    None)

        ap_point = _sub_balance_analytics(
            source, '60', '60.01', month_end)
        append_component_series(
            card_map['ap'].get('analytics'),
            {item.get('label'): item.get('value') for item in ap_point},
            'RUB')
        for item in card_map['ap'].get('analytics') or []:
            key = (item.get('account'), item.get('analytic_key'))
            if item.get('series'):
                item['series'][-1]['value'] = next(
                    (row.get('value') for row in ap_point
                     if (row.get('account'), row.get('analytic_key')) == key),
                    None)

        cogs_point = defaultdict(float)
        cogs_known = defaultdict(bool)
        for row in month_pl['cost_rows']:
            analytic_key, _ = _analytic_identity(row)
            if row.get('obd_known') is False:
                cogs_known[analytic_key] = True
            else:
                cogs_point[analytic_key] += row.get('obd', 0)
        for item in card_map['cogs'].get('analytics') or []:
            value = (None if cogs_known[item.get('analytic_key')]
                     else round(cogs_point[item.get('analytic_key')], 2))
            item.setdefault('series', []).append({
                'date': point_date, 'label': option['label'], 'value': value,
                'unit': 'RUB', 'granularity': series_kind, 'synthetic': False})

        # The selected report's component rows (68/69/70 and the two credit
        # accounts) are also useful modal series.  Missing account rows remain
        # missing rather than becoming zeros.
        credit_values = {}
        for account in ('66', '67'):
            credit_values[f'Счёт {account}'] = _top_balance(
                source, account, month_end, credit=True)[0]
        append_component_series(
            card_map['credits'].get('breakdown'), credit_values, 'RUB')
        append_component_series(
            card_map['ap'].get('components'),
            {item['label']: item['value'] for item in (
                [{'label': 'Поставщики и подрядчики',
                  'value': _sub_balance(
                      source, '60', '60.01', month_end, credit=True)[0]}] +
                [{'label': label, 'value': _top_balance(
                    source, account, month_end, credit=True)[0]}
                 for account, label in (
                     ('68', 'Налоги и сборы'),
                     ('69', 'Страховые взносы'),
                     ('70', 'Расчёты с персоналом'))])},
            'RUB')

        # ``details`` is the common, generic modal contract.  Keep the
        # authenticated measures that already have dedicated series in that
        # contract as well, so keyboard/pin interactions work consistently
        # for cards with a specialised renderer.
        salary_details = card_map['salary'].setdefault('details', [])
        if not salary_details:
            salary_details.extend([
                {'label': 'Кредитовое сальдо 70', 'value': salary},
                {'label': 'Дебетовое сальдо 70', 'value': salary_debit},
                {'label': 'Кредитовый оборот 70',
                 'value': salary_turnovers['credit']},
                {'label': 'Кредитовый оборот 69',
                 'value': insurance_turnovers['credit']},
            ])
        append_component_series(
            salary_details,
            {'Кредитовое сальдо 70': _top_balance(
                source, '70', month_end, credit=True)[0],
             'Дебетовое сальдо 70': _top_balance(source, '70', month_end)[0],
             'Кредитовый оборот 70': month_salary_turnovers['credit'],
             'Кредитовый оборот 69': month_insurance_turnovers['credit']},
            'RUB')

        capex_details = card_map['capex'].setdefault('details', [])
        if not capex_details:
            capex_details.extend([
                {'label': 'Дебетовый оборот 08',
                 'value': month_capex_turnovers['debit']},
                {'label': 'Кредитовый оборот 08',
                 'value': month_capex_turnovers['credit']},
                {'label': 'Дебетовое сальдо 08',
                 'value': _top_balance(source, '08', month_end)[0]},
            ])
        append_component_series(
            capex_details,
            {'Дебетовый оборот 08': month_capex_turnovers['debit'],
             'Кредитовый оборот 08': month_capex_turnovers['credit'],
             'Дебетовое сальдо 08': _top_balance(
                 source, '08', month_end)[0]},
            'RUB')

    # Cash is a balance snapshot, not a period movement. Keep its modal
    # series on the real calendar even when another account has only monthly
    # period reports. A period aggregate may contribute an authentic
    # month-end observation, but it must never be copied to every day (or
    # used to imply a daily fluctuation).
    cash_daily_series = []
    cash_daily_values = {account: [] for account in _CASH_ACCOUNTS}
    cash_cursor = start
    while cash_cursor <= end:
        cash_point = _cash_observation_snapshot(source, cash_cursor)
        point_date = cash_cursor.isoformat()
        point_label = _options([cash_cursor], 'day')[0]['label']
        cash_daily_series.append({
            'label': point_label,
            'date': point_date,
            'value': cash_point['value'],
            'accounts': list(cash_point['accounts']),
            'missing_accounts': list(cash_point['missing_accounts']),
            'coverage': cash_point['coverage'],
            'granularity': 'day',
            'synthetic': False,
        })
        values = dict(cash_point['parts'])
        for account in _CASH_ACCOUNTS:
            cash_daily_values[account].append({
                'label': point_label,
                'date': point_date,
                'value': values.get(account),
                'unit': 'RUB',
                'granularity': 'day',
                'synthetic': False,
            })
        cash_cursor += timedelta(days=1)
    card_map['cash']['series'] = cash_daily_series
    for item in card_map['cash'].get('breakdown') or []:
        match = re.search(r'(\d{2})', str(item.get('label') or ''))
        account = match.group(1) if match else None
        if account in cash_daily_values:
            item['series'] = cash_daily_values[account]

    series_accounts = {
        'cash': {'series': value_accounts['cash']},
        'revenue': {'series': ('90',)},
        'collection': {'turnover_series': ('62',)},
        'ar': {'series': ('62',)},
        'ap': {'series': ('60',)},
        'cogs': {'series': ('90',)},
        'profit': {
            'series': ('90',),
            'gross_series': ('90',),
            'sales_profit_series': ('90',),
            'other_result_series': ('91',),
            'pretax_series': ('90', '91'),
        },
        'credits': {'series': value_accounts['credits']},
        'salary': {
            'series': ('70',),
            'accrual_series': ('70',),
            'insurance_series': ('69',),
        },
        'capex': {
            'series': ('08',),
            'credit_series': ('08',),
            'balance_series': ('08',),
        },
    }
    for card_id, fields in series_accounts.items():
        # Cash points are always exact calendar-day snapshots. They must not
        # be paired with the period aggregate slots used by other cards.
        if card_id == 'cash':
            continue
        for field, accounts_for_point in fields.items():
            points = card_map[card_id].get(field) or []
            for option, point in zip(month_options, points):
                if point.get('value') is None:
                    continue
                point_accounts = (
                    tuple(point.get('accounts') or accounts_for_point)
                    if card_id == 'cash' else accounts_for_point)
                point_end = _iso(option['value'])
                point_start = (_iso(option['_start'])
                               if option.get('_start') else
                               _range(series_kind, point_end)[0])
                provenance = _combined_period_provenance(
                    source, point_accounts, point_start, point_end)
                if provenance is None:
                    continue
                point['provenance'] = 'period_aggregate'
                point['provenance_accounts'] = list(point_accounts)

    for index, point in enumerate(card_map['cash']['series']):
        previous = (card_map['cash']['series'][index - 1]
                    if index else None)
        point['comparison'] = _cash_comparison(
            point, previous,
            reason=('Нет предыдущей точки'
                    if index == 0 else None))
        # Keep the prior period-end difference as metadata for quarter/year
        # cards, but never feed it to the daily change chart.  The primary
        # series comparison above is always adjacent calendar-day data.
        point_date = _iso(point['date'])
        if period in ('quarter', 'year') and point_date and point_date.day == \
                calendar.monthrange(point_date.year, point_date.month)[1]:
            previous_date = point_date.replace(day=1) - timedelta(days=1)
            if previous_date < start:
                point['interval_comparison'] = _cash_comparison(
                    point, reason='Нет предыдущей точки')
            else:
                point['interval_comparison'] = _cash_comparison(
                    point, _cash_snapshot(source, previous_date))

    # Non-cash cards compare only an immediately preceding equivalent
    # selection with a declared complete report span and identical basis.
    # Cash additionally requires the preceding calendar slot below.
    ranged_period = range_start_endpoint != endpoint
    selected_idx = (-1 if ranged_period else next(
        (i for i, item in enumerate(options)
         if item['value'] == endpoint.isoformat()), -1))
    if ranged_period:
        card_map['cash']['comparison'] = _cash_comparison(
            cash_snapshot,
            reason='Сравнение недоступно для выбранного диапазона дат')
    else:
        cash_previous_end = _previous_cash_endpoint(period, end)
        cash_previous_slot = (
            cash_previous_end is not None and
            cash_previous_end.isoformat() in {item['value'] for item in options})
        previous_cash = _cash_snapshot(source, cash_previous_end)
        card_map['cash']['comparison'] = _cash_comparison(
            cash_snapshot, previous_cash,
            reason=(None if cash_previous_slot
                    else 'Нет предыдущего календарного слота'))
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
                if cid == 'profit' and profit_from_sales is not None:
                    previous_selling = _sub_turnover(
                        source, '90', '90.07', prev_start, prev_end, 'obd')
                    previous_admin = _sub_turnover(
                        source, '90', '90.08', prev_start, prev_end, 'obd')
                    previous = (
                        round(previous - previous_selling - previous_admin, 2)
                        if previous is not None
                        and previous_selling is not None
                        and previous_admin is not None else None)
                if current is not None and previous is not None:
                    card_map[cid]['delta'] = {
                        'value': round(current - previous, 2), 'unit': 'RUB',
                        'label': 'к предыдущему сопоставимому периоду'}
        previous_capex = _account_turnovers(
            source, '08', prev_start, prev_end)['debit']
        if (_account_covers(source, '08', start, end) and
                _account_covers(source, '08', prev_start, prev_end) and
                capex is not None and previous_capex is not None):
            card_map['capex']['delta'] = {
                'value': round(capex - previous_capex, 2),
                'unit': 'RUB', 'label': 'к предыдущему сопоставимому периоду'}
        else:
            card_map['capex']['notes'].append(
                'Сравнение скрыто: нет двух полностью сопоставимых периодов.')
        cash_comparison = card_map['cash']['comparison']
        if cash_comparison['comparable']:
            card_map['cash']['delta'] = {
                'value': cash_comparison['value'],
                'unit': 'RUB',
                'previous_date': cash_comparison['previous_date'],
                'date': cash_comparison['date'],
                'accounts': list(cash_snapshot['accounts']),
                'coverage': cash_snapshot['coverage'],
                'label': (
                    f'{cash_comparison["previous_date"]} → '
                    f'{cash_comparison["date"]}'),
            }
        previous_ar = _sub_balance(source, '62', '62.01', prev_end)[0]
        previous_ap = _sub_balance(source, '60', '60.01', prev_end, credit=True)[0]
        if ar is not None and previous_ar is not None and not allowed_76:
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

    # Tell clients which granularity they received.  A month-end point from
    # an ОСВ period report remains a monthly aggregate and must not be treated
    # as a missing daily observation by the renderer.
    for card in cards:
        card['granularity'] = 'day' if card['id'] == 'cash' else series_kind
        for field in (
                'series', 'turnover_series', 'gross_series',
                'sales_profit_series', 'other_result_series', 'pretax_series',
                'accrual_series', 'insurance_series', 'credit_series',
                'balance_series'):
            for point in card.get(field) or []:
                point.setdefault(
                    'granularity',
                    'day' if card['id'] == 'cash' else series_kind)
                point.setdefault('synthetic', False)
        for collection in (
                card.get('breakdown') or [], card.get('details') or [],
                card.get('analytics') or [], card.get('components') or []):
            for item in collection:
                for point in item.get('series') or []:
                    point.setdefault(
                        'granularity',
                        'day' if card['id'] == 'cash' else series_kind)
                    point.setdefault('synthetic', False)

    # Object table uses one and the same path1 dimension for both sides.
    rev_group, vat_group, cost_group = (
        defaultdict(float), defaultdict(float), defaultdict(float))
    rev_group_unknown, vat_group_unknown, cost_group_unknown = set(), set(), set()
    for row in rev_rows:
        label = _analytic_label(row)
        if row.get('obk_known') is False:
            rev_group_unknown.add(label)
        else:
            rev_group[label] += row['obk']
    for row in vat_rows:
        label = _analytic_label(row)
        if row.get('obd_known') is False:
            vat_group_unknown.add(label)
        else:
            vat_group[label] += row['obd']
    for row in cost_rows:
        label = _analytic_label(row)
        if row.get('obd_known') is False:
            cost_group_unknown.add(label)
        else:
            cost_group[label] += row['obd']
    rev_segments = set(rev_group) | rev_group_unknown
    vat_segments = set(vat_group) | vat_group_unknown
    cost_segments = set(cost_group) | cost_group_unknown
    segments = sorted(rev_segments | cost_segments)
    vat_allocable = bool(vat_rows) and vat_segments.issubset(rev_segments)
    object_rows = []
    for segment in segments:
        has_revenue = segment in rev_segments
        has_cost = segment in cost_segments
        rv = (rev_group.get(segment)
              if has_revenue and segment not in rev_group_unknown else None)
        if vat_allocable and rv is not None:
            rv = (None if segment in vat_group_unknown
                  else rv - vat_group.get(segment, 0.0))
        co = (cost_group.get(segment)
              if has_cost and segment not in cost_group_unknown else None)
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
                   'profit': gross_profit,
                   'margin': round(gross_profit / obj_rev * 100, 2)
                   if gross_profit is not None and obj_rev else None},
        'reason': obj_reason, 'notes': obj_notes,
    }
    mapping = {}
    for item in revenue_mappings or []:
        if not isinstance(item, dict):
            continue
        account = str(item.get('account') or '')
        analytic_key = str(item.get('analytic_key') or '')
        category = str(item.get('category') or '')
        if account in ('90.01', '90.03') and analytic_key and \
                category in {key for key, _ in REVENUE_CATEGORIES}:
            mapping[(account, analytic_key)] = category

    rev_by_key, vat_by_key = defaultdict(float), defaultdict(float)
    labels_by_key = {}
    rev_unknown, vat_unknown = set(), set()
    for row in rev_rows:
        identity, label = _analytic_identity(row)
        labels_by_key[identity] = label
        if row.get('obk_known') is False:
            rev_unknown.add(identity)
        else:
            rev_by_key[identity] += row['obk']
    for row in vat_rows:
        identity, label = _analytic_identity(row)
        labels_by_key[identity] = label
        if row.get('obd_known') is False:
            vat_unknown.add(identity)
        else:
            vat_by_key[identity] += row['obd']
    rev_keys = set(rev_by_key) | rev_unknown
    vat_keys = set(vat_by_key) | vat_unknown
    discovered = [
        {'account': account, 'analytic_key': analytic_key,
         'analytic_label': labels_by_key[analytic_key],
         'category': mapping.get((account, analytic_key)),
         'amount_known': analytic_key not in unknown}
        for account, keys, unknown in (
            ('90.01', rev_keys, rev_unknown),
            ('90.03', vat_keys, vat_unknown))
        for analytic_key in sorted(keys, key=lambda key: labels_by_key[key])
    ]
    conflicts = []
    for analytic_key in sorted(rev_keys & vat_keys):
        rev_category = mapping.get(('90.01', analytic_key))
        vat_category = mapping.get(('90.03', analytic_key))
        if rev_category and vat_category and rev_category != vat_category:
            conflicts.append({
                'analytic_key': analytic_key,
                'analytic_label': labels_by_key[analytic_key],
                'message': 'Одинаковая аналитика 90.01 и 90.03 отнесена к разным категориям',
            })

    unclassified = [item for item in discovered if not item['category']]
    unknown_amounts = [item for item in discovered if not item['amount_known']]
    classification_complete = (
        bool(discovered) and not unclassified and not conflicts
        and not unknown_amounts and revenue is not None)
    segment_rows = []
    classified_total = 0.0
    for category, title in REVENUE_CATEGORIES:
        category_rev = [amount for analytic_key, amount in rev_by_key.items()
                        if mapping.get(('90.01', analytic_key)) == category]
        category_vat = [amount for analytic_key, amount in vat_by_key.items()
                        if mapping.get(('90.03', analytic_key)) == category]
        has_rows = bool(category_rev or category_vat)
        value = None
        if classification_complete and has_rows:
            value = sum(category_rev) - sum(category_vat)
            value = round(value, 2)
            classified_total += value
        segment_rows.append({
            'id': category, 'label': title, 'value': value,
            'basis': ('net' if vat_rows else 'gross') if has_rows else None,
        })
    reconciliation_difference = (
        round(revenue - classified_total, 2)
        if classification_complete else None
    )
    revenue_segments = {
        'categories': segment_rows,
        'analytics': discovered,
        'unclassified': unclassified,
        'unknown_amounts': unknown_amounts,
        'conflicts': conflicts,
        'classified_total': round(classified_total, 2)
        if classification_complete else None,
        'total': revenue,
        'reconciliation_difference': reconciliation_difference,
        'reconciled': (reconciliation_difference is not None and
                       abs(reconciliation_difference) <= 0.01 and
                       not conflicts and not unclassified),
    }
    for item in discovered:
        add_period_detail_provenance(item, '90')
    recommendations = []
    if profit_display is not None and profit_display < 0:
        recommendations.append({
            'id': 'sales-loss', 'severity': 'bad',
            'title': ('Отрицательная прибыль от продаж'
                      if profit_from_sales is not None else
                      'Отрицательная валовая прибыль'),
            'description': ('Проверьте состав выручки, себестоимости и '
                            'признанных расходов по убыточным объектам учёта.'),
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
    metric_accounts = {
        'cash': ('50', '51', '52', '55'),
        'collection': ('62',),
        'ar': ('62.01',),
        'ap': ('60.01',),
        'credits': ('66', '67'),
        'salary': ('70',),
        'capex': ('08',),
    }
    missing = []
    if not rev_rows:
        missing.append('90.01')
    if not cost_rows:
        missing.append('90.02')
    if not vat_rows:
        missing.append('90.03')
    for c in cards:
        if c['value'] is None and c['id'] not in ('revenue', 'cogs', 'profit'):
            missing.extend(metric_accounts.get(c['id'], ()))
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
        'period': {'kind': period, 'date': endpoint.isoformat(),
                   'date_from': range_start_endpoint.isoformat(),
                   'series_kind': series_kind,
                   'provenance': (
                       'period_aggregate' if aggregate_slots else 'daily'),
                   'label': label,
                   'start': start.isoformat(), 'end': end.isoformat(),
                   'options': options, 'available_kinds': available,
                   'warnings': list(dict.fromkeys(warnings))},
        'cards': cards, 'objects': objects, 'revenue_segments': revenue_segments,
        'recommendations': recommendations,
        'quality': quality, 'methodology': methodology,
    }

def _debtor_report_analytics(rows, sub):
    """Extract terminal debtor coordinates without cross-branch depth rules."""
    result = []
    seen = set()
    for index, row in enumerate(rows):
        level = int(row.get('lvl') or 0)
        # Ordered ОСВ hierarchy: an immediately following deeper row proves
        # that this row is a subtotal/parent, not a terminal balance.
        if index + 1 < len(rows) and int(rows[index + 1].get('lvl') or 0) > level:
            continue
        hierarchy = [str(value).strip()
                     for value in (row.get('hierarchy_path') or [])
                     if str(value).strip()]
        account_candidates = hierarchy + [
            str(row.get(key) or '').strip()
            for key in ('p0', 'p1', 'p2', 'p3')]
        if sub == '62.01':
            if '62.01' in account_candidates:
                account = '62.01'
            elif '62' in account_candidates:
                # Preserve root-level debtor leaves as excluded detail. The
                # root is not a substitute for the requested 62.01 balance.
                account = '62'
            else:
                continue
        else:
            candidates = [
                value for value in account_candidates
                if re.fullmatch(r'76(?:\.\d+)+', value)]
            if not candidates and '76' in account_candidates:
                # As above, expose root-level detail without allowing it into
                # the explicitly scoped 76 subaccount total.
                candidates = ['76']
            if not candidates:
                continue
            account = max(candidates, key=lambda value: value.count('.'))
        parts = [value for value in hierarchy
                 if not re.fullmatch(r'\d{2}(?:\.\d+)*', value)]
        # Some parser-shaped snapshots begin at an analytic leaf and carry
        # its account ancestry only in p0..p3.
        if not parts:
            parts = [
                str(row.get(key) or '').strip()
                for key in ('p0', 'p1', 'p2', 'p3')
                if str(row.get(key) or '').strip()
                and not re.fullmatch(
                    r'\d{2}(?:\.\d+)*',
                    str(row.get(key) or '').strip())
            ]
        if not parts:
            continue
        analytic_key = revenue_analytic_key(parts)
        marker = (account, analytic_key, row.get('skd'), row.get('skk'))
        if marker in seen:
            continue
        seen.add(marker)
        result.append({
            'account': account,
            'analytic_key': analytic_key,
            'label': ' / '.join(f'«{part}»' for part in parts),
            'value': round(max(
                0.0, row.get('skd', 0) - row.get('skk', 0)), 2),
        })
    return result
