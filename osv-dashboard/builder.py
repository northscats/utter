# -*- coding: utf-8 -*-
"""
Сборка JSON-датасета дашборда из распарсенных ОСВ.

Универсальная версия:
  - Работает с любым планом счетов БП 3.0 (авто-фолбек если субсчетов нет).
  - Для суточных ОСВ (path3 = '') использует account-level totals.
  - build_daily_charts() строит суточные ряды из data[acc]['daily'].
  - Нейтральные тексты резюме без отраслевой специфики.
  - Адаптивные пороги (относительные, не абсолютные).
"""
import re
from collections import defaultdict

MONTH_NAMES = {
    '01': 'Январь', '02': 'Февраль', '03': 'Март', '04': 'Апрель',
    '05': 'Май', '06': 'Июнь', '07': 'Июль', '08': 'Август',
    '09': 'Сентябрь', '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь',
}
MONTH_SHORT = {
    '01': 'Янв', '02': 'Фев', '03': 'Мар', '04': 'Апр', '05': 'Май',
    '06': 'Июн', '07': 'Июл', '08': 'Авг', '09': 'Сен', '10': 'Окт',
    '11': 'Ноя', '12': 'Дек',
}
ACC_LABELS = {
    '10': 'Материалы (10)',       '20': 'Осн. производство (20)',
    '25': 'Общепроизв. (25)',     '26': 'Управленческие (26)',
    '50': 'Касса (50)',           '51': 'Расчётный счёт (51)',
    '60': 'Поставщики — КЗ (60)','62': 'Покупатели — ДЗ (62)',
    '66': 'Кр/срочные займы (66)','68': 'Налоги (68)',
    '69': 'Страховые взносы (69)','70': 'Оплата труда (70)',
    '76': 'Прочие расчёты (76)', '90': 'Продажи (90)',
    '91': 'Прочие дох/расх (91)',
}


def _fm(n):
    m = n / 1e6
    s = f'{abs(m):.0f}' if abs(m) >= 100 else f'{abs(m):.1f}'.replace('.', ',')
    return ('−' if n < 0 else '') + s + ' млн ₽'


# ──────────────────────────────────────────────────────────────────────────────
class Src:
    """Удобный доступ к распарсенным данным с устойчивостью к пропускам."""

    def __init__(self, data):
        self.data = data

    def rows(self, acc):
        return self.data.get(acc, {}).get('rows', [])

    def daily(self, acc):
        return self.data.get(acc, {}).get('daily', [])

    def top(self, acc):
        return self.data.get(acc, {}).get('top',
               dict(snd=0, snk=0, obd=0, obk=0, skd=0, skk=0))

    def has(self, acc):
        return acc in self.data


# ──────────────────────────────────────────────────────────────────────────────
def _msum(daily, months, key, flt=None):
    acc = defaultdict(float)
    for d in daily:
        if flt is None or flt(d):
            acc[d['month']] += d.get(key, 0)
    return [round(acc.get(m, 0.0), 2) for m in months]


def _try_msum(daily, months, key, *filters):
    """Пробует фильтры по порядку, возвращает первый ненулевой результат.
    Если ни один фильтр не дал ненулевых значений — возвращает суммарный."""
    for flt in filters:
        result = _msum(daily, months, key, flt)
        if any(v != 0 for v in result):
            return result
    return _msum(daily, months, key)


def _group_by(daily, months, group_key, val_key, flt=None):
    g = defaultdict(lambda: defaultdict(float))
    for d in daily:
        if flt is None or flt(d):
            g[d.get(group_key, '')][d['month']] += d.get(val_key, 0)
    return {name: [round(mm.get(m, 0.0), 2) for m in months]
            for name, mm in g.items() if name}


def _rel_threshold(values, frac=0.001, minimum=1.0):
    """Адаптивный порог = max(frac × max_abs, minimum)."""
    if not values:
        return minimum
    return max(max(abs(v) for v in values) * frac, minimum)


# ──────────────────────────────────────────────────────────────────────────────
def build_daily_charts(data):
    """
    Строит суточные ряды из data[acc]['daily'].
    Каждая запись daily должна содержать:
        date (str YYYY-MM-DD), month, obd, obk
        и опционально: skd, skk, snd, snk  (если переданы из DailyRow).
    Возвращает dict:
        dates   — список дат (str YYYY-MM-DD)
        accounts — dict {acc: {label, obd, obk, balance}}
    """
    if not data:
        return {'dates': [], 'accounts': {}}

    all_dates: set = set()
    for acc_data in data.values():
        for d in acc_data.get('daily', []):
            if d.get('date'):
                all_dates.add(d['date'])

    dates = sorted(all_dates)
    if not dates:
        return {'dates': [], 'accounts': {}}

    date_idx = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    # Порядок отображения счетов
    PRIORITY = ['51', '62', '60', '70', '20', '25', '26', '50', '90', '91',
                '68', '69', '76', '10', '66']
    acc_order = PRIORITY + sorted(set(data.keys()) - set(PRIORITY))

    accounts = {}
    for acc in acc_order:
        if acc not in data:
            continue
        daily_entries = data[acc].get('daily', [])
        if not daily_entries:
            continue

        obd  = [0.0]  * n
        obk  = [0.0]  * n
        skd  = [None] * n
        skk  = [None] * n

        for d in daily_entries:
            i = date_idx.get(d.get('date', ''))
            if i is None:
                continue
            obd[i] = round(d.get('obd', 0), 2)
            obk[i] = round(d.get('obk', 0), 2)
            if d.get('skd') is not None:
                skd[i] = round(d['skd'], 2)
            if d.get('skk') is not None:
                skk[i] = round(d['skk'], 2)

        has_balance = any(v is not None for v in skd) or any(v is not None for v in skk)
        balance = None
        if has_balance:
            balance = [round((d or 0) - (k or 0), 2) for d, k in zip(skd, skk)]

        if not any(obd) and not any(obk) and not has_balance:
            continue

        accounts[acc] = {
            'label':   ACC_LABELS.get(acc, f'Счёт {acc}'),
            'obd':     obd,
            'obk':     obk,
            'balance': balance,
        }

    return {'dates': dates, 'accounts': accounts}


# ──────────────────────────────────────────────────────────────────────────────
def build_dataset(data, company_name=''):
    s = Src(data)

    # ── месяцы периода
    months = sorted({d['month'] for a in data.values()
                     for d in a.get('daily', [])})
    if not months:
        raise ValueError(
            'Ни в одном файле не найдены строки «Обороты за дату» — '
            'выгрузите ОСВ с детализацией по дням/месяцам.')

    # ── наличие аналитики (path3 непустой)
    d90, d91 = s.daily('90'), s.daily('91')
    has_path3 = any(d.get('path3') for d in d90 + d91)

    # ── P&L
    if has_path3:
        rev = _try_msum(d90, months, 'obk',
                        lambda d: d['path3'] == '90.01.1',
                        lambda d: d['path3'].startswith('90.01'))
        nds  = _msum(d90, months, 'obd', lambda d: d['path3'] == '90.03')
        cost = _try_msum(d90, months, 'obd',
                         lambda d: d['path3'] == '90.02.1',
                         lambda d: d['path3'].startswith('90.02'))
        adm  = _msum(s.daily('26'), months, 'obk')
        oinc = _try_msum(d91, months, 'obk',
                         lambda d: d['path3'].startswith('91.01'))
        oexp_d = _try_msum(d91, months, 'obd',
                           lambda d: d['path3'].startswith('91.02'))
        oexp_k = _msum(d91, months, 'obk',
                       lambda d: d['path3'].startswith('91.02'))
        oexp   = [round(a - b, 2) for a, b in zip(oexp_d, oexp_k)]
        close90 = _msum(d90, months, 'obk', lambda d: d['path3'] == '90.09')
        close91 = _msum(d91, months, 'obd', lambda d: d['path3'].startswith('91.09'))
        has_close = True
    else:
        # Суточные данные: только account-level totals, без субсчетов
        rev   = _msum(d90, months, 'obk')          # брутто с НДС
        nds   = [0.0] * len(months)
        cost  = _msum(d90, months, 'obd')          # все дебеты счёта 90
        adm   = _msum(s.daily('26'), months, 'obk')
        oinc  = _msum(d91, months, 'obk')
        oexp  = _msum(d91, months, 'obd')
        close90 = close91 = [0.0] * len(months)
        has_close = False

    # ── статус закрытия
    closing, ghost = [], []
    for i in range(len(months)):
        if not has_close:
            closing.append('данные частичные'); ghost.append(False)
        elif close90[i] > 0 or close91[i] > 0:
            closing.append('закрыт'); ghost.append(False)
        elif cost[i] > 0:
            closing.append('фин.рез. не списан'); ghost.append(False)
        else:
            closing.append('НЕ ЗАКРЫТ'); ghost.append(True)

    # ── аналитики выручки и затрат
    if has_path3:
        rev_dir   = _group_by(d90, months, 'path1', 'obk',
                              lambda d: d['path3'].startswith('90.01'))
        cost_dep  = _group_by(d90, months, 'path2', 'obd',
                              lambda d: d['path3'].startswith('90.02'))
        ci_src    = [d for a in ('20', '25', '26') for d in s.daily(a)]
        cost_items = _group_by(ci_src, months, 'path1', 'obd') if ci_src else {}
    else:
        rev_dir    = {}
        cost_dep   = {}
        ci_src     = [d for a in ('20', '25', '26') for d in s.daily(a)]
        cost_items = _group_by(ci_src, months, 'path1', 'obd') if ci_src else {}

    cost_acc = {a: _msum(s.daily(a), months, 'obd')
                for a in ('20', '25', '26') if s.has(a)}

    # ── ДЗ / КЗ по контрагентам (адаптивный порог)
    def partners(acc, sub, sn_key, sk_key):
        agg = defaultdict(lambda: [0.0, 0.0])
        for r in s.rows(acc):
            if r.get('p1') == sub and r.get('lvl') == 3:
                agg[r['label']][0] += r[sn_key]
                agg[r['label']][1] += r[sk_key]
        all_vals = [max(abs(v[0]), abs(v[1])) for v in agg.values()]
        thr = _rel_threshold(all_vals, frac=0.001, minimum=1.0)
        out = [[n, round(v[0], 2), round(v[1], 2)] for n, v in agg.items()
               if abs(v[0]) > thr or abs(v[1]) > thr]
        return sorted(out, key=lambda x: -abs(x[2]))

    dz = partners('62', '62.01', 'snd', 'skd')
    kz = partners('60', '60.01', 'snk', 'skk')

    def sub_row(acc, label):
        return next((r for r in s.rows(acc) if r.get('label') == label), None)

    r6201 = sub_row('62', '62.01')
    r6001 = sub_row('60', '60.01')
    r6202 = sub_row('62', '62.02')
    r6002 = sub_row('60', '60.02')
    dz_tot = dict(sn=round(r6201['snd'], 2) if r6201 else 0,
                  sk=round(r6201['skd'], 2) if r6201 else 0)
    kz_tot = dict(sn=round(r6001['snk'], 2) if r6001 else 0,
                  sk=round(r6001['skk'], 2) if r6001 else 0)

    # ── деньги
    t51, t50 = s.top('51'), s.top('50')
    cf = dict(
        inn=_msum(s.daily('51'), months, 'obd'),
        out=_msum(s.daily('51'), months, 'obk'),
        b51_start=round(t51['snd'] - t51['snk'], 2),
        b51_end  =round(t51['skd'] - t51['skk'], 2),
        b50_start=round(t50['snd'] - t50['snk'], 2),
        b50_end  =round(t50['skd'] - t50['skk'], 2),
    )
    kassa = sorted([[r['label'], round(r['obd'], 2), round(r['obk'], 2)]
                    for r in s.rows('50') if r.get('lvl') == 2],
                   key=lambda x: -x[1])

    # ── налоги, ФОТ
    NAL_NAMES = {
        '68.01': 'НДФЛ', '68.02': 'НДС', '68.04': 'Налог на прибыль',
        '68.05': 'Прочие налоги', '68.07': 'Транспортный налог',
        '68.08': 'Налог на имущество', '68.10': 'Прочие',
        '68.90': 'Единый налоговый счет (ЕНС)',
        '69.01': 'Соцстрах (до 2023)', '69.02': 'ПФР (до 2023)',
        '69.09': 'Страховые взносы (единый тариф)',
        '69.11': 'Взносы от НС и ПЗ',
    }
    nal = [[r['label'], NAL_NAMES.get(r['label'], r['label']),
            round(r['snk'] - r['snd'], 2), round(r['obd'], 2),
            round(r['obk'], 2), round(r['skk'] - r['skd'], 2)]
           for a in ('68', '69') for r in s.rows(a) if r.get('lvl') == 1]
    zp = dict(n=_msum(s.daily('70'), months, 'obk'),
              v=_msum(s.daily('70'), months, 'obd'))

    # ── прочие доходы/расходы 91 (адаптивный порог)
    vals91 = [abs(r['obk']) for r in s.rows('91') if r.get('lvl') == 3]
    thr91  = _rel_threshold(vals91, frac=0.01, minimum=1000.0)

    def stat91(sub, plus, minus):
        agg = defaultdict(float)
        for r in s.rows('91'):
            if r.get('p1') == sub and r.get('lvl') == 3:
                agg[r['label']] += r[plus] - (r[minus] if minus else 0)
        return sorted([[n, round(v, 2)] for n, v in agg.items()
                       if abs(v) > thr91], key=lambda x: -x[1])

    i91 = stat91('91.01', 'obk', None)
    e91 = stat91('91.02', 'obd', 'obk')

    rez_created  = sum(r['obd'] for r in s.rows('91')
                       if r.get('p1') == '91.02' and r.get('lvl') == 3
                       and 'резерв' in r.get('label', '').lower())
    rez_restored = sum(r['obk'] for r in s.rows('91')
                       if r.get('p1') == '91.01' and r.get('lvl') == 3
                       and 'резерв' in r.get('label', '').lower())
    r7602      = sub_row('76', '76.02')
    pretenzii  = round(r7602['skd'], 2) if r7602 else 0

    # ── кредиты и займы (66 краткосрочные, 67 долгосрочные)
    t66, t67 = s.top('66'), s.top('67')
    credits = dict(
        sn66=round(max(0.0, t66['snk'] - t66['snd']), 2),
        sk66=round(max(0.0, t66['skk'] - t66['skd']), 2),
        sn67=round(max(0.0, t67['snk'] - t67['snd']), 2),
        sk67=round(max(0.0, t67['skk'] - t67['skd']), 2),
    )
    credits['sn'] = credits['sn66'] + credits['sn67']
    credits['sk'] = credits['sk66'] + credits['sk67']

    # ── коммерческие расходы сч. 44
    comm44 = _msum(s.daily('44'), months, 'obd')

    # ── служебные метки периода
    ys = sorted({m[:4] for m in months})
    period_label     = (f"{MONTH_NAMES[months[0][5:]]}–{MONTH_NAMES[months[-1][5:]]} {ys[-1]}"
                        if months else '')
    period_end_label = (f"{MONTH_NAMES[months[-1][5:]]} {months[-1][:4]}"
                        if months else '')

    open_m = [MONTH_NAMES[months[i][5:]] for i in range(len(months)) if ghost[i]]
    part_m = [MONTH_NAMES[months[i][5:]] for i in range(len(months))
              if closing[i] == 'фин.рез. не списан']

    if not has_close:
        hazard = ('<b>Суточные данные:</b> загружены только суточные ОСВ — '
                  'месячное закрытие не отслеживается. '
                  'Для полного P&amp;L загрузите квартальную или полугодовую выгрузку.')
    elif open_m:
        hazard = (f"<b>Внимание к данным:</b> {', '.join(open_m).lower()} в учёте "
                  f"не закрыт(ы) — затраты отражены не полностью. "
                  f"Показатели прибыли за эти месяцы недостоверны.")
        if part_m:
            hazard += (f" {', '.join(part_m)}: затраты собраны, "
                       f"но финрезультат не списан на сч. 99.")
    elif part_m:
        hazard = (f"<b>Примечание:</b> {', '.join(part_m).lower()} — "
                  f"затраты собраны, но финрезультат не списан на сч. 99 "
                  f"(рассчитан аналитически).")
    else:
        hazard = ''

    # ── нейтральное резюме
    ok_idx = [i for i in range(len(months)) if not ghost[i]]

    def ssum(arr, idx=None):
        if idx is None:
            idx = ok_idx
        return sum(arr[i] for i in idx)

    revn_ok  = ssum(rev) - ssum(nds)
    spend_ok = ssum(cost) + ssum(adm)
    sales_ok = revn_ok - spend_ok
    net_ok   = sales_ok + ssum(oinc) - ssum(oexp)
    dz_delta = dz_tot['sk'] - dz_tot['sn']
    kz_delta = kz_tot['sk'] - kz_tot['sn']

    pop      = sum(r[2] for r in dz
                   if re.search(r'население|физические лица|физлиц', r[0], re.I))
    tax_debt = sum(max(r[5], 0) for r in nal)
    subs     = sum(v for n, v in i91 if 'субсиди' in n.lower())
    peni     = sum(v for n, v in i91
                   if re.search(r'пени|повышающ|штраф', n.lower()))

    cash_end    = cf['b51_end'] + cf['b50_end']
    avg_out_all = sum(cf['out']) / max(len(months), 1)
    cash_days   = cash_end / (avg_out_all / 21) if avg_out_all else 0
    top_kz      = kz[0] if kz else ['—', 0, 0]
    ok_lbl      = (f"{MONTH_NAMES[months[ok_idx[0]][5:]].lower()}–"
                   f"{MONTH_NAMES[months[ok_idx[-1]][5:]].lower()}") if ok_idx else '—'
    sales_word  = 'убыточна' if sales_ok < 0 else 'прибыльна'

    points = []
    # Факт 1: основная деятельность
    if revn_ok != 0 or spend_ok != 0:
        nds_note = ' (без НДС)' if has_path3 else ' (брутто)'
        points.append(
            f"<b>Основная деятельность {sales_word}.</b> "
            f"За {ok_lbl}: доходы {_fm(revn_ok)}{nds_note}, "
            f"расходы {_fm(spend_ok)} — "
            f"результат <span class=\"{'bad' if sales_ok < 0 else 'ok'}\">"
            f"{_fm(sales_ok)}</span>."
        )
    # Факт 2: ДЗ
    if dz_tot['sk'] != 0 or dz_delta != 0:
        s_dz = f"<b>Дебиторская задолженность.</b> На конец периода: {_fm(dz_tot['sk'])}"
        if dz_delta:
            s_dz += (f" (<span class=\"{'bad' if dz_delta>0 else 'ok'}\">"
                     f"{'+' if dz_delta>=0 else ''}{_fm(dz_delta)}</span> за период)")
        if pop > 0:
            s_dz += f", из них {_fm(pop)} — население / физлица"
        if rez_created > rez_restored:
            s_dz += f". Признано сомнительными: {_fm(rez_created - rez_restored)}"
        points.append(s_dz + '.')
    # Факт 3: КЗ
    if kz_tot['sk'] != 0 or kz_delta != 0:
        s_kz = f"<b>Кредиторская задолженность.</b> На конец периода: {_fm(kz_tot['sk'])}"
        if kz_delta:
            s_kz += (f" (<span class=\"{'bad' if kz_delta>0 else 'ok'}\">"
                     f"{'+' if kz_delta>=0 else ''}{_fm(kz_delta)}</span> за период)")
        if top_kz[0] != '—':
            s_kz += f", крупнейший кредитор — {top_kz[0]} ({_fm(top_kz[2])})"
        if tax_debt > 0:
            s_kz += f". Долг перед бюджетом: {_fm(tax_debt)}"
        points.append(s_kz + '.')
    # Факт 4: прочие доходы
    if i91 or subs or peni:
        s_oth = "<b>Прочие доходы/расходы.</b>"
        if subs > 0:
            s_oth += f" Субсидии — {_fm(subs)}."
        if peni > 0:
            s_oth += f" Пени и штрафы — {_fm(peni)}."
        if net_ok != sales_ok:
            cls = 'bad' if net_ok < 0 else 'ok'
            s_oth += (f" Итог с учётом прочих: "
                      f"<span class=\"{cls}\">{_fm(net_ok)}</span>.")
        points.append(s_oth)
    # Факт 5: деньги
    if cash_end != 0 or avg_out_all != 0:
        if 0 < cash_days < 60:
            points.append(
                f"<b>Запас денег.</b> На счетах и в кассе {_fm(cash_end)} "
                f"при среднемесячных платежах {_fm(avg_out_all)} — "
                f"≈\u202f{max(cash_days, 0):.0f} рабочих дней."
            )
        else:
            points.append(f"<b>Запас денег.</b> На счетах и в кассе {_fm(cash_end)}.")

    if not points:
        points.append("<b>Данных для резюме недостаточно.</b> "
                      "Загрузите месячные/квартальные ОСВ для полного анализа.")

    foot_items = []
    if dz_delta > 0:
        foot_items.append('контроль дебиторской задолженности')
    if kz_delta > 0:
        foot_items.append('согласование графика погашения с кредиторами')
    if 0 < cash_days < 10:
        foot_items.append('обеспечение текущей ликвидности')
    if open_m:
        foot_items.append(f"закрыть в учёте: {', '.join(m.lower() for m in open_m)}")
    if not has_path3:
        foot_items.append('загрузить периодические ОСВ для детального P&L')
    foot = ('<b>Приоритеты:</b> ' + '; '.join(foot_items) + '.') if foot_items else ''

    lead_parts = []
    if revn_ok != 0:
        lead_parts.append(f"Основная деятельность {sales_word}.")
    if dz_tot['sk']:
        lead_parts.append(f"Дебиторская задолженность на конец периода — {_fm(dz_tot['sk'])}.")
    if cash_end:
        lead_parts.append(f"На счетах {_fm(cash_end)}.")
    lead = ' '.join(lead_parts) or 'Данные загружены. Используйте разделы ниже для анализа.'

    brief = dict(
        title_note=(f"— данные за {ok_lbl}" if ok_lbl != '—' else ''),
        lead=lead,
        points=points,
        foot=foot,
        is_loss=sales_ok < 0,
    )

    # ── суточные графики
    daily_charts = build_daily_charts(data)

    # ── флаги наличия разделов
    sections = dict(
        has_pl       =s.has('90') or s.has('91'),
        has_rev_dir  =bool(rev_dir),
        has_cost_items=bool(cost_items),
        has_dz       =bool(r6201) or bool(dz),
        has_kz       =bool(r6001) or bool(kz),
        has_cash     =s.has('51') or s.has('50'),
        has_taxes    =bool(nal),
        has_zp       =s.has('70'),
        has_91       =bool(i91) or bool(e91),
        has_credits  =credits['sk'] > 0,
        has_comm44   =s.has('44'),
        has_daily    =bool(daily_charts['dates']),
        has_subaccs  =has_path3,
    )

    dataset = dict(
        company_name   =company_name,
        months         =months,
        month_names    =[MONTH_NAMES[m[5:]] for m in months],
        month_short    =[MONTH_SHORT[m[5:]] for m in months],
        closing_label  =closing,
        ghost          =ghost,
        period_label   =period_label,
        period_end_label=period_end_label,
        hazard         =hazard,
        brief          =brief,
        pl=dict(rev=rev, nds=nds, cost=cost, adm=adm, oinc=oinc, oexp=oexp),
        rev_dir        =rev_dir,
        cost_dep       =cost_dep,
        cost_items     =cost_items,
        cost_acc       =cost_acc,
        dz=dz, kz=kz, dz_tot=dz_tot, kz_tot=kz_tot,
        avans_pol      =round(r6202['skk'], 2) if r6202 else 0,
        avans_vyd      =round(r6002['skd'], 2) if r6002 else 0,
        cf=cf, kassa=kassa, nal=nal, zp=zp, i91=i91, e91=e91,
        credits=credits, comm44=comm44,
        rezerv=dict(created=round(rez_created, 2), restored=round(rez_restored, 2)),
        pretenzii      =pretenzii,
        daily_charts   =daily_charts,
        sections       =sections,
        accounts_loaded=sorted(data.keys()),
    )
    # Статический дашборд получает уже рассчитанные сервером KPI и не
    # воспроизводит финансовые формулы в браузере.
    dataset['server_kpis'] = extract_kpis(dataset)
    return dataset


# ──────────────────────────────────────────────────────────────────────────────
# KPI-снимок для сводной матрицы главной страницы
# ──────────────────────────────────────────────────────────────────────────────
def extract_kpis(dataset):
    """Вычисляет 10 показателей рейтинга из dataset.
    Возвращает dict {metrics, signals, display, score, counts}."""
    pl       = dataset.get('pl', {})
    rev_arr  = pl.get('rev', [])
    nds_arr  = pl.get('nds', [])
    cost_arr = pl.get('cost', [])
    adm_arr  = pl.get('adm', [])

    rev_total  = sum(rev_arr)
    nds_total  = sum(nds_arr)
    rev_net    = rev_total - nds_total
    cost_total = sum(cost_arr)
    adm_total  = sum(adm_arr)
    ebitda     = rev_net - cost_total - adm_total

    dz_tot = dataset.get('dz_tot', {})
    kz_tot = dataset.get('kz_tot', {})
    dz_sk  = dz_tot.get('sk', 0) or 0
    kz_sk  = kz_tot.get('sk', 0) or 0

    cf       = dataset.get('cf', {})
    cash_end = (cf.get('b51_end', 0) or 0) + (cf.get('b50_end', 0) or 0)

    credits      = dataset.get('credits', {})
    credits_sk   = credits.get('sk', 0) or 0
    credits_sn   = credits.get('sn', 0) or 0
    credits_avg  = (credits_sk + credits_sn) / 2

    e91      = dataset.get('e91', [])
    interest = sum(v for n, v in e91 if 'процент' in n.lower())

    comm44       = dataset.get('comm44', [])
    comm44_total = sum(comm44) if comm44 else -1

    zp        = dataset.get('zp', {})
    zp_total  = sum(zp.get('v', []))

    nal        = dataset.get('nal', [])
    tax_social = sum(r[4] for r in nal if r[0].startswith('69') or r[0] == '68.01')

    n_months = len(dataset.get('months', [])) or 1
    n_days   = n_months * 30

    def _safe(a, b):
        return (a / b) if b else None

    raw = {
        'dz_days':    _safe(dz_sk * n_days, rev_net)            if rev_net > 0 and dz_sk > 0    else None,
        'kz_dz':      _safe(kz_sk, dz_sk)                       if dz_sk > 0                    else None,
        'liquidity':  _safe(cash_end, kz_sk)                    if kz_sk > 0                    else None,
        'debt_ebitda':_safe(credits_sk - cash_end, ebitda)      if credits_sk > 0 and ebitda > 0 else None,
        'rate':       _safe(interest / credits_avg / n_months * 12 * 100, 1)
                                                                  if credits_avg > 0 and interest > 0 else None,
        'tax_burden': _safe(tax_social * 100, zp_total)         if zp_total > 0                 else None,
        'nvv_pct':    None,
        'cost_pct':   _safe(cost_total * 100, rev_net)          if rev_net > 0                  else None,
        'comm_pct':   _safe(comm44_total * 100, rev_net)        if comm44_total >= 0 and rev_net > 0 else None,
        'adm_pct':    _safe(adm_total * 100, rev_net)           if rev_net > 0                  else None,
    }

    from rating import metric_signal
    signals = {key: metric_signal(value, key) for key, value in raw.items()}

    def _fmt(key, v):
        if v is None:
            return '—'
        fmts = {
            'dz_days':    lambda x: f'{round(x)} дн.',
            'kz_dz':      lambda x: f'{x:.2f}×',
            'liquidity':  lambda x: f'{x:.2f}',
            'debt_ebitda':lambda x: f'{x:.1f}×',
            'rate':       lambda x: f'{x:.1f}%',
            'tax_burden': lambda x: f'{x:.1f}%',
            'nvv_pct':    lambda x: '—',
            'cost_pct':   lambda x: f'{x:.1f}%',
            'comm_pct':   lambda x: f'{x:.1f}%',
            'adm_pct':    lambda x: f'{x:.1f}%',
        }
        return fmts.get(key, str)(v)

    display = {k: _fmt(k, v) for k, v in raw.items()}
    display['nvv_pct'] = '—'

    counts = {'green': 0, 'yellow': 0, 'red': 0, 'na': 0}
    for s in signals.values():
        counts[s] = counts.get(s, 0) + 1
    scoreable = counts['green'] + counts['yellow'] + counts['red'] or 1
    score = round((counts['green'] * 100 + counts['yellow'] * 50) / (scoreable * 100) * 100)

    return dict(metrics=raw, signals=signals, display=display, score=score, counts=counts)
