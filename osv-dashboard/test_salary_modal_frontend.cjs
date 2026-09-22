const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');
for (const kind of ['day', 'month']) {
  const nodes = {};
  const cells = [];
  const context = vm.createContext({
    window: {}, Intl, console, setTimeout: fn => fn(),
    document: {
      addEventListener() {},
      getElementById: id => nodes[id] ||= {innerHTML: '', querySelectorAll: () => []},
    },
  });
  // Inject only the API period metadata, leaving the production renderer intact.
  vm.runInContext(source.replace('data: null,', `data: {period: {series_kind: '${kind}'}},`), context);
  const app = context.window.KI_App;
  app.collectionCell = (content, tooltip) => {
    cells.push({content, tooltip});
    return `<td>${content}</td>`;
  };
  const analytics = [{
    label: 'Синтетическая аналитика', debit_balance: 1100000,
    credit_balance: 2200000, debit_turnover: 3300000, credit_turnover: 4400000,
  }];
  const card = {
    id: 'salary', title: 'Задолженность', unit: 'RUB', value: 7700000,
    formula: 'СТАРОЕ САЛЬДО', source: 'СТАРЫЙ ИСТОЧНИК',
    debit_balance: 5500000, debit_turnover: 6600000, credit_turnover: 8800000,
    analytics,
    details: [
      {label:'Кредитовое сальдо 70', value:7700000, series:[{label:'A', value:7700000}]},
      {label:'Дебетовое сальдо 70', value:5500000, series:[{label:'A', value:5500000}]},
      {label:'Кредитовый оборот 70', value:8800000, series:[{label:'A', value:8800000}]},
    ],
    insurance: {debit_balance: 9000000, credit_balance: 10000000,
      debit_turnover: 12000000, credit_turnover: 13000000, analytics},
    accrual_series: [
      {date:'2026-01-05', label:'A', value:21000000},
      {date:'2026-01-12', label:'B', value:0},
      {date:'2026-01-19', label:'C', value:null},
    ],
    previous_year_accrual: 18000000,
    accrual_delta: 3000000,
    accrual_delta_percent: 16.67,
    previous_year_accrual_series: [
      {date:'2026-01-05', label:'A · АПГ', value:18000000},
      {date:'2026-01-12', label:'B · АПГ', value:0},
      {date:'2026-01-19', label:'C · АПГ', value:null},
    ],
    accrual_delta_series: [
      {date:'2026-01-05', label:'A · дельта АПГ', value:3000000},
      {date:'2026-01-12', label:'B · дельта АПГ', value:0},
      {date:'2026-01-19', label:'C · дельта АПГ', value:null},
    ],
    monthly_comparison: [
      {date:'2026-01-31', label:'A', current_accrual:21000000, employee_debt:31000000,
        previous_year_accrual:18000000, delta:3000000},
      {date:'2026-02-28', label:'B', current_accrual:0, employee_debt:32000000,
        previous_year_accrual:0, delta:0},
      {date:'2026-03-31', label:'C', current_accrual:null, employee_debt:null,
        previous_year_accrual:null, delta:null},
    ],
    series: [
      {date:'2026-01-05', label:'A', value:31000000},
      {date:'2026-01-12', label:'B', value:null},
      {date:'2026-01-19', label:'C', value:0},
    ],
    insurance_series: [{label:'A', value:41000000}, {label:'B', value:0}, {label:'C', value:null}],
  };
  const before = JSON.stringify(card);
  app.renderSalaryModal(card);
  assert.equal(JSON.stringify(card), before, 'rendering must not mutate data');
  for (const cell of cells.filter(c => /млн ₽|^Н\/Д$/.test(c.content))) {
    assert.equal(cell.tooltip.fact, cell.content, `tooltip fact must match ${cell.content}`);
  }
  const rub = v => `${app.formatValue(v, 'RUB').val} млн ₽`;
  assert(cells.some(c => c.tooltip.formula === 'ОбК(69) за выбранный период' && c.tooltip.fact === rub(41000000)));
  assert(cells.some(c => c.tooltip.formula === 'max(СКк(70) − СКд(70), 0)' && c.tooltip.fact === rub(31000000)));
  const html = nodes['ki-salary-modal-content'].innerHTML;
  assert.equal((html.match(/class="cash-summary-label"/g) || []).length, 4);
  assert.equal((html.match(/cash-chart-mode-selected/g) || []).length, 1,
    'only employee accruals are selected by default');
  assert.match(html,
    /cash-chart-mode-selected[^>]*data-salary-chart-mode="salary-accrual"/,
    'employee accruals are the default chart series');
  for (const label of ['Начисления работникам', 'Задолженность работникам',
    'Начисления АПГ', 'Дельта АПГ']) assert.match(html, new RegExp(label));
  assert.match(html, /Дельта АПГ[\s\S]*\+16\.67% к АПГ/,
    'the delta card shows the reference percentage when APG is non-zero');
  assert.doesNotMatch(html, /Изменение к АПГ/,
    'the calculated APG delta is not repeated as an unavailable metric');
  assert.match(html, /Месяц<\/th><th>Начисления<\/th><th>Начисления АПГ<\/th><th>Дельта АПГ/);
  assert.match(html, /class="cash-table salary-account-table"/);
  assert.match(html, /<thead><tr><th>Счёт<\/th><th>Начислено<\/th><th>Выплачено<\/th><th>Задолженность<\/th><\/tr><\/thead>/);
  assert.match(html, /class="cash-total-row"[\s\S]*<td>Итого<\/td>/);
  for (const mode of ['salary-accrual', 'salary-debt', 'salary-apg', 'salary-delta']) {
    assert.match(html, new RegExp(`data-salary-chart-mode="${mode}"`));
  }
  assert.match(html, /class="ki-modal-dynamics-block salary-chart-block"[\s\S]*data-layout-key="dynamics"[\s\S]*id="ki-salary-line-chart" class="cash-line-chart"/,
    'salary uses the same specialized line-chart structure as receivables');
  assert.match(fs.readFileSync(`${__dirname}/static/key_indicators.css`, 'utf8'),
    /\.salary-chart-block\s*\{[^}]*width:100%[^}]*grid-column:1\s*\/\s*-1/,
    'the salary chart spans the full modal layout width');
  assert.match(html, /class="cash-chart-legend"[\s\S]*Начисления работникам[\s\S]*Задолженность работникам[\s\S]*Начисления АПГ[\s\S]*Дельта АПГ/,
    'the salary chart has the same static legend pattern as receivables');
  assert.match(html, />—<\/td>/, 'missing comparison values remain missing');
  assert.doesNotMatch(html, /ki-metric-tile-active|ki-metric-tile-pinned/,
    'salary tiles have only the single receivables-style selected border');
  assert.match(source, /toggleSalaryChartMode\(mode\)[\s\S]*salaryModes\.add\(mode\)/,
    'clicking another salary tile adds its line');
  const chartHtml = nodes['ki-salary-line-chart'].innerHTML;
  assert.match(chartHtml, /class="cash-chart-salary-accrual"/);
  assert.match(chartHtml, /d="M [^"]+ C [^"]+"/,
    'the default monthly employee accrual series draws a curved line');
  assert.doesNotMatch(chartHtml, /cash-chart-salary-debt/,
    'other salary lines are not drawn until selected');
}
console.log('Salary modal: cell facts, turnover metadata, day/month and zero/null passed');