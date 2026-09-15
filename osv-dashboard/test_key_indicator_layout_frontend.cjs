// Browser-independent layout controller checks.  The small DOM below keeps
// real parent/child relationships, comments (anchors), and class selectors so
// restoration tests exercise the same node-moving code used by the browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class ClassList {
    constructor(element) { this.element = element; }
    values() { return this.element.className.split(/\s+/).filter(Boolean); }
    add(...names) { this.element.className = [...new Set(this.values().concat(names))].join(' '); }
    remove(...names) { this.element.className = this.values().filter(name => !names.includes(name)).join(' '); }
    contains(name) { return this.values().includes(name); }
    toggle(name, enabled) { enabled ? this.add(name) : this.remove(name); }
}

class Element {
    constructor(tag = 'div', className = '', id = '') {
        this.nodeType = 1;
        this.tagName = tag.toUpperCase();
        this.className = className;
        this.id = id;
        this.childNodes = [];
        this.parentNode = null;
        this.style = {display: ''};
        this.dataset = {};
        this.hidden = false;
        this.textContent = '';
        this.innerHTML = '';
        this.classList = new ClassList(this);
    }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    appendChild(node) {
        if (node.parentNode) node.parentNode.removeChild(node);
        this.childNodes.push(node);
        node.parentNode = this;
        return node;
    }
    insertBefore(node, before) {
        if (node.parentNode) node.parentNode.removeChild(node);
        const index = this.childNodes.indexOf(before);
        this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, node);
        node.parentNode = this;
        return node;
    }
    removeChild(node) {
        const index = this.childNodes.indexOf(node);
        if (index >= 0) this.childNodes.splice(index, 1);
        node.parentNode = null;
        return node;
    }
    selectorClasses(selector) {
        return selector.split(',').map(part => part.trim())
            .map(part => (part.match(/\.([a-zA-Z0-9_-]+)/g) || []).pop())
            .filter(Boolean).map(name => name.slice(1));
    }
    matches(selector) { return this.selectorClasses(selector).some(name => this.classList.contains(name)); }
    querySelectorAll(selector) {
        const matches = [];
        const visit = node => {
            node.children.forEach(child => {
                if (child.matches(selector)) matches.push(child);
                visit(child);
            });
        };
        visit(this);
        return matches;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getAttribute(name) {
        if (name === 'id') return this.id || null;
        return this.dataset[name] || null;
    }
    setAttribute() {}
    addEventListener() {}
}

class Document {
    createElement(tag) { return new Element(tag); }
    createComment() { return {nodeType: 8, parentNode: null}; }
    querySelector() { return null; }
}

const document = new Document();
const context = vm.createContext({
    window: {KI_CONFIG: {layoutBaseUrl: '/company/1/key-indicators/layout'}},
    document,
    console,
    fetch: async () => ({ok: true, json: async () => ({layout: {version: 1, items: {}}, revision: 0})})
});
vm.runInContext(fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'), context);
const layout = context.window.KI_Layout;

function summaryFixture() {
    const container = new Element();
    const primary = new Element('div', 'cash-summary-grid');
    const first = new Element('div', 'cash-summary-card');
    first.textContent = 'Ноль';
    const second = new Element('div', 'cash-summary-card');
    second.textContent = 'Н/Д';
    primary.appendChild(first);
    primary.appendChild(second);
    const missing = new Element('div', 'ar-missing-grid');
    const auxiliary = new Element('div', 'cash-analysis-card needs-data');
    auxiliary.textContent = 'Дополнительные данные';
    missing.appendChild(auxiliary);
    const title = new Element('div', 'modal-section-title');
    title.textContent = 'Формула';
    const formula = new Element();
    formula.textContent = 'fact';
    container.appendChild(primary);
    container.appendChild(missing);
    container.appendChild(title);
    container.appendChild(formula);
    return {container, primary, missing, first, second, auxiliary};
}

(async () => {
// Empty persisted preferences must retain defaults; auxiliary cards are not
// promoted to Main, and zero/missing cards still consume structural slots.
{
    const fixture = summaryFixture();
    layout.state.activeContainer = fixture.container;
    layout.captureOriginalDom(fixture.container, 1);
    layout.state.items = layout.extractItems(fixture.container, 'cash');
    const defaults = layout.generateDefaultLayout();
    assert.equal(layout.state.items.length, 4);
    assert.equal(Object.values(defaults.items).filter(value => value === 'main').length, 2);
    assert.equal(layout.state.items[2].defaultPlacement, 'detail');
    assert.equal(layout.mergeLayout({version: 1, items: {}}).items[layout.state.items[0].id], 'main');
    layout.restoreOriginalDom();
    assert.equal(fixture.container.children[0], fixture.primary);
    assert.deepEqual(fixture.primary.children, [fixture.first, fixture.second]);
    assert.equal(fixture.missing.children[0], fixture.auxiliary);
}

// Annual generic content has no summary cards: the fact/formula section is a
// usable Main tab by default.
{
    const container = new Element();
    const title = new Element('div', 'modal-section-title');
    title.textContent = 'Факт и формула';
    const formula = new Element();
    formula.textContent = 'Годовой показатель';
    container.appendChild(title);
    container.appendChild(formula);
    layout.state.activeContainer = container;
    layout.captureOriginalDom(container, 2);
    layout.state.items = layout.extractItems(container, 'revenue');
    const defaults = layout.generateDefaultLayout();
    assert.equal(Object.values(defaults.items).filter(value => value === 'main').length, 1);
    layout.restoreOriginalDom();
}

// Ordering is registry-stable for old preferences, while a partial saved
// order is retained and new registry IDs are appended.
{
    const fixture = summaryFixture();
    layout.state.activeContainer = fixture.container;
    layout.captureOriginalDom(fixture.container, 3);
    layout.state.items = layout.extractItems(fixture.container, 'cash');
    const defaults = layout.generateDefaultLayout();
    assert.deepEqual(Array.from(defaults.order),
        Array.from(layout.state.items, item => item.id));
    const reordered = layout.mergeLayout({
        version: 1,
        items: {[layout.state.items[0].id]: 'main'},
        order: [layout.state.items[2].id]
    });
    assert.deepEqual(Array.from(reordered.order), [
        layout.state.items[2].id,
        layout.state.items[0].id,
        layout.state.items[1].id,
        layout.state.items[3].id
    ]);
    layout.restoreOriginalDom();
}

// Drag/drop's model operation supports mixed columns and keeps the placement
// update and order update in one draft mutation.
{
    layout.state.items = [
        {id: 'a', type: 'card'}, {id: 'b', type: 'section'},
        {id: 'c', type: 'card'}, {id: 'd', type: 'section'}
    ];
    layout.state.layoutDraft = {
        version: 1,
        items: {a: 'main', b: 'detail', c: 'main', d: 'hidden'},
        order: ['a', 'b', 'c', 'd']
    };
    layout.reorderDraftItem('b', 'main', 'c', 'before');
    assert.deepEqual(Array.from(layout.state.layoutDraft.order), ['a', 'b', 'c', 'd']);
    assert.equal(layout.state.layoutDraft.items.b, 'main');
    layout.moveDraftItemOrder('c', 'up');
    assert.deepEqual(Array.from(layout.state.layoutDraft.order), ['a', 'c', 'b', 'd']);
    layout.reorderDraftItem('d', 'detail', null, 'end');
    assert.deepEqual(Array.from(layout.state.layoutDraft.order), ['a', 'c', 'b', 'd']);
    assert.equal(layout.state.layoutDraft.items.d, 'detail');
    layout.state.layoutDraft = null;
    layout.state.items = [];
}

// A stale first GET may finish after a second open, but only the second
// context may update revision/configuration.
{
    const fixture = summaryFixture();
    layout.mountSkeleton = () => {};
    layout.showLoadingState = () => {};
    layout.showLoadError = () => {};
    layout.setBusy = () => {};
    layout.renderView = () => {};
    let resolveFirst;
    let resolveSecond;
    context.fetch = () => new Promise(resolve => {
        if (!resolveFirst) resolveFirst = resolve;
        else resolveSecond = resolve;
    });
    const firstOpen = layout.onModalOpen('cash', fixture.container, 'osv');
    const secondOpen = layout.onModalOpen('cash', fixture.container, 'osv');
    resolveFirst({ok: true, json: async () => ({layout: {version: 1, items: {}}, revision: 1})});
    resolveSecond({ok: true, json: async () => ({
        layout: {version: 1, items: {}}, revision: 7
    })});
    await Promise.all([firstOpen, secondOpen]);
    assert.equal(layout.state.revision, 7);
    assert.equal(layout.state.ready, true);
    assert.equal(layout.state.layoutConfig.items[layout.state.items[0].id], 'main');
    layout.onModalClose(true);
}

// Dirty state reflects actual draft content, and selected batch moves update
// every selected item.  A save finishing after close cannot mutate the reset
// context.
{
    layout.state.layoutConfig = {version: 1, items: {a: 'main', b: 'detail'}};
    layout.state.layoutDraft = JSON.parse(JSON.stringify(layout.state.layoutConfig));
    layout.state.isConstructor = true;
    layout.state.selected = new Set(['a', 'b']);
    assert.equal(layout.isDirty(), false);
    layout.moveSelected('hidden');
    assert.equal(layout.isDirty(), true);
    assert.equal(layout.state.layoutDraft.items.a, 'hidden');
    assert.equal(layout.state.layoutDraft.items.b, 'hidden');

    layout.state.activeContainer = new Element();
    layout.state.apiUrl = '/layout';
    layout.state.ready = true;
    layout.state.revision = 0;
    layout.getWrapper = () => null;
    let resolveSave;
    context.fetch = () => new Promise(resolve => { resolveSave = resolve; });
    const save = layout.saveLayout();
    layout.onModalClose(true);
    resolveSave({ok: true, json: async () => ({
        layout: {version: 1, items: {a: 'hidden', b: 'hidden'}}, revision: 1
    })});
    await save;
    assert.equal(layout.state.activeContainer, null);
}

// Real renderView (not the async-test stub) must hide empty-state text for a
// populated panel and remove the loading message after a successful GET.
{
    const freshWindow = {};
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        {window: freshWindow, document, console});
    const fresh = freshWindow.KI_Layout;
    const status = new Element();
    status.textContent = 'Загрузка сохранённой раскладки…';
    const panels = {};
    for (const tab of ['main', 'detail']) {
        const fields = {
            '.ki-layout-grid-container': new Element(),
            '.ki-layout-sections': new Element(),
            '.ki-layout-empty': new Element(),
        };
        panels[tab] = {
            fields,
            querySelector: selector => fields[selector],
        };
    }
    fresh.getWrapper = () => ({
        querySelector: selector => selector === '.ki-layout-load-status'
            ? status : panels[selector.includes('"main"') ? 'main' : 'detail'],
    });
    fresh.detachRenderedItems = () => {};
    fresh.updateTabs = () => {};
    fresh.rerenderCharts = () => {};
    Object.assign(fresh.state, {
        ready: true,
        loading: false,
        layoutConfig: {version: 1, items: {a: 'main'}},
        items: [{id: 'a', type: 'card', elements: [new Element()]}],
    });
    fresh.renderView();
    assert.equal(status.textContent, '');
    assert.equal(status.hidden, true);
    assert.equal(panels.main.fields['.ki-layout-empty'].hidden, true);
    assert.equal(panels.detail.fields['.ki-layout-empty'].hidden, false);
    const css = fs.readFileSync(
        `${__dirname}/static/key_indicator_layout.css`, 'utf8');
    assert.match(css, /\.ki-layout-wrapper \[hidden\]\s*\{\s*display:\s*none !important;/);
}

// Main/detail presentation consumes the one mixed order stream, rather than
// sorting cards ahead of sections in separate containers.
{
    const freshWindow = {};
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        {window: freshWindow, document, console});
    const fresh = freshWindow.KI_Layout;
    const grid = new Element();
    const empty = new Element();
    const panel = {
        querySelector: selector => selector === '.ki-layout-grid-container' ? grid :
            selector === '.ki-layout-empty' ? empty : new Element()
    };
    const wrapper = {
        querySelector: selector => selector.includes('"main"') ? panel : panel
    };
    const title = new Element('div', 'modal-section-title');
    const formula = new Element();
    const first = new Element('div', 'cash-summary-card');
    const second = new Element('div', 'cash-summary-card');
    fresh.getWrapper = () => wrapper;
    fresh.detachRenderedItems = () => {};
    fresh.updateTabs = () => {};
    fresh.rerenderCharts = () => {};
    Object.assign(fresh.state, {
        ready: true,
        loading: false,
        layoutConfig: {
            version: 1,
            items: {card1: 'main', section: 'main', card2: 'main'},
            order: ['section', 'card2', 'card1']
        },
        items: [
            {id: 'card1', type: 'card', elements: [first]},
            {id: 'section', type: 'section', elements: [title, formula]},
            {id: 'card2', type: 'card', elements: [second]}
        ]
    });
    fresh.renderView();
    assert.equal(grid.children.length, 3);
    assert.equal(grid.children[0].classList.contains('ki-layout-item-section'), true);
    assert.equal(grid.children[0].children[0], title);
    assert.equal(grid.children[1], second);
    assert.equal(grid.children[2], first);
}

// Saving sends the optional order alongside the version-1 placement map.
{
    let requestBody = null;
    const freshWindow = {KI_CONFIG: {csrfToken: 'test'}};
    const sandbox = {
        window: freshWindow,
        document,
        console,
        fetch: async (url, options) => {
            requestBody = JSON.parse(options.body);
            return {
                ok: true,
                json: async () => ({
                    layout: requestBody.layout,
                    revision: 2
                })
            };
        }
    };
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        sandbox);
    const fresh = freshWindow.KI_Layout;
    fresh.getWrapper = () => null;
    Object.assign(fresh.state, {
        activeContainer: new Element(),
        apiUrl: '/layout',
        ready: true,
        isConstructor: true,
        revision: 1,
        layoutConfig: {version: 1, items: {a: 'main', b: 'detail'}, order: ['a', 'b']},
        layoutDraft: {version: 1, items: {a: 'detail', b: 'main'}, order: ['b', 'a']}
    });
    await fresh.saveLayout();
    assert.deepEqual(requestBody.layout.order, ['b', 'a']);
    assert.equal(requestBody.layout.version, 1);
    assert.deepEqual(requestBody.layout.items, {a: 'detail', b: 'main'});
    fresh.onModalClose(true);
}

// Cancelling a draft discards its order edits without mutating the saved
// layout that will be used when the constructor is opened again.
{
    const freshWindow = {};
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        {window: freshWindow, document, console});
    const fresh = freshWindow.KI_Layout;
    const gear = new Element('button');
    const view = new Element();
    const constructor = new Element();
    const wrapper = {
        querySelector: selector => selector === '.ki-layout-view' ? view :
            selector === '.ki-layout-constructor' ? constructor : null,
        querySelectorAll: () => []
    };
    fresh.getWrapper = () => wrapper;
    fresh.getToolbar = () => ({querySelector: () => gear});
    fresh.setBusy = () => {};
    fresh.renderView = () => {};
    Object.assign(fresh.state, {
        activeContainer: new Element(),
        ready: true,
        isConstructor: true,
        layoutConfig: {version: 1, items: {a: 'main', b: 'detail'}, order: ['a', 'b']},
        layoutDraft: {version: 1, items: {a: 'main', b: 'detail'}, order: ['b', 'a']}
    });
    fresh.toggleConstructor(false);
    assert.equal(fresh.state.layoutDraft, null);
    assert.deepEqual(Array.from(fresh.state.layoutConfig.order), ['a', 'b']);
}

// Moving the toolbar outside the body must retain tab/gear updates and cleanup.
{
    const freshWindow = {};
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        {window: freshWindow, document, console});
    const fresh = freshWindow.KI_Layout;
    const host = new Element();
    const toolbar = host.appendChild(new Element());
    const wrapper = new Element();
    const tabs = ['main', 'detail'].map(tab => {
        const button = toolbar.appendChild(new Element('button', 'ki-layout-tab'));
        button.dataset.tab = tab;
        const panel = wrapper.appendChild(new Element('div', 'ki-layout-content'));
        panel.dataset.tabContent = tab;
        return button;
    });
    const gear = toolbar.appendChild(new Element('button', 'ki-layout-btn-gear'));
    fresh.state.toolbar = toolbar;
    fresh.state.activeTab = 'detail';
    fresh.getWrapper = () => wrapper;
    fresh.rerenderCharts = () => {};
    fresh.setGearEnabled(true);
    assert.equal(gear.disabled, false);
    fresh.updateTabs();
    assert.equal(tabs[0].classList.contains('active'), false);
    assert.equal(tabs[1].classList.contains('active'), true);
    assert.equal(wrapper.children[0].hidden, true);
    assert.equal(wrapper.children[1].hidden, false);
    fresh.restoreOriginalDom();
    assert.equal(host.children.length, 0);
    assert.equal(fresh.state.toolbar, null);
}

// The shared operational dynamics block is extracted as its own movable
// constructor item, rather than being glued to the surrounding section.
{
    const freshWindow = {};
    vm.runInNewContext(
        fs.readFileSync(`${__dirname}/static/key_indicator_layout.js`, 'utf8'),
        {window: freshWindow, document, console});
    const fresh = freshWindow.KI_Layout;
    const container = new Element();
    const dynamics = new Element('div');
    dynamics.dataset.layoutKey = 'dynamics';
    const title = new Element('div', 'modal-section-title');
    title.textContent = 'Динамика';
    dynamics.appendChild(title);
    container.appendChild(dynamics);
    fresh.state.activeContainer = container;
    fresh.captureOriginalDom(container, 22);
    fresh.state.items = fresh.extractItems(container, 'operational');
    assert.equal(fresh.state.items.length, 1);
    assert.equal(fresh.state.items[0].type, 'chart');
    assert.equal(fresh.state.items[0].title, 'Динамика');
    assert.match(fresh.state.items[0].id, /dynamics/);
    assert.equal(fresh.state.items[0].defaultPlacement, 'detail');
    fresh.restoreOriginalDom();
}

    console.log('PASS: key-indicator layouts, rendered states and relocated header toolbar');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});