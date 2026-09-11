// Browser-independent regressions for dashboard card preferences.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(
  `${__dirname}/static/key_dashboard_layout.js`, 'utf8');
const context = vm.createContext({
  window: {
    KI_CONFIG: {
      companyId: 7,
      dashboardLayoutURL:
        '/company/7/key-indicators/layout/__MODE__/__dashboard__',
    },
    KI_App: {state: {mode: 'operational', data: {cards: [
      {id: 'cash', title: 'Деньги'},
      {id: 'revenue', title: 'Выручка'},
    ]}}},
    KI_AnnualApp: {state: {data: null}},
    addEventListener() {},
  },
  document: {
    addEventListener() {},
    getElementById() { return null; },
    createElement() {
      return {textContent: '', innerHTML: ''};
    },
  },
  AbortController,
  confirm: () => true,
  console,
});
vm.runInContext(source, context);
const manager = context.window.KI_DashboardLayout;

assert.equal(
  manager.getUrl('operational'),
  '/company/7/key-indicators/layout/osv/__dashboard__');
assert.deepEqual(JSON.parse(JSON.stringify(manager.mergeLayout('operational', {
  version: 1,
  items: {revenue: 'hidden'},
  order: ['revenue'],
}))), {
  version: 1,
  items: {
    cash: 'main', revenue: 'hidden', collection: 'main', ar: 'main',
    ap: 'main', cogs: 'main', profit: 'main', credits: 'main',
    salary: 'main', capex: 'main',
  },
  order: ['revenue', 'cash', 'collection', 'ar', 'ap', 'cogs', 'profit',
    'credits', 'salary', 'capex'],
});

// Applying a saved all-hidden layout only hides cards; the external gear
// remains the recovery control and a later render receives the saved order.
const cards = [
  {dataset: {metric: 'cash'}, hidden: false},
  {dataset: {metric: 'revenue'}, hidden: false},
];
const grid = {
  children: cards.slice(),
  appendChild(card) {
    this.children = this.children.filter(item => item !== card);
    this.children.push(card);
  },
};
context.document.getElementById = () => grid;
manager.state.loaded.operational = true;
manager.state.layouts.operational = {
  version: 1,
  items: {cash: 'hidden', revenue: 'hidden'},
  order: ['revenue', 'cash'],
};
manager.apply('operational');
assert.deepEqual(grid.children.map(card => card.dataset.metric), ['revenue', 'cash']);
assert.equal(grid.children.every(card => card.hidden), true);

// Dirty source changes are guarded before KI_App changes the mode.
manager.state.dialog = {hidden: false};
manager.state.dialogMode = 'operational';
manager.state.draft = {version: 1, items: {cash: 'hidden'}, order: ['cash']};
manager.state.savedDraft = {version: 1, items: {cash: 'main'}, order: ['cash']};
context.confirm = () => false;
assert.equal(manager.prepareModeSwitch('annual'), false);

console.log('PASS: dashboard layout URL, unknown/new-card append, visibility merge, dirty guard');