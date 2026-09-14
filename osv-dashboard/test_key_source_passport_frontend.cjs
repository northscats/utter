// Isolated renderer checks for the read-only source passport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(
  `${__dirname}/static/key_source_passport.js`, 'utf8'
);
const nodes = new Map();
let toggleHandler = null;

const makeNode = id => {
  if (nodes.has(id)) return nodes.get(id);
  const classes = new Set();
  const node = {
    id,
    style: {},
    hidden: false,
    innerHTML: '',
    textContent: '',
    dataset: {},
    classList: {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
      contains: name => classes.has(name)
    },
    setAttribute: (name, value) => { node[name] = value; },
    addEventListener: (name, callback) => {
      if (id === 'ki-source-passport-toggle' && name === 'click') {
        toggleHandler = callback;
      }
    },
    querySelector: selector => {
      if (selector === 'h2') {
        return makeNode('ki-source-passport-heading');
      }
      if (selector === 'span') {
        return makeNode('ki-source-passport-toggle-label');
      }
      return null;
    }
  };
  nodes.set(id, node);
  return node;
};

const context = vm.createContext({
  window: {
    location: {search: '', origin: 'http://localhost'},
  },
  document: {
    readyState: 'complete',
    addEventListener() {},
    getElementById: makeNode
  },
  console
});
vm.runInContext(script, context);

const passport = context.window.KI_SourcePassport;
assert.ok(passport, 'passport renderer should be exported');
passport.renderOperational({
  source_passport: {
    uploaded_at: '2026-07-03T10:11:12Z',
    actual_period: {start: '2026-06-01', end: '2026-06-30'},
    actual_accounts: [
      {account: '51', name: 'Расчётный счёт'},
      {account: '90'}
    ],
    metadata_accounts: ['51', '62', '90'],
    exact_balance_dates: ['2026-06-30', '2026-06-29'],
    turnover_gaps: [{start: '2026-06-12', end: '2026-06-14', reason: 'Нет оборотов'}],
    duplicates: [{date: '2026-06-20', count: 2}],
    discrepancies: ['Контрольное равенство <не сходится>'],
    sources: [{
      filename: 'ОСВ <июнь>.xlsx',
      uploaded_at: '2026-07-03T10:11:12Z',
      actual_period_end: '2026-06-30',
      version: '1',
      status: 'Принята'
    }]
  }
});

let html = nodes.get('ki-source-passport-body').innerHTML;
assert.match(html, /Загружен в систему/);
assert.match(html, /Фактический конец периода/);
assert.match(html, /03\.07\.2026 10:11:12/);
assert.match(html, /30\.06\.2026/);
assert.match(html, /51 — Расчётный счёт/);
assert.match(html, /Счета из метаданных/);
assert.match(html, /12\.06\.2026 — 14\.06\.2026/);
assert.match(html, /строк: 2/);
assert.match(html, /Контрольное равенство &lt;не сходится&gt;/);
assert.match(html, /ОСВ &lt;июнь&gt;\.xlsx/);
assert.ok(!html.includes('<июнь>'), 'untrusted source text must be escaped');

assert.equal(nodes.get('ki-source-passport-body').hidden, true);
assert.equal(nodes.get('ki-source-passport-toggle')['aria-expanded'], 'false');
toggleHandler();
assert.equal(nodes.get('ki-source-passport-body').hidden, false);
assert.equal(nodes.get('ki-source-passport-toggle')['aria-expanded'], 'true');

passport.renderOperational({
  source_passport: {
    latest_upload_at: '2026-07-03T10:11:12Z',
    actual_period: {start: '2026-06-01', end: '2026-06-30'},
    available_accounts: ['51'],
    uploads: [{accounts_loaded: ['51', '62']}],
    accounts: {
      '51': {
        exact_balances: {
          opening: {
            date: '2026-06-01',
            debit: 0,
            credit: null,
            kind: 'report_opening',
            source_file: 'accepted.xlsx'
          },
          closing: {
            date: '2026-06-30',
            debit: null,
            credit: 1250,
            kind: 'report_closing',
            source_file: 'accepted.xlsx'
          },
          all: [{date: '2026-06-30', kind: 'report_closing'}]
        },
        turnover_coverage: {
          status: 'partial',
          gaps: [{start: '2026-06-12', end: '2026-06-14', days: 3}]
        }
      }
    },
    files: [{
      source_file: 'accepted.xlsx',
      period_end: '2026-06-30',
      source_updated_at: '2026-07-03T11:00:00Z',
      status: 'complete'
    }],
    metadata_discrepancies: [{message: 'Счёт 62 не найден'}],
    cards: [{id: 'cash', status: 'unavailable', reason: 'Нет сальдо'}]
  }
});
html = nodes.get('ki-source-passport-body').innerHTML;
assert.match(html, /51: 30\.06\.2026/);
assert.match(html, /Точные остатки на границах периода/);
assert.match(html, />0<.*Недоступно/s);
assert.match(html, /1\s?250/);
assert.match(html, /Неполное покрытие/);
assert.match(html, /Изменён файл \(mtime\)/);
assert.match(html, /accepted\.xlsx<\/td>\s*<td>Не указано/);
assert.match(html, /62/);
assert.match(html, /Нет сальдо/);
assert.match(html, /Счёт 62 не найден/);

passport.renderAnnual({
  year_from: 2024,
  year_to: 2025,
  cards: [
    {id: 'revenue', title: 'Выручка', value: 100},
    {id: 'cash', title: 'Деньги', value: null, missing_reason: 'Нет листа <Cash>'}
  ],
  source_passport: {
    status: 'accepted',
    selected_range: {
      year_from: 2024,
      year_to: 2025,
      period_label: '2024–2025',
      requested_year: null
    },
    accepted_years: [2024, 2025],
    accepted_versions: [{
      year: 2025,
      filename: 'annual.xlsx',
      version: '2',
      import_id: 'import-2',
      file_hash: 'hash-2',
      uploaded_at: '2026-02-03T09:00:00Z',
      confirmed_at: '2026-02-09T12:00:00Z',
      activated_at: '2026-02-10T12:00:00Z',
      factual_period_end: '2025-12-31'
    }],
    inactive_version_history: [{
      year: 2025, filename: 'annual-old.xlsx', version: '1',
      uploaded_at: '2026-01-03T09:00:00Z'
    }],
    duplicate_uploads: [{message: 'Повторный импорт'}],
    active_conflicts: [{message: 'Две активные версии'}],
    card_reason_summary: [{
      id: 'cash', title: 'Деньги', available: false,
      missing_reason: 'Нет листа <Cash>'
    }],
    warnings: ['Пропущен 2023 год']
  }
});
html = nodes.get('ki-source-passport-body').innerHTML;
assert.match(html, /Принятые годовые версии/);
assert.match(html, /03\.02\.2026 09:00:00/);
assert.match(html, /31\.12\.2025/);
assert.match(html, /10\.02\.2026 12:00:00/);
assert.match(html, /import-2/);
assert.match(html, /hash-2/);
assert.match(html, /История неактивных версий/);
assert.match(html, /Повторный импорт/);
assert.match(html, /Две активные версии/);
assert.match(html, /Пропущен 2023 год/);
assert.match(html, /Нет листа &lt;Cash&gt;/);
assert.ok(!html.includes('<Cash>'), 'annual missing reason must be escaped');

// Exact API contract: selected dates must never become factual source dates.
passport.renderOperational({source_passport: {
  actual_period: {start: '2026-01-01', end: '2026-01-31'},
  period: {start: '2026-02-01', end: '2026-02-28'},
  status: 'complete', period_status: 'partial',
  accounts: {}, available_accounts: [], cards: []
}});
html = nodes.get('ki-source-passport-body').innerHTML;
assert.match(html, /Фактическое начало периода<\/dt><dd>01\.01\.2026/);
assert.match(html, /Фактический конец периода<\/dt><dd>31\.01\.2026/);
assert.match(html, /Выбранный период<\/dt><dd>01\.02\.2026 — 28\.02\.2026/);
assert.match(html, /Покрытие выбранного периода<\/dt><dd>Неполное покрытие/);
passport.renderOperational({source_passport: {
  actual_period: {start: '2026-01-01', end: '2026-01-31'},
  period: {start: '2026-01-01', end: '2026-01-31'},
  status: 'partial', period_status: 'complete',
  metadata_discrepancies: [{message: 'Связь файла с загрузкой не подтверждена'}],
  accounts: {}, available_accounts: [], cards: []
}});
html = nodes.get('ki-source-passport-body').innerHTML;
assert.match(html, /Покрытие выбранного периода<\/dt><dd>Полное покрытие/);
assert.match(html, /Есть замечания к источникам и метаданным/);
assert.ok(!html.includes('Неполное покрытие'));

passport.clear('Network <failure>');
html = nodes.get('ki-source-passport-body').innerHTML;
assert.ok(!html.includes('annual.xlsx'), 'clear must remove stale passport data');
assert.match(html, /Network &lt;failure&gt;/);

console.log('PASS: source passport operational/annual rendering, dates, gaps, reasons and XSS escaping');