// Execute the real cash renderers and tooltip handlers without browser/DB state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodePath = require('node:path');
const vm = require('node:vm');
const nodes = new Map();
function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
        innerHTML: '', textContent: '', style: {},
        attributes: {},
        classList: {
            values: new Set(),
            add(value) { this.values.add(value); },
            remove(value) { this.values.delete(value); },
            contains(value) { return this.values.has(value); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        querySelector() { return {focus() {}}; },
        querySelectorAll() {
            return Array.from(this.innerHTML.matchAll(/<circle class="cash-chart-hit-target" data-kind="([^"]+)" data-index="(\d+)" data-period="([^"]+)"/g),
                ([, kind, index, period]) => ({
                    dataset: {kind, index, period},
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
    document: {
        activeElement: null,
        addEventListener() {},
        getElementById: element,
        querySelectorAll: () => [],
    },
    requestAnimationFrame(callback) { callback(); },
    console, Intl, AbortController,
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8'), context);
const cashCss = fs.readFileSync(`${__dirname}/static/key_indicators.css`, 'utf8');
const cashBrowserTest = fs.readFileSync(
    nodePath.join(__dirname, 'test_cash_indicators_browser.py'), 'utf8');
const app = context.window.KI_App;
const tips = [];
app.showRichTooltip = tip => tips.push(tip);
app.state.data = {period: {label: 'Январь 2026'}};

function snapshot(date, value, accounts = ['51']) {
    return {
        date, label: date, value, debit_value: value,
        credit_value: value == null ? null : value / 2,
        accounts, cumulative_accounts: accounts,
        missing_accounts: ['50', '51', '52', '55'].filter(a => !accounts.includes(a)),
        cumulative_missing_accounts: ['50', '51', '52', '55'].filter(a => !accounts.includes(a)),
        coverage: value == null ? 'missing' : accounts.length === 4 ? 'complete' : 'partial',
        cumulative_coverage: value == null ? 'missing' : accounts.length === 4 ? 'complete' : 'partial',
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
function classPath(svg, className) {
    const match = svg.match(new RegExp(`<path d="([^"]*)" class="${className}"`));
    return match ? match[1] : '';
}

const jan1 = snapshot('2026-01-01', 100);
const jan2 = compare(snapshot('2026-01-02', 150, ['51', '55']), jan1, false);
let svg = draw([jan1, jan2]);
assert.equal((classPath(svg, 'cash-chart-debit').match(/M /g) || []).length, 2,
    'coverage change breaks debit line');
assert.ok(!classPath(svg, 'cash-chart-debit').includes(' L'));
assert.ok(!svg.includes('cash-chart-area'), 'coverage change must not produce a filled area');

const jan2same = compare(snapshot('2026-01-02', 150), jan1, true);
svg = draw([jan1, jan2same]);
assert.match(classPath(svg, 'cash-chart-debit'), / C /);
assert.equal((svg.match(/data-kind="debit"/g) || []).length, 2);
assert.equal((svg.match(/data-kind="credit"/g) || []).length, 2);
assert.equal((svg.match(/cash-chart-area/g) || []).length, 1);
assert.match(svg, /cash-chart-grid-vertical/);
assert.ok(!svg.includes('cash-chart-point'));
assert.match(svg, /class="cash-chart-debit"[^>]+stroke-width="1.5"/);
assert.match(svg, /class="cash-chart-credit"[^>]+stroke-width="1.5"/);
assert.match(svg, /class="cash-chart-hit-target"[^>]+fill="transparent" stroke="none"/);
assert.ok(tips.every(tip => tip.detail.includes('Частичный охват')));
assert.ok(tips.some(tip => tip.title.startsWith('Накопленный дебет')));
assert.ok(tips.some(tip => tip.title.startsWith('Накопленный кредит')));

const gap = compare(snapshot('2026-01-02', null, []), jan1, false, 'Нет точного остатка');
const jan3 = compare(snapshot('2026-01-03', 200), gap, false, 'Нет точного остатка');
svg = draw([jan1, gap, jan3]);
assert.ok(!classPath(svg, 'cash-chart-debit').includes(' L'));
assert.equal((svg.match(/cash-chart-area/g) || []).length, 0);
assert.equal((svg.match(/data-kind="debit"/g) || []).length, 2);
const gapXs = Array.from(svg.matchAll(/data-kind="debit"[^>]+cx="([^"]+)"/g))
    .map(match => Number(match[1]));
assert.equal(gapXs.length, 2);
assert.ok(gapXs[1] > gapXs[0]);
assert.match(draw([jan1]), /Одна точка данных/);
assert.match(draw([snapshot('2026-01-01', null, [])]), /Нет сопоставимых данных/);
assert.equal((draw([snapshot('2026-01-01', 0, ['50', '51', '52', '55'])]).match(/data-kind="debit"/g) || []).length, 1);
const longSeries = Array.from({length: 61}, (_, index) => {
    const date = new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10);
    return snapshot(date, index + 1);
});
assert.ok(!draw(longSeries).includes('cash-chart-point'));

// Even malformed or old payloads cannot cause client-side subtraction.
assert.match(draw([{label: 'a', value: 10}, {label: 'b', value: 20}]), /Нет сопоставимых данных/);
const wrongDate = structuredClone(jan2same);
wrongDate.comparison.previous_date = '2025-12-31';
assert.equal((draw([jan1, wrongDate]).match(/data-kind="debit"/g) || []).length, 2);
const changedButMarkedComparable = compare(snapshot('2026-01-02', 150, ['51', '55']), jan1, true);
assert.ok(!draw([jan1, changedButMarkedComparable]).includes('cash-chart-area'));
const monthlyOnly = snapshot('2026-02-28', 200, ['51']);
monthlyOnly.comparison = {
    comparable: true, previous_date: '2026-01-31',
    date: '2026-02-28', accounts: ['51'], value: 100, reason: null,
};
assert.ok(draw([
    snapshot('2026-02-27', 100, ['51']),
    monthlyOnly,
]).includes('cash-chart-area'));

// Quarter range uses the same exact-date comparison, including monthly gaps.
const months = ['01-31', '02-28', '03-31', '04-30', '05-31', '06-30']
    .map((d, i) => snapshot(`2026-${d}`, i === 3 ? null : (i + 1) * 100, i === 3 ? [] : ['51']));
months.forEach((point, i) => {
    if (i) compare(point, months[i - 1], point.value !== null && months[i - 1].value !== null, 'Нет точного остатка');
});
svg = draw(months);
assert.equal((svg.match(/data-kind="debit"/g) || []).length, 5);
assert.equal((classPath(svg, 'cash-chart-debit').match(/M /g) || []).length, 5);
assert.equal((svg.match(/cash-chart-area/g) || []).length, 0);

const card = {
    ...jan2same, id: 'cash', title: 'Денежные средства', unit: 'RUB',
    breakdown: [{label: 'Счёт 51', value: 150}], series: [jan1, jan2same],
    previous_year_series: [
        snapshot('2025-01-01', 80),
        snapshot('2025-01-02', 120),
    ],
    delta: {...jan2same.comparison, unit: 'RUB', label: '2026-01-01 → 2026-01-02'},
};
app.renderCashModal(card);
let html = element('ki-cash-modal-content').innerHTML;
assert.match(html, /cash-summary-sub[^>]*>[^<]*на 2026-01-02<br>Частичный охват/);
assert.match(html, /Накопленный дебет: СНд \+ ОД/);
assert.match(html, /Накопленный кредит: СНк \+ ОК/);
assert.match(html, /data-cash-chart-mode="cash-current"/);
assert.match(html, /data-cash-chart-mode="cash-apg"/);
assert.match(html, /data-cash-chart-mode="cumulative-current"/);
assert.match(html, /data-cash-chart-mode="cumulative-apg"/);
assert.match(html, /Денежные средства АПГ/);
assert.match(html, /Накопленный ДК АПГ/);
assert.match(html, /class="cash-table-scroll" tabindex="0" role="region"/);
assert.match(html, /class="cash-total-row cash-tooltip-host"/);
assert.match(html, /class="cash-account-name">Расчётные счета/);
assert.match(html, /class="cash-data-state ok">Загружен/);
assert.match(html, /class="cash-data-state missing">Нет данных/);
assert.match(html, /class="cash-share-fill" style="width:100\.0%"/);
assert.match(html, /class="cash-coverage-note">Частичный охват/);
assert.match(cashCss, /\.cash-table th\s*\{[^}]*background:#173A66;[^}]*color:#fff;/s);
assert.match(cashCss, /\.cash-table\s*\{[^}]*min-width:760px;/s);
assert.match(cashCss, /\.cash-total-row td\s*\{[^}]*border-top:2px solid #2563EB;/s);
assert.match(cashCss, /\.cash-table-scroll:focus-visible/);
assert.match(cashBrowserTest, /page\.locator\('\.cash-table-scroll'\)\.screenshot/);
assert.match(cashBrowserTest, /tableScroll'\] > dimensions\['tableClient'/);
for (const snapshotName of [
    'cash-table-full.png',
    'cash-table-partial.png',
    'cash-modal-narrow.png',
]) {
    assert.ok(fs.existsSync(nodePath.join(__dirname, '..', 'screenshots', snapshotName)),
        `missing reviewed cash visual reference: ${snapshotName}`);
}
assert.match(html, /0,00 \/ 0,00 <span class="metric-inline-unit">млн ₽/);
assert.match(html, /id="ki-cash-line-chart"/);
assert.match(html, /Изменение остатка/);
assert.ok(!html.includes('NCF /'));
assert.match(html, /2026-01-01 → 2026-01-02/);
app.state.data.cards = [card];
app.renderCards();
html = element('ki-cards-grid').innerHTML;
assert.match(html, /Частичный охват: сч. 51/);
assert.ok(!html.includes('data-metric="cash_cumulative"'));
assert.ok(!html.includes('Накопленный ДК'));

// Production modal lifecycle must mount the specialised cash chart, not the
// generic value-only dynamics renderer.
let cashChartMounts = 0;
const realCashRenderer = app.renderCashLineChart.bind(app);
app.renderCashLineChart = (...seriesGroups) => {
    cashChartMounts += 1;
    realCashRenderer(...seriesGroups);
};
app.mountSharedDynamics = () => assert.fail('cash modal must not mount generic dynamics');
app.clearChartJobs = () => {};
app.hideModalSwitchStatus = () => {};
app.updateModalSubtitle = () => {};
app.enhanceTables = () => {};
app.bindMetricTiles = () => {};
app.state.mode = 'operational';
app.openModal('cash');
assert.equal(cashChartMounts, 1);
assert.match(element('ki-cash-modal-content').innerHTML, /cash-summary-grid/);
assert.match(element('ki-cash-modal-content').innerHTML, /Накопленный ДК/);
assert.match(element('ki-cash-modal-content').innerHTML, /0,00 \/ 0,00/);
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-debit"/);
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-credit"/);
app.toggleCashChartMode('cumulative-apg');
assert.equal(cashChartMounts, 2);
assert.equal(element('ki-cash-chart-title').textContent,
    'Накопленный ДК и Накопленный ДК АПГ');
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-debit-apg"/);
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-credit-apg"/);
assert.match(element('ki-cash-line-chart').innerHTML, /2025-01-02/);
app.toggleCashChartMode('cumulative-current');
assert.equal(cashChartMounts, 3);
assert.equal(element('ki-cash-chart-title').textContent, 'Накопленный ДК АПГ');
assert.ok(!element('ki-cash-line-chart').innerHTML.includes('class="cash-chart-debit"'));
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-debit-apg"/);
app.toggleCashChartMode('cash-current');
assert.equal(cashChartMounts, 4);
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-balance"/);
assert.match(element('ki-cash-line-chart').innerHTML, /2026-01-02/);
app.toggleCashChartMode('cash-apg');
assert.equal(cashChartMounts, 5);
assert.match(element('ki-cash-line-chart').innerHTML, /class="cash-chart-balance-apg"/);
assert.match(element('ki-cash-chart-title').textContent,
    /Денежные средства и Денежные средства АПГ/);

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
assert.match(html, /cash-total-row[\s\S]*?<span class="cash-share-value">—<\/span>/);
assert.ok(!/cash-total-row[\s\S]*?cash-share-fill/.test(html),
    'zero total must not render a misleading total share bar');
console.log('PASS: cash coverage, safe paths/deltas, exact-date tooltips, gaps, zero, day, months, quarter range, visible partial totals');