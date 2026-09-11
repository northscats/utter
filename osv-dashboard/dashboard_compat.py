# -*- coding: utf-8 -*-
"""Response-only compatibility upgrades for saved dashboard HTML.

Saved dashboards are intentionally immutable artifacts.  This module renders
known generated snapshots through the current presentation template while a
dashboard is being served, with a narrow label fallback for partial legacy
HTML.  It never reads current financial sources or writes the saved artifact.
"""
import json
import re


def json_for_script(value):
    """Serialize JSON for an inline script without HTML/script breakouts."""
    return (json.dumps(value, ensure_ascii=False)
            .replace('&', r'\u0026')
            .replace('<', r'\u003C')
            .replace('>', r'\u003E')
            .replace('\u2028', r'\u2028')
            .replace('\u2029', r'\u2029'))


_DAILY_70_MARKUP_RE = re.compile(
    r'(?P<prefix><div\s+class="daily-card"\s+id="daily-70-wrap">\s*'
    r'<h3>Оплата\s+труда\s+70\s+—\s+обороты</h3>\s*'
    r'<div\s+class="chartwrap"\s+id="ch-daily-70"></div>\s*'
    r'<div\s+class="daily-legend">\s*)'
    r'<span><i\s+style="background:#2e6e8e"></i>Начислено \(Дт\)</span>\s*'
    r'<span><i\s+style="background:#7d97a8"></i>Выплачено \(Кт\)</span>'
    r'(?P<suffix>\s*</div>\s*</div>)',
    re.DOTALL,
)

_DAILY_70_MARKUP = (
    '<span><i style="background:#2e6e8e"></i>'
    'ОбД70 — дебетовый оборот</span>\n'
    '          <span><i style="background:#7d97a8"></i>'
    'ОбК70 — начисления (Кт)</span>\n'
    '          <span class="daily-inference">'
    'ОбД70 может включать выплаты, удержания и корректировки; '
    'общий оборот не равен денежной выплате. Нулевой ОбД70 ≠ доказательство '
    'отсутствия выплаты: график отражает только загруженные обороты ОСВ '
    'за выбранные дни.</span>'
)

_DAILY_70_EXEC_CALL_RE = re.compile(
    r'(?P<prefix>dailyBarsPair\(\s*el70\s*,\s*dates\s*,\s*'
    r"accs\['70'\]\.obd\s*,\s*accs\['70'\]\.obk\s*,\s*)"
    r"'Начислено'\s*,\s*'Выплачено'"
    r"(?P<suffix>\s*,\s*'#2e6e8e'\s*,\s*'#7d97a8'\s*\))",
)

_DAILY_70_DETAIL_FUNCTION_RE = re.compile(
    r'(?P<prefix>/\* ----------\s*рендер\s+суточных\s+в\s+'
    r'детальной\s+вкладке\s*---------- \*/\s*'
    r'function\s+renderDailyDetail\(\)\s*\{)'
    r'(?P<body>.*?)'
    r'(?P<suffix>\n\}\s*\n/\* ----------\s*главный\s+рендер\s*---------- \*/)',
    re.DOTALL,
)

_DAILY_70_DETAIL_OLD = """    if(mode==='line'){
      const v=ad.balance;
      if(v)dailyLine(cw,dates,[{n:acc,c:c1,v}]);
      else dailyBarsPair(cw,dates,ad.obd,ad.obk,'Дт','Кт',c1,c2||'#a05a5a');
    }else if(mode==='bars'){
      dailyBarsPair(cw,dates,ad.obd,ad.obk,'Дт','Кт',c1,c2||'#a05a5a');
    }else{
      // bars_single — только Дт
      dailyBarsPair(cw,dates,ad.obd,[].map(()=>0),'Дт','',c1,c1);
    }"""

_DAILY_70_DETAIL_NEW = """    const nIn=acc==='70'?'ОбД70':'Дт',nOut=acc==='70'?'ОбК70':'Кт';
    if(mode==='line'){
      const v=ad.balance;
      if(v)dailyLine(cw,dates,[{n:acc,c:c1,v}]);
      else dailyBarsPair(cw,dates,ad.obd,ad.obk,nIn,nOut,c1,c2||'#a05a5a');
    }else if(mode==='bars'){
      dailyBarsPair(cw,dates,ad.obd,ad.obk,nIn,nOut,c1,c2||'#a05a5a');
    }else{
      // bars_single — только Дт
      dailyBarsPair(cw,dates,ad.obd,[].map(()=>0),nIn,'',c1,c1);
    }"""

_DAILY_70_MODAL_LEG_RE = re.compile(
    r'(?P<prefix>function\s+openDailyModal\(acc\)\s*\{.*?'
    r'var\s+legs\s*=\s*\{.*?\'70\'\s*:\s*)'
    r"'<span><i\s+style=\"background:#2e6e8e\"></i>Начислено \(Дт\)</span>"
    r"<span><i\s+style=\"background:#7d97a8\"></i>Выплачено \(Кт\)</span>'",
    re.DOTALL,
)

_DAILY_70_MODAL_LEG = (
    "'<span><i style=\"background:#2e6e8e\"></i>"
    "ОбД70 — дебетовый оборот</span>"
    "<span><i style=\"background:#7d97a8\"></i>"
    "ОбК70 — начисления (Кт)</span>"
    "<span class=\"daily-inference\">"
    "ОбД70 может включать выплаты, удержания и корректировки; "
    "общий оборот не равен денежной выплате. Нулевой ОбД70 ≠ доказательство "
    "отсутствия выплаты: график отражает только загруженные обороты ОСВ "
    "за выбранные дни.</span>'"
)

_DAILY_70_MODAL_CALL_RE = re.compile(
    r'(?P<prefix>else\s+if\(_dmAcc===\'70\'\s*&&\s*accs\['
    r"'70'\]\)\s*dailyBarsPair\(\s*box\s*,\s*dates\s*,\s*"
    r"accs\['70'\]\.obd\.slice\(s0\s*,\s*s1\+1\)\s*,\s*"
    r"accs\['70'\]\.obk\.slice\(s0\s*,\s*s1\+1\)\s*,\s*)"
    r"'Начислено'\s*,\s*'Выплачено'"
    r"(?P<suffix>\s*,\s*'#2e6e8e'\s*,\s*'#7d97a8')",
)


def _replace_daily_markup(html):
    """Upgrade the exact legacy account-70 overview markup, if present."""
    return _DAILY_70_MARKUP_RE.sub(
        lambda match: match.group('prefix') + _DAILY_70_MARKUP
        + match.group('suffix'),
        html,
        count=1,
    )


def _replace_daily_detail_labels(html):
    """Upgrade only the known daily-detail renderer function."""
    def replace_function(match):
        body = match.group('body')
        if _DAILY_70_DETAIL_OLD not in body:
            return match.group(0)
        body = body.replace(_DAILY_70_DETAIL_OLD, _DAILY_70_DETAIL_NEW, 1)
        return match.group('prefix') + body + match.group('suffix')

    return _DAILY_70_DETAIL_FUNCTION_RE.sub(replace_function, html, count=1)


def upgrade_legacy_salary_daily_labels(html):
    """Return a response-only upgrade for known legacy salary daily labels.

    The substitutions are anchored to the generated dashboard's account-70
    markup and renderer functions.  In particular, no general text or JSON
    replacement is performed, and the monthly ``D.zp.n``/``D.zp.v`` chart is
    deliberately outside the compatibility scope.
    """
    if not isinstance(html, str):
        raise TypeError('dashboard HTML must be text')

    html = _replace_daily_markup(html)
    html = _DAILY_70_EXEC_CALL_RE.sub(
        r"\g<prefix>'ОбД70', 'ОбК70'\g<suffix>", html, count=1)
    html = _replace_daily_detail_labels(html)
    html = _DAILY_70_MODAL_LEG_RE.sub(
        lambda match: match.group('prefix') + _DAILY_70_MODAL_LEG,
        html,
        count=1,
    )
    html = _DAILY_70_MODAL_CALL_RE.sub(
        r"\g<prefix>'ОбД70','ОбК70'\g<suffix>", html, count=1)
    return html


_DATA_MARKER_RE = re.compile(r'\bconst\s+D\s*=\s*')


def _extract_saved_dataset(html):
    """Decode the embedded generated dataset without executing saved JS."""
    match = _DATA_MARKER_RE.search(html)
    if not match:
        return None
    payload = html[match.end():].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def upgrade_saved_dashboard_template(html, template):
    """Render a known saved dashboard with the current legacy template.

    The saved ``D`` object is the only source of data.  This is deliberately a
    response-time transformation: the saved HTML is never rewritten and no
    current database/source files are read.  Unknown/partial HTML falls back
    to the narrow salary-label compatibility pass used by older snapshots.
    """
    if not isinstance(html, str) or not isinstance(template, str):
        raise TypeError('dashboard HTML and template must be text')
    # Scope the replacement to the generated dashboard format.  Arbitrary
    # saved HTML (including small compatibility fixtures) must not be replaced
    # by a full application document.
    required = (
        'function renderAll(', 'function barsLine(', 'id="ch-main"',
        'id="daily-modal-ov"',
    )
    if not all(marker in html for marker in required):
        return None
    dataset = _extract_saved_dataset(html)
    if dataset is None:
        return None

    # Current JS treats these as empty sources.  Existing financial arrays are
    # untouched, and no monthly value is made into a daily observation.
    dataset = dict(dataset)
    had_daily_temporal = 'daily_temporal' in dataset
    daily_charts = dataset.get('daily_charts')
    if isinstance(daily_charts, dict):
        daily_charts = dict(daily_charts)
        if not had_daily_temporal:
            daily_charts['legacy_unknown'] = True
        dataset['daily_charts'] = daily_charts
    dataset.setdefault('daily_charts', {'dates': [], 'accounts': {}})
    dataset.setdefault('daily_temporal', {
        'dates': [],
        'available': {},
        'pl': {},
        'rev_dir': {},
        'cash': {},
        'salary': {},
    })
    rendered = template.replace('/*__DATA__*/', json_for_script(dataset), 1)
    return rendered
