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
  window: {KI_CONFIG: {legacyUrl: '/dashboard/1/dashboard_current.html'}},
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
app.switchTab('ai');
assert.equal(node('tab-ai').classList.contains('active'), true);
assert.equal(node('ki-ai-frame').src, '/dashboard/1/dashboard_current.html');
assert.equal(node('ki-ai-empty').style.display, 'none');
app.switchTab('analytics');
assert.equal(node('tab-ai').classList.contains('active'), false);
context.window.KI_CONFIG.legacyUrl = '/dashboard/2/dashboard_current.html';
app.switchTab('ai');
assert.equal(node('ki-ai-frame').src, '/dashboard/2/dashboard_current.html');
context.window.KI_CONFIG.legacyUrl = null;
app.switchTab('ai');
assert.equal(node('ki-ai-frame').style.display, 'none');
assert.equal(node('ki-ai-frame').src, '');
assert.equal(node('ki-ai-empty').style.display, 'block');
const template = fs.readFileSync(`${__dirname}/templates/key_indicators.html`, 'utf8');
assert.ok(template.indexOf('data-tab="recommendations"') < template.indexOf('data-tab="ai"'));
assert.ok(template.indexOf('data-tab="ai"') < template.indexOf('data-tab="data"'));
const home = fs.readFileSync(`${__dirname}/templates/home.html`, 'utf8');
assert.equal(home.includes('data-nav="kpi"'), false);
console.log('PASS: AI tab placement, report loading, return, company change and empty state');