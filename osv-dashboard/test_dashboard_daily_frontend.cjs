// Standalone synthetic regression checks for the legacy dashboard's daily 70 charts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync(`${__dirname}/templates/dashboard_template.html`, 'utf8');

function extractFunction(name) {
    const marker = `function ${name}(`;
    const start = template.indexOf(marker);
    assert.notEqual(start, -1, `missing ${name}()`);
    const open = template.indexOf('){', start) + 1;
    assert.ok(open > start, `missing body for ${name}()`);
    let depth = 0;
    for (let i = open; i < template.length; i += 1) {
        if (template[i] === '{') depth += 1;
        if (template[i] === '}') {
            depth -= 1;
            if (depth === 0) return template.slice(start, i + 1);
        }
    }
    throw new Error(`unterminated ${name}()`);
}

function makeNode(tagName = 'div') {
    const classes = new Set();
    const node = {
        tagName,
        attributes: {},
        children: [],
        style: {},
        textContent: '',
        value: '',
        max: '',
        classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); },
        },
        appendChild(child) {
            this.children.push(child);
            return child;
        },
        setAttribute(name, value) {
            this.attributes[name] = String(value);
        },
        querySelector(selector) {
            if (selector !== 'svg') return null;
            return this.children.find(child => child.tagName === 'svg') || null;
        },
    };
    let html = '';
    Object.defineProperty(node, 'innerHTML', {
        get() { return html; },
        set(value) {
            html = String(value);
            this.children = [];
        },
    });
    return node;
}

function makeElement(tagName, attributes, parent) {
    const node = makeNode(tagName);
    Object.entries(attributes || {}).forEach(([name, value]) => node.setAttribute(name, value));
    if (parent) parent.appendChild(node);
    return node;
}

function descendants(node, tagName) {
    return node.children.flatMap(child => [
        ...(child.tagName === tagName ? [child] : []),
        ...descendants(child, tagName),
    ]);
}

const nodes = new Map();
const getNode = id => {
    if (!nodes.has(id)) nodes.set(id, makeNode('div'));
    return nodes.get(id);
};
const shown = new Map();
const dates = ['2026-05-01', '2026-05-02', '2026-05-03'];
const account70 = {
    // Deliberately distinct values; zero and null exercise supported gaps.
    obd: [125, 0, null],
    obk: [0, 235, 465],
};

const context = vm.createContext({
    console,
    D: {
        daily_charts: {
            dates,
            accounts: { '70': account70 },
        },
    },
    document: {
        getElementById: getNode,
        addEventListener() {},
        removeEventListener() {},
        querySelectorAll() { return []; },
    },
    el: makeElement,
    fr(value) { return String(value); },
    show(id, value) { shown.set(id, value); },
    _dmBindSliders() {},
    _dmFill() {},
    _dmEsc() {},
    renderDailyDetail() {},
});

vm.runInContext([
    extractFunction('barsLine'),
    extractFunction('temporalUnavailable'),
    extractFunction('legacyDailySeries'),
    extractFunction('dailyLine'),
    extractFunction('dailyBarsPair'),
    extractFunction('renderDailyExec'),
    extractFunction('openDailyModal'),
    extractFunction('_dmRedraw'),
    extractFunction('renderAll'),
    'var _dmAcc = null, _dmDates = null;',
].join('\n'), context);

assert.match(
    template,
    /<span><i style="background:#2e6e8e"><\/i>ОбД70 — дебетовый оборот<\/span>/,
    'overview legend must identify debit turnover as ОбД70',
);
assert.match(
    template,
    /<span><i style="background:#7d97a8"><\/i>ОбК70 — начисления \(Кт\)<\/span>/,
    'overview legend must identify credit turnover as ОбК70',
);
assert.match(template, /Нулевой ОбД70 ≠ доказательство отсутствия выплаты/);

// Overview visualization: obd must remain the first (debit) series and obk the second.
context.renderDailyExec();
const overviewRects = descendants(getNode('ch-daily-70'), 'rect');
assert.equal(overviewRects.length, 3, 'zero/null values should not create bars');
const overviewTitles = overviewRects.map(rect =>
    rect.children.find(child => child.tagName === 'title').textContent,
);
assert.deepEqual(overviewTitles, [
    'ОбД70, 2026-05-01: 125 ₽',
    'ОбК70, 2026-05-02: 235 ₽',
    'ОбК70, 2026-05-03: 465 ₽',
]);
assert.equal(overviewRects[0].attributes.fill, '#2e6e8e');
assert.equal(overviewRects[1].attributes.fill, '#7d97a8');
assert.equal(overviewRects[2].attributes.fill, '#7d97a8');
assert.equal(shown.get('daily-70-wrap'), true);

// Sparse balance lines use calendar spacing and a dashed endpoint-to-endpoint
// bridge.  The missing day must not get a marker or hover title.
const lineBox = getNode('daily-gap-line');
context.dailyLine(lineBox, dates, [
    { n: 'Остаток', c: '#1f7a9c', v: [100, null, 300] },
]);
const linePaths = descendants(lineBox, 'path');
assert.equal(linePaths.length, 1, 'one bridge joins known endpoints');
assert.equal(linePaths[0].attributes['stroke-dasharray'], '6 4');
assert.equal(descendants(lineBox, 'circle').length, 2);
assert.equal(descendants(lineBox, 'title').length, 2,
    'missing dates do not receive fake hover values');

// Modal visualization: labels and source directions must survive the date slice.
context.openDailyModal('70');
assert.match(getNode('daily-modal-leg').innerHTML, /ОбД70/);
assert.match(getNode('daily-modal-leg').innerHTML, /ОбК70/);
assert.match(getNode('daily-modal-leg').innerHTML, /доказательство отсутствия выплаты/);
assert.deepEqual(
    descendants(getNode('daily-modal-chart'), 'rect')
        .map(rect => rect.children.find(child => child.tagName === 'title').textContent),
    overviewTitles,
);

getNode('dms0').value = '1';
getNode('dms1').value = '2';
context._dmRedraw();
assert.deepEqual(
    descendants(getNode('daily-modal-chart'), 'rect')
        .map(rect => rect.children.find(child => child.tagName === 'title').textContent),
    [
        'ОбК70, 2026-05-02: 235 ₽',
        'ОбК70, 2026-05-03: 465 ₽',
    ],
);

// Main legacy render path: zp.n is credit/accrual and zp.v is debit/payment.
Object.assign(context.D, {
    company_name: 'Synthetic ОСВ',
    period_label: 'Май–Июнь 2026',
    period_end_label: '30.06.2026',
    months: ['2026-05', '2026-06'],
    month_names: ['Май', 'Июнь'],
    month_short: ['Май', 'Июн'],
    closing_label: ['закрыт', 'закрыт'],
    ghost: [false, false],
    sections: {
        has_pl: false,
        has_rev_dir: false,
        has_cost_items: false,
        has_dz: false,
        has_kz: false,
        has_cash: true,
        has_taxes: false,
        has_zp: true,
        has_91: false,
        has_daily: false,
        has_subaccs: false,
    },
    brief: { is_loss: false, title_note: '', lead: '', points: [], foot: '' },
    pl: {
        rev: [0, 0], nds: [0, 0], cost: [0, 0], adm: [0, 0],
        oinc: [0, 0], oexp: [0, 0],
    },
    dz_tot: { sn: 0, sk: 0 },
    kz_tot: { sn: 0, sk: 0 },
    nal: [],
    cf: {
        inn: [0, 0], out: [0, 0],
        b51_start: 0, b51_end: 0, b50_start: 0, b50_end: 0,
    },
    dz: [],
    kz: [],
    cost_items: {},
    cost_dep: {},
    rezerv: { created: 0, restored: 0 },
    pretenzii: 0,
    kassa: [],
    daily_charts: {
        dates: ['2026-05-01', '2026-05-02'],
        accounts: {
            '51': { obd: [0, 50], obk: [20, 0] },
            '70': { obd: [333, 444], obk: [111, 222] },
        },
        legacy_unknown: true,
    },
    // Keep these asymmetric so a swapped series cannot pass accidentally.
    zp: { n: [111, 222], v: [333, 444] },
    accounts_loaded: ['70'],
});
context.MN = context.D.month_names;
context.MSHORT = context.D.month_short;
context.GHOST = context.D.ghost;
context.sel = [true, true];
context.fm = value => String(value);
context.sum = values => values.reduce((total, value) => total + value, 0);
context.selSum = values => values.reduce((total, value, index) =>
    total + (context.sel[index] ? value : 0), 0);
context.plRows = () => context.D.months.map((_, i) => ({
    i, revg: 0, nds: 0, revn: 0, cost: 0, adm: 0,
    sales: 0, oinc: 0, oexp: 0, other: 0, net: 0,
}));
context.hbars = () => {};
context.renderItems = () => {};
context.renderDeps = () => {};
context.renderDz = () => {};
context.renderKz = () => {};
context.table = () => {};

context.renderAll();
const zpTitles = descendants(getNode('ch-zp'), 'rect').map(rect =>
    rect.children.find(child => child.tagName === 'title').textContent,
);
assert.deepEqual(zpTitles, [
    'Начислено (Кт 70), 2026-05-01: 111 ₽',
    'Выплачено и удержано (Дт 70), 2026-05-01: 333 ₽',
    'Начислено (Кт 70), 2026-05-02: 222 ₽',
    'Выплачено и удержано (Дт 70), 2026-05-02: 444 ₽',
]);
assert.match(getNode('lg-zp').innerHTML, /Начислено/);
assert.match(getNode('lg-zp').innerHTML, /Выплачено и удержано/);
assert.match(getNode('note-zp').textContent, /старый снимок/);
assert.deepEqual(
    descendants(getNode('ch-cash'), 'rect')
        .map(rect => rect.children.find(child => child.tagName === 'title').textContent),
    [
        'Ушло со счета, 2026-05-01: 20 ₽',
        'Пришло на счет, 2026-05-02: 50 ₽',
    ],
);

console.log('PASS: daily 70 overview and modal preserve ОбД70/ОбК70 directions, gaps, and inference note');
console.log('PASS: main legacy render preserves distinct zp.n accrual and zp.v payment series');

// Legacy monthly tables receive chronologically ordered source rows. Sort by
// that order rather than by Russian month names (which repeat across years).
const legacyTableNode = {
    dataset: {}, _html: '', _headers: null,
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = value; this._headers = null; },
    querySelectorAll() {
        if (!this._headers) this._headers =
            [...this.innerHTML.matchAll(/<th data-i="(\d+)"/g)]
                .map(([, i]) => ({dataset: {i}, onclick: null}));
        return this._headers;
    }
};
const legacySortContext = vm.createContext({
    document: {getElementById: () => legacyTableNode},
    esc: value => String(value),
    fr: value => String(value),
});
vm.runInContext(extractFunction('table'), legacySortContext);
legacySortContext.table('months', [{h: 'Месяц', sort: 'period'}, {h: 'Сумма', f: 'n'}],
    [['Декабрь', 12], ['Январь', 1], ['Февраль', 2]], {total: true});
const tableMonths = () => [...legacyTableNode.innerHTML.matchAll(/<tr><td>([^<]+)<\/td>/g)]
    .map(([, month]) => month);
legacyTableNode.querySelectorAll('th')[0].onclick();
assert.deepEqual(tableMonths(), ['Февраль', 'Январь', 'Декабрь']);
legacyTableNode.querySelectorAll('th')[0].onclick();
assert.deepEqual(tableMonths(), ['Декабрь', 'Январь', 'Февраль']);
console.log('PASS: legacy month sorting follows source chronology in both directions');
