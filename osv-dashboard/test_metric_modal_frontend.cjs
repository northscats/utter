const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const modalCss = fs.readFileSync('static/key_indicators.css', 'utf8');
assert.match(modalCss,
    /\.ki-modal-dynamics-block \.modal-chart-wrap\s*\{[^}]*height:auto;[^}]*padding:0;[^}]*border:0;/s,
    'Shared dynamics wrapper must grow with the chart and not draw a second layer');
assert.match(modalCss, /\.ki-metric-tile-active,\s*\.ki-metric-tile-pinned\s*\{/,
    'Pinned and active metric tiles keep the series-colored outline');

const nodes = new Map();
const timers = [];
let nextTimerId = 1;
function setTimer(fn) {
    const timer = {id: nextTimerId++, fn, cancelled: false};
    timers.push(timer);
    return timer.id;
}
function clearTimer(id) {
    const timer = timers.find(item => item.id === id);
    if (timer) timer.cancelled = true;
}
function flushTimers() {
    while (timers.length) {
        const timer = timers.shift();
        if (!timer.cancelled) timer.fn();
    }
}

function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
        innerHTML: '', textContent: '', style: {},
        dataset: {},
        classList: { contains: () => false, add: () => {}, remove: () => {} },
        setAttribute: () => {},
        getAttribute: () => null,
        __cache: {},
        querySelectorAll(selector) {
            if (selector.includes('[data-series-id]') || selector.includes('tr[data-series-id]')) {
                const matches = [...this.innerHTML.matchAll(/data-series-id="([^"]+)"/g)];
                return matches.map(m => {
                    let id = m[1];
                    if (!this.__cache[id]) {
                        let handlers = {};
                        this.__cache[id] = {
                            dataset: { seriesId: id },
                            style: {},
                            addEventListener: (evt, fn) => { handlers[evt] = fn; },
                            closest: (sel) => null,
                            setAttribute: () => {},
                            getAttribute: () => null,
                            dispatchEvent: (evt, data) => {
                                if (handlers[evt]) handlers[evt](data);
                            },
                            getHandlers: () => handlers
                        };
                    }
                    return this.__cache[id];
                });
            }
            if (selector.includes('input[name="gapMode"]') ||
                selector.includes('input[name="timelineMode"]')) {
                const group = selector.includes('timelineMode') ? 'timelineMode' : 'gapMode';
                const pattern = new RegExp(`name="${group}" value="([^"]+)"`, 'g');
                const matches = [...this.innerHTML.matchAll(pattern)];
                return matches.map(m => {
                    let val = m[1];
                    const cacheKey = `radio:${group}:${val}`;
                    if (!this.__cache[cacheKey]) {
                        let handlers = {};
                        this.__cache[cacheKey] = {
                            value: val,
                            addEventListener: (evt, fn) => { handlers[evt] = fn; },
                            dispatchEvent: (evt, data) => {
                                if (handlers[evt]) handlers[evt](data);
                            }
                        };
                    }
                    return this.__cache[cacheKey];
                });
            }
            return [];
        }
    });
    return nodes.get(id);
}

const context = vm.createContext({
    window: {},
    document: {addEventListener() {}, getElementById: element, querySelectorAll: () => []},
    console, Intl, AbortController,
    setTimeout: setTimer,
    clearTimeout: clearTimer,
    requestAnimationFrame: (fn) => fn()
});

vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8'), context);
const app = context.window.KI_App;
app.state.mode = 'operational';

function snapshot(date, value) {
    return { date, label: date, value, comparison: { comparable: true, value: 0 } };
}

const mainSeries = [snapshot('2026-01-01', 100), snapshot('2026-01-02', 110)];
const subSeries1 = [snapshot('2026-01-01', 50), snapshot('2026-01-02', 55)];
const gapSeries = [
    snapshot('2026-01-01', 50),
    {...snapshot('2026-01-02', null), value: null, comparison: {comparable: false}},
    {...snapshot('2026-01-03', 60), comparison: {comparable: false}}
];
const monthlySeries = [
    {...snapshot('2026-01-31', 100), granularity: 'month'},
    {...snapshot('2026-02-28', null), value: null, granularity: 'month'},
    {...snapshot('2026-03-31', 140), comparison: {comparable: false}, granularity: 'month'}
];
const componentTemplates = ['62.01 Дебет', '62.01 Кредит',
    '62.02 Дебет', '62.02 Кредит'].map((label, index) => ({
        label, account: label.slice(0, 5),
        side: label.includes('Дебет') ? 'debit' : 'credit',
        value: 25 + index, unit: 'RUB',
        series: [snapshot('2026-01-01', 20 + index),
            snapshot('2026-01-02', 25 + index)]
    }));
const analytics = {
    label: 'Analytic detail', value: 30, unit: 'RUB',
    series: [snapshot('2026-01-01', 30), snapshot('2026-01-02', 30)]
};

const card = {
    id: 'generic_test',
    title: 'Test Metric',
    unit: 'RUB',
    series: mainSeries,
    breakdown: [
        { label: 'Sub 1', value: 55, series: subSeries1 },
        { label: 'Sub 2 (gap)', value: 60, series: gapSeries },
        { label: 'Sub 3 (monthly)', value: 140, unit: 'RUB',
          series: monthlySeries }
    ],
    granularity: 'day',
    // Simulate the real API: details/components are independent JSON
    // objects, not shared references.
    details: componentTemplates.map(item => JSON.parse(JSON.stringify(item))),
    components: componentTemplates.map(item => JSON.parse(JSON.stringify(item))),
    analytics: [analytics]
};

element('ki-metric-modal-svg');

app.state.data = { cards: [card] };
app.openModal('generic_test');
flushTimers();

let chCont = nodes.get('ki-modal-chart-container');
let bdCont = nodes.get('ki-modal-breakdown-container');
let svgHtml = nodes.get('ki-metric-modal-svg').innerHTML;

assert.equal(app.state.modalChart.activeId, '__main__');
assert.ok(bdCont.innerHTML.includes('border-left:3px solid #3B82F6'), 'Active tile highlighted');
assert.ok(bdCont.innerHTML.includes('ki-metric-interactive-tile'), 'Metric cards are tiles');
assert.ok(!bdCont.innerHTML.includes('<table'), 'Metric breakdown is not rendered as a table');

let rows = bdCont.querySelectorAll('[data-series-id]');
let sub1Row = rows.find(r => r.dataset.seriesId === '0');
sub1Row.dispatchEvent('click', { detail: 1, target: { closest: () => null } });
flushTimers();
assert.equal(app.state.modalChart.activeId, '0');
assert.ok(sub1Row.style['--ki-selected-series-color'],
    'Selected metric tile receives the same stable color as its chart line');

sub1Row.dispatchEvent('dblclick', { target: { closest: () => null } });
assert.ok(app.state.modalChart.pinnedIds.has('0'), 'Pinned sub 1');

let sub2Row = bdCont.querySelectorAll('[data-series-id]').find(r => r.dataset.seriesId === '1');
sub2Row.dispatchEvent('keydown', { key: ' ', preventDefault: () => {} });
assert.ok(app.state.modalChart.pinnedIds.has('1'), 'Pinned sub 2 via keyboard');

sub2Row.dispatchEvent('keydown', { key: 'Enter', preventDefault: () => {} });
assert.equal(app.state.modalChart.activeId, '1');
flushTimers();

let activeAndPinnedDrawCount = [...nodes.get('ki-metric-modal-svg').innerHTML.matchAll(/<path d=" M /g)].length;
assert.ok(activeAndPinnedDrawCount > 1, 'Multiple series are drawn');

app.state.modalChart.gapMode = 1;
app.drawMetricModalSvg(card);
const connectedGenericSvg = nodes.get('ki-metric-modal-svg').innerHTML;
assert.ok(connectedGenericSvg.includes('stroke-dasharray="4,4"'), 'Dashed line rendered for gap');
assert.match(connectedGenericSvg, /class="metric-date-tick"/,
    'General metric chart renders a horizontal calendar scale');
assert.match(connectedGenericSvg, /class="metric-modal-chart-plot"/,
    'General metric SVG and hit targets share one coordinate container');
assert.match(connectedGenericSvg, /class="metric-chart-point-svg"/,
    'Visible metric points are rendered inside the same SVG as their lines');
assert.match(connectedGenericSvg,
    /<path d="[^"]*L[^"]*"[^>]*stroke-dasharray="4,4"/,
    'Connect mode renders a non-empty line between general metric points');

app.state.modalChart.gapMode = 2;
app.drawMetricModalSvg(card);
let svg2 = nodes.get('ki-metric-modal-svg').innerHTML;
assert.ok(!svg2.includes('stroke-dasharray="4,4"'), 'No dashed line in gap mode 2');
assert.ok(svg2.includes('Синтетическое значение'), 'Synthetic value labeled in tooltip');
assert.ok(svg2.includes('gap-transform-rect'), 'Hit area for gap transform rendered');

app.state.modalChart.pinnedIds.clear(); app.state.modalChart.activeId = '2'; // sub 3 (monthly)
app.drawMetricModalSvg(card);
let svg3 = nodes.get('ki-metric-modal-svg').innerHTML;
assert.ok(svg3.includes('gap-transform-rect'),
    'Extend mode works for sparse points shown on the selected calendar scale');

const analyticRow = bdCont.querySelectorAll('[data-series-id]')
    .find(row => row.dataset.seriesId === 'analytics:0');
assert.ok(analyticRow, 'Analytics is selectable');
analyticRow.dispatchEvent('dblclick', {target: {closest: () => null}});
assert.ok(app.state.modalChart.pinnedIds.has('analytics:0'), 'Analytics can be pinned');
const componentRows = bdCont.querySelectorAll('[data-series-id]');
assert.equal(componentRows.filter(row => /^detail:/.test(row.dataset.seriesId)).length, 0,
    'JSON-duplicated detail components are semantically deduplicated');
assert.equal(componentRows.filter(row => /^component:/.test(row.dataset.seriesId)).length, 4,
    'Four component series retain their canonical component prefix');
const firstComponent = componentRows.find(row => row.dataset.seriesId === 'component:0');
firstComponent.dispatchEvent('dblclick', {target: {closest: () => null}});
assert.ok(app.state.modalChart.pinnedIds.has('component:0'),
    'A duplicated JSON component has one stable pin target');

// Cash __main__ uses the actual daily balance series in the shared renderer.
// A missing calendar day remains a gap rather than a carried-forward value.
const cashSeries = [
    snapshot('2026-01-01', 100),
    {...snapshot('2026-01-02', null), value: null},
    {...snapshot('2026-01-03', 130), comparison: {comparable: false}}
].map(point => ({...point, granularity: 'day'}));
const cashCard = {
    id: 'cash', title: 'Денежные средства', unit: 'RUB', value: 130,
    series: cashSeries,
    breakdown: [{label: 'Счёт 51', value: 130, unit: 'RUB',
        series: cashSeries.map(point => ({...point,
            value: point.value === null ? null : point.value - 10}))}],
    details: []
};
app.state.modalCharts.cash = {activeId: null, pinnedIds: new Set(), gapMode: 1};
app.state.modalChart = app.state.modalCharts.cash;
app.state.data = {cards: [cashCard]};
app.renderCashModal(cashCard);
const cashContent = nodes.get('ki-cash-modal-content');
app.bindMetricTiles(cashCard, cashContent);
const cashMainTile = cashContent.querySelectorAll('[data-series-id]')
    .find(tile => tile.dataset.seriesId === '__main__');
assert.ok(cashMainTile, 'Cash headline tile is the shared __main__ target');
cashMainTile.dispatchEvent('click', {detail: 1, target: {closest: () => null}});
flushTimers();
assert.equal(app.state.modalChart.activeId, '__main__',
    'Cash headline click selects __main__');
app.drawMetricModalSvg(cashCard, nodes.get('ki-metric-modal-svg'));
const cashSvg = nodes.get('ki-metric-modal-svg').innerHTML;
assert.equal((cashSvg.match(/cash-modal-point/g) || []).length, 2,
    'Only real daily cash values receive point markers');
assert.ok(cashSvg.includes('cash-modal-area'), 'Cash main has a baseline area fill');
assert.ok(cashSvg.includes('Денежные средства (сч. 50, 51, 52, 55)'),
    'Cash chart has the account legend');
assert.ok(cashSvg.includes('cash-date-tick'), 'Cash chart has readable date ticks');
assert.ok(cashSvg.includes('cash-modal-chart-plot'),
    'Cash SVG and point overlays share one bounded plot layer');
assert.ok(!cashSvg.includes('NCF') && !cashSvg.includes('Норматив'),
    'Cash chart has no NCF or norm line labels');
assert.ok((cashSvg.match(/cash-modal-line-gap/g) || []).length > 0,
    'Null daily cash point remains a visual gap');
const connectedCashPath = cashSvg.match(
    /<path d="([^"]*L[^"]*)"[^>]*class="cash-modal-line-gap"/);
assert.ok(connectedCashPath && connectedCashPath[1].trim(),
    'Connect mode renders a non-empty dashed segment between known cash points');
app.state.modalChart.gapMode = 2;
app.drawMetricModalSvg(cashCard, nodes.get('ki-metric-modal-svg'));
const extendedCashSvg = nodes.get('ki-metric-modal-svg').innerHTML;
const extendedCashPath = extendedCashSvg.match(
    /<path d="([^"]*L[^"]*)"[^>]*class="cash-modal-line"/);
assert.ok(extendedCashPath && extendedCashPath[1].trim(),
    'Extend mode renders a non-empty stepped segment between known cash points');
assert.notEqual(extendedCashPath[1], connectedCashPath[1],
    'Connect and extend modes render different line geometry');
app.state.modalChart.gapMode = 1;
assert.match(cashContent.innerHTML, /data-series-id="0"/,
    'Cash account row has a stable breakdown series id');
const cashAccountTile = cashContent.querySelectorAll('[data-series-id]')
    .find(tile => tile.dataset.seriesId === '0');
cashAccountTile.dispatchEvent('dblclick', {target: {closest: () => null}});
assert.ok(app.state.modalChart.pinnedIds.has('0'),
    'Cash account series can remain pinned');
cashAccountTile.dispatchEvent('click', {detail: 1, target: {closest: () => null}});
flushTimers();
assert.equal(app.state.modalChart.activeId, '0',
    'Cash account selection stays in the specialised renderer');
app.drawMetricModalSvg(cashCard, nodes.get('ki-metric-modal-svg'));
assert.ok(!nodes.get('ki-metric-modal-svg').innerHTML.includes('cash-modal-area'),
    'Area fill is reserved for the selected cash main series');
assert.ok(!nodes.get('ki-metric-modal-svg').innerHTML.includes(
    'Денежные средства (сч. 50, 51, 52, 55)'),
    'Legend does not claim that the total is plotted after selecting one account');

// Calendar scale aggregates balances by the last factual observation in each
// day/week/month bucket without inventing values for empty buckets.
const periodicCashCard = {
    id: 'cash', title: 'Денежные средства', unit: 'RUB', value: 160,
    series: [
        snapshot('2026-01-01', 100),
        snapshot('2026-01-08', 120),
        snapshot('2026-02-02', 160)
    ],
    breakdown: [], details: []
};
app.state.modalChart.activeId = '__main__';
app.state.modalChart.pinnedIds.clear();
app.state.modalChart.gapMode = 1;
app.state.modalChart.timelineMode = 'day';
app.drawMetricModalSvg(periodicCashCard, nodes.get('ki-metric-modal-svg'));
const dayPointCount = (nodes.get('ki-metric-modal-svg').innerHTML
    .match(/class="cash-modal-point"/g) || []).length;
app.state.modalChart.timelineMode = 'week';
app.drawMetricModalSvg(periodicCashCard, nodes.get('ki-metric-modal-svg'));
const weekSvg = nodes.get('ki-metric-modal-svg').innerHTML;
const weekPointCount = (weekSvg.match(/class="cash-modal-point"/g) || []).length;
assert.ok(weekSvg.includes('cash-modal-line-gap'),
    'Weekly scale draws a line between weekly balance points');
app.state.modalChart.timelineMode = 'month';
app.drawMetricModalSvg(periodicCashCard, nodes.get('ki-metric-modal-svg'));
const monthSvg = nodes.get('ki-metric-modal-svg').innerHTML;
const monthPointCount = (monthSvg.match(/class="cash-modal-point"/g) || []).length;
assert.match(monthSvg, /class="cash-modal-line"/,
    'Monthly scale draws a line between adjacent monthly balance points');
assert.equal(dayPointCount, 3, 'Daily scale keeps all factual observations');
assert.equal(weekPointCount, 3, 'Weekly scale keeps the last observation of each occupied week');
assert.equal(monthPointCount, 2, 'Monthly scale keeps the last observation of each occupied month');

// A single click waits; a subsequent double click cancels that pending click.
app.state.modalChart.activeId = '__main__';
const sub1Again = bdCont.querySelectorAll('[data-series-id]')
    .find(row => row.dataset.seriesId === '0');
sub1Again.dispatchEvent('click', {detail: 1, target: {closest: () => null}});
sub1Again.dispatchEvent('dblclick', {target: {closest: () => null}});
flushTimers();
assert.equal(app.state.modalChart.activeId, '__main__',
    'Double click cancels pending single-click activation');

// The real card IDs all route through their specialized renderer and still
// expose the shared interactive surface.
const specialized = ['cash', 'cash_dz', 'revenue', 'collection', 'ar', 'ap',
    'cogs', 'profit', 'credits', 'salary', 'capex'];
const routed = [];
const specializedCard = id => ({
    id, title: id, unit: 'RUB', value: 1,
    series: [snapshot('2026-01-01', 1)],
    breakdown: [], details: []
});
const originalRenderers = {};
new Set(specialized.map(id => id === 'cash_dz' ? 'cash' : id)).forEach(id => {
    const rendererId = id === 'cash_dz' ? 'cash' : id;
    const method = `render${rendererId[0].toUpperCase()}${rendererId.slice(1)}Modal`;
    originalRenderers[method] = app[method];
    app[method] = card => routed.push(card.id);
});
app.state.data = {cards: specialized.map(specializedCard)};
specialized.forEach(id => app.openModal(id));
assert.deepEqual(routed, specialized, 'Specialized modal routing remains intact');

console.log('PASS: timing click/dblclick, pin, gap transform, multiple series, keyboard, monthly granularity refusal');
