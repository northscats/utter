const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
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
                            addEventListener: (evt, fn) => { handlers[evt] = fn; },
                            closest: (sel) => null,
                            dispatchEvent: (evt, data) => {
                                if (handlers[evt]) handlers[evt](data);
                            },
                            getHandlers: () => handlers
                        };
                    }
                    return this.__cache[id];
                });
            }
            if (selector.includes('input[name="gapMode"]')) {
                const matches = [...this.innerHTML.matchAll(/value="([^"]+)"/g)];
                return matches.map(m => {
                    let val = m[1];
                    if (!this.__cache['radio'+val]) {
                        let handlers = {};
                        this.__cache['radio'+val] = {
                            value: val,
                            addEventListener: (evt, fn) => { handlers[evt] = fn; },
                            dispatchEvent: (evt, data) => {
                                if (handlers[evt]) handlers[evt](data);
                            }
                        };
                    }
                    return this.__cache['radio'+val];
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
assert.ok(nodes.get('ki-metric-modal-svg').innerHTML.includes('stroke-dasharray="4,4"'), 'Dashed line rendered for gap');

app.state.modalChart.gapMode = 2;
app.drawMetricModalSvg(card);
let svg2 = nodes.get('ki-metric-modal-svg').innerHTML;
assert.ok(!svg2.includes('stroke-dasharray="4,4"'), 'No dashed line in gap mode 2');
assert.ok(svg2.includes('Синтетическое значение'), 'Synthetic value labeled in tooltip');
assert.ok(svg2.includes('gap-transform-rect'), 'Hit area for gap transform rendered');

app.state.modalChart.pinnedIds.clear(); app.state.modalChart.activeId = '2'; // sub 3 (monthly)
app.drawMetricModalSvg(card);
let svg3 = nodes.get('ki-metric-modal-svg').innerHTML;
assert.ok(!svg3.includes('gap-transform-rect'), 'Monthly granularity refused gap transform carry');

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
const specialized = ['cash', 'revenue', 'collection', 'ar', 'ap',
    'cogs', 'profit', 'credits', 'salary', 'capex'];
const routed = [];
const specializedCard = id => ({
    id, title: id, unit: 'RUB', value: 1,
    series: [snapshot('2026-01-01', 1)],
    breakdown: [], details: []
});
const originalRenderers = {};
specialized.forEach(id => {
    const method = `render${id[0].toUpperCase()}${id.slice(1)}Modal`;
    originalRenderers[method] = app[method];
    app[method] = () => routed.push(id);
});
app.state.data = {cards: specialized.map(specializedCard)};
specialized.forEach(id => app.openModal(id));
assert.deepEqual(routed, specialized, 'Specialized modal routing remains intact');

console.log('PASS: timing click/dblclick, pin, gap transform, multiple series, keyboard, monthly granularity refusal');
