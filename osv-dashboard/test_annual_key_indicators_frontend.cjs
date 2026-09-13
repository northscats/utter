const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const appScript = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');
const annualScript = fs.readFileSync(`${__dirname}/static/annual_key_indicators.js`, 'utf8');

async function testAnnual() {
    const nodes = new Map();
    const listeners = new Map();
    let historyURL = 'http://localhost/';
    let requests = [];

    const context = vm.createContext({
        sessionStorage: {
            store: {},
            getItem(k) { return this.store[k] || null; },
            setItem(k, v) { this.store[k] = v; }
        },
        window: {
            location: { 

                search: '?mode=annual', 
                pathname: '/', 
                href: 'http://localhost/?mode=annual',
                toString() { return this.href; }
            },
            history: { replaceState: (a, b, url) => { historyURL = url.toString(); } },
            KI_CONFIG: {
                annualDataUrl: '/annual-data',
                dataUrl: '/operational-data',
                companyName: 'Test Corp'
            }
        },
        AbortController,
        Intl,
        console,
        URLSearchParams,
        URL,
        setTimeout: (fn) => fn(),
        document: {
            addEventListener(name, callback, capture) { listeners.set(name, { callback, capture }); },
            querySelectorAll(sel) {
                if (sel === '.mode-btn') {
                    return [{
                        dataset: { mode: 'annual' },
                        classList: { toggle: (cls, val) => {} },
                        addEventListener: () => {}
                    }, {
                        dataset: { mode: 'operational' },
                        classList: { toggle: (cls, val) => {} },
                        addEventListener: () => {}
                    }];
                }
                return [];
            },
            querySelector(sel) {
                return {
                    style: {},
                    classList: { add: () => {}, remove: () => {} }
                };
            },
            activeElement: { focus: () => {} },
            getElementById(id) {
                if (!nodes.has(id)) {
                    const nodeClasses = new Set();
                    nodes.set(id, {
                        style: {}, textContent: '', innerHTML: '', dataset: {},
                        classList: {
                            add: name => nodeClasses.add(name),
                            remove: name => nodeClasses.delete(name),
                            contains: name => nodeClasses.has(name),
                        },
                        setAttribute: () => {},
                        getAttribute: () => {},
                        addEventListener: () => {},
                        querySelector: () => ({ onclick: null, focus: () => {} })
                    });
                }
                return nodes.get(id);
            }
        },
        fetch: async url => {
            requests.push(url);
            if (url.includes('error')) {
                return { ok: false, status: 400, text: async () => JSON.stringify({ error: 'Test error' }) };
            }
            return {
                ok: true, status: 200,
                text: async () => JSON.stringify({
                    mode: 'annual', 
                    year: 2025,
                    years: [2024, 2025],
                    timeline_years: [2024, 2025],
                    previous_year: 2024,
                    cards: [
                        { id: 'revenue', title: 'Rev', value: 1000000, unit: 'RUB', upper_bound: false, delta: 500000, delta_percent: 50, previous_upper_bound: false },
                        { id: 'profitability', title: 'Prof', value: 0, unit: 'percent', upper_bound: true, delta: 0, delta_percent: 0, previous_upper_bound: true, comparison_note: 'was negative' },
                        { id: 'cash', title: 'Cash', value: null, unit: 'RUB', upper_bound: false, missing_reason: 'no cash flow data' },
                        { id: 'current_liquidity', title: 'Liq', value: 1.5, unit: 'ratio', delta: null, comparison_note: 'No prev data' }
                    ]
                })
            };
        }
    });

    vm.runInContext(appScript, context);
    vm.runInContext(annualScript, context);

    const KI_App = context.window.KI_App;
    const KI_AnnualApp = context.window.KI_AnnualApp;
    KI_AnnualApp.state.rangeStart = 2023;
    KI_AnnualApp.state.rangeEnd = 2025;
    assert.equal(KI_AnnualApp.displayedPeriodLabel(), '2023–2025');
    KI_AnnualApp.state.rangeStart = null;
    KI_AnnualApp.state.rangeEnd = null;
    
    // stub loading since they are called cross-app
    KI_App.showLoading = () => {};
    KI_App.hideLoading = () => {};

    KI_App.init(); // This should trigger annual mode activation based on search params
    KI_AnnualApp.init();

    await new Promise(r => setImmediate(r)); // wait for fetches

    assert.equal(KI_App.state.mode, 'annual');
    assert.equal(KI_AnnualApp.state.active, true);
    assert.ok(requests[0].includes('/annual-data'));

    const yearButtons = nodes.get('ki-annual-year-buttons');
    assert.equal(yearButtons.style.display, 'flex');
    assert.ok(yearButtons.innerHTML.includes('data-year="2024"'));
    assert.ok(yearButtons.innerHTML.includes('data-year="2025"'));
    assert.ok(yearButtons.innerHTML.includes('annual-year-btn active'));
    assert.ok(yearButtons.innerHTML.includes('aria-pressed="true"'));

    KI_AnnualApp.onYearChange(2024);
    await new Promise(r => setImmediate(r));
    assert.ok(requests.at(-1).includes('year=2024'));
    assert.ok(historyURL.includes('year=2024'));

    let zeroFmt = KI_AnnualApp.formatValue(0, 'percent', true);
    assert.equal(zeroFmt.val, '< 0');
    let nullFmt = KI_AnnualApp.formatValue(null, 'RUB', false);
    assert.equal(nullFmt.val, 'Н/Д');
    let rubFmt = KI_AnnualApp.formatValue(1500000, 'RUB', false);
    assert.equal(rubFmt.val, '1,5');
    assert.equal(rubFmt.u, 'млн ₽');

    // Test populated cards assertions
    const cardsGrid = nodes.get('ki-annual-cards-grid');
    // We didn't implement innerHTML for all child structures in our basic mock DOM,
    // so we can test the `KI_AnnualApp.formatValue` directly for edge cases if innerHTML doesn't store perfectly.
    // However, our string concatenation logic in `renderCards` sets innerHTML. Let's see if we can regex it.
    let gridHTML = cardsGrid.innerHTML;
    // profitability has zero delta, percent unit, and previous_upper_bound
    assert.ok(gridHTML.includes('≈ 0,0 п.п.'), 'Zero delta with percent unit and upper bound should use п.п. and ≈');
    assert.ok(gridHTML.includes('was negative'), 'comparison note should render');
    // revenue has delta, prev upper bound false
    assert.ok(gridHTML.includes('+0,5 млн ₽ (+50%)'), 'Delta formatting should format numeric correctly without ≈');
    // cash has missing value and reason
    assert.ok(gridHTML.includes('no cash flow data'), 'Null delta should render missing reason');
    // current_liquidity has null delta and comparison note
    assert.ok(gridHTML.includes('No prev data'), 'Null delta should still render comparison note');

    // Repeated modal opens
    KI_AnnualApp.openModal('revenue');
    let modal = nodes.get('ki-modal');
    assert.equal(modal.style.visibility, 'visible');
    assert.ok(modal.classList.contains('visible'));
    
    KI_App.closeModal();
    assert.equal(modal.style.visibility, 'hidden');
    assert.equal(modal.classList.contains('visible'), false);

    KI_AnnualApp.openModal('profitability');
    assert.equal(modal.style.visibility, 'visible');

    KI_App.switchMode('operational');
    assert.equal(KI_AnnualApp.state.active, false);
    assert.equal(KI_App.state.mode, 'operational');
    assert.ok(historyURL.includes('mode=operational'));
    
    // Switch should hide annual specific controls
    assert.equal(nodes.get('ki-annual-year-select').style.display, 'none');
    assert.equal(nodes.get('ki-period-selector').style.display, 'flex');

    // Switch should clear errors and close modal
    assert.equal(nodes.get('ki-error').style.display, 'none');
    assert.equal(modal.style.visibility, 'hidden');

    console.log("PASS: Annual mode VM tests");
}

async function testAnnualRange() {
    const nodes = new Map();
    const requests = [];
    const makeNode = id => {
        if (!nodes.has(id)) {
            const classes = new Set();
            nodes.set(id, {
                style: {}, textContent: '', innerHTML: '', dataset: {},
                classList: {
                    add: name => classes.add(name),
                    remove: name => classes.delete(name),
                    contains: name => classes.has(name),
                    toggle: (name, value) => value ? classes.add(name) : classes.delete(name)
                },
                setAttribute: () => {},
                getAttribute: () => {},
                addEventListener: () => {},
                querySelector: () => ({ onclick: null, focus: () => {} })
            });
        }
        return nodes.get(id);
    };
    const context = vm.createContext({
        window: {
            location: {
                search: '?mode=annual&year=2025&year_from=2023&year_to=2025',
                pathname: '/', href: 'http://localhost/?mode=annual&year=2025&year_from=2023&year_to=2025',
                toString() { return this.href; }
            },
            history: { replaceState: (a, b, url) => {
                context.window.location.href = url.toString();
                context.window.location.search = new URL(url.toString()).search;
            } },
            KI_CONFIG: {
                companyId: 1,
                companyName: 'Range Corp',
                annualDataUrl: '/annual-data',
                dataUrl: '/operational-data',
                aiReportUrl: '/ai-report'
            }
        },
        AbortController, Intl, console, URLSearchParams, URL,
        setTimeout: fn => fn(),
        document: {
            addEventListener() {},
            querySelectorAll() { return []; },
            querySelector() { return { style: {} }; },
            getElementById: makeNode
        },
        fetch: async url => {
            requests.push(url);
            const parsed = new URL(url, 'http://localhost');
            const isRange = parsed.searchParams.has('year_from');
            return {
                ok: true, status: 200,
                text: async () => JSON.stringify(isRange ? {
                    mode: 'annual',
                    year: 2025, year_from: 2023, year_to: 2025,
                    period_label: '2023–2025 гг.',
                    previous_range: {
                        year_from: 2020, year_to: 2022, label: '2020–2022 гг.'
                    },
                    years: [2020, 2021, 2022, 2023, 2024, 2025],
                    timeline_years: [2023, 2024, 2025],
                    cards: [
                        {id: 'revenue', title: 'Выручка', kind: 'flow',
                         value: 6000000, unit: 'RUB', period_label: 'за год',
                         delta: 1000000, comparison_note: null,
                         provenance: [
                             {year: 2023, filename: 'revenue-2023.xlsx',
                              sheet: 'Итоги', cell: 'B2'},
                             {year: 2025, filename: 'revenue-2025.xlsx',
                              sheet: 'Итоги', cell: 'B2'}
                         ]},
                        {id: 'cash', title: 'Денежные средства', kind: 'balance',
                         value: 2000000, unit: 'RUB', period_label: 'на 31.12',
                         delta: null},
                        {id: 'profitability', title: 'Рентабельность',
                         kind: 'ratio', value: 12.5, unit: 'percent',
                         period_label: 'по итогам 2023–2025 годов', delta: null,
                         provenance: [
                             {year: 2023, component: 'numerator',
                              provenance: {filename: 'profit-2023.xlsx',
                                  sheet: 'Расчет', cell: 'C3'}},
                             {year: 2023, component: 'denominator',
                              provenance: {filename: 'revenue-2023.xlsx',
                                  sheet: 'Расчет', cell: 'C4'}}
                         ],
                         provenance_by_year: {
                             2023: [
                                 {year: 2023, component: 'numerator',
                                  provenance: {filename: 'profit-2023.xlsx',
                                      sheet: 'Расчет', cell: 'C3'}},
                                 {year: 2023, component: 'denominator',
                                  provenance: {filename: 'revenue-2023.xlsx',
                                      sheet: 'Расчет', cell: 'C4'}}
                             ]
                         }}
                    ]
                } : {
                    mode: 'annual', year: 2024, years: [2023, 2024, 2025],
                    timeline_years: [2024], period_label: '2024',
                    cards: [
                        {id: 'revenue', title: 'Выручка', kind: 'flow',
                         value: 1000000, unit: 'RUB', period_label: 'за год',
                         delta: null}
                    ]
                })
            };
        }
    });
    vm.runInContext(appScript, context);
    vm.runInContext(annualScript, context);
    const app = context.window.KI_App;
    const annual = context.window.KI_AnnualApp;
    app.showLoading = () => {};
    app.hideLoading = () => {};
    annual.init();
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(requests[0],
        '/annual-data?year=2025&year_from=2023&year_to=2025');
    assert.equal(annual.state.rangeStart, 2023);
    assert.equal(annual.state.rangeEnd, 2025);
    assert.equal(annual.displayedPeriodLabel(), '2023–2025 гг.');
    assert.match(makeNode('ki-topbar-subtitle').textContent, /2023–2025 гг\./);
    const grid = makeNode('ki-annual-cards-grid').innerHTML;
    assert.match(grid, /за 2023–2025 гг\./);
    assert.match(grid, /на 31\.12\.2025/);
    assert.doesNotMatch(grid, /на 31\.12[^<]*2023–2025/);
    assert.match(app.state.aiReport.url,
        /mode=annual&year=2025&year_from=2023&year_to=2025/);
    annual.openModal('revenue');
    assert.equal(makeNode('ki-modal-subtitle').textContent,
        'Детализация по данным Отчета · 2023–2025 гг.');
    assert.match(makeNode('ki-modal-source').textContent, /2023 год/);
    assert.match(makeNode('ki-modal-source').textContent, /revenue-2025\.xlsx/);
    annual.openModal('profitability');
    const ratioSource = makeNode('ki-modal-source').textContent;
    assert.match(ratioSource, /2023 год/);
    assert.match(ratioSource, /numerator.*profit-2023\.xlsx/);
    assert.match(ratioSource, /denominator.*revenue-2023\.xlsx/);

    annual.onYearChange(2024);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.at(-1), '/annual-data?year=2024');
    assert.equal(annual.state.rangeStart, null);
    assert.equal(annual.state.rangeEnd, null);
    assert.equal(annual.displayedPeriodLabel(), '2024');
    assert.equal(app.state.aiReport.url, '/ai-report?mode=annual&year=2024');
    assert.match(makeNode('ki-topbar-subtitle').textContent, /2024$/);

    // A settled first click remains the anchor for the next ordinary button
    // click; only the second click sends the interval request.
    annual.state.selectionStart = null;
    annual.state.rangeStart = null;
    annual.state.rangeEnd = null;
    annual.state.year = 2025;
    annual.onYearChange(2023);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.at(-1), '/annual-data?year=2023');
    assert.equal(annual.state.selectionStart, 2023);
    annual.onYearChange(2025);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.at(-1),
        '/annual-data?year=2025&year_from=2023&year_to=2025');
    assert.equal(annual.state.rangeStart, 2023);
    assert.equal(annual.state.rangeEnd, 2025);
    console.log('PASS: Annual range request, labels, AI URL, and single-year switch');
}

(async () => {
    await testAnnual();
    await testAnnualRange();
})().catch(e => { console.error(e); process.exitCode = 1; });