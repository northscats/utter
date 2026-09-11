// Execute the real cash renderers and tooltip handlers without browser/DB state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
        innerHTML: '', textContent: '', style: {},
        querySelectorAll() {
            return Array.from(this.innerHTML.matchAll(/<circle class="cash-chart-point" data-kind="([^"]+)" data-index="(\d+)"/g),
                ([, kind, index]) => ({
                    dataset: {kind, index},
                    addEventListener(event, handler) {
                        if (event === 'pointerenter') {
                            handler({clientX: 10, clientY: 10});
                        }
                    }
                }));
        }
    });
    return nodes.get(id);
}
const context = vm.createContext({
    window: {},
    document: {addEventListener() {}, getElementById: element, querySelectorAll: () => []},
    console, Intl, AbortController,
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8'), context);
const app = context.window.KI_App;
const tips = [];
app.showRichTooltip = tip => tips.push(tip);
app.state.data = {period: {label: 'Январь 2026'}};

function snapshot(date, value, accounts = ['51']) {
    return {
        date, label: date, value, accounts,
        missing_accounts: ['50', '51', '52', '55'].filter(a => !accounts.includes(a)),
        coverage: value == null ? 'missing' : accounts.length === 4 ? 'complete' : 'partial',
        comparison: {comparable: false, previous_date: null, date, value: null, reason: 'Нет предыдущей точки'},
    };
}
function compare(current, previous, comparable, reason = 'Изменился состав счетов') {
    current.comparison = {
        comparable, date: current.date, previous_date: previous.date, accounts: current.accounts,
        value: comparable ? current.value - previous.value : null, reason: comparable ? null : reason,
    };
    return current;
}
function draw(series) {
    tips.length = 0;
    app.renderCashLineChart(series);
    return element('ki-cash-line-chart').innerHTML;
}
function path(svg, index = 0) {
    return Array.from(svg.matchAll(/<path d="([^"]*)"/g))[index][1];
}

const jan1 = snapshot('2026-01-01', 100);
const jan2 = compare(snapshot('2026-01-02', 150, ['51', '55']), jan1, false);
let svg = draw([jan1, jan2]);
assert.equal((path(svg).match(/ M/g) || []).length, 2, 'coverage change breaks balance line');
assert.ok(!path(svg).includes(' L'));
assert.ok(!svg.includes('data-kind="change"'), 'new account must not produce false growth');
assert.match(tips[1].detail, /Изменился состав счетов/);
assert.match(tips[1].detail, /2026-01-01 → 2026-01-02/);

const jan2same = compare(snapshot('2026-01-02', 150), jan1, true);
svg = draw([jan1, jan2same]);
assert.match(path(svg), / M.* L/);
assert.equal((svg.match(/data-kind="change"/g) || []).length, 1);
assert.match(svg, /#D97706/);
assert.ok(tips.every(tip => tip.detail.includes('Частичный охват')));
const changeTip = tips.find(tip => tip.title.startsWith('Изменение'));
assert.match(changeTip.detail, /2026-01-01 → 2026-01-02/);
assert.ok(!changeTip.description.includes('месяца'), 'day tooltip must not claim monthly data');

const gap = compare(snapshot('2026-01-02', null, []), jan1, false, 'Нет точного остатка');
const jan3 = compare(snapshot('2026-01-03', 200), gap, false, 'Нет точного остатка');
svg = draw([jan1, gap, jan3]);
assert.ok(!path(svg).includes(' L'));
assert.match(path(svg, 1), / M.* L/);
assert.match(svg, /cash-chart-line-gap/);
assert.equal((svg.match(/data-kind="balance"/g) || []).length, 2);
assert.ok(!svg.includes('data-kind="change"'));
const gapXs = Array.from(svg.matchAll(/data-kind="balance"[^>]+cx="([^"]+)"/g))
    .map(match => Number(match[1]));
assert.equal(gapXs.length, 2);
assert.ok(gapXs[1] > gapXs[0]);
assert.match(draw([jan1]), /Одна точка данных/);
assert.match(draw([snapshot('2026-01-01', null, [])]), /Нет сопоставимых данных/);
assert.equal((draw([snapshot('2026-01-01', 0, ['50', '51', '52', '55'])]).match(/data-kind="balance"/g) || []).length, 1);

// Even malformed or old payloads cannot cause client-side subtraction.
assert.ok(!draw([{label: 'a', value: 10}, {label: 'b', value: 20}]).includes('data-kind="change"'));
const wrongDate = structuredClone(jan2same);
wrongDate.comparison.previous_date = '2025-12-31';
assert.ok(!draw([jan1, wrongDate]).includes('data-kind="change"'));
const changedButMarkedComparable = compare(snapshot('2026-01-02', 150, ['51', '55']), jan1, true);
assert.ok(!draw([jan1, changedButMarkedComparable]).includes('data-kind="change"'));
const monthlyOnly = snapshot('2026-02-28', 200, ['51']);
monthlyOnly.comparison = {
    comparable: true, previous_date: '2026-01-31',
    date: '2026-02-28', accounts: ['51'], value: 100, reason: null,
};
assert.ok(!draw([
    snapshot('2026-02-27', 100, ['51']),
    monthlyOnly,
]).includes('data-kind="change"'));

// Quarter range uses the same exact-date comparison, including monthly gaps.
const months = ['01-31', '02-28', '03-31', '04-30', '05-31', '06-30']
    .map((d, i) => snapshot(`2026-${d}`, i === 3 ? null : (i + 1) * 100, i === 3 ? [] : ['51']));
months.forEach((point, i) => {
    if (i) compare(point, months[i - 1], point.value !== null && months[i - 1].value !== null, 'Нет точного остатка');
});
svg = draw(months);
assert.equal((svg.match(/data-kind="change"/g) || []).length, 3);
assert.equal((path(svg).match(/ M/g) || []).length, 5);
assert.match(svg, /cash-chart-line-gap/);
assert.ok(tips.some(tip => tip.detail.includes('2026-05-31 → 2026-06-30')));

const card = {
    ...jan2same, id: 'cash', title: 'Денежные средства', unit: 'RUB',
    breakdown: [{label: 'Счёт 51', value: 150}], series: [jan1, jan2same],
    delta: {...jan2same.comparison, unit: 'RUB', label: '2026-01-01 → 2026-01-02'},
};
app.renderCashModal(card);
let html = element('ki-cash-modal-content').innerHTML;
assert.match(html, /100% известного итога<br>Частичный охват/);
assert.match(html, /cash-summary-sub[^>]*>[^<]*на 2026-01-02<br>Частичный охват/);
assert.match(html, /Изменение остатка/);
assert.ok(!html.includes('NCF /'));
assert.match(html, /2026-01-01 → 2026-01-02/);
app.state.data.cards = [card];
app.renderCards();
assert.match(element('ki-cards-grid').innerHTML, /Частичный охват: сч. 51/);

app.renderCashModal({...card, ...jan2, delta: null});
html = element('ki-cash-modal-content').innerHTML;
assert.match(html, /Изменился состав счетов/);
assert.match(html, /cash-summary-value cash-value-missing">Нужны данные/);
const gapCard = {...card, comparison: {
    comparable: false, previous_date: '2026-01-02', date: '2026-01-03',
    value: null, reason: 'Нет предыдущего календарного слота',
}};
app.renderCashModal(gapCard);
assert.match(element('ki-cash-modal-content').innerHTML, /cash-summary-value cash-value-missing">Нужны данные/);
assert.match(element('ki-cash-modal-content').innerHTML, /Нет предыдущего календарного слота/);
app.state.data.cards = [gapCard];
app.renderCards();
assert.ok(!element('ki-cards-grid').innerHTML.includes('kpi-delta up'), 'headline must not show a stale positive delta across a gap');
app.renderCashModal({...card, ...snapshot('2026-01-01', 0, ['50', '51', '52', '55']), delta: null, series: []});
html = element('ki-cash-modal-content').innerHTML;
assert.ok(!html.includes('100% известного итога'), 'zero denominator has no percentage');
assert.match(html, /Полный охват/);
console.log('PASS: cash coverage, safe paths/deltas, exact-date tooltips, gaps, zero, day, months, quarter range, visible partial totals');