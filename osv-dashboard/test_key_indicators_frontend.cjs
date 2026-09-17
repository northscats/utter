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
          nodes.set(id, {
            style: {}, textContent: '',
            classList: {
              add: name => classes.add(name), remove: name => classes.delete(name),
              contains: name => classes.has(name),
            },
            setAttribute: (name, value) => attributes.set(name, value),
            getAttribute: name => attributes.get(name),
            addEventListener() {},
            querySelectorAll() { return []; },
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
    stored.set('brain4sales:key-indicators:period-selection', JSON.stringify({
      period: 'month', date: '2026-06-30', dateFrom: '2026-01-31',
    }));
    assert.equal(app.restorePeriodSelection(), true);
    assert.equal(app.state.period, 'month');
    assert.equal(app.state.date, '2026-06-30');
    assert.equal(app.state.dateFrom, '2026-01-31');
    app.savePeriodSelection();
    assert.deepEqual(
      JSON.parse(stored.get('brain4sales:key-indicators:period-selection')),
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
    accounting_proxy: {value: 70},
    components: [
      {account: '62.01', side: 'debit', value: 100000000},
      {account: '62.01', side: 'credit', value: 60000000},
      {account: '62.02', side: 'debit', value: 20000000},
      {account: '62.02', side: 'credit', value: 30000000},
    ],
  });
  app.renderCollectionModal(proxyComponents);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /70,0 млн ₽/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ОбК 62\.01 \+ ОбК 62\.02 − ОбД 62\.02/);
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
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ФАКТИЧЕСКАЯ СОБИРАЕМОСТЬ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /Население/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /ИСТОЧНИКИ НАЧИСЛЕНИЙ/);
  assert.match(nodes.get('ki-collection-modal-content').innerHTML, /1С: Расчёты/);
  const previousData = app.state.data;
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

  app.state.period = 'quarter';
  app.state.data = {period: {
    kind: 'quarter', date: '2026-06-30', date_from: '2026-06-30',
    options: [
      {value: '2024-03-31', label: '1 квартал 2024'},
      {value: '2025-03-31', label: '1 квартал 2025'},
      {value: '2025-06-30', label: '2 квартал 2025'},
      {value: '2026-03-31', label: '1 квартал 2026'},
      {value: '2026-06-30', label: '2 квартал 2026'},
    ],
  }};
  app.renderPeriodSelector();
  const yearSelect = nodes.get('ki-date-select');
  assert.equal(yearSelect.style.display, 'inline-block');
  assert.equal(yearSelect.getAttribute('aria-label'), 'Год');
  assert.match(yearSelect.innerHTML, /value="2025-06-30"[^>]*>2025</);
  assert.match(yearSelect.innerHTML, /value="2024-03-31"[^>]*>2024</);
  assert.match(yearSelect.innerHTML, /value="2026-06-30" selected>2026</);
  assert.match(nodes.get('ki-quarter-buttons').innerHTML, /aria-label="2 квартал 2026"/);
  let fetched = 0;
  app.fetchData = () => { fetched++; };
  app.state.date = '2026-06-30';
  app.state.dateFrom = '2026-03-31';
  app.state.quarterAnchor = '2026-03-31';
  app.onDateChange('2025-06-30');
  assert.equal(app.state.date, '2025-06-30');
  assert.equal(app.state.dateFrom, '2025-06-30');
  assert.equal(app.state.quarterAnchor, null);
  assert.equal(fetched, 1);
  app.state.data.period.date = '2025-06-30';
  app.state.data.period.date_from = '2025-06-30';
  app.renderPeriodSelector();
  assert.match(nodes.get('ki-quarter-buttons').innerHTML, /aria-label="1 квартал 2025"/);
  assert.doesNotMatch(nodes.get('ki-quarter-buttons').innerHTML, /квартал 2026/);
  app.onQuarterChange('2025-03-31');
  app.onQuarterChange('2025-06-30');
  assert.equal(app.state.dateFrom, '2025-03-31');
  assert.equal(app.state.date, '2025-06-30');
  app.state.period = 'month';
  app.renderPeriodSelector();
  assert.equal(yearSelect.getAttribute('aria-label'), 'Конечная дата');
  assert.equal(nodes.get('ki-quarter-buttons').style.display, 'none');
  assert.match(script, /delta < 0 \? 'cash-value-negative'/,
    'negative cash balance changes must use the carrot color class');
  assert.match(script, /<th>Группа<\/th>/,
    'the former Segment table must be labeled Group');
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