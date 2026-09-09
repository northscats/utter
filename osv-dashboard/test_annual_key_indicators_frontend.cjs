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

testAnnual().catch(e => { console.error(e); process.exitCode = 1; });