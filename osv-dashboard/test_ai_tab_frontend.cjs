const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, {
      style: {}, src: '',
      classList: {
        add: x => classes.add(x), remove: x => classes.delete(x),
        contains: x => classes.has(x),
      },
      getAttribute() { return this.src; },
      removeAttribute() { this.src = ''; },
      querySelector() { return null; },
    });
  }
  return nodes.get(id);
}
const context = vm.createContext({
  window: {KI_CONFIG: {companyId: 1, aiReportUrl: '/company/1/key-indicators/ai-report'}},
  document: {
    addEventListener() {},
    getElementById: node,
    querySelector: node,
    querySelectorAll: selector => selector === '.tab-content'
      ? ['tab-ai', 'tab-analytics', 'tab-recommendations'].map(node) : [],
  },
  console, Intl, URL,
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8'), context);
const app = context.window.KI_App;
app.setAiReport('operational', {
  period: {kind: 'day', date: '2026-01-31', date_from: '2026-01-31'},
});
app.switchTab('ai');
assert.equal(node('tab-ai').classList.contains('active'), true);
assert.equal(node('ki-ai-frame').src, '/company/1/key-indicators/ai-report?period=day&date=2026-01-31');
node('ki-ai-frame').onload();
assert.equal(node('ki-ai-frame').style.display, 'block');
assert.equal(node('ki-ai-empty').style.display, 'none');
app.switchTab('analytics');
app.switchTab('ai');
assert.equal(node('ki-ai-frame').style.display, 'block');
assert.equal(node('ki-ai-empty').style.display, 'none');
const oldLoad = node('ki-ai-frame').onload;
// A new selection clears an old report before its normalized data arrives.
app.invalidateAiReport('loading');
assert.equal(node('ki-ai-frame').src, '');
assert.equal(node('ki-ai-empty').style.display, 'block');
app.setAiReport('operational', {
  period: {kind: 'month', date: '2026-02-28', date_from: '2026-01-31'},
});
assert.equal(node('ki-ai-frame').src,
  '/company/1/key-indicators/ai-report?period=month&date=2026-02-28&date_from=2026-01-31');
oldLoad();
assert.equal(node('ki-ai-frame').style.display, 'none');
app.invalidateAiReport('loading');
app.setAiReport('annual', {year: 2025});
assert.equal(node('ki-ai-frame').src,
  '/company/1/key-indicators/ai-report?mode=annual&year=2025');
app.invalidateAiReport('empty');
assert.equal(node('ki-ai-frame').src, '');
assert.equal(node('ki-ai-empty').style.display, 'block');
const template = fs.readFileSync(`${__dirname}/templates/key_indicators.html`, 'utf8');
assert.ok(template.indexOf('data-tab="recommendations"') < template.indexOf('data-tab="ai"'));
assert.ok(template.indexOf('data-tab="ai"') < template.indexOf('data-tab="data"'));
const home = fs.readFileSync(`${__dirname}/templates/home.html`, 'utf8');
assert.equal(home.includes('data-nav="kpi"'), false);
const script = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');
assert.equal(script.includes("document.querySelector('.tabs-bar').style.display = 'none'"), false);
console.log('PASS: AI tab uses normalized period report, clears stale frame and supports annual state');