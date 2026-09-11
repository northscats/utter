// Focused regression checks for in-place company changes in the KPI iframe.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const appScript = fs.readFileSync(`${__dirname}/static/key_indicators.js`, 'utf8');
const annualScript = fs.readFileSync(`${__dirname}/static/annual_key_indicators.js`, 'utf8');
const homeTemplate = fs.readFileSync(`${__dirname}/templates/home.html`, 'utf8');

function companyConfig(id, name) {
    return {
        companyId: id,
        companyName: name,
        pageUrl: `/company/${id}/key-indicators`,
        dataUrl: `/company/${id}/key-indicators/data`,
        annualDataUrl: `/company/${id}/key-indicators/annual-data`,
        mappingsUrl: `/company/${id}/key-indicators/revenue-mappings`,
        costMappingsUrl: `/company/${id}/key-indicators/cost-mappings`,
        debtorMappingsUrl: `/company/${id}/key-indicators/debtor-mappings`,
        layoutBaseURL: `/company/${id}/key-indicators/layout/__MODE__/__METRIC__`
    };
}

async function testOperationalSwitchIgnoresStaleResponse() {
    const nodes = new Map();
    const pending = [];
    const listeners = new Map();
    const makeNode = id => {
        const classes = new Set(id === 'ki-modal' ? ['visible'] : []);
        return {
            id, style: {}, textContent: '', innerHTML: '',
            classList: {
                add: name => classes.add(name),
                remove: name => classes.delete(name),
                contains: name => classes.has(name),
                toggle: (name, value) => value ? classes.add(name) : classes.delete(name)
            },
            setAttribute() {},
            getAttribute() { return null; },
            addEventListener() {},
            querySelector() { return { focus() {} }; }
        };
    };
    const context = vm.createContext({
        window: {
            location: {
                origin: 'http://localhost',
                href: 'http://localhost/company/1/key-indicators?embedded=1',
                pathname: '/company/1/key-indicators',
                search: '?embedded=1'
            },
            parent: {},
            history: { replaceState() {} },
            addEventListener(name, callback) { listeners.set(name, callback); }
        },
        URL,
        URLSearchParams,
        AbortController,
        Intl,
        console,
        confirm: () => true,
        document: {
            addEventListener() {},
            querySelectorAll() { return []; },
            activeElement: { focus() {} },
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, makeNode(id));
                return nodes.get(id);
            }
        },
        fetch(url, options) {
            return new Promise((resolve, reject) => {
                const request = {url, resolve, reject};
                pending.push(request);
                if (options && options.signal) {
                    options.signal.addEventListener('abort', () => {
                        const error = new Error('aborted');
                        error.name = 'AbortError';
                        reject(error);
                    });
                }
            });
        }
    });
    vm.runInContext('window.KI_CONFIG = ' + JSON.stringify(Object.assign({
        embedded: true, companyId: 1, companyName: 'A'
    }, companyConfig(1, 'A'))) + ';', context);
    vm.runInContext(appScript, context);
    vm.runInContext(annualScript, context);
    const app = context.window.KI_App;
    const annual = context.window.KI_AnnualApp;
    app.render = () => {};
    app.openModal = cardId => {
        app._opened = cardId;
        app.state.modalCardId = cardId;
    };
    annual.render = () => {};
    annual.openModal = cardId => {
        app._opened = cardId;
        annual.state.modalCardId = cardId;
    };
    app.state.mode = 'operational';
    app.state.data = {cards: [{id: 'revenue'}]};
    app.state.modalCardId = 'revenue';

    assert.equal(app.switchCompany(companyConfig(2, 'B')), true);
    assert.equal(app.switchCompany(companyConfig(3, 'C')), true);
    assert.equal(pending.length, 2);

    // The first request is aborted and must never reopen the modal with B.
    pending[0].resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [{id: 'revenue'}], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    pending[1].resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [{id: 'revenue'}], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.companySwitchToken, 2);
    assert.equal(context.window.KI_CONFIG.companyId, 3);
    assert.equal(app._opened, 'revenue');
    assert.equal(listeners.has('message'), true);

    // A missing metric keeps its identity so switching back can recover it.
    assert.equal(app.switchCompany(companyConfig(5, 'missing')), true);
    pending.at(-1).resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.modalCardId, 'revenue');
    assert.equal(app.state.pendingModalCardId, 'revenue');
    assert.equal(app.switchCompany(companyConfig(10, 'error')), true);
    pending.at(-1).resolve({
        ok: false, status: 500,
        text: async () => JSON.stringify({error: 'temporary failure'})
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.modalCardId, 'revenue');
    assert.equal(app.state.pendingModalCardId, 'revenue');
    assert.equal(app.switchCompany(companyConfig(6, 'recover')), true);
    pending.at(-1).resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [{id: 'revenue'}], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app._opened, 'revenue');
    assert.equal(app.state.pendingModalCardId, null);

    // Cancelling the unsaved-layout prompt keeps the parent on C.
    context.confirm = () => false;
    context.window.KI_Layout = {
        isDirty: () => true,
        onModalClose() { throw new Error('must not reset on cancel'); }
    };
    assert.equal(app.switchCompany(companyConfig(9, 'discarded')), false);
    assert.equal(context.window.KI_CONFIG.companyId, 6);
    context.confirm = () => true;
    context.window.KI_Layout = undefined;

    // Closing during an OSV switch leaves its request alive and renders data.
    app.state.mode = 'operational';
    assert.equal(app.switchCompany(companyConfig(7, 'closed')), true);
    app.closeModal(true);
    assert.equal(app.state.companySwitching, false);
    assert.equal(app.state.pendingModalCardId, null);
    pending.at(-1).resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [{id: 'revenue'}], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.data.cards[0].id, 'revenue');
    assert.equal(app._opened, 'revenue');

    // Annual company switch retries once without a retained year missing for B.
    nodes.get('ki-modal').classList.add('visible');
    app.state.mode = 'annual';
    annual.state.active = true;
    annual.state.year = 2024;
    annual.state.modalCardId = 'revenue';
    app._opened = null;
    assert.equal(app.switchCompany(companyConfig(13, 'annual unrelated failure')), true);
    const unrelatedYearRequest = pending.at(-1);
    unrelatedYearRequest.resolve({
        ok: false, status: 400,
        text: async () => JSON.stringify({error: 'Другой отказ сервера'})
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(pending.at(-1), unrelatedYearRequest);
    assert.match(nodes.get('ki-modal-switch-status').textContent,
        /annual unrelated failure[\s\S]*Другой отказ сервера/i);

    nodes.get('ki-modal').classList.add('visible');
    annual.state.modalCardId = 'revenue';
    assert.equal(app.switchCompany(companyConfig(8, 'annual fallback')), true);
    const missingYearRequest = pending.at(-1);
    assert.match(missingYearRequest.url, /\/annual-data\?year=2024$/);
    missingYearRequest.resolve({
        ok: false, status: 400,
        text: async () => JSON.stringify({
            error: 'Запрошенный год отсутствует среди принятых данных'
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    const fallbackRequest = pending.at(-1);
    assert.match(fallbackRequest.url, /\/annual-data$/);
    fallbackRequest.resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            year: 2025, years: [2025], cards: [{id: 'revenue'}]
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(annual.state.year, 2025);
    assert.match(nodes.get('ki-modal-subtitle').textContent, /annual fallback.*2024.*2025/i);
    assert.equal(app._opened, 'revenue');

    // Closing during the retried annual switch must not abort its request.
    nodes.get('ki-modal').classList.add('visible');
    annual.state.modalCardId = 'revenue';
    app._opened = null;
    assert.equal(app.switchCompany(companyConfig(9, 'annual closed')), true);
    app.closeModal(true);
    const annualRequest = pending.at(-1);
    annualRequest.resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            year: 2025, years: [2025], cards: [{id: 'revenue'}]
        })
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(context.window.KI_CONFIG.companyId, 9);
    assert.equal(annual.state.data.cards[0].id, 'revenue');
    assert.equal(app._opened, null);

    // An old mapping save may finish, but must not close/reopen the new modal.
    annual.state.active = false;
    app.state.mode = 'operational';
    app.state.modalCardId = 'revenue';
    nodes.get('ki-modal').classList.add('visible');
    const savePromise = app.saveRevenueMappings();
    const saveRequest = pending.at(-1);
    assert.match(saveRequest.url, /\/company\/9\/key-indicators\/revenue-mappings$/);
    assert.equal(app.switchCompany(companyConfig(11, 'save race')), true);
    const dataRequest = pending.at(-1);
    saveRequest.resolve({
        ok: true, status: 200,
        json: async () => ({ok: true})
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.state.companySwitching, true);
    dataRequest.resolve({
        ok: true, status: 200,
        text: async () => JSON.stringify({
            cards: [{id: 'revenue'}], period: {kind: 'year', date: '2026-12-31'}
        })
    });
    await savePromise;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app._opened, 'revenue');

    // An annual request that fails after close still reports the current tenant error.
    nodes.get('ki-modal').classList.add('visible');
    app.state.mode = 'annual';
    annual.state.active = true;
    annual.state.modalCardId = 'revenue';
    assert.equal(app.switchCompany(companyConfig(12, 'annual error')), true);
    app.closeModal(true);
    pending.at(-1).resolve({
        ok: false, status: 503,
        text: async () => JSON.stringify({error: 'annual unavailable'})
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(nodes.get('ki-error').style.display, 'flex');
}

function testParentUsesServerGeneratedMetadata() {
    assert.match(homeTemplate, /data-ki-config=/);
    assert.match(homeTemplate, /trySwitchOpenIndicatorsCompany/);
    assert.match(homeTemplate, /if \(indicatorSwitch !== true\) renderNavView/);
    assert.match(appScript, /event\.source !== window\.parent/);
    assert.match(appScript, /companySwitchToken/);
}

(async () => {
    testParentUsesServerGeneratedMetadata();
    await testOperationalSwitchIgnoresStaleResponse();
    console.log('PASS: KPI modal company switch keeps selected metric and drops stale requests');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});