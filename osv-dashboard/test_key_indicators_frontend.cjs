// Fast browser-independent checks of bootstrap, loading cleanup and units.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');

async function checkBootstrap(configuration) {
  const nodes = new Map();
  const requests = [];
  const listeners = new Map();
  const context = vm.createContext({
    window: {},
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
  } else {
    assert.equal(requests.length, 0);
    assert.match(nodes.get('ki-error-text').textContent, /адрес источника/);
  }
  const million = app.formatValue(1_000_000, 'RUB');
  assert.equal(Number(million.val.replace(',', '.')), 1);
  assert.equal(million.u, 'млн ₽');
  assert.equal(app.formatValue(0, 'RUB').val, '0');
  assert.equal(app.formatValue(null, 'RUB').val, 'Н/Д');
  const previousData = app.state.data;
  app.state.data = {period: {start: '2026-01-01', end: '2026-06-30', label: '2026'}};
  assert.equal(app.displayedPeriodLabel(), '01.01.2026 — 30.06.2026');
  app.updateModalSubtitle();
  assert.equal(nodes.get('ki-modal-subtitle').textContent,
    'Детализация по данным ОСВ · 01.01.2026 — 30.06.2026');
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