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
  const capexCard = {
    id: 'capex', value: 100_000_000, debit_turnover: 100_000_000,
    credit_turnover: 20_000_000, opening_balance: 10_000_000,
    closing_balance: 90_000_000, previous_year_value: 50_000_000,
    series: [{date: '2026-01-31', label: 'Январь', value: 100_000_000}],
    credit_series: [{date: '2026-01-31', label: 'Январь', value: 20_000_000}],
    previous_year_series: [{
      date: '2026-01-31', source_date: '2025-01-31',
      label: 'Январь', value: 50_000_000,
    }],
    monthly_comparison: [{
      kind: 'month', label: 'Январь', invested: 100_000_000,
      developed: 20_000_000, not_developed: 80_000_000,
      not_developed_total: null, details: [{
        subaccount: '08.04', object: 'Объект А',
        invested: 100_000_000, developed: 20_000_000,
        not_developed: 80_000_000,
        investment_dates: ['2026-01-15'],
        development_dates: ['2026-01-15'],
      }],
    }],
  };
  const monthToggleAttrs = new Map([
    ['aria-controls', 'ki-capex-month-details-0'], ['aria-expanded', 'false']
  ]);
  const monthToggle = {
    getAttribute: key => monthToggleAttrs.get(key),
    setAttribute: (key, value) => monthToggleAttrs.set(key, value),
    addEventListener: (event, callback) => {
      if (event === 'click') monthToggle.click = callback;
    },
  };
  const capexContainer = context.document.getElementById('ki-capex-modal-content');
  const originalCapexQuery = capexContainer.querySelectorAll;
  capexContainer.querySelectorAll = selector => selector === '[data-capex-month-toggle]'
    ? [monthToggle] : originalCapexQuery.call(capexContainer, selector);
  const monthPanel = context.document.getElementById('ki-capex-month-details-0');
  monthPanel.hidden = true;
  app.renderCapexModal(capexCard);
  monthToggle.click();
  assert.equal(monthPanel.hidden, false, 'month details must expand on click');
  assert.equal(monthToggle.getAttribute('aria-expanded'), 'true');
  monthToggle.click();
  assert.equal(monthPanel.hidden, true, 'month details must collapse on a second click');
  const capexHtml = nodes.get('ki-capex-modal-content').innerHTML;
  assert.match(capexHtml, /data-capex-chart-mode="credit"/);
  assert.ok(/Кредитовый оборот[\s\S]*?20,0 млн ₽/.test(capexHtml),
    'credit turnover must show its own value, not the debit turnover');
  assert.ok(/Не освоено всего[\s\S]*?<td[^>]*>—<\/td>/.test(capexHtml),
    'an unverified balance must not appear as N/D or an invented zero');
  const capexPreviousData = app.state.data;
  const capexPreviousModalChart = app.state.modalChart;
  app.state.data = {cards: [capexCard]};
  app.state.modalChart = {capexModes: new Set(['current', 'apg'])};
  const chartCalls = [];
  const renderCapexSvg = app.renderCashLineChart;
  app.renderCashLineChart = (...args) => {
    chartCalls.push(args);
    renderCapexSvg.apply(app, args);
  };
  app.renderCapexChartModes(capexCard);
  assert.deepEqual(Array.from(chartCalls.at(-1)[5].map(group => group.label)),
    ['Формирование вложений', 'Формирование вложений АПГ', 'Кредитовый оборот 08']);
  assert.equal(chartCalls.at(-1)[5][0].series[0].value, 100_000_000);
  assert.equal(chartCalls.at(-1)[5][2].series.length, 0);
  app.toggleCapexChartMode('credit');
  assert.equal(chartCalls.at(-1)[5][2].series[0].value, 20_000_000);
  assert.match(nodes.get('ki-capex-chart-title').textContent, /Кредитовый оборот 08/);
  const svg = nodes.get('ki-capex-line-chart').innerHTML;
  assert.ok(svg.includes('aria-label="Кредитовый оборот 08 на 2026-01-31'),
    'credit turnover should have its own chart point');
  assert.ok(svg.includes('aria-label="Формирование вложений АПГ на 2025-01-31'),
    'previous-year point should name its actual source date');
  app.state.data = capexPreviousData;
  app.state.modalChart = capexPreviousModalChart;
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
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ДИНАМИКА СОБИРАЕМОСТИ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /СОБИРАЕМОСТЬ ЗА ПЕРИОД млн ₽/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /КОМПОНЕНТЫ РАСЧЁТА \(ОСВ\)/);
  const proxyComponents = Object.assign({}, collectionBase, {
    value: 70,
    previous_year_value: 65,
    previous_year_delta: 5,
    accounting_proxy: {value: 70},
    period_table: {
      rows: [{
        month: '2026-01', amount: 185000000, accrual: 200000000, level: 92.5,
        previous_amount: 177000000, previous_accrual: 200000000, previous_level: 88.5,
        delta: 8000000, delta_percent: 4.52,
        coverage: 'complete', previous_coverage: 'complete',
      }],
      total: {
        month: 'total', amount: 185000000, accrual: 200000000, level: 92.5,
        previous_amount: 177000000, previous_accrual: 200000000, previous_level: 88.5,
        delta: 8000000, delta_percent: 4.52,
        coverage: 'complete', previous_coverage: 'complete',
      },
    },
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
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /70\.0%/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ДИНАМИКА СОБИРАЕМОСТИ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /СОБИРАЕМОСТЬ ЗА ПЕРИОД млн ₽/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /Начислено \(Дт 62\.01\)/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /АПГ значение/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /Δ АПГ/);
  assert.doesNotMatch(nodes.get('ki-collection-modal-content').innerHTML, />Дельта АПГ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /Итого/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /\+8/);
  assert.doesNotMatch(
    nodes.get('ki-collection-modal-content').innerHTML,
    /<td>[^<]*млн ₽<\/td>/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /КОМПОНЕНТЫ РАСЧЁТА \(ОСВ\)/);
  const aggregatedProxy = app.collectionProxyAggregate([
    {date: '2026-01-01', debit_6201: 40, credit_6201: 20, debit_6202: 10, credit_6202: 20},
    {date: '2026-01-02', debit_6201: 60, credit_6201: 30, debit_6202: 20, credit_6202: 40},
  ], 'month');
  assert.equal(aggregatedProxy.length, 1);
  assert.equal(aggregatedProxy[0].denominator, 100);
  assert.equal(aggregatedProxy[0].numerator, 80);
  assert.equal(aggregatedProxy[0].value, 80,
    'proxy must be recalculated from aggregated components, not averaged percentages');
  const extremeProxy = app.collectionProxyAggregate([
    {date: '2026-02-01', debit_6201: 100, credit_6201: 120, debit_6202: 0, credit_6202: 0},
    {date: '2026-03-01', debit_6201: 100, credit_6201: -10, debit_6202: 0, credit_6202: 0},
  ], 'day');
  assert.equal(extremeProxy[0].value, 120);
  assert.equal(extremeProxy[1].value, -10);
  app.drawCollectionProxy({
    turnover_series: [
      {date: '2026-01-01', debit_6201: 100, credit_6201: 80,
       debit_6202: 10, credit_6202: 20},
      {date: '2026-01-02', debit_6201: null, credit_6201: null,
       debit_6202: null, credit_6202: null},
      {date: '2026-01-03', debit_6201: 100, credit_6201: 90,
       debit_6202: 10, credit_6202: 20},
    ],
  }, {period: 'day', view: 'result'}, value => String(value),
  value => value == null ? 'Н/Д' : `${value}%`);
  assert.doesNotMatch(
    nodes.get('ki-collection-proxy-chart').innerHTML,
    /collection-proxy-percent-line/,
    'percentage line must not bridge an unavailable period');
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
      id: 'cash_dz', value: 437405275.57, opening_value: 100000000,
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
    app.state.data.cards.push({
      id: 'cash_dz', value: 200000000,
    });
    app.renderApModal({
      id: 'ap', value: 100000000, opening_value: 90000000,
      known_total: 100000000, credit_turnover: 50000000,
      analytics: [], components: [], details: [],
    });
    assert.match(nodes.get('ki-ap-modal-content').innerHTML, />2\.00</);
    let reopenedCardId = null;
    const originalCloseModal = app.closeModal;
    const originalFetchData = app.fetchData;
    const originalOpenModal = app.openModal;
    app.closeModal = () => {};
    app.fetchData = async () => {};
    app.openModal = cardId => { reopenedCardId = cardId; };
    context.fetch = async () => ({
      ok: true,
      json: async () => ({ok: true}),
    });
    await app.saveDebtorMappings();
    assert.equal(reopenedCardId, 'cash_dz',
      'saving debtor categories must reopen the retained debtor card');
    app.closeModal = originalCloseModal;
    app.fetchData = originalFetchData;
    app.openModal = originalOpenModal;
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
  assert.match(script, /class="modal-section-title cash-details-toggle"/,
    'cash account details must have a clickable disclosure control');
  assert.match(script, /aria-expanded="false" aria-controls="ki-cash-details-table"/,
    'cash account details must start collapsed and expose its controlled table');
  assert.match(script, /toggleCashDetails\(button\)/,
    'cash account details disclosure must update the table visibility');
  assert.match(script, /Формирование вложений АПГ/,
    'capex modal must expose a previous-year metric');
  assert.match(script, /class="capex-metrics-strip capex-summary"/,
    'capex metrics must remain separate layout cards');
  assert.match(script, /class="capex-overview ki-modal-dynamics-block" data-layout-key="dynamics"/,
    'capex chart must remain a separate full-width layout item');
  assert.match(script, /data-layout-title="Динамика капитальных вложений"/,
    'capex modal must expose a dedicated chart surface');
  assert.doesNotMatch(script, /cashMetricCard\('Незавершёнка'/,
    'capex summary must not show the WIP metric');
  assert.doesNotMatch(script, /cashMetricCard\('Изменение незавершёнки'/,
    'capex summary must not show the WIP change metric');
  assert.match(script, /id="ki-capex-line-chart" class="cash-line-chart/,
    'capex modal must use the same chart surface styling as cash');
  assert.match(script, /renderCapexChartModes\(card\)/,
    'capex modal must render selected metrics through the cash-style chart adapter');
  assert.match(script, /data-capex-chart-mode=/,
    'capex formation metrics must expose interactive chart controls');
  assert.match(script, /toggleCapexChartMode\(mode\)/,
    'capex chart controls must toggle the selected lines');
  assert.match(script, /card\.debit_turnover == null, '', '', 'current'/,
    'capex chart controls must not also bind the generic series selector');
  assert.match(script, /aria-controls="ki-capex-details-panel"/,
    'capex account details must be collapsible');
  assert.match(script, /<th>ОбД 08 АПГ<\/th>/,
    'capex detail table must compare the current turnover with APG');
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
    year_over_year_components: {
      start: '2025-01-01', end: '2025-06-30', metrics: {
        gross: {current: 510_714_933.29, previous: 231_929_419.40,
          delta: 278_785_513.89, percent: 120.21, available: true},
        vat: {current: 92_094_976.73, previous: 40_000_000,
          delta: 52_094_976.73, percent: 130.24, available: true},
        net: {current: 418_619_956.56, previous: 191_929_419.40,
          delta: 226_690_537.16, percent: 118.11, available: true},
      },
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
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /231,93 млн ₽/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /40,0 млн ₽/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /191,93 млн ₽/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /\+120\.21%/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /\+130\.24%/);
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
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /191,93 млн ₽/);
  switches.find(button => button.dataset.revenueMetric === 'yoy').click();
  app.state.data.revenue_segments.activity_groups = {reconciled: false, rows: []};
  const missingRevenue = {...revenueCard, details: [
    {label: 'Выручка брутто', value: 510_714_933.29},
    {label: 'НДС', value: null, reason: 'Строки 90.03 отсутствуют'},
  ], value: 510_714_933.29, year_over_year: null,
    year_over_year_components: {metrics: {
      gross: {current: 510_714_933.29, previous: 0,
        delta: 510_714_933.29, percent: null, available: true},
      vat: {current: null, previous: null, delta: null,
        percent: null, available: false},
      net: {current: null, previous: null, delta: null,
        percent: null, available: false},
    }}};
  app.renderRevenueModal(missingRevenue);
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /Нужны данные/);
  assert.doesNotMatch(nodes.get('ki-revenue-chart').innerHTML, /Аналогичный период прошлого года<\/strong>/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /Нужны данные/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Выручка без НДС \(база не подтверждена\)<\/td><td><span class="cash-value-missing">Нужны данные/);
  const zeroBaseRevenue = {...missingRevenue, year_over_year: {
    current: 510_714_933.29, previous: 0, delta: 510_714_933.29,
    percent: null, basis: 'gross', start: '2025-01-01', end: '2025-06-30',
  }};
  assert.doesNotThrow(() => app.renderRevenueModal(zeroBaseRevenue));
  assert.match(nodes.get('ki-revenue-chart').innerHTML, /процент не рассчитывается при нулевой базе/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Выручка без НДС \(база не подтверждена\)/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML,
    /Не рассчитывается при нулевой базе/);
  assert.match(nodes.get('ki-revenue-summary-table').innerHTML, /510,71 млн ₽/);
  assert.match(script,
    /activeContainer !== cashContent &&\s*activeContainer !== revenueContent/,
    'the legacy shared dynamics chart must not mount inside the revenue modal');

  const profitCardWithAcc90 = { id: 'profit', gross_profit: 50_000_000 };
  app.state.data = {
    cards: [],
    account90_categories: {
      warnings: ['Нераспределенная выручка'],
      categories: [
        { label: 'Услуга 1', visible: true, revenue: 100, cogs: 40, ohr: 10, gross_profit: 50, profitability: 50.0, revenue_share: 100 }
      ],
      totals: { revenue: 100, cogs: 40, ohr: 10, gross_profit: 50 }
    }
  };
  assert.doesNotThrow(() => app.renderProfitModal(profitCardWithAcc90));
  const profitHtml = nodes.get('ki-profit-modal-content').innerHTML;
  assert.match(profitHtml, /ki-account90-table/, 'must render account90 table');
  assert.match(profitHtml, /Услуга 1/, 'must render account90 rows');
  assert.match(profitHtml, /100,00/, 'must render account90 values already expressed in millions');
  assert.match(profitHtml, /10,00/, 'must render account90 OHR values');
  assert.match(profitHtml, /50,0%/, 'must format profitability');
  assert.match(profitHtml, /obj-bar-fill-pos/, 'must render share bar');
  assert.match(profitHtml, /badge-green/, 'must render status pill');
  assert.match(profitHtml, /ki-account90-chart/, 'must render account90 bar chart');
  assert.match(profitHtml, /Выручка, себестоимость с ОХР и валовая прибыль по объектам/, 'must render chart title');
  assert.match(profitHtml, /aria-label="Услуга 1, Выручка: 100,00 млн ₽"/, 'must use table values in chart bars');
  assert.match(profitHtml, /aria-label="Услуга 1, Себестоимость \+ ОХР: 50,00 млн ₽"/, 'must add COGS and OHR in chart');
  assert.doesNotMatch(profitHtml, /Обратите внимание/, 'must not render account90 warnings');
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