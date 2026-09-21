/* key_dashboard_layout.js
 *
 * Per-user order and visibility for the two top-level KPI card grids.
 * This controller intentionally does not share state with key_indicator_layout.js:
 * metric-modal preferences and dashboard preferences have different lifecycles.
 */
(function() {
    'use strict';

    const FALLBACK_IDS = {
        operational: ['cash', 'cash_dz', 'revenue', 'collection', 'ap', 'cogs',
            'profit', 'credits', 'salary', 'capex'],
        annual: ['revenue', 'cogs', 'net_profit', 'profitability', 'cash',
            'ar', 'ap', 'equity', 'assets', 'current_liquidity']
    };
    const FALLBACK_TITLES = {
        cash: 'Денежные средства',
        cash_dz: 'Дебиторская задолженность',
        revenue: 'Выручка',
        collection: 'Уровень собираемости',
        ar: 'Дебиторская задолженность',
        ap: 'Кредиторская задолженность',
        cogs: 'Себестоимость продаж',
        profit: 'Прибыль от продаж',
        credits: 'Кредиты и займы',
        salary: 'Расчёты по оплате труда',
        capex: 'Капитальные вложения',
        net_profit: 'Чистая прибыль (убыток)',
        profitability: 'Рентабельность продаж',
        equity: 'Собственный капитал',
        assets: 'Активы',
        current_liquidity: 'Текущая ликвидность'
    };
    const clone = value => JSON.parse(JSON.stringify(value));

    const state = {
        companyId: null,
        loaded: {},
        loading: {},
        errors: {},
        layouts: {},
        revisions: {},
        tokens: {},
        controllers: {},
        dialog: null,
        dialogMode: null,
        draft: null,
        savedDraft: null,
        saving: false,
        lastFocused: null,
        initialized: false
    };

    const config = () => window.KI_CONFIG || {};
    const modeOf = mode => mode === 'annual' ? 'annual' : 'operational';
    const serverModeOf = mode => modeOf(mode) === 'annual' ? 'annual' : 'osv';
    const currentMode = () => window.KI_App && window.KI_App.state &&
        window.KI_App.state.mode === 'annual' ? 'annual' : 'operational';

    const escapeHtml = value => {
        const div = document.createElement('div');
        div.textContent = value === null || value === undefined ? '' : String(value);
        return div.innerHTML;
    };

    const DashboardLayout = {
        state,

        init() {
            if (state.initialized) return;
            state.initialized = true;
            state.companyId = Number(config().companyId) || null;
            this.ensureDialog();
            const gear = document.getElementById('ki-dashboard-layout-gear');
            if (gear) gear.addEventListener('click', () => this.open());
            document.addEventListener('keydown', event => {
                if (!state.dialog || state.dialog.hidden) return;
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.close();
                } else if (event.key === 'Tab') {
                    if (typeof state.dialog.querySelectorAll !== 'function') return;
                    const focusable = Array.from(state.dialog.querySelectorAll(
                        'button, input, [href], [tabindex]:not([tabindex="-1"])'
                    )).filter(element => !element.disabled);
                    if (!focusable.length) return;
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];
                    if (event.shiftKey && document.activeElement === first) {
                        event.preventDefault();
                        last.focus();
                    } else if (!event.shiftKey && document.activeElement === last) {
                        event.preventDefault();
                        first.focus();
                    }
                }
            });
            // This capture guard runs before KI_App changes mode, so a dirty
            // constructor cannot be silently discarded by a source change.
            document.addEventListener('click', event => {
                const button = event.target && event.target.closest ?
                    event.target.closest('.mode-btn') : null;
                if (!button || !button.dataset) return;
                if (!this.prepareModeSwitch(button.dataset.mode)) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    return;
                }
                // KI_App changes its mode in its own click listener.  Queue
                // the preference load after that listener so the gear stays
                // available even when annual data has no cards yet.
                if (typeof setTimeout === 'function') {
                    setTimeout(() => {
                        this.syncCompany();
                        this.ensureLoaded(button.dataset.mode);
                        this.setGearState();
                    }, 0);
                }
            }, true);
            if (typeof window.addEventListener === 'function') {
                window.addEventListener('message', () => {
                    // KI_App receives the same message first and replaces its
                    // tenant config.  A fresh render will also call this path;
                    // this eagerly starts the new tenant's preference request.
                    this.syncCompany();
                    this.ensureLoaded(currentMode());
                });
            }
            this.installRenderHooks();
            this.ensureLoaded(currentMode());
        },

        installRenderHooks() {
            const wrap = (app, method, mode) => {
                if (!app || typeof app[method] !== 'function' ||
                    app[`__dashboardLayoutWrapped_${method}`]) return;
                const original = app[method];
                const self = this;
                app[method] = function() {
                    const result = original.apply(this, arguments);
                    self.syncCompany();
                    self.ensureLoaded(mode);
                    self.apply(mode);
                    return result;
                };
                app[`__dashboardLayoutWrapped_${method}`] = true;
            };
            wrap(window.KI_App, 'renderCards', 'operational');
            wrap(window.KI_AnnualApp, 'renderCards', 'annual');
        },

        syncCompany() {
            const next = Number(config().companyId) || null;
            if (state.companyId === null) {
                state.companyId = next;
                return;
            }
            if (next && next !== state.companyId) {
                this.invalidate();
                state.companyId = next;
            }
        },

        invalidate() {
            Object.keys(state.controllers).forEach(mode => {
                if (state.controllers[mode]) state.controllers[mode].abort();
            });
            Object.keys(state.tokens).forEach(mode => { state.tokens[mode] += 1; });
            state.loaded = {};
            state.loading = {};
            state.errors = {};
            state.layouts = {};
            state.revisions = {};
            state.controllers = {};
            state.saving = false;
            this.close(true);
            this.setGearState();
        },

        availableIds(mode) {
            const ids = [];
            const add = id => {
                if (typeof id === 'string' && id && !ids.includes(id)) ids.push(id);
            };
            let data = null;
            if (modeOf(mode) === 'annual') {
                data = window.KI_AnnualApp && window.KI_AnnualApp.state &&
                    window.KI_AnnualApp.state.data;
            } else {
                data = window.KI_App && window.KI_App.state && window.KI_App.state.data;
            }
            if (data && Array.isArray(data.cards)) data.cards.forEach(card => add(card && card.id));
            (FALLBACK_IDS[modeOf(mode)] || []).forEach(add);
            return ids;
        },

        labels(mode) {
            const result = Object.assign({}, FALLBACK_TITLES);
            let data = null;
            if (modeOf(mode) === 'annual') {
                data = window.KI_AnnualApp && window.KI_AnnualApp.state &&
                    window.KI_AnnualApp.state.data;
            } else {
                data = window.KI_App && window.KI_App.state && window.KI_App.state.data;
            }
            if (data && Array.isArray(data.cards)) data.cards.forEach(card => {
                if (card && card.id && card.title) result[card.id] = card.title;
            });
            return result;
        },

        defaultLayout(mode) {
            const ids = this.availableIds(mode);
            const items = {};
            ids.forEach(id => { items[id] = 'main'; });
            return {version: 1, items, order: ids.slice()};
        },

        mergeLayout(mode, saved) {
            const defaults = this.defaultLayout(mode);
            const result = {
                version: 1,
                items: Object.assign({}, defaults.items),
                order: []
            };
            const savedItems = saved && saved.items && typeof saved.items === 'object' ?
                saved.items : {};
            Object.keys(result.items).forEach(id => {
                if (savedItems[id] === 'hidden') result.items[id] = 'hidden';
                else if (savedItems[id] === 'main' || savedItems[id] === 'detail') {
                    result.items[id] = 'main';
                }
            });
            const known = new Set(Object.keys(result.items));
            const append = id => {
                if (typeof id === 'string' && known.has(id) && !result.order.includes(id)) {
                    result.order.push(id);
                }
            };
            if (saved && Array.isArray(saved.order)) saved.order.forEach(append);
            defaults.order.forEach(append);
            return result;
        },

        getUrl(mode) {
            const template = config().dashboardLayoutURL ||
                config().dashboardLayoutUrl;
            if (typeof template !== 'string' || !template) return null;
            return template
                .replace('__MODE__', encodeURIComponent(serverModeOf(mode)))
                .replace('__METRIC__', encodeURIComponent('__dashboard__'));
        },

        getCsrfToken() {
            return config().csrfToken || '';
        },

        ensureLoaded(mode) {
            this.installRenderHooks();
            this.syncCompany();
            mode = modeOf(mode);
            if (state.loaded[mode] || state.loading[mode]) {
                this.setGearState();
                return;
            }
            const url = this.getUrl(mode);
            if (!url) {
                state.errors[mode] = new Error('Не настроен адрес раскладки карточек.');
                this.setGearState();
                return;
            }
            const token = (state.tokens[mode] || 0) + 1;
            state.tokens[mode] = token;
            const companyId = state.companyId;
            const controller = new AbortController();
            state.controllers[mode] = controller;
            state.loading[mode] = true;
            delete state.errors[mode];
            this.setGearState();
            fetch(url, {
                signal: controller.signal,
                cache: 'no-store',
                headers: {Accept: 'application/json'}
            }).then(async response => {
                if (response.status === 401 ||
                    (response.redirected && response.url &&
                     response.url.includes('/login'))) {
                    window.location.href = '/login?next=' +
                        encodeURIComponent(window.location.pathname);
                    return null;
                }
                const text = await response.text();
                let data = {};
                try { data = text ? JSON.parse(text) : {}; } catch (error) {
                    throw new Error('Сервер вернул некорректную раскладку карточек.');
                }
                if (!response.ok) throw new Error(
                    data.error || `Не удалось загрузить настройки (${response.status})`);
                if (!data.layout || typeof data.layout !== 'object') {
                    throw new Error('Сервер вернул некорректную раскладку карточек.');
                }
                if (data.layout.version !== 1 ||
                    !data.layout.items || typeof data.layout.items !== 'object' ||
                    Array.isArray(data.layout.items)) {
                    throw new Error('Сервер вернул неподдерживаемую раскладку карточек.');
                }
                return data;
            }).then(data => {
                if (!data || token !== state.tokens[mode] ||
                    companyId !== state.companyId) return;
                state.layouts[mode] = this.mergeLayout(mode, data.layout);
                state.revisions[mode] = Number.isInteger(data.revision) ? data.revision : 0;
                state.loaded[mode] = true;
                state.loading[mode] = false;
                delete state.errors[mode];
                this.apply(mode);
                if (state.dialogMode === mode && state.dialog && !state.draft) {
                    state.draft = clone(state.layouts[mode]);
                    state.savedDraft = clone(state.layouts[mode]);
                    this.renderDialog();
                }
            }).catch(error => {
                if (token !== state.tokens[mode] || companyId !== state.companyId ||
                    error.name === 'AbortError') return;
                state.loading[mode] = false;
                state.errors[mode] = error;
                // Keep default-rendered cards intact.  A failed GET must never
                // turn authentic dashboard data into a hidden/empty screen.
                this.setGearState();
                if (state.dialogMode === mode && state.dialog && !state.draft) {
                    this.renderDialog();
                }
            }).finally(() => {
                if (token === state.tokens[mode] && companyId === state.companyId) {
                    delete state.controllers[mode];
                    state.loading[mode] = false;
                    this.setGearState();
                }
            });
        },

        setGearState() {
            const gear = document.getElementById('ki-dashboard-layout-gear');
            if (!gear) return;
            const mode = currentMode();
            const tabs = gear.closest ? gear.closest('.tabs-bar') : null;
            // Annual mode historically hides the source tabs.  The dashboard
            // constructor remains the recovery path even when every card is
            // hidden, so keep its existing bar/opener available in both modes.
            if (tabs && mode === 'annual') tabs.style.display = 'flex';
            // Allow opening the loading/error state instead of ignoring clicks.
            gear.disabled = state.saving;
            gear.title = state.errors[mode] ?
                'Настройки карточек не загружены — нажмите для повтора' :
                'Настроить карточки показателей';
        },

        apply(mode) {
            mode = modeOf(mode);
            if (!state.loaded[mode] || !state.layouts[mode]) return;
            const grid = document.getElementById(mode === 'annual' ?
                'ki-annual-cards-grid' : 'ki-cards-grid');
            if (!grid) return;
            const layout = this.mergeLayout(mode, state.layouts[mode]);
            state.layouts[mode] = layout;
            const cards = new Map();
            Array.from(grid.children).forEach(card => {
                if (card.dataset && card.dataset.metric) cards.set(card.dataset.metric, card);
            });
            const ordered = [];
            layout.order.forEach(id => {
                if (cards.has(id)) ordered.push(cards.get(id));
            });
            cards.forEach(card => {
                if (!ordered.includes(card)) ordered.push(card);
            });
            ordered.forEach(card => {
                const visible = layout.items[card.dataset.metric] !== 'hidden';
                card.hidden = !visible;
                grid.appendChild(card);
            });
        },

        ensureDialog() {
            if (!document.body || typeof document.createElement !== 'function') return;
            if (document.getElementById('ki-dashboard-layout-dialog')) {
                state.dialog = document.getElementById('ki-dashboard-layout-dialog');
                return;
            }
            const dialog = document.createElement('div');
            dialog.id = 'ki-dashboard-layout-dialog';
            dialog.className = 'ki-dashboard-layout-dialog';
            dialog.hidden = true;
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('aria-labelledby', 'ki-dashboard-layout-title');
            dialog.innerHTML = `
                <div class="ki-dashboard-layout-panel" role="document">
                  <div class="ki-dashboard-layout-head">
                    <div>
                      <h2 class="ki-dashboard-layout-title" id="ki-dashboard-layout-title">Настройка карточек</h2>
                      <p class="ki-dashboard-layout-description">Отметьте видимые карточки и измените их порядок.</p>
                    </div>
                    <button type="button" class="ki-dashboard-layout-close"
                            data-action="close" aria-label="Закрыть">×</button>
                  </div>
                  <div class="ki-dashboard-layout-status" role="status" aria-live="polite"></div>
                  <div class="ki-dashboard-layout-list"></div>
                  <div class="ki-dashboard-layout-footer">
                    <button type="button" class="danger" data-action="reset">Сбросить</button>
                    <button type="button" data-action="cancel">Отмена</button>
                    <button type="button" class="primary" data-action="save">Сохранить</button>
                  </div>
                </div>`;
            document.body.appendChild(dialog);
            state.dialog = dialog;
            dialog.addEventListener('click', event => {
                if (event.target === dialog) this.close();
                const action = event.target && event.target.dataset &&
                    event.target.dataset.action;
                if (action === 'cancel') this.close();
                if (action === 'close') this.close();
                if (action === 'save') this.save();
                if (action === 'reset') this.reset();
                if (event.target.closest && event.target.closest('[data-move]')) {
                    const button = event.target.closest('[data-move]');
                    this.move(button.dataset.move, button.dataset.direction);
                }
                if (event.target.matches && event.target.matches('[data-card-visible]')) {
                    this.setVisibility(event.target.dataset.cardVisible,
                        event.target.checked);
                }
            });
        },

        open() {
            this.syncCompany();
            const mode = currentMode();
            state.dialogMode = mode;
            state.lastFocused = document.activeElement;
            this.ensureLoaded(mode);
            if (state.loading[mode]) {
                this.renderDialog();
                state.dialog.hidden = false;
                const close = state.dialog.querySelector('.ki-dashboard-layout-close');
                if (close) close.focus();
                return;
            }
            if (!state.loaded[mode] || !state.layouts[mode]) {
                this.renderDialog();
                state.dialog.hidden = false;
                const retry = state.dialog.querySelector('[data-action="retry"]');
                if (retry) retry.focus();
                return;
            }
            state.draft = clone(state.layouts[mode]);
            state.savedDraft = clone(state.layouts[mode]);
            state.saving = false;
            this.renderDialog();
            state.dialog.hidden = false;
            const first = state.dialog.querySelector('[data-card-visible]');
            if (first) first.focus();
        },

        close(force) {
            if (!state.dialog || state.dialog.hidden) return true;
            if (!force && state.draft && state.savedDraft &&
                JSON.stringify(state.draft) !== JSON.stringify(state.savedDraft)) {
                if (typeof confirm !== 'function' ||
                    !confirm('Есть несохраненные изменения. Закрыть без сохранения?')) {
                    return false;
                }
            }
            if (state.saving && !force) return false;
            state.dialog.hidden = true;
            state.draft = null;
            state.savedDraft = null;
            state.dialogMode = null;
            state.saving = false;
            if (state.lastFocused && typeof state.lastFocused.focus === 'function') {
                state.lastFocused.focus();
            }
            this.setGearState();
            return true;
        },

        prepareModeSwitch() {
            if (!state.dialog || state.dialog.hidden) return true;
            return this.close(false);
        },

        prepareCompanySwitch() {
            if (!this.prepareModeSwitch()) return false;
            this.invalidate();
            return true;
        },

        isDirty() {
            return !!(state.draft && state.savedDraft &&
                JSON.stringify(state.draft) !== JSON.stringify(state.savedDraft));
        },

        setVisibility(id, visible) {
            if (!state.draft || !Object.prototype.hasOwnProperty.call(state.draft.items, id)) return;
            state.draft.items[id] = visible ? 'main' : 'hidden';
            this.renderDialog();
        },

        move(id, direction) {
            if (!state.draft || !Array.isArray(state.draft.order)) return;
            const index = state.draft.order.indexOf(id);
            const target = direction === 'up' ? index - 1 : index + 1;
            if (index < 0 || target < 0 || target >= state.draft.order.length) return;
            [state.draft.order[index], state.draft.order[target]] =
                [state.draft.order[target], state.draft.order[index]];
            this.renderDialog();
        },

        reset() {
            if (typeof confirm === 'function' &&
                !confirm('Сбросить порядок и видимость карточек?')) return;
            state.draft = this.defaultLayout(state.dialogMode);
            this.renderDialog();
        },

        renderDialog() {
            if (!state.dialog) return;
            const mode = state.dialogMode || currentMode();
            const status = state.dialog.querySelector('.ki-dashboard-layout-status');
            const list = state.dialog.querySelector('.ki-dashboard-layout-list');
            const save = state.dialog.querySelector('[data-action="save"]');
            const reset = state.dialog.querySelector('[data-action="reset"]');
            const title = state.dialog.querySelector('.ki-dashboard-layout-title');
            title.textContent = mode === 'annual' ?
                'Настройка годовых карточек' : 'Настройка карточек ОСВ';
            list.innerHTML = '';
            const error = state.errors[mode];
            if (error && !state.draft) {
                status.className = 'ki-dashboard-layout-status error';
                status.innerHTML = `<span>Не удалось загрузить настройки: ${escapeHtml(error.message)}</span>
                    <button type="button" data-action="retry">Повторить</button>`;
                const retry = status.querySelector('[data-action="retry"]');
                if (retry) retry.addEventListener('click', () => {
                    delete state.errors[mode];
                    this.ensureLoaded(mode);
                    this.renderDialog();
                });
                save.disabled = true;
                reset.disabled = true;
                return;
            }
            if (state.loading[mode]) {
                status.className = 'ki-dashboard-layout-status';
                status.textContent = 'Загрузка сохранённых настроек…';
                save.disabled = true;
                reset.disabled = true;
                return;
            }
            if (state.saving) {
                status.className = 'ki-dashboard-layout-status';
                status.textContent = 'Сохранение…';
            } else if (!this.isDirty()) {
                status.className = 'ki-dashboard-layout-status';
                status.textContent = '';
            }
            if (!state.draft) {
                save.disabled = true;
                reset.disabled = true;
                return;
            }
            save.disabled = state.saving;
            reset.disabled = state.saving;
            const labels = this.labels(mode);
            state.draft.order.forEach((id, index) => {
                const row = document.createElement('div');
                const visible = state.draft.items[id] !== 'hidden';
                row.className = 'ki-dashboard-layout-row' + (visible ? '' : ' is-hidden');
                row.innerHTML = `
                    <label class="ki-dashboard-layout-label">
                      <input type="checkbox" data-card-visible="${escapeHtml(id)}"
                             ${visible ? 'checked' : ''}>
                      <span></span>
                    </label>
                    <div class="ki-dashboard-layout-row-actions">
                      <button type="button" data-move="${escapeHtml(id)}" data-direction="up"
                              aria-label="Переместить выше" ${index === 0 ? 'disabled' : ''}>↑</button>
                      <button type="button" data-move="${escapeHtml(id)}" data-direction="down"
                              aria-label="Переместить ниже"
                              ${index === state.draft.order.length - 1 ? 'disabled' : ''}>↓</button>
                    </div>`;
                const label = row.querySelector('span');
                label.textContent = labels[id] || id;
                list.appendChild(row);
            });
        },

        async save() {
            const mode = state.dialogMode;
            if (!mode || !state.draft || state.saving || !state.loaded[mode]) return;
            const url = this.getUrl(mode);
            if (!url) return;
            const token = state.tokens[mode];
            const companyId = state.companyId;
            const draft = this.mergeLayout(mode, clone(state.draft));
            state.saving = true;
            this.renderDialog();
            try {
                const response = await fetch(url, {
                    method: 'PUT',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCsrfToken()
                    },
                    body: JSON.stringify({
                        layout: draft,
                        revision: state.revisions[mode] || 0
                    })
                });
                let data = {};
                if (typeof response.json === 'function') {
                    data = await response.json().catch(() => ({}));
                } else if (typeof response.text === 'function') {
                    const text = await response.text();
                    try { data = text ? JSON.parse(text) : {}; } catch (error) {
                        data = {};
                    }
                }
                if (token !== state.tokens[mode] || companyId !== state.companyId) return;
                if (response.status === 409) {
                    throw new Error('Настройки уже изменены в другой вкладке. Повторите загрузку.');
                }
                if (!response.ok) throw new Error(
                    data.error || `Не удалось сохранить настройки (${response.status})`);
                if (!data.layout || typeof data.layout !== 'object') {
                    throw new Error('Сервер вернул некорректные сохранённые настройки.');
                }
                state.layouts[mode] = this.mergeLayout(mode, data.layout);
                state.revisions[mode] = Number.isInteger(data.revision) ?
                    data.revision : (state.revisions[mode] || 0) + 1;
                state.loaded[mode] = true;
                state.saving = false;
                state.draft = null;
                state.savedDraft = null;
                this.apply(mode);
                this.close(true);
            } catch (error) {
                if (token !== state.tokens[mode] || companyId !== state.companyId ||
                    error.name === 'AbortError') return;
                state.saving = false;
                // Keep the draft and server revision untouched after any error.
                const status = state.dialog && state.dialog.querySelector(
                    '.ki-dashboard-layout-status');
                if (status) {
                    status.className = 'ki-dashboard-layout-status error';
                    status.textContent = `Не удалось сохранить настройки: ${error.message}`;
                }
                this.renderDialog();
            } finally {
                if (token === state.tokens[mode] && companyId === state.companyId) {
                    state.saving = false;
                    this.setGearState();
                }
            }
        }
    };

    window.KI_DashboardLayout = DashboardLayout;
    if (typeof document !== 'undefined') {
        document.addEventListener('DOMContentLoaded', () => DashboardLayout.init());
    }
})();