// Fast browser-independent checks of bootstrap, loading cleanup and units.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');

async function checkBootstrap(configuration) {
  const nodes = new Map();
  const requests = [];
  const listeners = new Map();
  const stored = new Map();
  const context = vm.createContext({
    window: {
      localStorage: {
        getItem: key => stored.has(key) ? stored.get(key) : null,
        setItem: (key, value) => stored.set(key, value),
      },
    },
    AbortController,
    Intl,
    console,
    document: {
      addEventListener(name, callback, capture) { listeners.set(name, { callback, capture }); },
      querySelectorAll() { return []; },
      getElementById(id) {
        if (!nodes.has(id)) {
          const classes = new Set();
          const attributes = new Map();
          const children = {};
          nodes.set(id, {
            style: {}, textContent: '', innerHTML: '', dataset: {},
            classList: {
              add: name => classes.add(name), remove: name => classes.delete(name),
              contains: name => classes.has(name),
              toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
            },
            setAttribute: (name, value) => attributes.set(name, value),
            getAttribute: name => attributes.get(name),
            addEventListener() {},
            querySelectorAll(selector) {
              if (selector !== '[data-revenue-metric]') return [];
              return [...this.innerHTML.matchAll(/data-revenue-metric="([^"]+)"[^>]*aria-pressed="([^"]+)"/g)]
                .map(match => {
                  const key = match[1];
                  if (!children[key]) {
                    const attrs = new Map([['aria-pressed', match[2]]]);
                    const childClasses = new Set();
                    const handlers = {};
                    children[key] = {
                      dataset: {revenueMetric: key},
                      classList: {toggle: (name, enabled) => enabled ? childClasses.add(name) : childClasses.delete(name)},
                      setAttribute: (name, value) => attrs.set(name, value),
                      getAttribute: name => attrs.get(name),
                      addEventListener: (name, fn) => { handlers[name] = fn; },
                      click: () => handlers.click && handlers.click(),
                    };
                  }
                  return children[key];
                });
            },
          });
        }
        return nodes.get(id);
      },
    },
    fetch: async url => {
      requests.push(url);
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify({
          cards: [], period: { kind: 'year', date: '2026-06-30' },
        }),
      };
    },
  });
  vm.runInContext(configuration, context);
  vm.runInContext(script, context);
  const app = context.window.KI_App;
  let loading = false;
  let renders = 0;
  app.showLoading = () => { loading = true; };
  app.hideLoading = () => { loading = false; };
  app.render = () => { renders++; };
  await app.fetchData();
  assert.equal(loading, false, 'loading must clear even on configuration errors');
  if (configuration) {
    assert.equal(requests[0], '/company/11/key-indicators/data?period=year');
    assert.equal(renders, 1, 'valid configuration must reach rendering');
    stored.set('brain4sales:key-indicators:period-selection:11', JSON.stringify({
      period: 'month', date: '2026-06-30', dateFrom: '2026-01-31',
    }));
    assert.equal(app.restorePeriodSelection(), true);
    assert.equal(app.state.period, 'month');
    assert.equal(app.state.date, '2026-06-30');
    assert.equal(app.state.dateFrom, '2026-01-31');
    app.savePeriodSelection();
    assert.deepEqual(
      JSON.parse(stored.get('brain4sales:key-indicators:period-selection:11')),
      {period: 'month', date: '2026-06-30', dateFrom: '2026-01-31'});
  } else {
    assert.equal(requests.length, 0);
    assert.match(nodes.get('ki-error-text').textContent, /адрес источника/);
  }
  const million = app.formatValue(1_000_000, 'RUB');
  assert.equal(Number(million.val.replace(',', '.')), 1);
  assert.equal(million.u, 'млн ₽');
  assert.equal(app.formatValue(0, 'RUB').val, '0');
  assert.equal(app.formatValue(null, 'RUB').val, 'Н/Д');
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.tableSortValue({dataset: {}, textContent: '1 234,50 ₽'}))),
    {type: 'number', value: 1234.5});
  const sortableRows = ['12,5%', '2,0%', '8,3%', 'ИТОГО'].map((text, index) => ({
    index,
    classList: {contains: () => false},
    cells: [{dataset: {}, textContent: text}]
  }));
  const sortedRows = [];
  app.sortTable({
    tBodies: [{rows: sortableRows, appendChild: row => sortedRows.push(row)}]
  }, 0, 'asc');
  assert.deepEqual(sortedRows.map(row => row.index), [1, 2, 0, 3]);
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.columnResizeWidths(200, 100, 80))),
    {current: 228, neighbor: 72},
    'column growth must be limited by the neighboring column');
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.columnResizeWidths(200, 100, -180))),
    {current: 72, neighbor: 228},
    'a resized column must retain its minimum width');
  app.mountSharedDynamics = () => {};
  const collectionBase = {
    id: 'collection', title: 'Собираемость', value: 60, unit: '%',
    accounting_proxy: {value: 60}, components: [],
    consumer_collection: {available: false, categories: [], series: []},
  };
  assert.doesNotThrow(() => app.renderCollectionModal(collectionBase));
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /УРОВЕНЬ СОБИРАЕМОСТИ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /НАЧИСЛЕНО/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ОПЛАЧЕНО/);
  const proxyComponents = Object.assign({}, collectionBase, {
    value: 70,
    previous_year_value: 65,
    previous_year_delta: 5,
    accounting_proxy: {value: 70},
    turnover_series: [
      {date: '2026-01-01', label: '01.01', value: 92,
       debit_6201: 100, credit_6201: 80, debit_6202: 3, credit_6202: 15},
      {date: '2026-01-02', label: '02.01', value: null},
      {date: '2026-01-03', label: '03.01', value: 96,
       debit_6201: 100, credit_6201: 85, debit_6202: 2, credit_6202: 13},
      {date: '2026-01-04', label: '04.01', value: 1459250},
      {date: '2026-01-05', label: '05.01', value: -3000},
    ],
    previous_year_turnover_series: [
      {date: '2026-01-01', source_date: '2025-01-01', label: '01.01',
       value: 88, debit_6201: 100, credit_6201: 80,
       debit_6202: 2, credit_6202: 10},
      {date: '2026-01-02', source_date: '2025-01-02', label: '02.01',
       value: null},
      {date: '2026-01-03', source_date: '2025-01-03', label: '03.01',
       value: 97, debit_6201: 100, credit_6201: 90,
       debit_6202: 2, credit_6202: 9},
    ],
    components: [
      {account: '62.01', side: 'debit', value: 100000000},
      {account: '62.01', side: 'credit', value: 60000000},
      {account: '62.02', side: 'debit', value: 20000000},
      {account: '62.02', side: 'credit', value: 30000000},
    ],
  });
  app.renderCollectionModal(proxyComponents);
  const collectionHtml = nodes.get('ki-collection-modal-content').innerHTML;
  assert.match(collectionHtml, /70,0 млн ₽/);
  assert.match(collectionHtml, /ОбК\(62\.01\) \+ ОбК\(62\.02\) − ОбД\(62\.02\)/);
  assert.match(collectionHtml, /Начисления/);
  assert.match(collectionHtml, /Поступления/);
  assert.match(collectionHtml, /сч\. 62 − 62\.02/);
  assert.match(collectionHtml, />АПГ</);
  assert.match(collectionHtml, /ДЕЛЬТА АПГ/);
  assert.doesNotMatch(collectionHtml, /РОЛЬ В ФОРМУЛЕ/);
  assert.doesNotMatch(collectionHtml, /Кредитовый оборот 62\.01/);
  assert.doesNotMatch(collectionHtml, /Кредитовый оборот 62\.02/);
  assert.doesNotMatch(collectionHtml, /Дебетовый оборот 62\.02/);
  assert.match(collectionHtml, /УРОВЕНЬ АПГ/);
  assert.match(collectionHtml, /\+5\.0 п\.п\./);
  assert.match(collectionHtml, /data-collection-period="day"/);
  assert.match(collectionHtml, /data-collection-period="month"/);
  assert.match(collectionHtml, /data-series-id="__apg__"/);
  const collectionChartHtml = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.match(collectionChartHtml, /collection-zone-red/);
  assert.match(collectionChartHtml, /collection-zone-yellow/);
  assert.match(collectionChartHtml, /collection-zone-green/);
  assert.equal((collectionChartHtml.match(/collection-dynamics-line/g) || []).length, 2);
  assert.match(collectionChartHtml, /Не соединено значений вне рабочей шкалы 0–100%: 2/);
  assert.doesNotMatch(collectionChartHtml, /1459250/);
  app.state.collectionTimelineMode = 'month';
  app.renderCollectionDynamics(proxyComponents.turnover_series);
  const monthlyCollectionChart = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.equal((monthlyCollectionChart.match(/collection-dynamics-point/g) || []).length, 1);
  assert.match(monthlyCollectionChart, /янв 26/);
  assert.match(monthlyCollectionChart, /Собираемость: 94\.0%/);
  app.renderCollectionDynamics([
    {date: '2026-02-10', value: 50, debit_6201: 40,
     credit_6201: 40, debit_6202: 2, credit_6202: 4.64},
    {date: '2026-02-28', value: 150, debit_6201: 60,
     credit_6201: 60, debit_6202: 3, credit_6202: 6.96},
    {date: '2026-03-10', value: 12, debit_6201: 40,
     credit_6201: 12, debit_6202: 2, credit_6202: 3.96},
    {date: '2026-03-31', value: 60, debit_6201: 60,
     credit_6201: 18, debit_6202: 3, credit_6202: 5.94},
    {date: '2026-07-31', value: 17.11, debit_6201: 100,
     credit_6201: 100, debit_6202: 5, credit_6202: 36.39},
  ]);
  const realMonthlyRatios = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.match(realMonthlyRatios, /Собираемость: 106\.6%/);
  assert.match(realMonthlyRatios, /Собираемость: 34\.9%/);
  assert.match(realMonthlyRatios, /Собираемость: 131\.4%/);
  app.state.collectionShowApg = true;
  app.renderCollectionDynamics(
    proxyComponents.turnover_series,
    proxyComponents.previous_year_turnover_series);
  const collectionWithApg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.match(collectionWithApg, /collection-dynamics-apg-line/);
  assert.match(collectionWithApg, /collection-dynamics-apg-point/);
  assert.match(collectionWithApg, /АПГ:/);
  assert.match(collectionWithApg, /collection-chart-legend/);
  app.state.collectionShowApg = false;
  app.state.collectionTimelineMode = 'day';
  assert.match(script, /activeContainer !== collectionContent/);
  const factualCollection = Object.assign({}, collectionBase, {
    value: 80,
    consumer_collection: {
      available: true, value: 80, accrual: 100, payment: 80,
      reconciled: false, reconciliation_difference: 20,
      rejected: [], series: [], accrual_sources: [{
        source: '1С: Расчёты', version: '2026.01', rows: 2,
        accrual: 100, date_from: '2026-01-15', date_to: '2026-01-20',
      }], categories: [{
        id: 'population', label: 'Население', accrual: 100,
        payment: 80, value: 80, plan: null, deviation: null, status: null,
      }],
    },
  });
  assert.doesNotThrow(() => app.renderCollectionModal(factualCollection));
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /УРОВЕНЬ СОБИРАЕМОСТИ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /Население/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ИСТОЧНИКИ НАЧИСЛЕНИЙ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /1С: Расчёты/);
  const previousData = app.state.data;
  if (configuration) {
  app.state.data = {
    period: {start: '2026-01-01', end: '2026-06-30', label: '2026'},
    cards: [{id: 'revenue', value: 1000000000, title: 'Выручка'}],
  };
  assert.equal(app.displayedPeriodLabel(), '01.01.2026 — 30.06.2026');
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.chartPeriodBounds([
      Date.UTC(2025, 11, 29), Date.UTC(2026, 8, 1)
    ]))),
    {start: Date.UTC(2026, 0, 1), end: Date.UTC(2026, 5, 30)},
    'chart bounds must use the exact selected period instead of bucket edges');
  app.updateModalSubtitle();
  assert.equal(nodes.get('ki-modal-subtitle').textContent,
    'Детализация по данным ОСВ · 01.01.2026 — 30.06.2026');
  if (configuration) {
    assert.doesNotThrow(() => app.renderArModal({
      id: 'ar', value: 437405275.57, opening_value: 100000000,
      consumer_advances: 53124872,
      analytics: [], details: [], series: [],
      debtor_segments: {
        categories: [], new: [], unmapped: [], conflicts: [],
        confirmed_total: 437405275.57, classified_total: 0,
        reconciliation_difference: 437405275.57, reconciled: false,
      },
    }));
    assert.match(nodes.get('ki-ar-modal-content').innerHTML, /ДЗ ПОТРЕБИТЕЛЕЙ/);
    assert.match(nodes.get('ki-ar-modal-content').innerHTML, /АВАНСЫ ПОТРЕБИТЕЛЕЙ/);
  }
  app.state.data.period.start = '2026-06-30';
  assert.equal(app.displayedPeriodLabel(), '30.06.2026');
  app.state.data = previousData;

  if (configuration) {
    context.fetch = async () => ({
      ok: false, status: 422,
      text: async () => JSON.stringify({ error: 'Не удалось прочитать файл ОСВ: sample.xls' }),
    });
    await app.fetchData();
    assert.equal(nodes.get('ki-error-text').textContent, 'Не удалось прочитать файл ОСВ: sample.xls');
    assert.equal(loading, false);
  }

  app.bindEvents();
  const modal = nodes.get('ki-modal');
  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');
  const keydown = listeners.get('keydown');
  assert.equal(keydown.capture, true, 'capture Escape even before dialog gains focus');
  let prevented = false;
  keydown.callback({
    key: 'Escape',
    preventDefault() { prevented = true; },
    stopPropagation() {},
  });
  assert.equal(prevented, true);
  assert.equal(modal.classList.contains('visible'), false);
  assert.equal(modal.style.visibility, 'hidden');
  assert.equal(modal.getAttribute('aria-hidden'), 'true');

  app.state.period = 'year';
  app.state.dateFrom = '2026-01-01';
  app.state.date = '2026-06-30';
  app.state.data = {period: {
    kind: 'year', date: '2026-06-30', date_from: '2026-01-01',
    start: '2026-01-01', end: '2026-06-30',
    options: [
      {value: '2024-12-31', label: '2024'},
      {value: '2025-12-31', label: '2025'},
      {value: '2026-06-30', label: '2026'},
    ],
  }};
  app.renderPeriodSelector();
  assert.equal(nodes.get('ki-date-from-select').value, '01.01.2026');
  assert.equal(nodes.get('ki-date-select').value, '30.06.2026');
  let fetched = 0;
  app.fetchData = () => { fetched++; };
  app.onPeriodPickerButton();
  assert.equal(fetched, 0, 'opening the picker must not load data');
  assert.equal(nodes.get('ki-period-picker-button').textContent, 'Загрузить период');
  assert.match(nodes.get('ki-period-years').innerHTML, />Мар</);
  app.selectPeriodMonth('2025-03-01');
  assert.equal(nodes.get('ki-date-from-select').value, '01.03.2025');
  assert.equal(nodes.get('ki-date-select').value, '—');
  assert.equal(fetched, 0, 'selecting the first month must not load data');
  app.selectPeriodMonth('2026-06-01');
  assert.equal(nodes.get('ki-date-select').value, '30.06.2026');
  assert.equal(fetched, 0, 'selecting the second month must not load data');
  app.shiftPeriodDay('from', 1);
  app.shiftPeriodDay('to', -2);
  assert.equal(nodes.get('ki-date-from-select').value, '02.03.2025');
  assert.equal(nodes.get('ki-date-select').value, '28.06.2026');
  app.shiftPeriodDay('from', -20);
  assert.equal(nodes.get('ki-date-from-select').value, '01.03.2025',
    'scrolling must not leave the selected start month');
  app.cancelPeriodPicker();
  assert.equal(nodes.get('ki-period-picker-button').textContent, 'Выбрать период');
  assert.equal(nodes.get('ki-date-from-select').value, '01.01.2026');
  assert.equal(nodes.get('ki-date-select').value, '30.06.2026');
  assert.equal(fetched, 0, 'closing outside must cancel the draft without loading');
  app.onPeriodPickerButton();
  app.selectPeriodMonth('2025-03-01');
  app.selectPeriodMonth('2026-06-01');
  app.onPeriodPickerButton();
  assert.equal(fetched, 1, 'data must load only after confirmation');
  assert.equal(app.state.period, 'day');
  assert.equal(app.state.dateFrom, '2025-03-01');
  assert.equal(app.state.date, '2026-06-30');
  assert.equal(nodes.get('ki-period-picker-button').textContent, 'Выбрать период');
  app.state.data.period.options = [
    {value: '2026-09-15', label: '15.09.2026'},
  ];
  app.onPeriodPickerButton();
  app.selectPeriodMonth('2026-09-01');
  app.selectPeriodMonth('2026-09-01');
  assert.equal(nodes.get('ki-date-select').value, '15.09.2026',
    'the current partial month must end on the last available data date');
  app.cancelPeriodPicker();
  assert.match(script, /delta < 0 \? 'cash-value-negative'/,
    'negative cash balance changes must use the carrot color class');
  assert.match(script, /<th>Группа<\/th>/,
    'the former Segment table must be labeled Group');
  assert.match(script, /Сумма с НДС, млн ₽/,
    'revenue analytics amounts must be labeled in millions of rubles');
  assert.ok(script.includes('.format(value / 1000000)'),
    'revenue analytics amounts must be scaled to millions');
  assert.match(script, /metric-inline-unit/,
    'money units must be rendered inline with metric values');
  assert.doesNotMatch(
    script,
    /cash-summary-sub">\$\{esc\((?:total|gross|cash)Formatted\.u\)/,
    'summary cards must not place money units on the line below the value');
  app.state.data = {
    period: {start: '2026-01-01', end: '2026-06-30'},
    revenue_segments: {
      categories: [], reconciled: false,
      activity_groups: {reconciled: true, rows: [{
        id: 'heat', label: 'Теплоснабжение',
        gross: 329_991_202.01, vat: 59_506_610.29, value: 270_484_591.72,
      }]},
    },
  };
  const revenueCard = {
    id: 'revenue', title: 'Выручка', value: 418_619_956.56, unit: 'RUB',
    source: 'ОСВ счёта 90', details: [
      {label: 'Выручка брутто', value: 510_714_933.29, unit: 'RUB'},
      {label: 'НДС', value: 92_094_976.73, unit: 'RUB'},
    ],
    year_over_year: {
      current: 418_619_956.56, previous: 191_929_419.40,
      delta: 226_690_537.16, percent: 118.11, basis: 'net',
      start: '2025-01-01', end: '2025-06-30',
    },
  };
  app.renderRevenueModal(revenueCard);
  const revenueHtml = nodes.get('ki-revenue-modal-content').innerHTML;
  assert.match(revenueHtml, /data-revenue-metric="total" aria-pressed="true"/);
  assert.match(revenueHtml, /data-revenue-metric="yoy" aria-pressed="true"/);
  assert.match(revenueHtml, />Выручка с НДС</);
  assert.match(revenueHtml, />Выручка без НДС</);
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /418,62 млн ₽/);
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /191,93 млн ₽/);
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /\+226,69 млн ₽ · \+118\.11%/);
  assert.doesNotMatch(nodes.get('ki-revenue-chart').innerHTML, /<path|Динамика|revenue-chart-line/);
  const switches = nodes.get('ki-revenue-modal-content').querySelectorAll('[data-revenue-metric]');
  switches.find(button => button.dataset.revenueMetric === 'gross').click();
  switches.find(button => button.dataset.revenueMetric === 'vat').click();
  assert.match(nodes.get('ki-revenue-chart').innerHTML,
    /510,71 млн ₽ − 92,09 млн ₽ = 418,62 млн ₽/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /НДС \(вычитается\)/);
  assert.match(revenueHtml, /Вид деятельности/);
  assert.match(revenueHtml, /Теплоснабжение/);
  assert.match(revenueHtml, /329,99/);
  assert.match(revenueHtml, /59,51/);
  assert.match(revenueHtml, /270,48/);
  assert.doesNotMatch(revenueHtml, /Новые аналитики без категории/);
  assert.match(revenueHtml, /Подтверждённая выручка по совпадающим листовым путям аналитики 90\.01 и 90\.03/);
  assert.doesNotMatch(revenueHtml, /Нужен утверждённый классификатор соответствия аналитик/);
  switches.find(button => button.dataset.revenueMetric === 'yoy').click();
  assert.doesNotMatch(nodes.get('ki-revenue-summary-table').innerHTML, /Сравнение с АПГ \(/);
  assert.doesNotMatch(nodes.get('ki-revenue-summary-table').innerHTML, /191,93 млн ₽/);
  switches.find(button => button.dataset.revenueMetric === 'yoy').click();
  app.state.data.revenue_segments.activity_groups = {reconciled: false, rows: []};
  const missingRevenue = {...revenueCard, details: [
    {label: 'Выручка брутто', value: 510_714_933.29},
    {label: 'НДС', value: null, reason: 'Строки 90.03 отсутствуют'},
  ], value: 510_714_933.29, year_over_year: null};
  app.renderRevenueModal(missingRevenue);
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /Нужны данные/);
  assert.doesNotMatch(nodes.get('ki-revenue-chart').innerHTML, /Аналогичный период прошлого года<\/strong>/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Сравнение с АПГ \(база не подтверждена\)/);
  const zeroBaseRevenue = {...missingRevenue, year_over_year: {
    current: 510_714_933.29, previous: 0, delta: 510_714_933.29,
    percent: null, basis: 'gross', start: '2025-01-01', end: '2025-06-30',
  }};
  assert.doesNotThrow(() => app.renderRevenueModal(zeroBaseRevenue));
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /процент не рассчитывается при нулевой базе/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Выручка без НДС \(база не подтверждена\)/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /Сравнение с АПГ \(брутто\)/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Не рассчитывается при нулевой базе/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /510,71 млн ₽/);
  assert.match(script,
    /activeContainer !== cashContent &&\s*activeContainer !== revenueContent/,
    'the legacy shared dynamics chart must not mount inside the revenue modal');
  }
  app.renderRevenueLineChart({series: [
    {date: '2026-01-31', label: 'Январь', value: 1_000_000, granularity: 'month'},
    {date: '2026-02-28', label: 'Февраль', value: 2_000_000, granularity: 'month'},
    {date: '2026-03-31', label: 'Март', value: 3_000_000, granularity: 'month'},
  ]});
  assert.match(
    nodes.get('ki-revenue-line-chart').innerHTML,
    /<path d="[^"]* L [^"]*" class="revenue-chart-line-solid"/,
    'adjacent monthly revenue points must be connected by a solid line');
}

(async () => {
  for (const configuration of [
    'window.KI_CONFIG = {dataUrl: "/company/11/key-indicators/data"};',
    'const KI_CONFIG = {dataUrl: "/company/11/key-indicators/data"};',
    '',
  ]) await checkBootstrap(configuration);
  const home = fs.readFileSync(`${__dirname}/templates/home.html`, 'utf8');
  const opened = [];
  const navContext = vm.createContext({
    document: {
      getElementById: () => ({
        style: {},
        contentWindow: { location: { href: 'http://localhost/company/11/key-indicators?mode=annual' } }
      })
    },
    URL,
    keyIndicatorsMode: 'osv',
    loadIframe: (...args) => opened.push(args),
  });
  for (const name of ['renderDashView', 'renderCardsView']) {
    vm.runInContext(home.match(new RegExp(`function ${name}\\(el\\) \\{[\\s\\S]*?\\n\\}`))[0], navContext);
  }
  const company = { dataset: {
    hasDash: '1', dash: '/dashboard/11/dashboard_current.html',
    indicators: '/company/11/key-indicators',
  } };
  navContext.renderDashView(company);
  navContext.renderCardsView(company);
  assert.deepEqual(opened[0], [company.dataset.dash, company.dataset.dash]);
  assert.deepEqual(opened[1], [
    company.dataset.indicators + '?embedded=1&mode=annual',
    company.dataset.indicators + '?mode=annual'
  ]);
  assert.match(script, /\(ОбК\(62\.01\) \+ ОбК\(62\.02\) − ОбД\(62\.02\)\) \/ ОбД\(62\.01\)/,
    'collection modal must show the strict ОСВ formula');
  assert.match(script, /Собираемость по ОСВ \(бухгалтерский прокси\)/,
    'collection UI must distinguish the accounting proxy from linked payments');
  assert.match(script, /Корень 62 и отсутствующие компоненты не заменяют/,
    'collection UI must not imply root 62 substitution');
  console.log('PASS: configuration scopes, loading/error cleanup, RUB scaling, zero/null, Escape dismissal');
  console.log('PASS: Основные показатели uses live screen; Анализ ОСВ preserves legacy dashboard');
})().catch(error => { console.error(error); process.exitCode = 1; });