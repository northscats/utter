"""Parser for a complete all-accounts trial balance control snapshot."""
import os
import re

from osvparser import read_rows


_ACCOUNT = re.compile(r'^\d{2}(?:\.\d+)*$')


def parse_consolidated_osv(path):
    """Return per-account parser payloads or ``None`` for a single-account ОСВ."""
    raw_rows = read_rows(path)
    coded = []
    roots = set()
    period = ''
    has_account_header = False
    has_grand_total = False
    grand_total = None
    for label, _level, values in raw_rows:
        text = str(label or '').strip()
        has_account_header = has_account_header or text.lower() == 'счет'
        if text.lower() == 'итого':
            has_grand_total = True
            grand_total = values
        if 'оборотно-сальдовая ведомость' in text.lower():
            period = text
        if not _ACCOUNT.fullmatch(text):
            continue
        root = text[:2]
        if _level == 0 and text == root:
            roots.add(root)
        coded.append((text, values))
    if (len(roots) < 3 or not period
            or not has_account_header or not has_grand_total):
        return None
    if grand_total is None or any(
        abs(grand_total[left] - grand_total[right]) > 0.01
        for left, right in ((0, 1), (2, 3), (4, 5))
    ):
        return None

    source_name = os.path.basename(path)
    result = {}
    keys = ('snd', 'snk', 'obd', 'obk', 'skd', 'skk')
    for root in sorted(roots):
        account_rows = []
        top = None
        for code, values in coded:
            if code != root and not code.startswith(root + '.'):
                continue
            parts = code.split('.')
            row = {
                'lvl': len(parts) - 1,
                'label': code,
                'p0': parts[0],
                'p1': '.'.join(parts[:2]) if len(parts) > 1 else '',
                'p2': '.'.join(parts[:3]) if len(parts) > 2 else '',
                'p3': '.'.join(parts[:4]) if len(parts) > 3 else '',
                **dict(zip(keys, values)),
            }
            account_rows.append(row)
            if code == root:
                top = dict(zip(keys, values))
        if top is None:
            continue
        result[root] = {
            'acc': root,
            'period': period,
            'top': top,
            'rows': account_rows,
            'daily': [],
            'warnings': [],
            'check_ok': True,
            'source_file': source_name,
            'source_kind': 'consolidated_control',
            'complete_snapshot': True,
        }
    return result or None