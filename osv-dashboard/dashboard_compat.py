# -*- coding: utf-8 -*-
"""Response-only compatibility upgrades for saved dashboard HTML.

Saved dashboards are intentionally immutable artifacts.  This module only
rewrites the known salary/daily presentation snippets while a dashboard is
being served; it never parses or changes the embedded dataset.
"""
import re


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
