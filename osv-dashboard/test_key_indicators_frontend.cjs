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
    URLSearchParams,
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
      const section = new URL(url, 'http://localhost').searchParams.get('section');
      return {
        ok: true, status: 200,
        text: async () => JSON.stringify({
          ...(section === 'cards' ? {cards: []} : {}),
          ...(section === 'meta' ? {period: {kind: 'year', date: '2026-06-30'}} : {}),
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
    assert.equal(requests.length, 4);
    assert.match(requests[0], /[?&]section=cards/);
    assert.match(requests[1], /[?&]section=meta/);
    assert.match(requests[2], /[?&]section=tables/);
    assert.match(requests[3], /[?&]section=charts/);
    assert.equal(renders, 4, 'each completed section must render progressively');
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
    chart_monthly_series: {
      debit: [
        {date: '2026-01-31', label: 'Январь', value: 10_000_000},
        {date: '2026-02-28', label: 'Февраль', value: 30_000_000},
        {date: '2026-03-31', label: 'Март', value: 50_000_000},
      ],
      credit: [
        {date: '2026-01-31', label: 'Январь', value: 2_000_000},
        {date: '2026-02-28', label: 'Февраль', value: 8_000_000},
        {date: '2026-03-31', label: 'Март', value: 10_000_000},
      ],
      apg: [
        {date: '2026-01-31', source_date: '2025-01-31',
          label: 'Январь', value: null},
        {date: '2026-02-28', source_date: '2025-02-28',
          label: 'Февраль', value: 20_000_000},
        {date: '2026-03-31', source_date: '2025-03-31',
          label: 'Март', value: 30_000_000},
      ],
    },
    chart_monthly_reconciliation: {
      debit: {difference: 10_000_000, missing_months: []},
      credit: {difference: 0, missing_months: []},
      apg: {difference: null, missing_months: ['Январь']},
    },
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
  app.state.modalChart = {};
  const chartCalls = [];
  const renderCapexSvg = app.renderCashLineChart;
  app.renderCashLineChart = (...args) => {
    chartCalls.push(args);
    renderCapexSvg.apply(app, args);
  };
  app.renderCapexChartModes(capexCard);
  assert.deepEqual(Array.from(chartCalls.at(-1)[5].map(group => group.label)),
    ['Формирование вложений', 'Формирование вложений АПГ', 'Кредитовый оборот 08']);
  assert.deepEqual(Array.from(chartCalls.at(-1)[5][0].series.map(point => point.value)),
    [10_000_000, 30_000_000, 50_000_000]);
  assert.equal(chartCalls.at(-1)[5][2].series.length, 3,
    'all three series must be visible by default');
  assert.match(nodes.get('ki-capex-chart-note').textContent, /10\s?000\s?000,00 ₽/);
  assert.match(nodes.get('ki-capex-chart-note').textContent, /Пропуски месячных данных/);
  assert.match(nodes.get('ki-capex-line-chart').innerHTML,
    /<path d="M [^"]* C [^"]*" class="cash-chart-balance"/);
  app.toggleCapexChartMode('credit');
  assert.equal(chartCalls.at(-1)[5][2].series.length, 0,
    'credit line must be hidden when toggled off');
  app.toggleCapexChartMode('credit');
  assert.equal(chartCalls.at(-1)[5][2].series[0].value, 2_000_000);
  assert.match(chartCalls.at(-1)[5][2].dash, /stroke-dasharray/);
  assert.match(nodes.get('ki-capex-chart-title').textContent, /Кредитовый оборот 08/);
  const svg = nodes.get('ki-capex-line-chart').innerHTML;
  assert.ok(svg.includes('aria-label="Кредитовый оборот 08 на 2026-01-31'),
    'credit turnover should have its own chart point');
  assert.ok(svg.includes('aria-label="Формирование вложений АПГ на 2025-02-28'),
    'previous-year point should name its actual source date');
  assert.ok(!svg.includes('Формирование вложений АПГ на 2025-01-31'),
    'missing prior-year month must not be plotted as zero');
  app.state.data = capexPreviousData;
  app.state.modalChart = capexPreviousModalChart;
  const cogsCard = {monthly_comparison: [
    {date: '2026-01-31', current_cost: 10_000_000, previous_year_cost: 8_000_000,
      current_revenue: 20_000_000, previous_year_revenue: 16_000_000},
    {date: '2026-02-28', current_cost: 20_000_000, previous_year_cost: 12_000_000,
      current_revenue: 30_000_000, previous_year_revenue: 24_000_000},
    {date: '2026-03-31', current_cost: 30_000_000, previous_year_cost: 15_000_000,
      current_revenue: 40_000_000, previous_year_revenue: 30_000_000},
  ]};
  app.state.modalChart = {cogsGroup: 'cost',
    cogsModes: new Set(['cogs-current', 'cogs-apg'])};
  app.renderCogsDynamics(cogsCard);
  let cogsSvg = nodes.get('ki-cogs-dynamics-chart').innerHTML;
  assert.match(cogsSvg, /d="M [^"]* C [^"]*" class="cash-chart-balance"/);
  assert.match(cogsSvg, /class="cash-chart-balance-apg"[^>]*stroke-dasharray="6 4"/);
  assert.match(cogsSvg, /stroke-width="2"/);
  assert.doesNotMatch(cogsSvg, /<circle[^>]*fill="#(?:111827|DB2777)"/);
  assert.equal((cogsSvg.match(/class="cash-chart-hit-target"/g) || []).length, 6);
  cogsCard.monthly_comparison[1].current_cost = null;
  app.renderCogsDynamics(cogsCard);
  cogsSvg = nodes.get('ki-cogs-dynamics-chart').innerHTML;
  const currentCogsPath = cogsSvg.match(/<path d="([^"]*)" class="cash-chart-balance"/)[1];
  assert.equal((currentCogsPath.match(/M /g) || []).length, 2,
    'missing month must break the cost line rather than become zero');
  assert.ok(!currentCogsPath.includes(' C '));
  app.state.modalChart = {cogsGroup: 'ratio',
    cogsModes: new Set(['cogs-ratio-current', 'cogs-ratio-apg'])};
  app.renderCogsDynamics(cogsCard);
  assert.match(nodes.get('ki-cogs-dynamics-chart').innerHTML,
    /class="cash-chart-balance-apg"[^>]*stroke-dasharray="6 4"/);
  assert.match(nodes.get('ki-cogs-dynamics-chart').innerHTML, /50\.0%/);
  app.state.modalChart = capexPreviousModalChart;
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.tableSortValue({dataset: {}, textContent: '1 234,50 ₽'}))),
    {type: 'number', value: 1234.5});
  const monthRows = ['Мар 2026', 'Янв 2026', 'Дек 2025', 'Фев 2026', 'Итого']
    .map((text, index) => ({
      index,
      classList: {contains: () => false},
      cells: [{dataset: {}, textContent: text}]
    }));
  const monthBody = {rows: monthRows, appendChild(row) {
    this.rows.splice(this.rows.indexOf(row), 1);
    this.rows.push(row);
  }};
  const monthTable = {tBodies: [monthBody]};
  app.sortTable(monthTable, 0, 'asc');
  assert.deepEqual(monthBody.rows.map(row => row.index), [2, 1, 3, 0, 4]);
  app.sortTable(monthTable, 0, 'desc');
  assert.deepEqual(monthBody.rows.map(row => row.index), [0, 3, 1, 2, 4]);
  assert.ok(app.tableSortValue({dataset: {}, textContent: 'декабрь 2025'}).value <
    app.tableSortValue({dataset: {}, textContent: 'январь 2026'}).value);
  assert.ok(app.tableSortValue({dataset: {sortValue: '2026-03-31'}}).value >
    app.tableSortValue({dataset: {sortValue: '2026-02-28'}}).value);
  const capexMonth = {classList: {contains: name => name === 'capex-month-row'},
    cells: [{dataset: {sortValue: '2026-02-28'}, textContent: 'Фев 2026'}]};
  const capexDetails = {classList: {contains: name => name === 'capex-month-details-row'},
    cells: [{dataset: {}, textContent: 'Детали'}]};
  const capexJanuary = {classList: {contains: name => name === 'capex-month-row'},
    cells: [{dataset: {sortValue: '2026-01-31'}, textContent: 'Янв 2026'}]};
  const capexBody = {rows: [capexMonth, capexDetails, capexJanuary],
    appendChild: monthBody.appendChild};
  app.sortTable({tBodies: [capexBody]}, 0, 'asc');
  assert.deepEqual(capexBody.rows, [capexJanuary, capexMonth, capexDetails]);
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
  assert.match(nodes.get('ki-collection-modal-content').innerHTML,
    /data-collection-chart-mode="collection-accrual"/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML,
    /data-collection-chart-mode="collection-paid"/);
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
  const priorCollectionData = app.state.data;
  const priorCollectionChart = app.state.modalChart;
  const priorCollectionTimeline = app.state.collectionTimelineMode;
  const priorQuerySelectorAll = context.document.querySelectorAll;
  const tileModes = ['collection-level', 'collection-apg',
    'collection-accrual', 'collection-paid'];
  const chartTiles = tileModes.map(mode => {
    const classes = new Set();
    const attributes = new Map();
    return {dataset: {collectionChartMode: mode},
      classList: {toggle: (name, active) =>
        active ? classes.add(name) : classes.delete(name),
        contains: name => classes.has(name)},
      setAttribute: (name, value) => attributes.set(name, value),
      getAttribute: name => attributes.get(name)};
  });
  context.document.querySelectorAll = selector =>
    selector === '[data-collection-chart-mode]' ? chartTiles :
      priorQuerySelectorAll(selector);
  app.state.data = {cards: [proxyComponents]};
  app.state.modalChart = {collectionGroup: 'ratio',
    collectionModes: new Set(['collection-level', 'collection-apg'])};
  app.state.collectionTimelineMode = 'day';
  const daySeries = [
    {date: '2026-01-01', label: '01.01', value: 92,
      debit_6201: 10_000_000, credit_6201: 8_000_000,
      credit_6202: 1_500_000, debit_6202: 300_000},
    {date: '2026-01-02', label: '02.01', value: 96,
      debit_6201: 12_000_000, credit_6201: 11_000_000,
      credit_6202: 1_000_000, debit_6202: 400_000},
  ];
  const apgSeries = daySeries.map((point, index) => ({
    ...point, source_date: `2025-01-0${index + 1}`, value: index ? 95 : 90,
  }));
  app.renderCollectionDynamics(daySeries, apgSeries);
  let collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.match(collectionSvg, /class="collection-threshold"[^>]*stroke|class="collection-threshold"/);
  assert.match(collectionSvg, />93%<\/text>/);
  assert.match(collectionSvg, /clip-path="url\(#collection-above-93\)"/);
  assert.match(collectionSvg, /clip-path="url\(#collection-below-93\)"/);
  assert.match(collectionSvg, /collection-level"[^>]*stroke="#16A34A"[^>]*stroke-width="2"/);
  assert.match(collectionSvg, /collection-apg"[^>]*stroke="#DC2626"[^>]*stroke-dasharray="6 4"/);
  assert.match(collectionSvg, /d="M [^"]* C [^"]*" class="collection-dynamics-line/);
  assert.doesNotMatch(collectionSvg, /collection-zone-(?:red|yellow|green)/);
  app.renderCollectionDynamics([
    {date: '2026-01-01', value: 92},
    {date: '2026-01-02', value: null},
    {date: '2026-01-03', value: 96},
  ], []);
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.doesNotMatch(collectionSvg, /d="M [^"]* C [^"]*" class="collection-dynamics-line collection-level"/,
    'missing percentage must break the line');
  assert.equal((collectionSvg.match(/class="collection-isolated-point collection-level"/g) || []).length, 2,
    'standalone daily values must remain visible when the line is broken');
  app.renderCollectionDynamics([
    {date: '2026-01-01', label: '01.01', value: 92},
    {date: '2026-01-03', label: '03.01', value: 96},
  ], [
    {date: '2026-01-01', source_date: '2025-01-01', value: 90},
    {date: '2026-01-03', source_date: '2025-01-03', value: 95},
  ]);
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.doesNotMatch(collectionSvg, /d="M [^"]* C [^"]*" class="collection-dynamics-line collection-apg"/,
    'APG must not connect two days when the day between them is absent');
  assert.equal((collectionSvg.match(/class="collection-isolated-point collection-apg"/g) || []).length, 2);
  assert.match(collectionSvg, /class="collection-isolated-point collection-level"[^>]+fill="#DC2626"/);
  assert.match(collectionSvg, /class="collection-isolated-point collection-level"[^>]+fill="#16A34A"/);
  app.renderCollectionDynamics([
    {date: '2026-01-01', label: '01.01', value: 92},
    {date: '2026-01-02', label: '02.01', value: null},
    {date: '2026-01-03', label: '03.01', value: 96},
    {date: '2026-01-04', label: '04.01', value: 94},
  ], [
    {date: '2026-01-01', source_date: '2025-01-01', value: 90},
    {date: '2026-01-03', source_date: '2025-01-03', value: 95},
  ]);
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  const currentLastX = collectionSvg.match(/class="collection-dynamics-point cash-chart-hit-target"[^>]+data-detail="03\.01[^"]*"[^>]+cx="([^"]+)"/)?.[1];
  const apgLastX = collectionSvg.match(/class="collection-dynamics-apg-point cash-chart-hit-target"[^>]+data-detail="2025-01-03[^"]*"[^>]+cx="([^"]+)"/)?.[1];
  assert.ok(currentLastX && apgLastX);
  assert.equal(currentLastX, apgLastX,
    'current and shorter APG series must share an x coordinate for the same day');
  app.renderCollectionDynamics([daySeries[0]], []);
  assert.match(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /class="collection-isolated-point collection-level"/,
    'a single daily value must not disappear as an invisible SVG move');
  app.state.modalChart.collectionModes = new Set(['collection-apg']);
  app.renderCollectionDynamics([daySeries[0]], [
    {date: '2026-01-01', source_date: '2025-01-01', value: 95},
  ]);
  assert.match(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /class="collection-isolated-point collection-apg"/,
    'APG-only daily mode must show its single confirmed point');
  app.state.modalChart.collectionModes = new Set(['collection-level', 'collection-apg']);
  const cumulativeProxy = [
    {date: '2026-01-01', label: '01.01', value: 100,
      debit_6201: 100, credit_6201: 100, debit_6202: 0, credit_6202: 0},
    {date: '2026-01-02', label: '02.01', value: null,
      debit_6201: 0, credit_6201: 20, debit_6202: 0, credit_6202: 0},
    {date: '2026-01-03', label: '03.01', value: 50,
      debit_6201: 100, credit_6201: 50, debit_6202: 0, credit_6202: 0},
  ];
  app.renderCollectionDynamics(cumulativeProxy, cumulativeProxy.map(point => ({
    ...point, source_date: point.date.replace('2026', '2025'),
  })));
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.match(collectionSvg, /Собираемость: 120\.0%/,
    'zero-accrual day must display the accumulated, not undefined, percentage');
  assert.match(collectionSvg, /Собираемость: 85\.0%/,
    'later daily level must use total payments divided by total accruals');
  assert.match(collectionSvg, /АПГ: 120\.0%/);
  assert.match(collectionSvg, /d="M [^"]* C [^"]* C [^"]*" class="collection-dynamics-line collection-level"/,
    'accumulated daily percentages must form a readable curve');
  assert.match(collectionSvg, /накопительно с начала выбранного периода/);
  app.renderCollectionDynamics([
    cumulativeProxy[0],
    {date: '2026-01-02', value: null},
    cumulativeProxy[2],
  ], []);
  assert.doesNotMatch(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /Собираемость: 85\.0%/,
    'missing component must not be treated as zero in accumulated level');
  app.toggleCollectionMetric('collection-apg');
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.deepEqual(Array.from(app.state.modalChart.collectionModes), ['collection-apg']);
  assert.equal(chartTiles[1].getAttribute('aria-pressed'), 'true');
  assert.equal(chartTiles[0].getAttribute('aria-pressed'), 'false');
  assert.match(collectionSvg, /class="collection-dynamics-line collection-apg"/);
  assert.doesNotMatch(collectionSvg, /class="collection-dynamics-line collection-level"/,
    'clicking APG must show it, not turn it off');
  app.toggleCollectionMetric('collection-apg');
  assert.deepEqual(Array.from(app.state.modalChart.collectionModes),
    ['collection-level', 'collection-apg']);
  app.toggleCollectionMetric('collection-accrual');
  collectionSvg = nodes.get('ki-collection-dynamics-chart').innerHTML;
  assert.equal(app.state.modalChart.collectionGroup, 'money');
  assert.deepEqual(Array.from(app.state.modalChart.collectionModes),
    ['collection-accrual', 'collection-paid']);
  assert.equal(chartTiles[2].getAttribute('aria-pressed'), 'true');
  assert.equal(chartTiles[3].classList.contains('cash-chart-mode-selected'), true);
  assert.equal(chartTiles[0].getAttribute('aria-pressed'), 'false');
  assert.doesNotMatch(collectionSvg, /collection-threshold|collection-above-93|93%/);
  assert.match(collectionSvg, /collection-accrual"[^>]*stroke="#111827"/);
  assert.match(collectionSvg, /collection-paid"[^>]*stroke="#DB2777"/);
  app.toggleCollectionMetric('collection-paid');
  assert.doesNotMatch(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /class="collection-dynamics-line collection-accrual"/);
  assert.match(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /class="collection-dynamics-line collection-paid"/);
  assert.equal(chartTiles[3].getAttribute('aria-pressed'), 'true');
  assert.equal(chartTiles[2].classList.contains('cash-chart-mode-selected'), false);
  app.toggleCollectionMetric('collection-level');
  assert.equal(app.state.modalChart.collectionGroup, 'ratio');
  assert.deepEqual(Array.from(app.state.modalChart.collectionModes),
    ['collection-level', 'collection-apg']);
  assert.equal(chartTiles[0].classList.contains('cash-chart-mode-selected'), true);
  app.state.modalChart = {collectionGroup: 'money',
    collectionModes: new Set(['collection-accrual', 'collection-paid'])};
  app.state.collectionTimelineMode = 'month';
  const cohort = [
    {date: '2026-01-15', label: '15.01', value: 0,
      accrual: 100_000_000, payment: 0},
  ];
  const monthlyCohort = [
    {date: '2026-01-31', label: 'янв 2026', value: 95,
      accrual: 100_000_000, payment: 95_000_000},
  ];
  app.renderCollectionDynamics(cohort, [], monthlyCohort);
  assert.match(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /Оплачено: 95\.00 млн ₽/,
    'month chart must use cohort payments through month end, not accrued-day payments');
  app.state.collectionTimelineMode = 'year';
  app.renderCollectionDynamics(cohort, [], [
    {...monthlyCohort[0], payment: 0, value: 0},
  ], [], [
    {date: '2026-12-31', label: '2026', value: 95,
      accrual: 100_000_000, payment: 95_000_000},
  ]);
  assert.match(nodes.get('ki-collection-dynamics-chart').innerHTML,
    /Оплачено: 95\.00 млн ₽/,
    'year must use payments through year cutoff rather than sum early monthly cutoffs');
  context.document.querySelectorAll = priorQuerySelectorAll;
  app.state.data = priorCollectionData;
  app.state.modalChart = priorCollectionChart;
  app.state.collectionTimelineMode = priorCollectionTimeline;
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
    const originalPeriod = app.state.data.period;
    app.state.data.period = {
      start: '2026-01-01', end: '2026-01-31',
      label: '01.01.2026 — 31.01.2026',
    };
    const apCard = {
      id: 'ap', value: 100000000, opening_value: 90000000,
      known_total: 100000000, credit_turnover: 50000000,
      series: [
        {date: '2026-01-01', label: '01.01', value: 90000000},
        {date: '2026-01-31', label: '31.01', value: 100000000},
      ],
      previous_year_series: [
        {date: '2026-01-01', source_date: '2025-01-01',
          label: '01.01', value: 70000000},
        {date: '2026-01-31', source_date: '2025-01-31',
          label: '31.01', value: 80000000},
      ],
      monthly_comparison: [
        {date: '2026-01-31', previous_year_date: '2025-01-31',
          label: 'янв 26', value: 100000000, change: 10000000,
          previous_year_value: 80000000, previous_year_delta: 20000000,
          previous_year_delta_percent: 25},
        {date: '2026-02-28', previous_year_date: '2025-02-28',
          label: 'фев 26', value: null, change: null,
          previous_year_value: null, previous_year_delta: null,
          previous_year_delta_percent: null},
      ],
      analytics: [], components: [], details: [],
    };
    app.state.data.cards.push(apCard);
    app.renderApModal(apCard);
    const apHtml = nodes.get('ki-ap-modal-content').innerHTML;
    assert.match(apHtml, />2\.00</);
    assert.match(apHtml, /data-ap-chart-mode="ap-current"/);
    assert.match(apHtml, /data-ap-chart-mode="ap-apg"/);
    assert.match(apHtml, /Изменение за период: \+10,\d+ млн ₽/);
    assert.doesNotMatch(apHtml, /cash-summary-label">Изменение КЗ/);
    assert.match(apHtml, /КЗ поставщикам АПГ/);
    assert.match(apHtml, /КЗ ПОСТАВЩИКАМ ПО МЕСЯЦАМ И АПГ/);
    assert.match(apHtml, /class="cash-table-scroll"/);
    assert.match(apHtml, /class="cash-table ap-monthly-table"/);
    assert.doesNotMatch(apHtml, /class="collection-detail-table ap-monthly-table"/);
    assert.match(apHtml, /на 31\.01\.2026/);
    assert.match(apHtml, /на 31\.01\.2025/);
    assert.match(apHtml, /\+20,\d+ <span class="cash-number-unit">млн ₽<\/span>/);
    assert.match(apHtml, /\+25\.0%/);
    assert.match(apHtml, /Н\/Д означает отсутствие подтверждённого сальдо/);
    assert.match(apHtml, /на 28\.02\.2025/);
    assert.doesNotMatch(apHtml, /на 28\.02\.2025.*>0\.00 млн ₽/);
    let apChart = nodes.get('ki-ap-line-chart').innerHTML;
    assert.match(apChart, /class="cash-chart-balance"/);
    assert.match(apChart, /class="cash-chart-balance-apg"/);
    assert.match(apChart, /КЗ поставщикам АПГ на 2025-01-31/);
    app.toggleApChartMode('ap-current');
    assert.deepEqual(Array.from(app.state.modalChart.apModes), ['ap-apg']);
    apChart = nodes.get('ki-ap-line-chart').innerHTML;
    assert.doesNotMatch(apChart, /class="cash-chart-balance"/);
    assert.match(apChart, /class="cash-chart-balance-apg"/);
    app.toggleApChartMode('ap-apg');
    assert.deepEqual(Array.from(app.state.modalChart.apModes), ['ap-apg']);
    app.toggleApChartMode('ap-current');
    assert.deepEqual(Array.from(app.state.modalChart.apModes),
      ['ap-apg', 'ap-current']);
    apChart = nodes.get('ki-ap-line-chart').innerHTML;
    assert.match(apChart, /class="cash-chart-balance"/);
    assert.match(apChart, /class="cash-chart-balance-apg"/);
    app.toggleApChartMode('ap-apg');
    assert.deepEqual(Array.from(app.state.modalChart.apModes), ['ap-current']);
    app.toggleApChartMode('ap-apg');
    assert.deepEqual(Array.from(app.state.modalChart.apModes),
      ['ap-current', 'ap-apg']);
    app.renderCashModal({
      id: 'cash', unit: 'RUB', value: 100000000,
      date: '2026-01-31', breakdown: [],
      series: [{date: '2026-01-31', label: '31.01',
        value: 100000000, debit_value: 120000000,
        credit_value: 20000000}],
      previous_year_series: [{date: '2025-01-31', label: '31.01',
        value: 80000000, debit_value: 90000000,
        credit_value: 10000000}],
      monthly_comparison: [
        {month: '2026-01', date: '2026-01-31',
          previous_year_date: '2025-01-31', value: 100000000,
          previous_year_value: 80000000, delta: 20000000,
          debit_turnover: 12000000, credit_turnover: 2000000,
          previous_year_debit_turnover: 8000000,
          previous_year_credit_turnover: 1000000},
        {month: '2026-02', date: '2026-02-01',
          previous_year_date: '2025-02-01', value: null,
          previous_year_value: null, delta: null,
          debit_turnover: null, credit_turnover: null,
          previous_year_debit_turnover: null,
          previous_year_credit_turnover: null},
      ],
    });
    const cashHtml = nodes.get('ki-cash-modal-content').innerHTML;
    assert.match(cashHtml, /ДЕНЕЖНЫЕ СРЕДСТВА ЗА ПЕРИОД млн ₽/);
    assert.match(cashHtml,
      /class="collection-detail-table collection-period-table collection-audit-table cash-monthly-table"/);
    assert.match(cashHtml, /январь 2026/);
    assert.match(cashHtml, /февраль 2026/);
    assert.match(cashHtml, /<td>100(?:,0)?<\/td>\s*<td>80(?:,0)?<\/td>\s*<td>\+20(?:,0)?<\/td>/);
    assert.match(cashHtml, /<td>12(?:,0)?<\/td>\s*<td>2(?:,0)?<\/td>/);
    assert.match(cashHtml, /<td>Н\/Д<\/td>\s*<td>Н\/Д<\/td>/);
    app.state.data.period = originalPeriod;
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
  assert.equal(nodes.get('ki-date-from-select').value, '2026-01-01');
  assert.equal(nodes.get('ki-date-select').value, '2026-06-30');
  let fetched = 0;
  app.fetchData = () => { fetched++; };
  app.onPeriodPickerButton();
  assert.equal(fetched, 0, 'opening the picker must not load data');
  assert.equal(nodes.get('ki-period-picker-button').textContent, 'Загрузить период');
  assert.match(nodes.get('ki-period-years').innerHTML, />Мар</);
  app.selectPeriodMonth('2025-03-01');
  assert.equal(nodes.get('ki-date-from-select').value, '2025-03-01');
  assert.equal(nodes.get('ki-date-select').value, '');
  assert.equal(fetched, 0, 'selecting the first month must not load data');
  app.selectPeriodMonth('2026-06-01');
  assert.equal(nodes.get('ki-date-select').value, '2026-06-30');
  assert.equal(fetched, 0, 'selecting the second month must not load data');
  app.shiftPeriodDay('from', 1);
  app.shiftPeriodDay('to', -2);
  assert.equal(nodes.get('ki-date-from-select').value, '2025-03-02');
  assert.equal(nodes.get('ki-date-select').value, '2026-06-28');
  app.shiftPeriodDay('from', -20);
  assert.equal(nodes.get('ki-date-from-select').value, '2025-03-01',
    'scrolling must not leave the selected start month');
  app.cancelPeriodPicker();
  assert.equal(nodes.get('ki-period-picker-button').textContent, 'Выбрать период');
  assert.equal(nodes.get('ki-date-from-select').value, '2026-01-01');
  assert.equal(nodes.get('ki-date-select').value, '2026-06-30');
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
  assert.equal(nodes.get('ki-date-select').value, '2026-09-15',
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