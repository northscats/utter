// Lightweight checks of the actual UI handlers and SVG without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const element = id => {
    if (!nodes.has(id)) nodes.set(id, {
        style: {}, innerHTML: '', classList: {add() {}, remove() {}, contains() { return false; }},
        clientWidth: 800, clientHeight: 260, querySelectorAll: () => [],
        setAttribute() {},
    });
    return nodes.get(id);
};
const context = {
    window: {KI_CONFIG: {dataUrl: '/company/1/key-indicators/data'}},
    document: {addEventListener() {}, getElementById: element, querySelectorAll: () => []},
    AbortController, URLSearchParams, console,
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
assert.equal(element('ki-date-from-select').value, '2026-03-31');
assert.equal(element('ki-date-select').value, '2026-09-30');
app.state.data.period.start = '2026-01-01';
app.state.data.period.end = '2026-01-06';
app.onQuarterChange('2026-09-30');
app.onQuarterChange('2026-06-30');
assert.equal(app.state.dateFrom, '2026-06-30');
assert.equal(app.state.date, '2026-09-30');
app.renderBarChart(
    [
        {date: '2026-01-01', label: '01.01.2026', value: 1},
        {date: '2026-01-02', label: '02.01.2026', value: 2},
        {date: '2026-01-04', label: '04.01.2026', value: null},
        {date: '2026-01-06', label: '06.01.2026', value: 5},
    ],
    '%', 'test-chart', {title: 'test'});
const svg = element('test-chart').innerHTML;
assert.equal((svg.match(/<circle/g) || []).length, 3);
assert.match(svg, /<path d=" M[^"]* L[^"]* M[^"]*"/);
assert.match(svg, /stroke-dasharray="5 4"/);
assert.match(svg, /Пунктир — пропуск данных, не факт/);
const chartPoints = Array.from(svg.matchAll(/<circle[^>]+cx="([^"]+)"/g))
    .map(match => Number(match[1]));
assert.equal(chartPoints[0], 40);
assert.equal(chartPoints[1], 190);
assert.equal(chartPoints[2], 790);
assert.ok(!svg.includes('<rect'));
app.renderBarChart([{date: '2026-01-01', label: '01.01.2026', value: 0}],
    '%', 'test-one-point', {title: 'test'});
assert.match(element('test-one-point').innerHTML, /Одна точка данных/);
app.renderBarChart([{date: '2026-01-01', label: '01.01.2026', value: null}],
    '%', 'test-all-missing', {title: 'test'});
assert.match(element('test-all-missing').innerHTML, /Нет данных для графика/);
app.onPeriodChange('month');
assert.equal(app.state.quarterAnchor, null);
assert.equal(app.state.dateFrom, null);
app.state.period = 'quarter';
app.state.dateFrom = '2026-03-31';
app.state.date = '2026-09-30';
const requested = [];
context.fetch = async url => {
    requested.push(url);
    return {ok: true, status: 200, text: async () => JSON.stringify({
        period: {kind: 'quarter', date_from: '2026-03-31', date: '2026-09-30'},
    })};
};
app.render = () => {};
fetchData.call(app).then(() => {
    assert.equal(requested.length, 4);
    assert.ok(requested.some(url => /section=meta/.test(url)));
    assert.ok(requested.some(url => /section=cards/.test(url)));
    assert.ok(requested.some(url => /section=tables/.test(url)));
    assert.ok(requested.some(url => /section=charts/.test(url)));
    assert.ok(requested.every(url => /from=2026-03-31/.test(url) &&
        /to=2026-09-30/.test(url)));
    console.log('Quarter range, request parameters and chart gaps: OK');
}).catch(error => {console.error(error); process.exitCode = 1;});