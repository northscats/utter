# -*- coding: utf-8 -*-
"""
Сборка JSON-датасета дашборда из распарсенных ОСВ.
Месяцы, статусы закрытия и текст резюме определяются по данным.
"""
import re
from collections import defaultdict

MONTH_NAMES = {'01': 'Январь', '02': 'Февраль', '03': 'Март', '04': 'Апрель',
               '05': 'Май', '06': 'Июнь', '07': 'Июль', '08': 'Август',
               '09': 'Сентябрь', '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь'}
MONTH_SHORT = {'01': 'Янв', '02': 'Фев', '03': 'Мар', '04': 'Апр', '05': 'Май',
               '06': 'Июн', '07': 'Июл', '08': 'Авг', '09': 'Сен', '10': 'Окт',
               '11': 'Ноя', '12': 'Дек'}


def _fm(n):
    m = n / 1e6
    s = f'{abs(m):.0f}' if abs(m) >= 100 else f'{abs(m):.1f}'.replace('.', ',')
    return ('−' if n < 0 else '') + s + ' млн ₽'


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
                                          dict(snd=0, snk=0, obd=0, obk=0,
                                               skd=0, skk=0))

    def has(self, acc):
        return acc in self.data


def _msum(daily, months, key, flt=None):
    acc = defaultdict(float)
    for d in daily:
        if flt is None or flt(d):
            acc[d['month']] += d[key]
    return [round(acc.get(m, 0.0), 2) for m in months]


def _group_by(daily, months, group_key, val_key, flt=None):
    g = defaultdict(lambda: defaultdict(float))
    for d in daily:
        if flt is None or flt(d):
            g[d[group_key]][d['month']] += d[val_key]
    return {name: [round(mm.get(m, 0.0), 2) for m in months]
            for name, mm in g.items()}


def build_dataset(data):
    s = Src(data)

    # ---- месяцы периода
    months = sorted({d['month'] for a in data.values() for d in a['daily']})
    if not months:
        raise ValueError('Ни в одном файле не найдены строки "Обороты за дату" — '
                         'выгрузите ОСВ с детализацией по дням/месяцам.')
    mi = {m: i for i, m in enumerate(months)}

    # ---- P&L из 90 / 91 / 26
    d90, d91 = s.daily('90'), s.daily('91')
    rev = _msum(d90, months, 'obk', lambda d: d['path3'] == '90.01.1')
    nds = _msum(d90, months, 'obd', lambda d: d['path3'] == '90.03')
    cost = _msum(d90, months, 'obd', lambda d: d['path3'] == '90.02.1')
    adm = _msum(s.daily('26'), months, 'obk')
    oinc = _msum(d91, months, 'obk', lambda d: d['path3'].startswith('91.01'))
    oexp_d = _msum(d91, months, 'obd', lambda d: d['path3'].startswith('91.02'))
    oexp_k = _msum(d91, months, 'obk', lambda d: d['path3'].startswith('91.02'))
    oexp = [round(a - b, 2) for a, b in zip(oexp_d, oexp_k)]

    # ---- статус закрытия месяца
    close90 = _msum(d90, months, 'obk', lambda d: d['path3'] == '90.09')
    close91 = _msum(d91, months, 'obd', lambda d: d['path3'].startswith('91.09'))
    closing, ghost = [], []
    for i, m in enumerate(months):
        if close90[i] > 0 or close91[i] > 0:
            closing.append('закрыт'); ghost.append(False)
        elif cost[i] > 0:
            closing.append('фин.рез. не списан'); ghost.append(False)
        else:
            closing.append('НЕ ЗАКРЫТ'); ghost.append(True)

    # ---- аналитики
    rev_dir = _group_by(d90, months, 'path1', 'obk',
                        lambda d: d['path3'] == '90.01.1')
    cost_dep = _group_by(d90, months, 'path2', 'obd',
                         lambda d: d['path3'] == '90.02.1')
    cost_items = _group_by(
        [d for a in ('20', '25', '26') for d in s.daily(a)],
        months, 'path1', 'obd')
    cost_acc = {a: _msum(s.daily(a), months, 'obd') for a in ('20', '25', '26')}

    # ---- ДЗ / КЗ по контрагентам
    def partners(acc, sub, sn_key, sk_key):
        agg = defaultdict(lambda: [0.0, 0.0])
        for r in s.rows(acc):
            if r['p1'] == sub and r['lvl'] == 3:
                agg[r['label']][0] += r[sn_key]
                agg[r['label']][1] += r[sk_key]
        out = [[n, round(v[0], 2), round(v[1], 2)] for n, v in agg.items()
               if abs(v[0]) > 1 or abs(v[1]) > 1]
        return sorted(out, key=lambda x: -x[2])

    dz = partners('62', '62.01', 'snd', 'skd')
    kz = partners('60', '60.01', 'snk', 'skk')

    def sub_row(acc, label):
        return next((r for r in s.rows(acc) if r['label'] == label), None)

    r6201 = sub_row('62', '62.01')
    r6001 = sub_row('60', '60.01')
    r6202 = sub_row('62', '62.02')
    r6002 = sub_row('60', '60.02')
    dz_tot = dict(sn=round(r6201['snd'], 2) if r6201 else 0,
                  sk=round(r6201['skd'], 2) if r6201 else 0)
    kz_tot = dict(sn=round(r6001['snk'], 2) if r6001 else 0,
                  sk=round(r6001['skk'], 2) if r6001 else 0)

    # ---- деньги
    t51, t50 = s.top('51'), s.top('50')
    cf = dict(inn=_msum(s.daily('51'), months, 'obd'),
              out=_msum(s.daily('51'), months, 'obk'),
              b51_start=round(t51['snd'] - t51['snk'], 2),
              b51_end=round(t51['skd'] - t51['skk'], 2),
              b50_start=round(t50['snd'] - t50['snk'], 2),
              b50_end=round(t50['skd'] - t50['skk'], 2))
    kassa = sorted([[r['label'], round(r['obd'], 2), round(r['obk'], 2)]
                    for r in s.rows('50') if r['lvl'] == 2],
                   key=lambda x: -x[1])

    # ---- налоги, ФОТ
    NAL_NAMES = {'68.01': 'НДФЛ', '68.02': 'НДС', '68.04': 'Налог на прибыль',
                 '68.05': 'Прочие налоги', '68.07': 'Транспортный налог',
                 '68.08': 'Налог на имущество', '68.10': 'Прочие',
                 '68.90': 'Единый налоговый счет (ЕНС)',
                 '69.01': 'Соцстрах (до 2023)', '69.02': 'ПФР (до 2023)',
                 '69.09': 'Страховые взносы (единый тариф)',
                 '69.11': 'Взносы от НС и ПЗ'}
    nal = [[r['label'], NAL_NAMES.get(r['label'], ''),
            round(r['snk'] - r['snd'], 2), round(r['obd'], 2),
            round(r['obk'], 2), round(r['skk'] - r['skd'], 2)]
           for a in ('68', '69') for r in s.rows(a) if r['lvl'] == 1]
    zp = dict(n=_msum(s.daily('70'), months, 'obk'),
              v=_msum(s.daily('70'), months, 'obd'))

    # ---- 91 по статьям
    def stat91(sub, plus, minus):
        agg = defaultdict(float)
        for r in s.rows('91'):
            if r['p1'] == sub and r['lvl'] == 3:
                agg[r['label']] += r[plus] - (r[minus] if minus else 0)
        return sorted([[n, round(v, 2)] for n, v in agg.items()
                       if abs(v) > 1000], key=lambda x: -x[1])

    i91 = stat91('91.01', 'obk', None)
    e91 = stat91('91.02', 'obd', 'obk')

    rez_created = sum(v for n, v in
                      [(r['label'], r['obd']) for r in s.rows('91')
                       if r['p1'] == '91.02' and r['lvl'] == 3
                       and 'резерв' in r['label'].lower()])
    rez_restored = sum(v for n, v in
                       [(r['label'], r['obk']) for r in s.rows('91')
                        if r['p1'] == '91.01' and r['lvl'] == 3
                        and 'резерв' in r['label'].lower()])
    r7602 = sub_row('76', '76.02')
    pretenzii = round(r7602['skd'], 2) if r7602 else 0

    # ---- служебные тексты
    ys = sorted({m[:4] for m in months})
    period_label = (f"{MONTH_NAMES[months[0][5:]]}–{MONTH_NAMES[months[-1][5:]]} "
                    f"{ys[-1]}")
    open_m = [MONTH_NAMES[months[i][5:]] for i in range(len(months)) if ghost[i]]
    part_m = [MONTH_NAMES[months[i][5:]] for i in range(len(months))
              if closing[i] == 'фин.рез. не списан']
    hazard = ''
    if open_m:
        hazard = (f"<b>Внимание к данным:</b> {', '.join(open_m).lower()} в учете "
                  f"не закрыт(ы) — выручка начислена, а затраты (в т.ч. зарплата) "
                  f"еще не отражены. Показатели прибыли за эти месяцы и за период "
                  f"в целом недостоверны.")
        if part_m:
            hazard += (f" {', '.join(part_m)}: затраты собраны, но финрезультат "
                       f"не списан на сч. 99 (рассчитан аналитически).")
    elif part_m:
        hazard = (f"<b>Примечание:</b> {', '.join(part_m).lower()} — затраты "
                  f"собраны, но финрезультат не списан на сч. 99 "
                  f"(рассчитан аналитически).")

    # ---- резюме «Что происходит» (по достоверным месяцам)
    ok_idx = [i for i in range(len(months)) if not ghost[i]]
    def ssum(arr, idx=ok_idx):
        return sum(arr[i] for i in idx)
    revn_ok = ssum(rev) - ssum(nds)
    spend_ok = ssum(cost) + ssum(adm)
    sales_ok = revn_ok - spend_ok
    net_ok = sales_ok + ssum(oinc) - ssum(oexp)
    dz_delta = dz_tot['sk'] - dz_tot['sn']
    kz_delta = kz_tot['sk'] - kz_tot['sn']
    pop = sum(r[2] for r in dz if re.search(r'Население|Физические лица', r[0]))
    tax_debt = sum(max(r[5], 0) for r in nal)
    subs = sum(v for n, v in i91 if 'субсиди' in n.lower())
    peni = sum(v for n, v in i91
               if re.search(r'пени|повышающ|штраф', n.lower()))
    cash_end = cf['b51_end'] + cf['b50_end']
    avg_out = ssum(cf['out'], range(len(months))) / max(len(months), 1)
    cash_days = cash_end / (avg_out / 21) if avg_out else 0
    top_kz = kz[0] if kz else ['—', 0, 0]
    ok_lbl = (f"{MONTH_NAMES[months[ok_idx[0]][5:]].lower()}–"
              f"{MONTH_NAMES[months[ok_idx[-1]][5:]].lower()}") if ok_idx else '—'

    sales_word = 'убыточна' if sales_ok < 0 else 'прибыльна'
    brief = dict(
        title_note=(f"— суть за период простыми словами ({ok_lbl}"
                    + ("; " + ", ".join(m.lower() for m in open_m)
                       + " в учете еще не закрыт(ы))" if open_m else ")")),
        lead=("Предприятие продает тепло и воду дешевле, чем обходится их "
              "производство, а потребители еще и не полностью оплачивают счета. "
              "Разрыв закрывается субсидиями из бюджета и ростом долга перед "
              "поставщиками. Ситуация устойчива ровно до тех пор, пока поступают "
              "субсидии и поставщики терпят долг.") if sales_ok < 0 else
             ("Основная деятельность за период прибыльна, однако обратите "
              "внимание на динамику долгов и запас денежных средств ниже."),
        points=[
            (f"<b>Основная деятельность {sales_word}.</b> За {ok_lbl} заработано "
             f"{_fm(revn_ok)} (без НДС), потрачено на производство и управление "
             f"{_fm(spend_ok)} — <span class=\"bad\">{_fm(sales_ok)}</span>."),
            (f"<b>Долги потребителей.</b> Задолженность перед предприятием: "
             f"{_fm(dz_tot['sn'])} → {_fm(dz_tot['sk'])} "
             f"(<span class=\"bad\">{'+' if dz_delta >= 0 else ''}{_fm(dz_delta)}"
             f"</span> за период), из них {_fm(pop)} — население и физлица. "
             f"Признано сомнительными к взысканию еще "
             f"{_fm(rez_created - rez_restored)}."),
            (f"<b>Наши долги.</b> Поставщикам: {_fm(kz_tot['sn'])} → "
             f"{_fm(kz_tot['sk'])}, крупнейший кредитор — {top_kz[0]} "
             f"({_fm(top_kz[2])}). Долг перед бюджетом по налогам и взносам: "
             f"{_fm(tax_debt)}."),
            (f"<b>Прочие доходы сглаживают результат.</b> Субсидии {_fm(subs)}, "
             f"пени и повышающие коэффициенты {_fm(peni)}. Итог периода с их "
             f"учетом — <span class=\"bad\">{_fm(net_ok)}</span> (расчетно, "
             f"до налога на прибыль)."),
            (f"<b>Запас денег.</b> На счетах и в кассе {_fm(cash_end)} при "
             f"среднемесячных платежах {_fm(avg_out)} — примерно "
             f"{max(cash_days, 0):.1f} рабочего дня.".replace('.', ',', 1)
             if cash_days < 5 else
             f"<b>Запас денег.</b> На счетах и в кассе {_fm(cash_end)}."),
        ],
        foot=("<b>На чем сфокусироваться:</b> собираемость с населения "
              "(претензионно-исковая работа), компенсация выпадающих доходов / "
              "тарифное регулирование, график погашения с ключевыми "
              "поставщиками."
              + (" И закрыть в учете: " + ", ".join(m.lower() for m in open_m)
                 + " — пока это не сделано, итог периода занижен в лучшую "
                   "сторону." if open_m else "")),
    )

    return dict(
        months=months,
        month_names=[MONTH_NAMES[m[5:]] for m in months],
        month_short=[MONTH_SHORT[m[5:]] for m in months],
        closing_label=closing, ghost=ghost,
        period_label=period_label, hazard=hazard, brief=brief,
        pl=dict(rev=rev, nds=nds, cost=cost, adm=adm, oinc=oinc, oexp=oexp),
        rev_dir=rev_dir, cost_dep=cost_dep, cost_items=cost_items,
        cost_acc=cost_acc,
        dz=dz, kz=kz, dz_tot=dz_tot, kz_tot=kz_tot,
        avans_pol=round(r6202['skk'], 2) if r6202 else 0,
        avans_vyd=round(r6002['skd'], 2) if r6002 else 0,
        cf=cf, kassa=kassa, nal=nal, zp=zp, i91=i91, e91=e91,
        rezerv=dict(created=round(rez_created, 2),
                    restored=round(rez_restored, 2)),
        pretenzii=pretenzii,
    )
