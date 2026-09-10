// Lightweight checks of the actual UI handlers and SVG without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const element = id => {
    if (!nodes.has(id)) nodes.set(id, {
        style: {}, innerHTML: '', classList: {add() {}, remove() {}},
        clientWidth: 800, clientHeight: 260, querySelectorAll: () => [],
    });
    return nodes.get(id);
};
const context = {
    window: {KI_CONFIG: {dataUrl: '/company/1/key-indicators/data'}},
    document: {addEventListener() {}, getElementById: element, querySelectorAll: () => []},
    AbortController, console,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8'), context);
const app = context.window.KI_App;
const fetchData = app.fetchData;
app.fetchData = () => {};
app.state.period = 'quarter';
app.onQuarterChange('2026-03-31');
app.onQuarterChange('2026-09-30');
assert.equal(app.state.dateFrom, '2026-03-31');
assert.equal(app.state.date, '2026-09-30');
assert.equal(app.state.quarterAnchor, null);
app.state.data = {period: {
    date: app.state.date, date_from: app.state.dateFrom,
    options: ['03-31', '06-30', '09-30', '12-31'].map(d => ({value: `2026-${d}`, label: d})),
}};
app.renderPeriodSelector();
assert.equal((element('ki-quarter-buttons').innerHTML.match(/quarter-btn active/g) || []).length, 3);
app.onQuarterChange('2026-09-30');
app.onQuarterChange('2026-06-30');
assert.equal(app.state.dateFrom, '2026-06-30');
assert.equal(app.state.date, '2026-09-30');
app.renderBarChart(
    [1, 2, null, 4, 5].map((value, i) => ({label: `day ${i}`, value})),
    '%', 'test-chart', {title: 'test'});
const svg = element('test-chart').innerHTML;
assert.equal((svg.match(/<circle/g) || []).length, 4);
assert.match(svg, /<path d=" M[^"]* L[^"]* M[^"]* L/);
assert.ok(!svg.includes('<rect'));
app.onPeriodChange('month');
assert.equal(app.state.quarterAnchor, null);
assert.equal(app.state.dateFrom, null);
app.state.period = 'quarter';
app.state.dateFrom = '2026-03-31';
app.state.date = '2026-09-30';
let requested;
context.fetch = async url => {
    requested = url;
    return {ok: true, status: 200, text: async () => JSON.stringify({
        period: {kind: 'quarter', date_from: '2026-03-31', date: '2026-09-30'},
    })};
};
app.render = () => {};
fetchData.call(app).then(() => {
    assert.match(requested, /date_from=2026-03-31/);
    assert.match(requested, /date=2026-09-30/);
    console.log('Quarter range, request parameters and chart gaps: OK');
}).catch(error => {console.error(error); process.exitCode = 1;});