/* key_indicator_layout.js
 *
 * The modal renderers own the contents of each modal.  This controller only
 * changes the presentation order.  In particular, it never clones a metric
 * card: cards can contain event handlers and mapping controls installed by the
 * renderer, so moving the original nodes is intentional.
 */
(function() {
    'use strict';

    const PLACEMENTS = ['main', 'detail', 'hidden'];
    const CARD_SELECTOR = '.cash-summary-card, .cash-analysis-card, .collection-summary-card';
    const SUMMARY_SELECTOR = [
        '.cash-summary-grid',
        '.cash-metrics-grid',
        '.cash-analysis-grid',
        '.modal-summary',
        '.collection-summary',
        '.salary-summary',
        '.cash-summary',
        '.revenue-missing-grid',
        '.collection-missing-grid',
        '.ar-missing-grid',
        '.cash-missing-grid',
        '.profit-missing-grid',
        '.credits-missing-grid',
        '.salary-missing-grid',
        '.capex-missing-grid'
    ].join(', ');

    const clone = value => JSON.parse(JSON.stringify(value));

    const LayoutManager = {
        state: {
            activeCardId: null,
            activeContainer: null,
            originalRoots: [],
            originalNodes: [],
            holding: null,
            anchors: [],
            mode: null,
            items: [],
            layoutConfig: null,
            layoutDraft: null,
            isConstructor: false,
            activeTab: 'main',
            revision: 0,
            reqId: 0,
            apiUrl: null,
            ready: false,
            loading: false,
            saving: false,
            loadError: null,
            saveError: null,
            selected: new Set(),
            lastFocused: null
        },

        gearIcon: '<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51 2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.6 15 1.65 1.65 0 003 14a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.6 1.65 1.65 0 0010 3a2 2 0 014 0v.09A1.65 1.65 0 0015 4.6a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9c.27.62.88 1 1.51 1H21a2 2 0 010 4h-.09c-.63 0-1.24.38-1.51 1z"/></svg>',

        onModalOpen: async function(cardId, container, mode) {
            if (!container) return;
            if (this.state.activeContainer) {
                if (!this.onModalClose(true)) return;
            }

            const token = ++this.state.reqId;
            const apiUrl = this.getApiUrl(mode, cardId);
            this.state.activeCardId = cardId;
            this.state.activeContainer = container;
            this.state.mode = mode;
            this.state.apiUrl = apiUrl;
            this.state.originalRoots = [];
            this.state.originalNodes = [];
            this.state.anchors = [];
            this.state.holding = null;
            this.state.items = [];
            this.state.layoutConfig = null;
            this.state.layoutDraft = null;
            this.state.activeTab = 'main';
            this.state.revision = 0;
            this.state.ready = false;
            this.state.loading = true;
            this.state.saving = false;
            this.state.loadError = null;
            this.state.saveError = null;
            this.state.isConstructor = false;
            this.state.selected = new Set();

            this.captureOriginalDom(container, token);
            this.state.items = this.extractItems(container, cardId);
            this.state.layoutConfig = this.generateDefaultLayout();
            this.mountSkeleton(container);
            this.setBusy(true);
            this.showLoadingState();

            try {
                await this.fetchLayout(token, apiUrl);
                if (!this.isCurrent(token)) return;
                this.state.ready = true;
                this.state.loading = false;
                this.state.loadError = null;
                this.renderView();
                this.setBusy(false);
            } catch (error) {
                if (!this.isCurrent(token)) return;
                this.state.loading = false;
                this.state.loadError = error;
                this.showLoadError(error);
                // A failed GET is not a usable draft.  The user can retry or
                // close the dialog, but cannot accidentally save defaults.
                this.setBusy(false);
                this.setGearEnabled(false);
            }
        },

        onModalClose: function(force) {
            if (!this.state.activeContainer) return true;
            if (!force && (this.state.loading || this.state.saving)) return false;
            this.state.reqId++;
            if (window.KI_App && typeof window.KI_App.clearChartJobs === 'function') {
                window.KI_App.clearChartJobs();
            }
            if (window.KI_AnnualApp && typeof window.KI_AnnualApp.clearChartJobs === 'function') {
                window.KI_AnnualApp.clearChartJobs();
            }
            this.state.isConstructor = false;
            this.detachRenderedItems();
            this.restoreOriginalDom();
            this.resetState();
            return true;
        },

        canClose: function() {
            return !!this.state.activeContainer && !this.state.loading && !this.state.saving;
        },

        isCurrent: function(token) {
            return token === this.state.reqId && !!this.state.activeContainer;
        },

        resetState: function() {
            this.state.activeCardId = null;
            this.state.activeContainer = null;
            this.state.originalRoots = [];
            this.state.originalNodes = [];
            this.state.holding = null;
            this.state.anchors = [];
            this.state.mode = null;
            this.state.items = [];
            this.state.layoutConfig = null;
            this.state.layoutDraft = null;
            this.state.revision = 0;
            this.state.activeTab = 'main';
            this.state.apiUrl = null;
            this.state.ready = false;
            this.state.loading = false;
            this.state.saving = false;
            this.state.loadError = null;
            this.state.saveError = null;
            this.state.selected = new Set();
        },

        captureOriginalDom: function(container, token) {
            const holding = document.createElement('div');
            holding.className = 'ki-layout-original-holding';
            holding.dataset.kiLayoutHolding = 'true';
            holding.id = `ki-layout-holding-${token}`;
            holding.setAttribute('aria-hidden', 'true');
            // It must stay attached so delayed renderer jobs can still find
            // IDs.  display:none also prevents hidden controls from receiving
            // focus while the constructor is open.
            holding.style.display = 'none';
            this.state.originalRoots = Array.from(container.childNodes);
            this.state.originalNodes = this.state.originalRoots;
            this.state.originalRoots.forEach(node => holding.appendChild(node));
            container.appendChild(holding);
            this.state.holding = holding;
        },

        restoreOriginalDom: function() {
            const container = this.state.activeContainer;
            const holding = this.state.holding;
            if (!container || !holding) return;
            this.state.anchors.forEach(record => {
                if (!record.node || !record.anchor || !record.anchor.parentNode) return;
                record.anchor.parentNode.insertBefore(record.node, record.anchor);
            });
            this.state.anchors.forEach(record => {
                if (record.anchor && record.anchor.parentNode) {
                    record.anchor.parentNode.removeChild(record.anchor);
                }
            });
            this.state.originalRoots.forEach(node => {
                if (node && node.parentNode) container.appendChild(node);
            });
            if (holding.parentNode) holding.parentNode.removeChild(holding);
            // The wrapper is not part of the original DOM.
            Array.from(container.querySelectorAll('.ki-layout-wrapper')).forEach(node => {
                if (node.parentNode === container) container.removeChild(node);
            });
        },

        anchorAndMove: function(node) {
            if (!node || !node.parentNode || !this.state.holding) return;
            const anchor = document.createComment('ki-layout-original-position');
            node.parentNode.insertBefore(anchor, node);
            this.state.anchors.push({node, anchor});
            this.state.holding.appendChild(node);
        },

        detachRenderedItems: function() {
            if (!this.state.holding) return;
            this.state.items.forEach(item => item.elements.forEach(node => {
                if (node && node.parentNode && node.parentNode !== this.state.holding) {
                    this.state.holding.appendChild(node);
                }
            }));
        },

        isElement: function(node) {
            return node && node.nodeType === 1;
        },

        classText: function(node) {
            return this.isElement(node) ? String(node.className || '') : '';
        },

        hasClass: function(node, fragment) {
            return this.classText(node).split(/\s+/).includes(fragment);
        },

        isCard: function(node) {
            return this.isElement(node) &&
                (node.matches ? node.matches(CARD_SELECTOR) :
                    /(^|\s)(cash-summary-card|cash-analysis-card|collection-summary-card)(\s|$)/.test(this.classText(node)));
        },

        isSummaryParent: function(node) {
            return this.isElement(node) &&
                (node.matches ? node.matches(SUMMARY_SELECTOR) :
                    /(^|\s)(cash-summary-grid|cash-metrics-grid|cash-analysis-grid|modal-summary|collection-summary|salary-summary|cash-summary|revenue-missing-grid|collection-missing-grid|ar-missing-grid|cash-missing-grid|profit-missing-grid|credits-missing-grid|salary-missing-grid|capex-missing-grid)(\s|$)/.test(this.classText(node)));
        },

        isAuxiliaryParent: function(node) {
            const classes = this.classText(node).toLowerCase();
            return classes.includes('missing') || classes.includes('auxiliary') ||
                classes.includes('unavailable') || classes.includes('revenue-missing');
        },

        directSummaryCards: function(parent) {
            if (!parent || !this.isElement(parent)) return [];
            const direct = Array.from(parent.children || []).filter(node => this.isCard(node));
            if (direct.length) return direct;
            if (!parent.querySelectorAll) return [];
            return Array.from(parent.querySelectorAll(CARD_SELECTOR)).filter(card => {
                let current = card.parentElement;
                while (current && current !== parent) {
                    if (this.isSummaryParent(current)) return false;
                    current = current.parentElement;
                }
                return current === parent;
            });
        },

        stableKey: function(cardId, element, type, slot) {
            const explicit = element.getAttribute &&
                (element.getAttribute('data-layout-key') ||
                    element.getAttribute('data-layout-id') ||
                    element.getAttribute('data-key') ||
                    element.id);
            if (explicit) return `${cardId}-${type}-${String(explicit).replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
            return `${cardId}-${type}-${slot}`;
        },

        titleFor: function(element, fallback) {
            if (!element) return fallback;
            const title = element.querySelector &&
                element.querySelector('.cash-summary-label, .cash-analysis-label, .collection-summary-label, .kpi-label, .modal-section-title');
            const value = title ? title.textContent : element.textContent;
            return String(value || fallback).replace(/\s+/g, ' ').trim().slice(0, 160) || fallback;
        },

        extractItems: function(container, cardId) {
            const items = [];
            const roots = this.state.originalRoots.filter(node => this.isElement(node));
            const rootSet = new Set(roots);
            const summaryParents = [];
            roots.forEach(root => {
                if (this.isSummaryParent(root)) summaryParents.push(root);
                if (root.querySelectorAll) {
                    Array.from(root.querySelectorAll(SUMMARY_SELECTOR)).forEach(parent => {
                        if (!summaryParents.includes(parent)) summaryParents.push(parent);
                    });
                }
            });
            const primaryParent = summaryParents.find(parent =>
                !this.isAuxiliaryParent(parent) && this.directSummaryCards(parent).length);
            const summaryRootsWithCards = new Set(summaryParents.filter(parent =>
                this.directSummaryCards(parent).length));
            const extracted = new Set();
            const consumed = new Set();
            let cardSlot = 0;
            let sectionSlot = 0;
            let bannerSlot = 0;

            // Do not identify a card by its rendered value.  All available and
            // missing cards consume a structural slot, keeping saved keys
            // stable when a zero/null metric appears.
            summaryParents.forEach(parent => {
                const cards = this.directSummaryCards(parent);
                cards.forEach(card => {
                    if (extracted.has(card)) return;
                    extracted.add(card);
                    const auxiliary = this.isAuxiliaryParent(parent);
                    items.push({
                        id: this.stableKey(cardId, card, 'card', cardSlot),
                        type: 'card',
                        elements: [card],
                        title: this.titleFor(card, `Карточка ${cardSlot + 1}`),
                        defaultPlacement: parent === primaryParent && !auxiliary ? 'main' : 'detail'
                    });
                    cardSlot++;
                    this.anchorAndMove(card);
                });
            });

            const isBoundary = node => this.isElement(node) && (
                this.hasClass(node, 'modal-section-title') ||
                this.hasClass(node, 'banner') ||
                this.hasClass(node, 'cash-info-banner') ||
                this.hasClass(node, 'modal-info-banner') ||
                this.isSummaryParent(node)
            );

            roots.forEach((root, rootIndex) => {
                // Summary parents are retained in the holding area so their
                // children can be restored to the exact nested positions.
                if (summaryRootsWithCards.has(root)) return;
                if (rootSet.has(root) && extracted.has(root)) return;
                if (this.hasClass(root, 'modal-section-title')) {
                    const elements = [root];
                    let index = rootIndex + 1;
                    while (index < roots.length && !isBoundary(roots[index])) {
                        elements.push(roots[index]);
                        index++;
                    }
                    if (elements.some(node => consumed.has(node))) return;
                    elements.forEach(node => consumed.add(node));
                    const visible = elements.some(node => node.style.display !== 'none');
                    items.push({
                        id: this.stableKey(cardId, root, 'section', sectionSlot),
                        type: 'section',
                        elements,
                        title: this.titleFor(root, `Секция ${sectionSlot + 1}`),
                        defaultPlacement: visible ? 'detail' : 'hidden'
                    });
                    sectionSlot++;
                    elements.forEach(node => this.anchorAndMove(node));
                    return;
                }
                if (this.hasClass(root, 'banner') || this.hasClass(root, 'cash-info-banner') ||
                    this.hasClass(root, 'modal-info-banner')) {
                    if (consumed.has(root)) return;
                    consumed.add(root);
                    items.push({
                        id: this.stableKey(cardId, root, 'banner', bannerSlot),
                        type: 'banner',
                        elements: [root],
                        title: this.titleFor(root, 'Информация'),
                        defaultPlacement: root.style.display === 'none' ? 'hidden' : 'main'
                    });
                    bannerSlot++;
                    this.anchorAndMove(root);
                    return;
                }
                if (root.tagName === 'SCRIPT' || root.tagName === 'STYLE' ||
                    root.classList && root.classList.contains('ki-layout-original-holding')) return;
                if (consumed.has(root)) return;
                consumed.add(root);
                // Empty summary shells are not user-visible items.
                if (this.isSummaryParent(root) && !root.children.length) return;
                items.push({
                    id: this.stableKey(cardId, root, 'section', sectionSlot),
                    type: 'section',
                    elements: [root],
                    title: this.titleFor(root, `Блок ${sectionSlot + 1}`),
                    defaultPlacement: root.style.display === 'none' ? 'hidden' : 'detail'
                });
                sectionSlot++;
                this.anchorAndMove(root);
            });
            return items;
        },

        generateDefaultLayout: function() {
            const config = {version: 1, items: {}};
            let hasPrimaryCard = false;
            this.state.items.forEach(item => {
                let placement = item.defaultPlacement;
                if (!PLACEMENTS.includes(placement)) placement = 'detail';
                config.items[item.id] = placement;
                if (item.type === 'card' && placement === 'main') hasPrimaryCard = true;
            });
            // Annual generic modals have no summary cards.  Keep the first
            // fact/formula section usable on the Main tab instead of opening
            // an apparently empty dialog.
            if (!hasPrimaryCard) {
                const firstSection = this.state.items.find(item =>
                    item.type === 'section' && item.defaultPlacement !== 'hidden');
                if (firstSection) {
                    config.items[firstSection.id] = 'main';
                }
            }
            return config;
        },

        mergeLayout: function(saved) {
            const defaults = this.generateDefaultLayout();
            const result = {version: 1, items: Object.assign({}, defaults.items)};
            const savedItems = saved && saved.items && typeof saved.items === 'object' ?
                saved.items : {};
            this.state.items.forEach(item => {
                if (Object.prototype.hasOwnProperty.call(savedItems, item.id) &&
                    PLACEMENTS.includes(savedItems[item.id])) {
                    result.items[item.id] = savedItems[item.id];
                }
            });
            return result;
        },

        isDirty: function() {
            if (!this.state.isConstructor || !this.state.layoutDraft || !this.state.layoutConfig) return false;
            return JSON.stringify(this.state.layoutDraft) !== JSON.stringify(this.state.layoutConfig);
        },

        mountSkeleton: function(container) {
            const wrapper = document.createElement('div');
            wrapper.className = 'ki-layout-wrapper';
            wrapper.innerHTML = `
                <div class="ki-layout-header">
                    <div class="ki-layout-tabs" role="tablist" aria-label="Разделы показателя">
                        <button type="button" class="ki-layout-tab active" role="tab" aria-selected="true" data-tab="main">Основной</button>
                        <button type="button" class="ki-layout-tab" role="tab" aria-selected="false" data-tab="detail">Детальный</button>
                    </div>
                    <div class="ki-layout-header-actions">
                        <span class="ki-layout-help">Скрытие меняет вид окна, но не финансовые данные.</span>
                        <button type="button" class="ki-layout-btn-gear" aria-label="Настроить вид" disabled>
                            ${this.gearIcon} Конструктор
                        </button>
                    </div>
                </div>
                <div class="ki-layout-load-status" role="status" aria-live="polite"></div>
                <div class="ki-layout-view">
                    <div class="ki-layout-content" role="tabpanel" data-tab-content="main" aria-label="Основной">
                        <div class="ki-layout-grid-container"></div>
                        <div class="ki-layout-sections"></div>
                        <div class="ki-layout-empty" data-empty-tab="main">Нет блоков на основной вкладке.</div>
                    </div>
                    <div class="ki-layout-content" role="tabpanel" data-tab-content="detail" hidden aria-hidden="true" aria-label="Детальный">
                        <div class="ki-layout-grid-container"></div>
                        <div class="ki-layout-sections"></div>
                        <div class="ki-layout-empty" data-empty-tab="detail">Нет блоков на детальной вкладке.</div>
                    </div>
                </div>
                <div class="ki-layout-constructor" hidden>
                    <div class="ki-constructor-topbar">
                        <div>
                            <div class="ki-constructor-title">Настройка отображения</div>
                            <div class="ki-constructor-description">Выберите строки и перенесите их между вкладками. Настройка скрывает только блоки окна.</div>
                        </div>
                        <div class="ki-constructor-actions">
                            <button type="button" class="ki-btn ki-btn-outline ki-btn-restore">По умолчанию</button>
                            <button type="button" class="ki-btn ki-btn-outline ki-btn-cancel">Отмена</button>
                            <button type="button" class="ki-btn ki-btn-primary ki-btn-save">Сохранить</button>
                        </div>
                    </div>
                    <div class="ki-constructor-batch" role="toolbar" aria-label="Действия над выбранными блоками">
                        <label class="ki-select-all"><input type="checkbox" class="ki-select-all-checkbox"> Выбрать все</label>
                        <span class="ki-selected-count" aria-live="polite">Выбрано: 0</span>
                        <button type="button" class="ki-btn ki-btn-outline ki-batch-main" disabled>В основной</button>
                        <button type="button" class="ki-btn ki-btn-outline ki-batch-detail" disabled>В детальный</button>
                        <button type="button" class="ki-btn ki-btn-outline ki-batch-hidden" disabled>Скрыть</button>
                    </div>
                    <div class="ki-constructor-workspace">
                        <div class="ki-constructor-col">
                            <div class="ki-constructor-col-header">Основной <span></span></div>
                            <div class="ki-constructor-list" data-placement="main"></div>
                        </div>
                        <div class="ki-constructor-col">
                            <div class="ki-constructor-col-header">Детальный <span></span></div>
                            <div class="ki-constructor-list" data-placement="detail"></div>
                        </div>
                        <div class="ki-constructor-col constructor-hidden">
                            <div class="ki-constructor-col-header">Скрытые <span></span></div>
                            <div class="ki-constructor-list" data-placement="hidden"></div>
                        </div>
                    </div>
                </div>
            `;
            wrapper.querySelectorAll('.ki-layout-tab').forEach(button => {
                button.addEventListener('click', () => {
                    if (this.state.isConstructor || this.state.loading || this.state.saving) return;
                    this.state.activeTab = button.dataset.tab;
                    this.updateTabs();
                });
            });
            wrapper.querySelector('.ki-layout-btn-gear').addEventListener('click', () => this.toggleConstructor());
            wrapper.querySelector('.ki-btn-cancel').addEventListener('click', () => {
                if (this.state.saving) return;
                this.state.layoutDraft = null;
                this.state.selected = new Set();
                this.toggleConstructor(false);
            });
            wrapper.querySelector('.ki-btn-restore').addEventListener('click', () => {
                if (this.state.saving) return;
                if (typeof confirm === 'function' && !confirm('Вернуть исходное распределение блоков?')) return;
                this.state.layoutDraft = this.generateDefaultLayout();
                this.state.selected = new Set();
                this.renderConstructor();
            });
            wrapper.querySelector('.ki-btn-save').addEventListener('click', () => this.saveLayout());
            wrapper.querySelector('.ki-select-all-checkbox').addEventListener('change', event => {
                if (this.state.saving) return;
                this.state.selected = event.target.checked ?
                    new Set(this.state.items.map(item => item.id)) : new Set();
                this.renderConstructor();
            });
            [
                ['.ki-batch-main', 'main'],
                ['.ki-batch-detail', 'detail'],
                ['.ki-batch-hidden', 'hidden']
            ].forEach(([selector, placement]) => wrapper.querySelector(selector).addEventListener(
                'click', () => this.moveSelected(placement)));
            container.appendChild(wrapper);
        },

        showLoadingState: function() {
            const wrapper = this.getWrapper();
            if (!wrapper) return;
            const status = wrapper.querySelector('.ki-layout-load-status');
            if (status) {
                status.hidden = false;
                status.textContent = 'Загрузка сохранённой раскладки…';
                status.className = 'ki-layout-load-status is-loading';
            }
            this.setGearEnabled(false);
        },

        showLoadError: function(error) {
            const wrapper = this.getWrapper();
            if (!wrapper) return;
            const status = wrapper.querySelector('.ki-layout-load-status');
            if (!status) return;
            status.hidden = false;
            status.className = 'ki-layout-load-status is-error';
            status.innerHTML = `<span>Не удалось загрузить раскладку: ${this.escapeHtml(error.message)}</span>
                <button type="button" class="ki-btn ki-btn-outline ki-btn-retry-layout">Повторить</button>`;
            status.querySelector('.ki-btn-retry-layout').addEventListener('click', () => this.retryLoad());
        },

        retryLoad: async function() {
            if (!this.state.activeContainer || this.state.loading || this.state.saving) return;
            const token = ++this.state.reqId;
            const url = this.state.apiUrl;
            this.state.loading = true;
            this.state.loadError = null;
            this.showLoadingState();
            this.setBusy(true);
            try {
                await this.fetchLayout(token, url);
                if (!this.isCurrent(token)) return;
                this.state.loading = false;
                this.state.ready = true;
                this.setBusy(false);
                this.renderView();
            } catch (error) {
                if (!this.isCurrent(token)) return;
                this.state.loading = false;
                this.state.loadError = error;
                this.showLoadError(error);
                this.setBusy(false);
                this.setGearEnabled(false);
            }
        },

        retry: function() {
            return this.retryLoad();
        },

        getWrapper: function() {
            return this.state.activeContainer &&
                this.state.activeContainer.querySelector('.ki-layout-wrapper');
        },

        setGearEnabled: function(enabled) {
            const wrapper = this.getWrapper();
            const button = wrapper && wrapper.querySelector('.ki-layout-btn-gear');
            if (button) button.disabled = !enabled;
        },

        setBusy: function(busy) {
            const wrapper = this.getWrapper();
            const close = document.querySelector ?
                document.querySelector('#ki-modal .modal-close') : null;
            if (close) {
                close.disabled = !!busy;
                close.setAttribute('aria-disabled', busy ? 'true' : 'false');
            }
            if (!wrapper) return;
            wrapper.classList.toggle('is-busy', !!busy);
            wrapper.querySelectorAll('button, input').forEach(control => {
                if (control.classList.contains('ki-btn-retry-layout')) {
                    control.disabled = !!this.state.loading || !!this.state.saving;
                    return;
                }
                if (control.classList.contains('ki-layout-tab')) {
                    control.disabled = !!busy || this.state.isConstructor;
                    return;
                }
                if (control.classList.contains('ki-layout-btn-gear')) {
                    control.disabled = !!busy || !this.state.ready;
                    return;
                }
                control.disabled = !!busy;
            });
        },

        updateTabs: function() {
            const wrapper = this.getWrapper();
            if (!wrapper) return;
            wrapper.querySelectorAll('.ki-layout-tab').forEach(button => {
                const active = button.dataset.tab === this.state.activeTab;
                button.classList.toggle('active', active);
                button.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            wrapper.querySelectorAll('.ki-layout-content').forEach(content => {
                const active = content.dataset.tabContent === this.state.activeTab;
                content.hidden = !active;
                content.style.display = active ? 'block' : 'none';
                content.setAttribute('aria-hidden', active ? 'false' : 'true');
            });
            this.rerenderCharts();
        },

        toggleConstructor: function(force) {
            if (!this.state.activeContainer || !this.state.ready || this.state.loading || this.state.saving) return;
            this.state.isConstructor = force === undefined ? !this.state.isConstructor : !!force;
            const wrapper = this.getWrapper();
            if (!wrapper) return;
            const gear = wrapper.querySelector('.ki-layout-btn-gear');
            const view = wrapper.querySelector('.ki-layout-view');
            const constructor = wrapper.querySelector('.ki-layout-constructor');
            if (this.state.isConstructor) {
                gear.classList.add('active');
                view.hidden = true;
                view.style.display = 'none';
                constructor.hidden = false;
                constructor.style.display = 'flex';
                this.state.layoutDraft = clone(this.state.layoutConfig);
                this.state.selected = new Set();
                this.renderConstructor();
            } else {
                gear.classList.remove('active');
                constructor.hidden = true;
                constructor.style.display = 'none';
                view.hidden = false;
                view.style.display = 'block';
                this.state.layoutDraft = null;
                this.state.selected = new Set();
                this.renderView();
                this.setBusy(false);
            }
        },

        renderView: function() {
            if (this.state.isConstructor || !this.state.layoutConfig) return;
            const wrapper = this.getWrapper();
            if (!wrapper) return;
            if (this.state.ready && !this.state.loading) {
                const status = wrapper.querySelector('.ki-layout-load-status');
                if (status) {
                    status.textContent = '';
                    status.hidden = true;
                }
            }
            this.detachRenderedItems();
            const content = {};
            ['main', 'detail'].forEach(tab => {
                const panel = wrapper.querySelector(`.ki-layout-content[data-tab-content="${tab}"]`);
                content[tab] = {
                    grid: panel.querySelector('.ki-layout-grid-container'),
                    sections: panel.querySelector('.ki-layout-sections'),
                    empty: panel.querySelector('.ki-layout-empty')
                };
                content[tab].grid.innerHTML = '';
                content[tab].sections.innerHTML = '';
            });
            const config = this.state.layoutConfig.items || {};
            this.state.items.forEach(item => {
                const placement = PLACEMENTS.includes(config[item.id]) ? config[item.id] : 'hidden';
                if (placement === 'hidden') return;
                const target = content[placement];
                if (item.type === 'card') {
                    item.elements.forEach(node => target.grid.appendChild(node));
                } else if (item.type === 'section') {
                    const group = document.createElement('div');
                    group.className = 'ki-layout-section-group';
                    item.elements.forEach(node => group.appendChild(node));
                    target.sections.appendChild(group);
                } else {
                    item.elements.forEach(node => target.sections.appendChild(node));
                }
            });
            ['main', 'detail'].forEach(tab => {
                const target = content[tab];
                target.grid.style.display = target.grid.children.length ? 'grid' : 'none';
                target.empty.hidden = target.grid.children.length + target.sections.children.length > 0;
            });
            this.updateTabs();
            this.rerenderCharts();
        },

        renderConstructor: function() {
            const wrapper = this.getWrapper();
            if (!wrapper || !this.state.layoutDraft) return;
            const lists = {
                main: wrapper.querySelector('.ki-constructor-list[data-placement="main"]'),
                detail: wrapper.querySelector('.ki-constructor-list[data-placement="detail"]'),
                hidden: wrapper.querySelector('.ki-constructor-list[data-placement="hidden"]')
            };
            Object.values(lists).forEach(list => { list.innerHTML = ''; });
            const counts = {main: 0, detail: 0, hidden: 0};
            this.state.items.forEach(item => {
                const placement = PLACEMENTS.includes(this.state.layoutDraft.items[item.id]) ?
                    this.state.layoutDraft.items[item.id] : 'hidden';
                counts[placement]++;
                const row = document.createElement('div');
                row.className = 'ki-constructor-item';
                row.dataset.itemId = item.id;
                row.tabIndex = 0;
                row.setAttribute('role', 'listitem');
                if (this.state.selected.has(item.id)) row.classList.add('selected');
                const kind = {card: 'Показатель', banner: 'Информация', section: 'Секция'}[item.type] || item.type;
                row.innerHTML = `
                    <label class="ki-constructor-select"><input type="checkbox" class="ki-item-checkbox" aria-label="Выбрать ${this.escapeHtml(item.title)}"></label>
                    <div class="ki-constructor-item-body">
                        <div class="ki-constructor-item-type">${this.escapeHtml(kind)}</div>
                        <div class="ki-constructor-item-title">${this.escapeHtml(item.title)}</div>
                    </div>
                    <div class="ki-constructor-item-actions">
                        ${placement !== 'main' ? '<button type="button" data-action="main">В основной</button>' : ''}
                        ${placement !== 'detail' ? '<button type="button" data-action="detail">В детальный</button>' : ''}
                        ${placement !== 'hidden' ? '<button type="button" data-action="hidden">Скрыть</button>' : ''}
                    </div>`;
                const checkbox = row.querySelector('.ki-item-checkbox');
                checkbox.checked = this.state.selected.has(item.id);
                checkbox.addEventListener('click', event => event.stopPropagation());
                checkbox.addEventListener('change', event => {
                    this.setSelected(item.id, event.target.checked);
                });
                row.addEventListener('click', event => {
                    if (event.target.closest('button, input, label')) return;
                    this.setSelected(item.id, !this.state.selected.has(item.id));
                });
                row.addEventListener('keydown', event => {
                    if ((event.key === 'Enter' || event.key === ' ') &&
                        !event.target.closest('button, input')) {
                        event.preventDefault();
                        this.setSelected(item.id, !this.state.selected.has(item.id));
                    }
                });
                row.querySelectorAll('button[data-action]').forEach(button => {
                    button.addEventListener('click', event => {
                        event.preventDefault();
                        event.stopPropagation();
                        this.moveDraftItem(item.id, button.dataset.action);
                    });
                });
                lists[placement].appendChild(row);
            });
            wrapper.querySelector('.ki-constructor-col:nth-child(1) span').textContent = `(${counts.main})`;
            wrapper.querySelector('.ki-constructor-col:nth-child(2) span').textContent = `(${counts.detail})`;
            wrapper.querySelector('.ki-constructor-col:nth-child(3) span').textContent = `(${counts.hidden})`;
            const selectedCount = wrapper.querySelector('.ki-selected-count');
            if (selectedCount) selectedCount.textContent = `Выбрано: ${this.state.selected.size}`;
            const all = wrapper.querySelector('.ki-select-all-checkbox');
            if (all) {
                all.checked = this.state.items.length > 0 &&
                    this.state.selected.size === this.state.items.length;
                all.indeterminate = this.state.selected.size > 0 &&
                    this.state.selected.size < this.state.items.length;
            }
            ['main', 'detail', 'hidden'].forEach(placement => {
                const button = wrapper.querySelector(`.ki-batch-${placement}`);
                if (button) button.disabled = this.state.selected.size === 0 || this.state.saving;
            });
            this.setBusy(this.state.saving);
            ['main', 'detail', 'hidden'].forEach(placement => {
                const button = wrapper.querySelector(`.ki-batch-${placement}`);
                if (button) button.disabled = this.state.selected.size === 0 || this.state.saving;
            });
        },

        setSelected: function(itemId, selected) {
            if (selected) this.state.selected.add(itemId);
            else this.state.selected.delete(itemId);
            this.renderConstructor();
        },

        moveSelected: function(placement) {
            if (!this.state.layoutDraft || this.state.saving || !PLACEMENTS.includes(placement)) return;
            this.state.selected.forEach(id => { this.state.layoutDraft.items[id] = placement; });
            this.renderConstructor();
        },

        moveDraftItem: function(itemId, targetPlacement) {
            if (!this.state.layoutDraft || this.state.saving || !PLACEMENTS.includes(targetPlacement)) return;
            this.state.layoutDraft.items[itemId] = targetPlacement;
            this.renderConstructor();
        },

        escapeHtml: function(value) {
            const div = document.createElement('div');
            div.textContent = value === null || value === undefined ? '' : String(value);
            return div.innerHTML;
        },

        getApiUrl: function(mode, cardId) {
            const config = window.KI_CONFIG || {};
            const template = config.layoutUrlTemplate || config.layoutBaseUrl;
            if (!template) return null;
            if (template.includes('__MODE__') || template.includes('__METRIC__')) {
                return template.replace('__MODE__', encodeURIComponent(mode || this.state.mode))
                    .replace('__METRIC__', encodeURIComponent(cardId || this.state.activeCardId));
            }
            return `${template.replace(/\/$/, '')}/${encodeURIComponent(mode || this.state.mode)}/${encodeURIComponent(cardId || this.state.activeCardId)}`;
        },

        getCsrfToken: function() {
            return window.KI_CONFIG ? window.KI_CONFIG.csrfToken : '';
        },

        fetchLayout: async function(token, url) {
            token = token === undefined ? this.state.reqId : token;
            url = url || this.state.apiUrl || this.getApiUrl();
            if (!url) throw new Error('Не настроен адрес раскладки показателя.');
            const response = await fetch(url, {
                headers: {Accept: 'application/json'},
                cache: 'no-store'
            });
            if (!response.ok) throw new Error(`API returned ${response.status}`);
            const data = response.json ? await response.json() :
                JSON.parse(await response.text());
            if (!this.isCurrent(token)) return;
            if (!data || !data.layout || typeof data.layout !== 'object') {
                throw new Error('Сервер вернул некорректную раскладку.');
            }
            this.state.layoutConfig = this.mergeLayout(data.layout);
            this.state.revision = Number.isInteger(data.revision) ? data.revision : 0;
        },

        saveLayout: async function() {
            if (!this.state.ready || !this.state.isConstructor || this.state.saving ||
                !this.state.layoutDraft || !this.state.apiUrl) return;
            const token = this.state.reqId;
            const url = this.state.apiUrl;
            const draft = clone(this.state.layoutDraft);
            const revision = this.state.revision;
            this.state.saving = true;
            this.state.saveError = null;
            this.setBusy(true);
            const wrapper = this.getWrapper();
            const saveButton = wrapper && wrapper.querySelector('.ki-btn-save');
            if (saveButton) saveButton.textContent = 'Сохранение…';
            try {
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCsrfToken()
                    },
                    body: JSON.stringify({layout: draft, revision})
                });
                const data = response.json ? await response.json().catch(() => ({})) :
                    JSON.parse(await response.text().catch(() => '{}'));
                if (response.status === 409) {
                    throw new Error('Конфликт revision: раскладка уже изменена в другой вкладке.');
                }
                if (!response.ok) throw new Error(data.error || `API returned ${response.status}`);
                if (!this.isCurrent(token) || this.state.apiUrl !== url) return;
                this.state.layoutConfig = this.mergeLayout(data.layout);
                this.state.revision = Number.isInteger(data.revision) ? data.revision : revision + 1;
                this.state.saving = false;
                this.state.saveError = null;
                this.state.layoutDraft = null;
                this.toggleConstructor(false);
            } catch (error) {
                if (!this.isCurrent(token)) return;
                this.state.saving = false;
                this.state.saveError = error;
                // Keep the draft intact so a transient failure cannot erase
                // the user's work.
                this.renderConstructor();
                const status = wrapper && wrapper.querySelector('.ki-layout-load-status');
                if (status) {
                    status.className = 'ki-layout-load-status is-error';
                    status.textContent = `Не удалось сохранить: ${error.message}`;
                }
                this.setBusy(false);
            } finally {
                if (this.isCurrent(token)) {
                    if (saveButton) saveButton.textContent = 'Сохранить';
                    if (this.state.isConstructor) this.renderConstructor();
                    else this.setBusy(this.state.saving);
                }
            }
        },

        rerenderCharts: function() {
            if (window.KI_App && typeof window.KI_App.rerenderVisibleCharts === 'function') {
                window.KI_App.rerenderVisibleCharts();
            }
            if (window.KI_AnnualApp && typeof window.KI_AnnualApp.rerenderVisibleCharts === 'function') {
                window.KI_AnnualApp.rerenderVisibleCharts();
            }
            // Keep compatibility with third-party renderers while the
            // first-party render jobs above handle the actual redraw.
            if (window.dispatchEvent && typeof Event === 'function') {
                window.dispatchEvent(new Event('resize'));
            }
        }
    };

    window.KI_Layout = LayoutManager;
})();