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
    accrual_series: [{label:'A', value:21000000}, {label:'B', value:0}, {label:'C', value:null}],
    series: [{label:'A', value:31000000}, {label:'B', value:null}, {label:'C', value:0}],
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
  assert.match(nodes['ki-salary-modal-content'].innerHTML, /Единственная мера графика — ОбК70/);
  assert.ok(!nodes['ki-salary-modal-content'].innerHTML.includes('ki-salary-line-chart'),
    'legacy specialized salary chart is suppressed');
  assert.match(nodes['ki-salary-modal-content'].innerHTML,
    /data-series-id="__main__"/, 'salary debt tile uses the main series');
  assert.match(nodes['ki-salary-modal-content'].innerHTML,
    /data-series-id="detail:1"/, 'salary debit tile uses its authentic detail series');
}
console.log('Salary modal: cell facts, turnover metadata, day/month and zero/null passed');