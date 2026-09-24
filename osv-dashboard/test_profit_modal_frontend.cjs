const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');
const css = fs.readFileSync(`${__dirname}/static/key_indicators.css`, 'utf8');
const nodes = {};
const context = vm.createContext({
  window: {}, Intl, console, setTimeout: fn => fn(),
  document: {
    addEventListener() {},
    getElementById: id => nodes[id] ||= {
      innerHTML: '',
      querySelectorAll: () => [],
    },
  },
});
vm.runInContext(source.replace(
  'data: null,',
  `data: {
    period: {series_kind: 'month'},
    cards: [
      {id:'revenue', value:300000000},
      {id:'cogs', value:180000000}
    ],
    objects: {rows: []}
  },`
), context);
const app = context.window.KI_App;
app.collectionCell = content => `<td>${content}</td>`;

const card = {
  id: 'profit',
  title: 'Прибыль от продаж',
  unit: 'RUB',
  gross_profit: 120000000,
  previous_year_gross_profit: 90000000,
  gross_profit_delta: 30000000,
  profit_from_sales: 100000000,
  other_result: 5000000,
  profit_before_tax: 105000000,
  other_analytics: [],
  gross_series: [],
  sales_profit_series: [],
  other_result_series: [],
  pretax_series: [],
  monthly_comparison: [
    {date:'2026-01-31', label:'Янв 2026', current_gross_profit:40000000,
      previous_year_gross_profit:30000000, delta:10000000},
    {date:'2026-02-28', label:'Фев 2026', current_gross_profit:0,
      previous_year_gross_profit:0, delta:0},
    {date:'2026-03-31', label:'Мар 2026', current_gross_profit:null,
      previous_year_gross_profit:null, delta:null},
  ],
};

app.renderProfitModal(card);
const html = nodes['ki-profit-modal-content'].innerHTML;
assert.equal((html.match(/class="cash-summary-label"/g) || []).length, 3);
for (const label of ['Валовая прибыль', 'Валовая прибыль АПГ', 'Дельта АПГ']) {
  assert.match(html, new RegExp(label));
}
assert.equal((html.match(/cash-chart-mode-selected/g) || []).length, 1);
assert.match(html,
  /cash-chart-mode-selected[^>]*data-profit-chart-mode="profit-gross"/);
assert.match(html,
  /class="ki-modal-dynamics-block profit-chart-block"[\s\S]*id="ki-profit-line-chart" class="cash-line-chart"/);
assert.match(html,
  /Месяц<\/th><th>Валовая прибыль<\/th><th>Валовая прибыль АПГ<\/th><th>Дельта АПГ/);
assert.match(html, />—<\/td>/, 'missing values remain missing');
assert.match(css,
  /\.profit-chart-block\s*\{[^}]*width:100%[^}]*grid-column:1\s*\/\s*-1/);
assert.match(source,
  /toggleProfitChartMode\(mode\)[\s\S]*profitModes\.add\(mode\)/);

const chartHtml = nodes['ki-profit-line-chart'].innerHTML;
assert.match(chartHtml, /class="cash-chart-profit-gross"/);
assert.match(chartHtml, /d="M [^"]+ C [^"]+"/);
assert.doesNotMatch(chartHtml, /cash-chart-profit-gross-apg/);

console.log('Profit modal: three metrics, comparable table and full-width chart passed');