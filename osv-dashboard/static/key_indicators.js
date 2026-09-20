/* key_indicators.js */
(function() {
    // Also accept the lexical configuration from an already cached template.
    const config = window.KI_CONFIG || (typeof KI_CONFIG !== 'undefined' ? KI_CONFIG : null);
    
    const state = {
        period: 'year',
        date: null,
        dateFrom: null,
        quarterAnchor: null,
        periodPickerOpen: false,
        pendingDateFrom: null,
        pendingDate: null,
        pickerYearStart: null,
        data: null,
        lastFocused: null,
        modalCardId: null,
        pendingModalCardId: null,
        companySwitchToken: 0,
        companySwitching: false,
        companySwitchPeriodFallback: null,
        reqId: 0,
        aiReport: null,
        tooltipsEnabled: true,
        chartJobs: {},
        modalChart: { activeId: null, pinnedIds: new Set(), gapMode: 1, timelineMode: 'week' },
        modalCharts: {},
        revenueSelection: new Set(['total', 'yoy'])
    };
    const periodSelectionStorageKey = () => {
        const configuredId = Number(config && config.companyId);
        const urlMatch = String(config && config.dataUrl || '').match(
            /\/company\/(\d+)\/key-indicators\/data(?:[/?#]|$)/);
        const companyId = Number.isInteger(configuredId) && configuredId > 0
            ? configuredId : urlMatch ? Number(urlMatch[1]) : 'default';
        return `brain4sales:key-indicators:period-selection:${companyId}`;
    };
    const tooltipPreferenceStorageKey = 'brain4sales:key-indicators:tooltips-enabled';

    const metricChartColor = id => {
        if (id === '__main__') return '#2563EB';
        const colors = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6',
            '#EC4899', '#06B6D4', '#F43F5E'];
        let hash = 0;
        String(id).split('').forEach(char => {
            hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
        });
        return colors[Math.abs(hash) % colors.length];
    };
    
    const svgIcon = (path) => `<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><path d="${path}"/></svg>`;

    const cardDefinitions = [
        { id: 'cash', icon: '<span aria-hidden="true">₽</span>' },
        { id: 'cash_dz', icon: '<span aria-hidden="true">₽</span>' },
        { id: 'revenue', icon: svgIcon('M3 3v18h18M3 16l6-6 4 4 8-8') },
        { id: 'collection', icon: svgIcon('M3 10h18M7 15h1m4 0h1M5 6h14a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2z') },
        { id: 'ar', icon: svgIcon('M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4') },
        { id: 'ap', icon: svgIcon('M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12') },
        { id: 'cogs', icon: svgIcon('M3 3v18h18M3 8l6 6 4-4 8 8') },
        { id: 'profit', icon: svgIcon('M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z') },
        { id: 'credits', icon: svgIcon('M3 21h18M5 21V5a2 2 0 012-2h10a2 2 0 012 2v16M9 21v-4a2 2 0 012-2h2a2 2 0 012 2v4') },
        { id: 'salary', icon: svgIcon('M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 7a4 4 0 100-8 4 4 0 000 8zm14 14v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75') },
        { id: 'capex', icon: svgIcon('M2 20h20M5 20V8l7-5 7 5v12M9 20v-5h6v5M7 11h2M15 11h2') }
    ];
    const isCashMetric = id => id === 'cash' || id === 'cash_dz';

    const esc = (str) => {
        if (str === null || str === undefined) return '';
        return String(str).replace(/[&<>'"]/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        })[m]);
    };

    const chartDate = item => {
        const value = item && (item.date || item.label);
        let match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
        if (match) return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
        match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(String(value || ''));
        return match ? Date.UTC(Number(match[3]), Number(match[2]) - 1, Number(match[1])) : null;
    };

    const chartPeriodBounds = dates => {
        const validDates = (dates || []).filter(value =>
            value !== null && Number.isFinite(value));
        const fallbackStart = validDates.length ? Math.min(...validDates) : null;
        const fallbackEnd = validDates.length ? Math.max(...validDates) : null;
        const period = state.data && state.data.period || {};
        const requestedStart = chartDate({
            date: period.start || period.date_from
        });
        const requestedEnd = chartDate({
            date: period.end || period.date
        });
        if (requestedStart !== null && requestedEnd !== null &&
                requestedStart <= requestedEnd) {
            return {start: requestedStart, end: requestedEnd};
        }
        return {start: fallbackStart, end: fallbackEnd};
    };

    // Keep non-ОСВ/annual callers index-based.  Daily ОСВ series carry an
    // exact date, so a missing calendar day occupies its real horizontal
    // distance rather than being compressed next to its neighbours.
    const chartTimeline = series => {
        const dates = series.map(chartDate);
        const calendar = dates.length > 0 && dates.every(value => value !== null);
        if (!calendar) {
            return {
                calendar: false,
                day: () => null,
                x: (index, width) => series.length <= 1
                    ? width / 2
                    : index * width / (series.length - 1)
            };
        }
        const bounds = chartPeriodBounds(dates);
        const first = bounds.start;
        const last = bounds.end;
        const span = Math.max(1, Math.round((last - first) / 86400000));
        return {
            calendar: true,
            day: index => Math.round((dates[index] - first) / 86400000),
            x: index => (Math.round((dates[index] - first) / 86400000) / span)
        };
    };

    // Return separate solid and dashed paths.  A dashed portion only joins
    // two known samples with unavailable slots between them; it never
    // extrapolates before the first or after the last known sample.
    const hasNumericValue = value => value !== null &&
        value !== undefined &&
        !(typeof value === 'string' && value.trim() === '') &&
        Number.isFinite(Number(value));

    // Details and components can arrive as separate JSON objects even when
    // they describe the same accounting component.  Use stable accounting
    // identity plus the authentic series signature, rather than object
    // identity, while keeping same-label items from other categories apart.
    const metricSeriesSignature = item => JSON.stringify((item &&
        item.series || []).map(point => [
        point && point.date, point && point.label, point && point.value,
        point && point.granularity, point && point.unit
    ]));
    const metricSelectableIdentity = (item, category) => {
        const pointUnit = item && item.series && item.series.find(point =>
            point && point.unit);
        const family = category === 'detail' || category === 'details' ||
            category === 'component' || category === 'components'
            ? 'component' : category;
        return JSON.stringify([
            family,
            item && item.label,
            item && item.account,
            item && item.side,
            (item && item.unit) || (pointUnit && pointUnit.unit) || '',
            metricSeriesSignature(item)
        ]);
    };

    // Keep the ids used by the chart and by rendered tiles in one place.  The
    // numeric fallback is retained for old API payloads and saved in-memory
    // modal state; providers may supply series_id when they have a durable
    // identifier for the component.
    const metricSeriesId = (item, category, index) => {
        if (category === 'main') return '__main__';
        const prefix = category === 'details' ? 'detail' :
            category === 'components' ? 'component' : category;
        const explicit = item && (item.series_id !== undefined && item.series_id !== null
            ? item.series_id : item.seriesId);
        if (explicit !== null && explicit !== undefined && String(explicit)) {
            return `${prefix}:${String(explicit)}`;
        }
        return prefix === 'breakdown' ? String(index) : `${prefix}:${index}`;
    };

    const metricSeriesItems = card => {
        const result = [];
        const seen = new Set();
        const add = (item, category, index, isMain) => {
            if (!item || !item.series || !item.series.length) return;
            const identity = metricSelectableIdentity(item, category);
            if (seen.has(identity)) return;
            seen.add(identity);
            result.push({
                id: metricSeriesId(item, category, index),
                label: isMain ? 'Итого' : item.label,
                item: isMain ? card : item,
                data: item.series,
                isMain: !!isMain
            });
        };
        add(card, 'main', 0, true);
        (card.breakdown || []).forEach((item, index) =>
            add(item, 'breakdown', index, false));
        ['components', 'details', 'analytics'].forEach(category => {
            (card[category] || []).forEach((item, index) =>
                add(item, category === 'details' ? 'detail' :
                    category === 'components' ? 'component' : category, index, false));
        });
        if (card.accrual_series && card.accrual_series.length) {
            add({
                label: 'Начисления работникам',
                series: card.accrual_series,
                series_id: 'credit-turnover'
            }, 'salary', 0, false);
        }
        return result;
    };

    const metricTileItems = card => {
        const result = [];
        const seen = new Set();
        const add = (item, category, index, isMain) => {
            if (!item || (!isMain && !item.label)) return;
            const identity = metricSelectableIdentity(item, category);
            if (seen.has(identity)) return;
            seen.add(identity);
            result.push({
                id: metricSeriesId(item, category, index),
                label: isMain ? 'Итого' : item.label,
                item: isMain ? card : item,
                hasSeries: !!(item.series && item.series.length),
                isMain: !!isMain
            });
        };
        if (card.series && card.series.length) add(card, 'main', 0, true);
        (card.breakdown || []).forEach((item, index) =>
            add(item, 'breakdown', index, false));
        ['components', 'details', 'analytics'].forEach(category => {
            (card[category] || []).forEach((item, index) =>
                add(item, category === 'details' ? 'detail' :
                    category === 'components' ? 'component' : category, index, false));
        });
        return result;
    };

    const metricSeriesIdForItem = (card, item) => {
        if (!item) return '';
        const match = metricSeriesItems(card).find(series => series.item === item);
        return match ? match.id : '';
    };

    const metricSeriesIdForLabel = (card, collection, label) => {
        const item = (card[collection] || []).find(candidate =>
            candidate && candidate.label === label);
        return metricSeriesIdForItem(card, item);
    };

    const chartLinePaths = (points, x, y, valueOf, timeline, connection) => {
        // The API declares the point granularity.  Only a daily series may
        // bridge an absent calendar day; monthly/period aggregates retain
        // their authentic slots and do not receive a daily gap bridge.
        const declaredGranularity = points.find(point => point &&
            point.granularity)?.granularity;
        const known = [];
        const solid = [];
        const dashed = [];
        points.forEach((point, index) => {
            const value = valueOf(point);
            if (!hasNumericValue(value)) return;
            known.push({index, value: Number(value), point});
        });
        known.forEach((current, knownIndex) => {
            const previous = knownIndex ? known[knownIndex - 1] : null;
            const consecutive = previous && (
                declaredGranularity === 'day' && timeline.calendar
                    ? timeline.day(current.index) - timeline.day(previous.index) === 1
                    : current.index - previous.index === 1
            );
            const kind = previous && declaredGranularity &&
                declaredGranularity !== 'day' && !consecutive
                ? 'none'
                : (previous && connection
                    ? connection(previous, current, consecutive)
                    : (previous ? (consecutive ? 'solid' : 'gap') : 'start'));
            if (!previous || kind === 'start' || kind === 'none' || kind === 'gap') {
                solid.push(` M ${x(current.index).toFixed(1)} ${y(current.value).toFixed(1)}`);
            } else if (kind === 'solid') {
                solid.push(` L ${x(current.index).toFixed(1)} ${y(current.value).toFixed(1)}`);
            }
            if (previous && kind === 'gap') {
                dashed.push(` M ${x(previous.index).toFixed(1)} ${y(previous.value).toFixed(1)}` +
                    ` L ${x(current.index).toFixed(1)} ${y(current.value).toFixed(1)}`);
            }
        });
        return {solid: solid.join(''), dashed: dashed.join('')};
    };

    let abortController = null;

    const App = {
        state: state,
        displayedPeriodLabel() {
            const period = (state.data && state.data.period) || {};
            const formatDate = value => {
                const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
                return match ? `${match[3]}.${match[2]}.${match[1]}` : null;
            };
            const start = formatDate(period.start);
            const end = formatDate(period.end);
            if (start && end) return start === end ? start : `${start} — ${end}`;
            return period.label || 'Период не указан';
        },

        updateModalSubtitle() {
            const label = state.mode === 'annual' && window.KI_AnnualApp &&
                typeof window.KI_AnnualApp.displayedPeriodLabel === 'function' ?
                window.KI_AnnualApp.displayedPeriodLabel() : this.displayedPeriodLabel();
            const source = state.mode === 'annual' ? 'Отчета' : 'ОСВ';
            document.getElementById('ki-modal-subtitle').textContent =
                `Детализация по данным ${source} · ${label}`;
        },

        isModalOpen() {
            const modal = document.getElementById('ki-modal');
            return !!(modal && modal.classList.contains('visible'));
        },

        normalizeCompanyConfig(next) {
            if (!next || typeof next !== 'object') {
                throw new Error('Сервер не передал данные предприятия.');
            }
            const companyId = Number(next.companyId);
            if (!Number.isInteger(companyId) || companyId <= 0 ||
                !String(next.companyName || '').trim()) {
                throw new Error('Сервер передал некорректные данные предприятия.');
            }
            const required = [
                'pageUrl', 'dataUrl', 'annualDataUrl', 'mappingsUrl',
                'costMappingsUrl', 'debtorMappingsUrl'
            ];
            const source = Object.assign({}, next, {
                layoutBaseURL: next.layoutBaseURL || next.layoutBaseUrl
            });
            if (!source.layoutBaseURL) {
                throw new Error('Сервер не передал полный набор адресов показателей.');
            }
            required.push('layoutBaseURL');
            const urls = {};
            required.forEach(name => {
                if (typeof source[name] !== 'string' || !source[name]) {
                    throw new Error('Сервер не передал полный набор адресов показателей.');
                }
                let parsed;
                try {
                    parsed = new URL(source[name], window.location.origin);
                } catch (error) {
                    throw new Error('Сервер передал некорректный адрес показателей.');
                }
                if (parsed.origin !== window.location.origin ||
                    !parsed.pathname.includes(`/company/${companyId}/`)) {
                    throw new Error('Адрес показателей не относится к выбранному предприятию.');
                }
                urls[name] = parsed.href;
            });
            if (source.aiReportUrl) {
                let reportURL;
                try {
                    reportURL = new URL(source.aiReportUrl, window.location.origin);
                } catch (error) {
                    throw new Error('Сервер передал некорректный адрес анализа показателей.');
                }
                if (reportURL.origin !== window.location.origin ||
                    !reportURL.pathname.includes(`/company/${companyId}/`)) {
                    throw new Error('Адрес анализа не относится к выбранному предприятию.');
                }
                urls.aiReportUrl = reportURL.href;
            }
            if (source.companyDataUrl) {
                let companyDataURL;
                try {
                    companyDataURL = new URL(source.companyDataUrl, window.location.origin);
                } catch (error) {
                    throw new Error('Сервер передал некорректный адрес данных предприятия.');
                }
                if (companyDataURL.origin !== window.location.origin ||
                    companyDataURL.pathname !== `/company/${companyId}`) {
                    throw new Error('Адрес данных не относится к выбранному предприятию.');
                }
                urls.companyDataUrl = companyDataURL.href;
            }
            if (source.dashboardLayoutURL) {
                let dashboardURL;
                try {
                    dashboardURL = new URL(source.dashboardLayoutURL, window.location.origin);
                } catch (error) {
                    throw new Error('Сервер передал некорректный адрес раскладки карточек.');
                }
                if (dashboardURL.origin !== window.location.origin ||
                    !dashboardURL.pathname.includes(`/company/${companyId}/`)) {
                    throw new Error('Адрес раскладки карточек не относится к выбранному предприятию.');
                }
                urls.dashboardLayoutURL = dashboardURL.href;
            }
            return Object.assign({}, source, urls, {companyId});
        },

        updateFrameUrl(next) {
            if (!next.pageUrl || !window.history || !window.location) return;
            try {
                const current = new URL(window.location.href);
                const page = new URL(next.pageUrl, window.location.origin);
                current.searchParams.forEach((value, key) => page.searchParams.set(key, value));
                if (config.embedded) page.searchParams.set('embedded', '1');
                window.history.replaceState({}, '', page.pathname + page.search);
            } catch (error) {
                // The server-generated page URL is still used for API requests.
                // A test or an older embedded host may not expose history.
            }
        },

        showModalSwitchStatus(message, isError = false) {
            const status = document.getElementById('ki-modal-switch-status');
            [
                'ki-cash-modal-content', 'ki-revenue-modal-content',
                'ki-collection-modal-content', 'ki-ar-modal-content',
                'ki-ap-modal-content', 'ki-cogs-modal-content',
                'ki-profit-modal-content', 'ki-credits-modal-content',
                'ki-salary-modal-content', 'ki-capex-modal-content',
                'ki-generic-modal-content'
            ].forEach(id => {
                const node = document.getElementById(id);
                if (node) node.style.display = 'none';
            });
            if (!status) return;
            status.textContent = message;
            status.classList.toggle('is-error', !!isError);
            status.style.display = 'flex';
        },

        hideModalSwitchStatus() {
            const status = document.getElementById('ki-modal-switch-status');
            if (!status) return;
            status.style.display = 'none';
            status.classList.remove('is-error');
            status.textContent = '';
        },

        finishCompanySwitch(token) {
            if (!state.companySwitching || token !== state.companySwitchToken) return;
            state.companySwitching = false;
            this.hideLoading();
            const cardId = state.pendingModalCardId;
            if (!cardId) return;
            const sourceData = state.mode === 'annual' && window.KI_AnnualApp &&
                window.KI_AnnualApp.state ? window.KI_AnnualApp.state.data : state.data;
            const cards = sourceData && Array.isArray(sourceData.cards) ? sourceData.cards : [];
            const annualYearFallback = state.mode === 'annual' && window.KI_AnnualApp &&
                typeof window.KI_AnnualApp.consumeCompanySwitchYearFallback === 'function' ?
                window.KI_AnnualApp.consumeCompanySwitchYearFallback() : null;
            const periodFallback = state.companySwitchPeriodFallback;
            state.companySwitchPeriodFallback = null;
            if (cards.some(card => card.id === cardId)) {
                state.pendingModalCardId = null;
                if (state.mode === 'annual' && window.KI_AnnualApp) {
                    window.KI_AnnualApp.openModal(cardId);
                } else {
                    this.openModal(cardId);
                }
                if (annualYearFallback) {
                    document.getElementById('ki-modal-subtitle').textContent =
                        `Предприятие «${config.companyName}»: год ${annualYearFallback.requestedYear} отсутствует, показан ${annualYearFallback.actualYear}.`;
                } else if (periodFallback) {
                    document.getElementById('ki-modal-subtitle').textContent =
                        `Предприятие «${config.companyName}»: запрошенный период недоступен, показан доступный период ${this.displayedPeriodLabel()}.`;
                }
            } else {
                state.modalCardId = cardId;
                const unavailableYear = annualYearFallback ?
                    `Год ${annualYearFallback.requestedYear} отсутствует, показан ${annualYearFallback.actualYear}.\n` : '';
                const unavailablePeriod = periodFallback ?
                    `Запрошенный период недоступен, показан доступный период ${this.displayedPeriodLabel()}.\n` : '';
                this.showModalSwitchStatus(
                    `${unavailableYear}${unavailablePeriod}Показатель «${cardId}» недоступен для предприятия «${config.companyName}».`,
                    true
                );
                document.getElementById('ki-modal-title').textContent =
                    'Показатель недоступен';
                this.updateModalSubtitle();
            }
        },

        showCompanySwitchError(token, message) {
            if (!state.companySwitching || token !== state.companySwitchToken) return;
            state.companySwitching = false;
            if (!state.modalCardId && state.pendingModalCardId) {
                state.modalCardId = state.pendingModalCardId;
            }
            this.showModalSwitchStatus(
                `Не удалось загрузить данные предприятия «${config.companyName}».\n${message}`,
                true
            );
            document.getElementById('ki-modal-title').textContent = 'Ошибка загрузки';
            this.updateModalSubtitle();
        },

        switchCompany(next) {
            let normalized;
            try {
                normalized = this.normalizeCompanyConfig(next);
            } catch (error) {
                this.showModalSwitchStatus(error.message, true);
                return false;
            }
            if (window.KI_DashboardLayout &&
                typeof window.KI_DashboardLayout.prepareCompanySwitch === 'function' &&
                window.KI_DashboardLayout.prepareCompanySwitch() === false) {
                return false;
            }
            if (!this.isModalOpen()) return false;
            if (window.KI_Layout && typeof window.KI_Layout.prepareCompanySwitch === 'function') {
                if (window.KI_Layout.prepareCompanySwitch() === false) return false;
            } else if (window.KI_Layout &&
                typeof window.KI_Layout.isDirty === 'function' &&
                window.KI_Layout.isDirty()) {
                if (typeof confirm !== 'function' ||
                    !confirm('Есть несохраненные изменения раскладки. Переключить предприятие и потерять изменения?')) {
                    return false;
                }
            }

            const modalCardId = state.modalCardId || state.pendingModalCardId ||
                (window.KI_AnnualApp && window.KI_AnnualApp.state &&
                    window.KI_AnnualApp.state.modalCardId);
            if (window.KI_Layout &&
                typeof window.KI_Layout.prepareCompanySwitch !== 'function' &&
                typeof window.KI_Layout.onModalClose === 'function') {
                // Force-reset before replacing config.  This invalidates layout
                // GET/PUT tokens and prevents an old draft from being written
                // with the new tenant's URL.
                window.KI_Layout.onModalClose(true);
            }
            state.companySwitchToken += 1;
            const token = state.companySwitchToken;
            state.companySwitching = true;
            state.pendingModalCardId = modalCardId || null;
            state.data = null;
            state.modalCardId = null;
            state.companySwitchPeriodFallback = null;
            this.clearChartJobs();
            this.invalidateAiReport('loading');
            Object.assign(config, normalized, {
                layoutBaseUrl: normalized.layoutBaseURL,
                layoutUrlTemplate: normalized.layoutBaseURL
            });
            if (!this.restorePeriodSelection()) {
                // A period date belongs to the company that supplied it.
                // Keeping it for another company causes a costly failed
                // calculation followed by an immediate retry.
                state.date = null;
                state.dateFrom = null;
                state.quarterAnchor = null;
            }
            // A cached/older switch payload may not yet contain this optional
            // URL.  Never retain the previous company's report endpoint.
            if (!normalized.aiReportUrl) delete config.aiReportUrl;
            this.updateFrameUrl(normalized);
            if (abortController) abortController.abort();
            state.reqId += 1;
            document.getElementById('ki-modal-title').textContent = 'Загрузка данных';
            document.getElementById('ki-modal-subtitle').textContent =
                `${config.companyName} · данные загружаются`;
            this.showModalSwitchStatus(`Загрузка данных предприятия «${config.companyName}»…`);

            if (state.mode === 'annual' && window.KI_AnnualApp &&
                typeof window.KI_AnnualApp.switchCompany === 'function') {
                window.KI_AnnualApp.switchCompany(normalized, token);
            } else {
                this.fetchData(token);
            }
            return true;
        },
        scheduleChart(containerId, renderer, delay = 50) {
            let key = containerId;
            if (typeof containerId !== 'string') {
                key = containerId && containerId.__kiChartJobKey;
                if (!key && containerId) {
                    key = `element:${state.reqId}:${Object.keys(state.chartJobs).length}`;
                    try { containerId.__kiChartJobKey = key; } catch (error) { /* read-only host */ }
                }
            }
            if (!key) return;
            state.chartJobs[key] = {container: containerId, renderer};
            if (typeof setTimeout !== 'function') {
                renderer();
                return;
            }
            setTimeout(() => {
                if (state.chartJobs[key] && state.chartJobs[key].renderer === renderer) renderer();
            }, delay);
        },

        rerenderVisibleCharts() {
            Object.keys(state.chartJobs).forEach(key => {
                const job = state.chartJobs[key];
                const container = typeof job.container === 'string'
                    ? document.getElementById(job.container) : job.container;
                if (container && this.isVisibleElement(container)) {
                    job.renderer();
                }
            });
        },

        clearChartJobs() {
            state.chartJobs = {};
        },

        isVisibleElement(element) {
            let current = element;
            while (current) {
                if (current.hidden || (current.style && current.style.display === 'none')) return false;
                current = current.parentElement;
            }
            return true;
        },

        init() {
            const urlParams = new URLSearchParams(window.location.search);
            const initialMode = urlParams.get('mode') || 'operational';
            this.restorePeriodSelection();
            this.restoreTooltipPreference();
            this.updateTooltipToggle();

            if (initialMode === 'annual') {
                state.mode = 'annual';
                document.querySelectorAll('.mode-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.mode === 'annual');
                });
                document.getElementById('ki-period-selector').style.display = 'none';
                document.getElementById('ki-date-select').style.display = 'none';
                document.getElementById('ki-date-from-select').style.display = 'none';
                const quarterButtons = document.getElementById('ki-quarter-buttons');
                if (quarterButtons) quarterButtons.style.display = 'none';
                document.querySelector('.tabs-bar').style.display = 'flex';
                document.getElementById('ki-operational-container').style.display = 'none';
                document.getElementById('ki-annual-container').style.display = 'block';
            } else {
                state.mode = 'operational';
                document.querySelectorAll('.mode-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.mode === 'operational');
                });
            }
            this.bindEvents();
            this.initRichTooltip();
            if (state.mode === 'operational') {
                this.fetchData();
            }
        },

        restorePeriodSelection() {
            let saved;
            try {
                if (!window.localStorage) return false;
                saved = JSON.parse(
                    window.localStorage.getItem(periodSelectionStorageKey()) || 'null');
            } catch (error) {
                return false;
            }
            const validPeriods = new Set(['year', 'quarter', 'month', 'day']);
            const validDate = value =>
                value === null || value === undefined ||
                /^\d{4}-\d{2}-\d{2}$/.test(String(value));
            if (!saved || !validPeriods.has(saved.period) ||
                    !validDate(saved.date) || !validDate(saved.dateFrom)) {
                return false;
            }
            state.period = saved.period;
            state.date = saved.date || null;
            state.dateFrom = saved.dateFrom || null;
            return true;
        },

        savePeriodSelection() {
            try {
                if (!window.localStorage) return;
                window.localStorage.setItem(periodSelectionStorageKey(), JSON.stringify({
                    period: state.period,
                    date: state.date,
                    dateFrom: state.dateFrom
                }));
            } catch (error) {
                // Private mode or a browser storage policy may deny access.
            }
        },

        restoreTooltipPreference() {
            try {
                if (!window.localStorage) return;
                const saved = window.localStorage.getItem(
                    tooltipPreferenceStorageKey);
                if (saved !== null) state.tooltipsEnabled = saved !== 'false';
            } catch (error) {
                // Private mode or a browser storage policy may deny access.
            }
        },

        updateTooltipToggle() {
            const button = document.getElementById('ki-tooltip-toggle');
            const disabled = !state.tooltipsEnabled;
            document.body.classList.toggle('ki-tooltips-disabled', disabled);
            if (!button) return;
            button.setAttribute('aria-pressed', String(disabled));
            const label = disabled ? 'Включить подсказки' : 'Отключить подсказки';
            button.setAttribute('aria-label', label);
            button.title = label;
        },

        toggleTooltips() {
            state.tooltipsEnabled = !state.tooltipsEnabled;
            if (!state.tooltipsEnabled) this.hideRichTooltip();
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(
                        tooltipPreferenceStorageKey,
                        String(state.tooltipsEnabled));
                }
            } catch (error) {
                // The visual setting still works for the current page.
            }
            this.updateTooltipToggle();
        },
        
        switchMode(mode) {
            if (state.mode === mode) return;
            if (window.KI_DashboardLayout &&
                typeof window.KI_DashboardLayout.prepareModeSwitch === 'function' &&
                window.KI_DashboardLayout.prepareModeSwitch(mode) === false) {
                return;
            }
            state.mode = mode;
            if (window.KI_SourcePassport &&
                typeof window.KI_SourcePassport.setMode === 'function') {
                window.KI_SourcePassport.setMode(mode);
            }

            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === mode);
            });

            const url = new URL(window.location);
            url.searchParams.set('mode', mode);
            window.history.replaceState({}, '', url);

            if (mode === 'annual') {
                state.reqId++;
                if (abortController) abortController.abort();
                this.invalidateAiReport('loading');
                document.getElementById('ki-period-selector').style.display = 'none';
                document.getElementById('ki-date-select').style.display = 'none';
                document.getElementById('ki-date-from-select').style.display = 'none';
                const quarterButtons = document.getElementById('ki-quarter-buttons');
                if (quarterButtons) quarterButtons.style.display = 'none';
                document.querySelector('.tabs-bar').style.display = 'flex';
                document.getElementById('tab-analytics').classList.add('active');
                if (document.getElementById('tab-recommendations')) document.getElementById('tab-recommendations').classList.remove('active');
                if (document.getElementById('tab-ai')) document.getElementById('tab-ai').classList.remove('active');
                document.querySelectorAll('.tab-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.tab === 'analytics');
                });
                document.getElementById('ki-operational-container').style.display = 'none';
                document.getElementById('ki-annual-container').style.display = 'block';
                document.getElementById('ki-error').style.display = 'none';
                this.closeModal(true);
                this.hideLoading();
                if (window.KI_AnnualApp) window.KI_AnnualApp.activate();
            } else {
                if (window.KI_AnnualApp) window.KI_AnnualApp.deactivate();
                this.invalidateAiReport('loading');
                const annualYearSelect = document.getElementById('ki-annual-year-select');
                const annualYearButtons = document.getElementById('ki-annual-year-buttons');
                if (annualYearSelect) annualYearSelect.style.display = 'none';
                if (annualYearButtons) annualYearButtons.style.display = 'none';
                document.getElementById('ki-period-selector').style.display = 'flex';
                document.querySelector('.tabs-bar').style.display = 'flex';
                document.getElementById('ki-operational-container').style.display = 'block';
                document.getElementById('ki-annual-container').style.display = 'none';
                document.getElementById('ki-error').style.display = 'none';
                this.closeModal(true);
                this.fetchData();
            }
        },

        tableSortValue(cell) {
            const raw = String(
                (cell && cell.dataset && cell.dataset.sortValue) ||
                (cell && cell.textContent) || '').replace(/\u00a0/g, ' ').trim();
            const dateMatch = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(raw);
            if (dateMatch) {
                return {
                    type: 'number',
                    value: Date.UTC(
                        Number(dateMatch[3]),
                        Number(dateMatch[2]) - 1,
                        Number(dateMatch[1]))
                };
            }
            const numeric = raw
                .replace(/\s+/g, '')
                .replace(/[₽%]/g, '')
                .replace(/(?:млн|тыс|дн(?:ей|я|ь)?)/gi, '')
                .replace(',', '.');
            if (/^[-+]?\d+(?:\.\d+)?$/.test(numeric)) {
                return {type: 'number', value: Number(numeric)};
            }
            return {type: 'text', value: raw.toLocaleLowerCase('ru-RU')};
        },

        chartPeriodBounds(dates) {
            return chartPeriodBounds(dates);
        },

        isTableSummaryRow(row) {
            if (!row) return false;
            if (row.classList && (
                    row.classList.contains('total-row') ||
                    row.classList.contains('revenue-mapping-total'))) {
                return true;
            }
            const firstCellText = row.cells && row.cells[0]
                ? String(row.cells[0].textContent || '').trim()
                : '';
            return /^итого(?:\s|$)/i.test(firstCellText);
        },

        sortTable(table, columnIndex, direction) {
            const body = table && table.tBodies && table.tBodies[0];
            if (!body) return;
            const allRows = Array.from(body.rows);
            const summaryRows = allRows.filter(
                row => this.isTableSummaryRow(row));
            const rows = allRows.filter(
                row => !this.isTableSummaryRow(row)).map((row, index) => ({
                row,
                index,
                sort: this.tableSortValue(row.cells[columnIndex])
            }));
            const sign = direction === 'desc' ? -1 : 1;
            rows.sort((left, right) => {
                if (!left.sort.value && left.sort.value !== 0) return 1;
                if (!right.sort.value && right.sort.value !== 0) return -1;
                let comparison;
                if (left.sort.type === 'number' && right.sort.type === 'number') {
                    comparison = left.sort.value - right.sort.value;
                } else {
                    comparison = String(left.sort.value).localeCompare(
                        String(right.sort.value), 'ru-RU', {
                            numeric: true, sensitivity: 'base'
                        });
                }
                return comparison ? comparison * sign : left.index - right.index;
            });
            rows.forEach(item => body.appendChild(item.row));
            summaryRows.forEach(row => body.appendChild(row));
        },

        columnResizeWidths(currentWidth, neighborWidth, delta, minimum = 72) {
            const clampedDelta = Math.max(
                minimum - currentWidth,
                Math.min(neighborWidth - minimum, delta));
            return {
                current: Math.round(currentWidth + clampedDelta),
                neighbor: Math.round(neighborWidth - clampedDelta)
            };
        },

        enhanceTable(table) {
            if (!table || table.dataset.kiTableTools === 'ready') return;
            const headerRow = table.tHead && table.tHead.rows[
                table.tHead.rows.length - 1];
            if (!headerRow || !headerRow.cells.length) return;
            table.dataset.kiTableTools = 'ready';
            table.classList.add('ki-adjustable-table');

            Array.from(headerRow.cells).forEach((header, columnIndex) => {
                if (header.colSpan > 1) return;
                header.classList.add('ki-sortable-column');
                header.tabIndex = 0;
                header.setAttribute('role', 'columnheader');
                header.setAttribute('aria-sort', 'none');
                header.title = 'Сортировать по столбцу';

                const sort = () => {
                    const nextDirection = header.dataset.sortDirection === 'asc'
                        ? 'desc' : 'asc';
                    Array.from(headerRow.cells).forEach(item => {
                        item.classList.remove('ki-sort-asc', 'ki-sort-desc');
                        item.dataset.sortDirection = '';
                        item.setAttribute('aria-sort', 'none');
                    });
                    header.dataset.sortDirection = nextDirection;
                    header.classList.add(`ki-sort-${nextDirection}`);
                    header.setAttribute(
                        'aria-sort',
                        nextDirection === 'asc' ? 'ascending' : 'descending');
                    this.sortTable(table, columnIndex, nextDirection);
                };
                header.addEventListener('click', event => {
                    if (!event.target.closest('.ki-column-resizer')) sort();
                });
                header.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        sort();
                    }
                });

                // The last column is resized from the separator on its left.
                // Keeping the outer-right edge fixed prevents the table from
                // growing beyond its modal or screen container.
                if (columnIndex === headerRow.cells.length - 1) return;
                const resizer = document.createElement('span');
                resizer.className = 'ki-column-resizer';
                resizer.setAttribute('role', 'separator');
                resizer.setAttribute('aria-orientation', 'vertical');
                resizer.setAttribute(
                    'aria-label',
                    `Изменить ширину столбца ${header.textContent.trim()}`);
                resizer.title = 'Перетащите для изменения ширины';
                resizer.addEventListener('pointerdown', event => {
                    event.preventDefault();
                    event.stopPropagation();
                    const startX = event.clientX;
                    const startWidth = header.getBoundingClientRect().width;
                    const startTableWidth = table.getBoundingClientRect().width;
                    const cells = Array.from(headerRow.cells);
                    const neighbor = cells[columnIndex + 1];
                    if (!neighbor) return;
                    const startNeighborWidth =
                        neighbor.getBoundingClientRect().width;
                    const startColumnWidths = cells.map(cell =>
                        cell.getBoundingClientRect().width);
                    const syncGroup = table.dataset.kiTableSync;
                    const syncTables = syncGroup
                        ? Array.from(document.querySelectorAll(
                            `table[data-ki-table-sync="${syncGroup}"]`))
                        : [];
                    const applySyncedWidths = widths => {
                        const percentages = widths.map(width =>
                            width / startTableWidth * 100);
                        syncTables.forEach(peer => {
                            const peerHeader = peer.tHead && peer.tHead.rows[
                                peer.tHead.rows.length - 1];
                            if (!peerHeader ||
                                    peerHeader.cells.length !== percentages.length) return;
                            peer.style.width = '100%';
                            peer.style.minWidth = '520px';
                            peer.style.maxWidth = '100%';
                            peer.style.tableLayout = 'fixed';
                            Array.from(peerHeader.cells).forEach((cell, index) => {
                                const width = `${percentages[index]}%`;
                                cell.style.width = width;
                                cell.style.minWidth = width;
                                cell.style.maxWidth = width;
                            });
                        });
                    };
                    if (syncTables.length) {
                        applySyncedWidths(startColumnWidths);
                    } else {
                        cells.forEach((cell, index) => {
                            const width = Math.round(startColumnWidths[index]);
                            cell.style.width = `${width}px`;
                            cell.style.minWidth = `${width}px`;
                            cell.style.maxWidth = `${width}px`;
                        });
                        table.style.width = `${Math.ceil(startTableWidth)}px`;
                        table.style.minWidth = `${Math.ceil(startTableWidth)}px`;
                        table.style.maxWidth = `${Math.ceil(startTableWidth)}px`;
                        table.style.tableLayout = 'fixed';
                    }
                    document.body.classList.add('ki-column-resizing');
                    const move = moveEvent => {
                        const widths = this.columnResizeWidths(
                            startWidth, startNeighborWidth,
                            moveEvent.clientX - startX);
                        if (syncTables.length) {
                            const nextWidths = startColumnWidths.slice();
                            nextWidths[columnIndex] = widths.current;
                            nextWidths[columnIndex + 1] = widths.neighbor;
                            applySyncedWidths(nextWidths);
                        } else {
                            header.style.width = `${widths.current}px`;
                            header.style.minWidth = `${widths.current}px`;
                            header.style.maxWidth = `${widths.current}px`;
                            neighbor.style.width = `${widths.neighbor}px`;
                            neighbor.style.minWidth = `${widths.neighbor}px`;
                            neighbor.style.maxWidth = `${widths.neighbor}px`;
                        }
                    };
                    const finish = () => {
                        document.body.classList.remove('ki-column-resizing');
                        document.removeEventListener('pointermove', move);
                        document.removeEventListener('pointerup', finish);
                        document.removeEventListener('pointercancel', finish);
                    };
                    document.addEventListener('pointermove', move);
                    document.addEventListener('pointerup', finish);
                    document.addEventListener('pointercancel', finish);
                });
                header.appendChild(resizer);
            });
        },

        enhanceTables(root) {
            const selector = '#ki-modal table, #ki-objects-table, #ki-annual-container table';
            if (root && root.matches && root.matches(selector)) {
                this.enhanceTable(root);
            }
            if (root && root.querySelectorAll) {
                root.querySelectorAll(selector).forEach(
                    table => this.enhanceTable(table));
            }
        },

        initTableTools() {
            this.enhanceTables(document);
            if (typeof MutationObserver === 'undefined') return;
            const observer = new MutationObserver(mutations => {
                mutations.forEach(mutation => {
                    mutation.addedNodes.forEach(node => {
                        if (node.nodeType === 1) this.enhanceTables(node);
                    });
                });
            });
            observer.observe(document.body, {childList: true, subtree: true});
            this.tableToolsObserver = observer;
        },

        bindEvents() {
            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.addEventListener('click', e => {
                    this.switchMode(e.target.dataset.mode);
                });
            });

            [
                ['ki-date-from-select', 'from'],
                ['ki-date-select', 'to']
            ].forEach(([id, edge]) => {
                const field = document.getElementById(id);
                if (!field) return;
                field.addEventListener('wheel', event => {
                    if (!state.periodPickerOpen) return;
                    event.preventDefault();
                    this.shiftPeriodDay(edge, event.deltaY > 0 ? 1 : -1);
                }, {passive: false});
                field.addEventListener('keydown', event => {
                    if (!state.periodPickerOpen ||
                            !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
                    event.preventDefault();
                    this.shiftPeriodDay(edge, event.key === 'ArrowDown' ? 1 : -1);
                });
            });

            document.addEventListener('pointerdown', event => {
                if (!state.periodPickerOpen) return;
                const control = document.getElementById('ki-period-selector');
                if (control && control.contains(event.target)) return;
                this.cancelPeriodPicker();
            });

            const modal = document.getElementById('ki-modal');
            modal.setAttribute('tabindex', '-1');
            modal.setAttribute('aria-labelledby', 'ki-modal-title');
            modal.setAttribute('aria-hidden', 'true');
            // Capture at document level: initial focus may still be on the
            // opener while the dialog's visibility transition is beginning.
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && state.periodPickerOpen) {
                    e.preventDefault();
                    App.cancelPeriodPicker();
                    return;
                }
                if (!modal.classList.contains('visible')) return;
                if (e.key === 'Escape') {
                    e.preventDefault();
                    e.stopPropagation();
                    App.closeModal();
                } else if (e.key === 'Tab') {
                    const focusable = Array.from(modal.querySelectorAll(
                        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
                    )).filter(element => App.isVisibleElement(element) && !element.disabled);
                    if(focusable.length === 0) return;
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];

                    if (!modal.contains(document.activeElement)) {
                        (e.shiftKey ? last : first).focus(); e.preventDefault();
                    } else if (e.shiftKey && document.activeElement === first) {
                        last.focus(); e.preventDefault();
                    } else if (!e.shiftKey && document.activeElement === last) {
                        first.focus(); e.preventDefault();
                    }
                }
            }, true);

            document.getElementById('ki-cards-grid').addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') {
                    const card = e.target.closest('.kpi-card.clickable');
                    if (card) {
                        e.preventDefault();
                        this.openModal(card.dataset.metric);
                    }
                }
            });
            this.initTableTools();
        },
        
        switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
            document.querySelector(`.tab-btn[data-tab="${tabName}"]`).classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
            if (tabName === 'ai') {
                this.renderAiReport();
            }
            if (tabName === 'data') {
                this.renderDataPage();
            }
        },

        renderDataPage() {
            const frame = document.getElementById('ki-data-frame');
            if (!frame || !config || !config.companyDataUrl) return;

            let dataUrl;
            try {
                const origin = window.location && window.location.origin ?
                    window.location.origin : 'http://localhost';
                dataUrl = new URL(config.companyDataUrl, origin);
                dataUrl.searchParams.set('embedded', '1');
            } catch (error) {
                frame.removeAttribute('src');
                return;
            }

            const expectedUrl = dataUrl.pathname + dataUrl.search + dataUrl.hash;
            if (frame.getAttribute('src') === expectedUrl) return;
            frame.onload = () => {
                frame.style.display = 'block';
            };
            frame.src = expectedUrl;
        },

        aiTabActive() {
            const tab = document.getElementById('tab-ai');
            return !!(tab && tab.classList.contains('active'));
        },

        setAiEmptyState(kind, message) {
            const frame = document.getElementById('ki-ai-frame');
            const empty = document.getElementById('ki-ai-empty');
            const title = document.getElementById('ki-ai-state-title');
            const text = document.getElementById('ki-ai-state-text');
            const retry = document.getElementById('ki-ai-retry');
            const dataLink = document.getElementById('ki-ai-data-link');
            if (frame) frame.style.display = 'none';
            if (!empty) return;
            empty.style.display = 'block';
            if (title) title.textContent = kind === 'error' ? 'Не удалось загрузить анализ' :
                (kind === 'empty' ? 'Нет данных для анализа' : 'Анализ готовится');
            if (text) text.textContent = message ||
                (kind === 'error' ? 'Проверьте подключение и повторите попытку.' :
                    'Дождитесь загрузки показателей выбранного периода.');
            if (retry) retry.style.display = kind === 'error' ? 'inline-block' : 'none';
            if (dataLink) {
                dataLink.style.display = kind === 'empty' ? 'inline-block' : 'none';
                if (config && config.historicalImportUrl) dataLink.href = config.historicalImportUrl;
            }
        },

        invalidateAiReport(kind = 'loading', message = null) {
            state.aiReport = null;
            const frame = document.getElementById('ki-ai-frame');
            if (frame) {
                frame.onload = null;
                frame.onerror = null;
                if (typeof frame.removeAttribute === 'function') {
                    frame.removeAttribute('src');
                } else {
                    frame.src = '';
                }
            }
            if (this.aiTabActive()) this.setAiEmptyState(kind, message);
        },

        setAiReport(mode, data) {
            if (!config || !config.aiReportUrl || !data) {
                this.invalidateAiReport('empty',
                    'Для выбранного периода нет доступного источника анализа.');
                return;
            }
            let report;
            try {
                const base = (window.location && window.location.origin) || 'http://localhost';
                report = new URL(config.aiReportUrl, base);
                if (mode === 'annual') {
                    report.searchParams.set('mode', 'annual');
                    // The report endpoint accepts the legacy end year as
                    // well as the complete selected interval.  Clear any
                    // query left by a cached/configured URL before applying
                    // the current payload so a previous range cannot leak
                    // into a single-year report.
                    report.searchParams.delete('year');
                    report.searchParams.delete('year_from');
                    report.searchParams.delete('year_to');
                    if (data.year !== null && data.year !== undefined) {
                        report.searchParams.set('year', String(data.year));
                    }
                    const yearFrom = Number(data.year_from);
                    const yearTo = Number(data.year_to);
                    if (Number.isInteger(yearFrom) && Number.isInteger(yearTo) &&
                        yearFrom !== yearTo) {
                        report.searchParams.set('year_from', String(Math.min(yearFrom, yearTo)));
                        report.searchParams.set('year_to', String(Math.max(yearFrom, yearTo)));
                        // The server contract defines `year` as the range
                        // end.  Prefer that canonical value when present.
                        report.searchParams.set('year', String(Math.max(yearFrom, yearTo)));
                    }
                } else {
                    const period = data.period || {};
                    report.searchParams.set('period', period.kind || state.period);
                    if (period.date) report.searchParams.set('date', period.date);
                    if (period.date_from && period.date_from !== period.date) {
                        report.searchParams.set('date_from', period.date_from);
                    }
                }
            } catch (error) {
                this.invalidateAiReport('error', 'Сервер передал некорректный адрес анализа.');
                return;
            }
            state.aiReport = {
                url: report.pathname + report.search,
                mode,
                companyId: config.companyId
            };
            if (this.aiTabActive()) this.renderAiReport();
        },

        renderAiReport() {
            const report = state.aiReport;
            if (!report || report.companyId !== config.companyId) {
                this.setAiEmptyState('loading');
                return;
            }
            const frame = document.getElementById('ki-ai-frame');
            const empty = document.getElementById('ki-ai-empty');
            if (!frame || !empty) return;
            const expectedUrl = report.url;
            if (report.loaded && frame.getAttribute('src') === expectedUrl) {
                frame.style.display = 'block';
                empty.style.display = 'none';
                return;
            }
            this.setAiEmptyState('loading', 'Загружается анализ выбранного периода…');
            frame.onload = () => {
                if (state.aiReport !== report) return;
                // A cleared iframe also emits load for about:blank. Never
                // treat that or an older navigation as the selected report.
                try {
                    if (frame.contentWindow &&
                        frame.contentWindow.location.href !==
                        new URL(expectedUrl, window.location.href).href) return;
                } catch (error) {
                    this.setAiEmptyState('error');
                    return;
                }
                report.loaded = true;
                if (!this.aiTabActive()) return;
                frame.style.display = 'block';
                empty.style.display = 'none';
            };
            frame.onerror = () => {
                if (state.aiReport && state.aiReport.url === expectedUrl) {
                    this.setAiEmptyState('error');
                }
            };
            if (frame.getAttribute('src') !== expectedUrl) frame.src = expectedUrl;
        },

        retryAiReport() {
            if (state.aiReport) {
                state.aiReport.loaded = false;
                const frame = document.getElementById('ki-ai-frame');
                if (frame && typeof frame.removeAttribute === 'function') {
                    frame.removeAttribute('src');
                } else if (frame) {
                    frame.src = '';
                }
                this.renderAiReport();
                return;
            }
            this.retry();
        },
        
        onPeriodChange(kind) {
            if (state.period === kind) return;
            state.period = kind;
            state.date = null;
            state.dateFrom = null;
            state.quarterAnchor = null;
            this.invalidateAiReport('loading');
            this.fetchData();
        },

        onQuarterChange(val) {
            if (!state.quarterAnchor) {
                state.quarterAnchor = val;
                state.dateFrom = val;
                state.date = val;
            } else {
                [state.dateFrom, state.date] = [state.quarterAnchor, val].sort();
                state.quarterAnchor = null;
            }
            this.invalidateAiReport('loading');
            this.fetchData();
        },
        
        onDateChange(val) {
            if (state.date === val) return;
            state.date = val;
            if (state.period === 'quarter') {
                // Changing the year starts a fresh quarter selection instead
                // of retaining an endpoint from the previously displayed year.
                state.dateFrom = val;
                state.quarterAnchor = null;
            }
            if ((state.period === 'month' || state.period === 'day')
                    && state.dateFrom && state.dateFrom > val) {
                state.dateFrom = val;
            }
            this.invalidateAiReport('loading');
            this.fetchData();
        },

        onDateFromChange(val) {
            if (state.dateFrom === val) return;
            state.dateFrom = val;
            if (state.date && val > state.date) state.date = val;
            this.invalidateAiReport('loading');
            this.fetchData();
        },

        periodPickerBounds() {
            const period = state.data && state.data.period || {};
            const values = (period.options || []).map(option => option.value)
                .filter(value => /^\d{4}-\d{2}-\d{2}$/.test(String(value))).sort();
            const first = values[0] || period.start || period.date_from || state.dateFrom;
            const inferredFirst = period.kind === 'year' && first
                ? `${String(first).slice(0, 4)}-01-01` : first;
            return {
                min: inferredFirst,
                max: values[values.length - 1] || period.end || period.date || state.date
            };
        },

        formatPeriodField(value) {
            const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
            return match ? `${match[3]}.${match[2]}.${match[1]}` : '—';
        },

        onPeriodPickerButton() {
            if (!state.periodPickerOpen) {
                state.periodPickerOpen = true;
                state.pendingDateFrom = null;
                state.pendingDate = null;
                const bounds = this.periodPickerBounds();
                state.pickerYearStart = Number(String(bounds.max || '').slice(0, 4)) - 2;
                this.renderPeriodPicker();
                return;
            }
            if (!state.pendingDateFrom || !state.pendingDate) return;
            state.periodPickerOpen = false;
            state.period = 'day';
            state.dateFrom = state.pendingDateFrom;
            state.date = state.pendingDate;
            this.invalidateAiReport('loading');
            this.renderPeriodPicker();
            this.fetchData();
        },

        cancelPeriodPicker() {
            if (!state.periodPickerOpen) return;
            state.periodPickerOpen = false;
            state.pendingDateFrom = null;
            state.pendingDate = null;
            this.renderPeriodPicker();
        },

        selectPeriodMonth(value) {
            if (!state.pendingDateFrom || state.pendingDate) {
                state.pendingDateFrom = value;
                state.pendingDate = null;
            } else {
                const [fromMonth, toMonth] =
                    [state.pendingDateFrom.slice(0, 7), value.slice(0, 7)].sort();
                const [year, month] = toMonth.split('-').map(Number);
                const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
                const calendarEnd =
                    `${toMonth}-${String(lastDay).padStart(2, '0')}`;
                const boundsMax = this.periodPickerBounds().max;
                state.pendingDateFrom = `${fromMonth}-01`;
                state.pendingDate = boundsMax &&
                    boundsMax.slice(0, 7) === toMonth &&
                    boundsMax < calendarEnd ? boundsMax : calendarEnd;
            }
            this.renderPeriodPicker();
        },

        shiftPeriodDay(edge, offset) {
            const key = edge === 'from' ? 'pendingDateFrom' : 'pendingDate';
            const current = state[key];
            if (!state.periodPickerOpen || !current || !offset) return;
            const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(current);
            if (!match) return;
            const year = Number(match[1]);
            const month = Number(match[2]);
            const day = Number(match[3]);
            const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
            let nextDay = Math.max(1, Math.min(lastDay, day + offset));
            if (edge === 'from' && state.pendingDate &&
                    state.pendingDate.slice(0, 7) === current.slice(0, 7)) {
                nextDay = Math.min(nextDay, Number(state.pendingDate.slice(8, 10)));
            }
            if (edge === 'to' && state.pendingDateFrom &&
                    state.pendingDateFrom.slice(0, 7) === current.slice(0, 7)) {
                nextDay = Math.max(nextDay, Number(state.pendingDateFrom.slice(8, 10)));
            }
            state[key] = `${match[1]}-${match[2]}-${String(nextDay).padStart(2, '0')}`;
            this.renderPeriodPicker();
        },

        shiftPeriodPickerYears(offset) {
            state.pickerYearStart = (state.pickerYearStart || new Date().getFullYear()) + offset;
            this.renderPeriodPicker();
        },

        renderPeriodPicker() {
            const picker = document.getElementById('ki-period-picker');
            const yearsContainer = document.getElementById('ki-period-years');
            const button = document.getElementById('ki-period-picker-button');
            const from = document.getElementById('ki-date-from-select');
            const to = document.getElementById('ki-date-select');
            if (!picker || !yearsContainer || !button || !from || !to) return;
            const bounds = this.periodPickerBounds();
            const visibleFrom = state.pendingDateFrom || state.dateFrom ||
                (state.data && state.data.period && state.data.period.start);
            const visibleTo = state.periodPickerOpen
                ? state.pendingDate
                : state.date || (state.data && state.data.period && state.data.period.end);
            from.value = this.formatPeriodField(visibleFrom);
            to.value = this.formatPeriodField(visibleTo);
            picker.hidden = !state.periodPickerOpen;
            button.textContent = state.periodPickerOpen ? 'Загрузить период' : 'Выбрать период';
            button.setAttribute('aria-expanded', String(state.periodPickerOpen));
            button.disabled = state.periodPickerOpen &&
                (!state.pendingDateFrom || !state.pendingDate);
            const hint = document.getElementById('ki-period-picker-hint');
            if (hint) hint.textContent = !state.pendingDateFrom || state.pendingDate
                ? 'Выберите месяц начала периода'
                : 'Выберите месяц окончания периода';
            if (!state.periodPickerOpen) return;

            const minMonth = String(bounds.min || '').slice(0, 7);
            const maxMonth = String(bounds.max || '').slice(0, 7);
            const startYear = state.pickerYearStart || Number(maxMonth.slice(0, 4)) - 2;
            const monthNames = ['Янв','Фев','Мар','Апр','Май','Июн',
                'Июл','Авг','Сен','Окт','Ноя','Дек'];
            yearsContainer.innerHTML = [startYear, startYear + 1, startYear + 2].map(year => {
                const months = monthNames.map((name, index) => {
                    const month = `${year}-${String(index + 1).padStart(2, '0')}`;
                    const value = `${month}-01`;
                    const disabled = (minMonth && month < minMonth) ||
                        (maxMonth && month > maxMonth);
                    const inRange = state.pendingDateFrom && state.pendingDate &&
                        value >= state.pendingDateFrom && value <= state.pendingDate;
                    const edge = month === String(state.pendingDateFrom || '').slice(0, 7) ||
                        month === String(state.pendingDate || '').slice(0, 7);
                    return `<button type="button" class="period-picker-month${inRange ? ' in-range' : ''}${edge ? ' range-edge' : ''}"
                        ${disabled ? 'disabled' : ''}
                        onclick="window.KI_App.selectPeriodMonth('${value}')"
                        aria-pressed="${edge || inRange ? 'true' : 'false'}">${name}</button>`;
                }).join('');
                return `<section><h3 class="period-picker-year-title">${year}</h3>
                    <div class="period-picker-months">${months}</div></section>`;
            }).join('');
        },
        
        retry() {
            if (state.mode === 'annual') {
                if (window.KI_AnnualApp) window.KI_AnnualApp.retry();
            } else {
                this.fetchData();
            }
        },

        dismissError() {
            const errorOverlay = document.getElementById('ki-error');
            if (errorOverlay) errorOverlay.style.display = 'none';
            this.hideLoading();
        },
        
        async fetchData(companySwitchToken = null, skipPeriodSelection = false,
                        fallbackSelection = null) {
            this.invalidateAiReport('loading');
            this.showLoading();
            if (window.KI_SourcePassport &&
                typeof window.KI_SourcePassport.clear === 'function') {
                // Never leave a previous company's or period's passport
                // visible while the read-only source request is in flight.
                window.KI_SourcePassport.clear('Загрузка данных');
            }
            document.getElementById('ki-error').style.display = 'none';
            
            if (abortController) abortController.abort();
            abortController = new AbortController();
            const reqId = ++state.reqId;
            
            try {
                if (!config || !config.dataUrl) throw new Error('Не настроен адрес источника показателей.');
                let url = `${config.dataUrl}?period=${encodeURIComponent(state.period)}`;
                const requestedPeriod = state.period;
                const requestedDate = state.date;
                const requestedDateFrom = state.dateFrom;
                if (requestedDate && !skipPeriodSelection) {
                    url += `&date=${encodeURIComponent(requestedDate)}`;
                }
                if (['month', 'day', 'quarter'].includes(requestedPeriod)
                        && requestedDateFrom && !skipPeriodSelection) {
                    url += `&date_from=${encodeURIComponent(requestedDateFrom)}`;
                }
                const unavailablePeriodErrors = new Set([
                    'date is not an available option for selected period',
                    'date_from is not an available option',
                    'date_from must not be after date'
                ]);
                const response = await fetch(url, { 
                    signal: abortController.signal, 
                    headers: { 'Accept': 'application/json' }
                });

                if (response.status === 401) {
                    window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
                    return; 
                }
                if (response.status === 403) throw new Error("Нет доступа (403)");

                const text = await response.text();
                let data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    throw new Error("Срок сессии истек или получен некорректный формат ответа");
                }

                const responseError = data && (data.error || data.message);
                const unavailablePeriod = data &&
                    (unavailablePeriodErrors.has(data.error) ||
                     unavailablePeriodErrors.has(data.message));
                if (response.status === 400 && !skipPeriodSelection &&
                    unavailablePeriod) {
                    if (reqId !== state.reqId ||
                        (companySwitchToken !== null &&
                         companySwitchToken !== state.companySwitchToken)) return;
                    return this.fetchData(companySwitchToken, true, {
                        period: requestedPeriod,
                        date: requestedDate,
                        dateFrom: requestedDateFrom
                    });
                }

                if (!response.ok) {
                    throw new Error(responseError || `Ошибка сервера: ${response.status}`);
                }
                
                if (reqId !== state.reqId ||
                    (companySwitchToken !== null &&
                     companySwitchToken !== state.companySwitchToken)) return;
                
                state.data = data;
                this.setAiReport('operational', data);
                if (companySwitchToken !== null) state.quarterAnchor = null;
                if (data.period) {
                    state.period = data.period.kind || state.period;
                    state.date = data.period.date || null;
                    state.dateFrom = data.period.date_from || data.period.date || null;
                    this.savePeriodSelection();
                }
                if (fallbackSelection && companySwitchToken !== null &&
                    state.companySwitching) {
                    state.companySwitchPeriodFallback = {
                        requested: fallbackSelection,
                        actual: {
                            period: state.period,
                            date: state.date,
                            dateFrom: state.dateFrom
                        }
                    };
                }
                
                this.render();
                this.finishCompanySwitch(companySwitchToken);
            } catch (e) {
                if (reqId !== state.reqId ||
                    (companySwitchToken !== null &&
                     companySwitchToken !== state.companySwitchToken) ||
                    e.name === 'AbortError') return;
                if (state.companySwitching) {
                    this.invalidateAiReport('error', e.message);
                    this.showCompanySwitchError(companySwitchToken, e.message);
                    return;
                }
                this.invalidateAiReport('error', e.message);
                if (window.KI_SourcePassport &&
                    typeof window.KI_SourcePassport.clear === 'function') {
                    window.KI_SourcePassport.clear(e.message);
                }
                document.getElementById('ki-error-text').textContent = e.message;
                document.getElementById('ki-error').style.display = 'flex';
            } finally {
                if (reqId === state.reqId &&
                    (companySwitchToken === null ||
                     companySwitchToken === state.companySwitchToken)) this.hideLoading();
            }
        },
        
        render() {
            if (!state.data) return;
            
            const subtitle = document.getElementById('ki-topbar-subtitle');
            subtitle.textContent = `${config.companyName} · ${this.displayedPeriodLabel()}`;
            
            this.renderPeriodSelector();
            this.renderWarnings();
            this.renderCards();
            this.renderObjectsTable();
            this.renderQuality();
            this.renderRecommendations();
            if (window.KI_SourcePassport &&
                typeof window.KI_SourcePassport.renderOperational === 'function') {
                window.KI_SourcePassport.renderOperational(state.data);
            }
        },
        
        renderPeriodSelector() {
            this.renderPeriodPicker();
        },
        
        renderWarnings() {
            const c = document.getElementById('ki-warnings-container');
            const warnings = new Set();
            
            if (state.data.period && state.data.period.warnings) {
                state.data.period.warnings.forEach(w => warnings.add(w));
            }
            if (state.data.quality && state.data.quality.warnings) {
                state.data.quality.warnings.forEach(w => warnings.add(w));
            }

            if (warnings.size === 0) {
                c.style.display = 'none';
                return;
            }
            
            let html = `<details class="warnings-details">
                <summary>Предупреждения и ограничения (${warnings.size})</summary>
                <div class="warnings-content"><ul>`;
            
            warnings.forEach(w => {
                html += `<li>${esc(w)}</li>`;
            });
            html += `</ul></div></details>`;
            
            c.innerHTML = html;
            c.style.display = 'block';
        },
        
        formatValue(v, unit) {
            if (v === null || v === undefined) return { val: 'Н/Д', u: '' };
            let num = v;
            let u = unit;
            if (unit === 'RUB') {
                num = v / 1000000;
                u = 'млн ₽';
            }
            if (num === 0) return { val: '0', u: u };
            return { 
                val: new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(num), 
                u: u 
            };
        },
        
        renderCards() {
            const grid = document.getElementById('ki-cards-grid');
            if (!state.data.cards) return;
            
            let html = '';
            const orderedIds = ['cash', 'cash_dz', 'revenue', 'collection', 'ar', 'ap', 'cogs', 'profit', 'credits', 'salary', 'capex'];
            (state.data.cards || []).forEach(card => {
                if (card && card.id && !orderedIds.includes(card.id)) orderedIds.push(card.id);
            });
            
            orderedIds.forEach(id => {
                const card = state.data.cards.find(c => c.id === id);
                if (!card) return;
                
                let statusClass = 'grey';
                if (card.status === 'good') statusClass = 'green';
                else if (card.status === 'warning') statusClass = 'yellow';
                else if (card.status === 'bad') statusClass = 'red';
                
                let isNull = (card.value === null || card.value === undefined);
                let formatted = this.formatValue(card.value, card.unit);
                
                let deltaHtml = '';
                if (!isNull && card.delta && (!isCashMetric(id) || (card.comparison && card.comparison.comparable))) {
                    let dClass = 'neutral';
                    if (String(card.delta.value).startsWith('+')) dClass = 'up';
                    if (String(card.delta.value).startsWith('-')) dClass = 'down';
                    
                    const numVal = parseFloat(card.delta.value);
                    let displayVal = card.delta.value;
                    let u = card.delta.unit;
                    if (card.delta.unit === 'RUB') {
                        if (!isNaN(numVal)) {
                            displayVal = (numVal > 0 ? '+' : '') + new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(numVal / 1000000);
                        }
                        u = 'млн ₽';
                    }
                    
                    deltaHtml = `<div class="kpi-delta ${dClass}">
                        ${esc(displayVal)} ${esc(u)} · ${esc(card.delta.label)}
                    </div>`;
                } else if (isNull && card.reason) {
                     deltaHtml = `<div class="kpi-delta neutral">${esc(card.reason)}</div>`;
                }
                
                const ariaLabel = `${card.title}. Значение: ${formatted.val} ${formatted.u}. Статус: ${card.status_label || card.status}.`;
                const metricTooltip = this.cashTooltip(this.metricTooltipData(card));
                
                html += `
                <div class="kpi-card status-${statusClass} clickable cash-tooltip-host" data-metric="${esc(id)}" tabindex="0" onclick="window.KI_App.openModal('${esc(id)}')" role="button" aria-label="${esc(ariaLabel)}">
                  <div class="kpi-label">${esc(card.title)} <span class="status-dot ${statusClass}"></span></div>
                  <div class="kpi-value">${esc(formatted.val)}<span class="kpi-unit">${esc(formatted.u)}</span></div>
                  ${isCashMetric(id) ? `<div class="kpi-delta neutral">${esc(this.cashCoverageText(card))}${card.date ? ` · на ${esc(card.date)}` : ''}</div>` : ''}
                  ${deltaHtml}
                    ${metricTooltip}
                </div>
                `;
            });
            grid.innerHTML = html;
        },
        
        renderObjectsTable() {
            const tbody = document.querySelector('#ki-objects-table tbody');
            const tfoot = document.querySelector('#ki-objects-totals');
            
            const objectsNotes = document.getElementById('ki-objects-notes');
            let notesHtml = '';
            if (state.data.objects && state.data.objects.reason) {
                notesHtml += `<div class="info-note" style="border-left-color:#F59E0B; background:#FEF3C7; color:#92400E;"><strong>Внимание:</strong> ${esc(state.data.objects.reason)}</div>`;
            }
            if (state.data.objects && state.data.objects.notes && state.data.objects.notes.length > 0) {
                state.data.objects.notes.forEach(n => {
                    notesHtml += `<div class="info-note"><strong>Примечание:</strong> ${esc(n)}</div>`;
                });
            }
            objectsNotes.innerHTML = notesHtml;
            
            if (!state.data.objects || !state.data.objects.rows || state.data.objects.rows.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:20px;">Нет данных по объектам</td></tr>';
                tfoot.innerHTML = '';
                const chartContainer = document.getElementById('ki-objects-chart');
                if (chartContainer) chartContainer.innerHTML = '';
                return;
            }
            
            const rows = state.data.objects.rows;
            const totals = state.data.objects.totals || {};
            
            let rowsHtml = '';
            rows.forEach(r => {
                const isNullProfit = r.profit === null || r.profit === undefined;
                const isPos = r.profit > 0;
                const isNeg = r.profit < 0;
                
                const valClass = isNullProfit ? '' : (isPos ? 'val-pos' : 'val-neg');
                const barFillClass = isNullProfit ? '' : (isPos ? 'obj-bar-fill-pos' : 'obj-bar-fill-neg');
                
                const pct = (totals.revenue && r.revenue !== null) ? (r.revenue / totals.revenue) * 100 : 0;
                
                let badge = '<span class="badge badge-grey">Недостаточно данных</span>';
                if (!isNullProfit) {
                    if (isPos) badge = '<span class="badge badge-green">Прибыль</span>';
                    else if (isNeg) badge = '<span class="badge badge-red">Убыток</span>';
                    else badge = '<span class="badge badge-grey">Ноль</span>';
                }
                
                const profitText = isNullProfit ? 'Н/Д' : ((isPos ? '+' : '') + this.formatValue(r.profit, 'RUB').val);
                
                rowsHtml += `<tr>
                    <td>
                      <div class="obj-name-wrap">
                        <span>${esc(r.label)}</span>
                      </div>
                    </td>
                    <td>${this.formatValue(r.revenue, 'RUB').val}</td>
                    <td>${this.formatValue(r.cost, 'RUB').val}</td>
                    <td class="${valClass}">${profitText}</td>
                    <td class="${valClass}">${r.margin !== null ? esc(r.margin) + '%' : 'Н/Д'}</td>
                    <td>
                        ${pct.toFixed(1)}%
                        ${pct > 0 ? `<div class="obj-bar"><div class="${barFillClass}" style="width:${Math.min(100, Math.max(0, pct))}%"></div></div>` : ''}
                    </td>
                    <td>${badge}</td>
                </tr>`;
            });
            tbody.innerHTML = rowsHtml;
            
            const isNullTotProfit = totals.profit === null || totals.profit === undefined;
            const isPosTot = totals.profit > 0;
            const isNegTot = totals.profit < 0;
            const valClassTot = isNullTotProfit ? '' : (isPosTot ? 'val-pos' : 'val-neg');
            const totProfitText = isNullTotProfit ? 'Н/Д' : ((isPosTot ? '+' : '') + this.formatValue(totals.profit, 'RUB').val);
            
            tfoot.innerHTML = `<tr class="total-row">
                <td>Итого по предприятию</td>
                <td>${this.formatValue(totals.revenue, 'RUB').val}</td>
                <td>${this.formatValue(totals.cost, 'RUB').val}</td>
                <td class="${valClassTot}">${totProfitText}</td>
                <td class="${valClassTot}">${totals.margin !== null ? esc(totals.margin) + '%' : 'Н/Д'}</td>
                <td>100%</td>
                <td></td>
            </tr>`;
            
            const chartContainer = document.getElementById('ki-objects-chart');
            if (chartContainer) {
                const validRows = rows.filter(r => r.revenue !== null || r.cost !== null);
                if (validRows.length > 0) {
                    this.renderObjectsChart(validRows, 'ki-objects-chart');
                } else {
                    chartContainer.innerHTML = '';
                }
            }
        },
        
        renderObjectsChart(rows, containerId) {
            const container = document.getElementById(containerId);
            
            const data = rows.map(r => ({
                label: r.label,
                rev: r.revenue !== null ? r.revenue / 1000000 : null,
                cost: r.cost !== null ? r.cost / 1000000 : null
            })).filter(r => r.rev !== null || r.cost !== null);
            
            if(data.length === 0) {
                container.innerHTML = '';
                return;
            }
            
            const width = container.clientWidth || 800;
            const height = 240;
            const padT = 20, padB = 40, padL = 50, padR = 10;
            const w = width - padL - padR;
            const h = height - padT - padB;
            
            let maxVal = 0;
            data.forEach(d => {
                if (d.rev !== null && d.rev > maxVal) maxVal = d.rev;
                if (d.cost !== null && d.cost > maxVal) maxVal = d.cost;
            });
            if (maxVal === 0) maxVal = 1;
            else maxVal += maxVal * 0.1;
            
            const step = w / data.length;
            const barW = Math.max(8, step * 0.25);
            
            let svg = `<svg viewBox="0 0 ${width} ${height}" style="width:100%; height:100%; display:block; margin-top:24px; border-top:1px solid #E5E7EB; padding-top:24px;">`;
            
            [0, maxVal/2, maxVal].forEach(val => {
                const y = padT + h - (val / maxVal) * h;
                const fmt = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(val);
                svg += `<text x="${padL-5}" y="${y+4}" font-size="10" fill="#9CA3AF" text-anchor="end">${esc(fmt)}</text>`;
                svg += `<line x1="${padL}" y1="${y}" x2="${width-padR}" y2="${y}" stroke="#E5E7EB" stroke-dasharray="2,2"/>`;
            });
            
            data.forEach((d, i) => {
                const cx = padL + i * step + step / 2;
                
                let lbl = d.label || '';
                if (lbl.length > 15) lbl = lbl.substring(0, 12) + '...';
                
                if (d.rev !== null) {
                    const bh = (d.rev / maxVal) * h;
                    const y = padT + h - bh;
                    svg += `<rect x="${cx - barW - 1}" y="${y}" width="${barW}" height="${Math.max(1, bh)}" fill="#3B82F6" rx="2"/>`;
                }
                
                if (d.cost !== null) {
                    const bh = (d.cost / maxVal) * h;
                    const y = padT + h - bh;
                    svg += `<rect x="${cx + 1}" y="${y}" width="${barW}" height="${Math.max(1, bh)}" fill="#9CA3AF" rx="2"/>`;
                }
                
                svg += `<text x="${cx}" y="${height - 20}" font-size="9" fill="#6B7280" text-anchor="middle">${esc(lbl)}</text>`;
            });
            
            svg += `<rect x="${padL}" y="${height - 10}" width="10" height="10" fill="#3B82F6" rx="2"/>
                    <text x="${padL+15}" y="${height - 1}" font-size="10" fill="#4B5563">Выручка</text>
                    <rect x="${padL+80}" y="${height - 10}" width="10" height="10" fill="#9CA3AF" rx="2"/>
                    <text x="${padL+95}" y="${height - 1}" font-size="10" fill="#4B5563">Себестоимость</text>`;
            
            svg += '</svg>';
            container.innerHTML = svg;
        },

        renderQuality() {
            const container = document.getElementById('ki-quality-section');
            let html = '';
            
            if (state.data.quality) {
                const q = state.data.quality;
                let qHtml = '<div class="quality-box"><div class="quality-title">Качество данных (ОСВ)</div>';
                if (q.source_label) {
                    qHtml += `<div style="margin-bottom:8px; font-size:12px;"><strong>Источник:</strong> ${esc(q.source_label)}</div>`;
                }
                if (q.accounts && q.accounts.length > 0) {
                    qHtml += `<div style="font-size:12px;"><strong>Загруженные счета:</strong> ${esc(q.accounts.join(', '))}</div>`;
                }
                if (q.missing_accounts && q.missing_accounts.length > 0) {
                    qHtml += `<div style="font-size:12px; color:#EF4444; margin-top:4px;"><strong>Отсутствующие счета:</strong> ${esc(q.missing_accounts.join(', '))}</div>`;
                }
                qHtml += '</div>';
                html += qHtml;
            }
            
            container.innerHTML = html;
        },
        
        renderRecommendations() {
            const list = document.getElementById('ki-recommendations-list');
            const badge = document.getElementById('ki-rec-badge');
            
            const recs = (state.data.recommendations || []).filter(r => r.severity !== 'info');
            
            if (recs.length === 0) {
                let limitsText = '';
                if (state.data.quality && state.data.quality.warnings && state.data.quality.warnings.length > 0) {
                    limitsText = '<br><br><em>Примечание: расчёт некоторых показателей ограничен из-за отсутствия полных данных (см. вкладку Аналитика).</em>';
                }
                
                list.innerHTML = `<div class="recommendations-card" style="border-left-color:#9CA3AF;">
                    <div class="rec-header">
                        <div class="rec-title">
                            <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                            Нет сформированных рекомендаций
                        </div>
                    </div>
                    <div class="rec-body">Рекомендации формируются на основе выявленных отклонений. В данный момент критических отклонений не зафиксировано, либо недостаточно данных для их расчета.${limitsText}</div>
                </div>`;
                badge.style.display = 'none';
                return;
            }
            
            let html = '';
            recs.forEach(r => {
                let iconSvg = '';
                let cardClass = 'ok';
                if (r.severity === 'bad') { iconSvg = '<svg viewBox="0 0 24 24" width="16" height="16" stroke="#EF4444" fill="none" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'; cardClass = 'critical'; }
                else if (r.severity === 'warning') { iconSvg = '<svg viewBox="0 0 24 24" width="16" height="16" stroke="#F59E0B" fill="none" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'; cardClass = 'warning'; }
                
                html += `<div class="recommendations-card ${cardClass}">
                    <div class="rec-header">
                        <div class="rec-title">${iconSvg} ${esc(r.title)}</div>
                        ${r.metric_id ? `<div class="rec-metric">ID: ${esc(r.metric_id)}</div>` : ''}
                    </div>
                    <div class="rec-body">${esc(r.description)}</div>
                </div>`;
            });
            
            list.innerHTML = html;
            badge.textContent = recs.length;
            badge.style.display = 'inline-block';
        },
        
        cashTooltip(data) {
            const deviationClass = data.deviationClass || (data.required ? 'missing' : 'good');
            return `<div class="metric-rich-tooltip" role="tooltip">
                <div class="metric-rich-title">${esc(data.title)}</div>
                <div class="metric-rich-section">
                    <div class="metric-rich-label">Описание</div>
                    <div>${esc(data.description)}</div>
                </div>
                <div class="metric-rich-section">
                    <div class="metric-rich-label">Формула расчёта</div>
                    <div class="metric-rich-formula">${esc(data.formula)}</div>
                </div>
                <div class="metric-rich-section">
                    <div class="metric-rich-label">Детализация</div>
                    <div>${esc(data.detail)}</div>
                </div>
                ${data.required ? `<div class="metric-rich-section">
                    <div class="metric-rich-label">Требуемые данные из 1С</div>
                    <div class="metric-rich-required">${esc(data.required)}</div>
                </div>` : ''}
                <div class="metric-rich-section">
                    <div class="metric-rich-label">Нормативное значение и факт</div>
                    <div class="metric-rich-facts">
                        <div class="metric-rich-fact-row"><span>Норматив:</span><strong>${esc(data.norm)}</strong></div>
                        <div class="metric-rich-fact-row"><span>Факт:</span><strong>${esc(data.fact)}</strong></div>
                        <div class="metric-rich-fact-row"><span>Отклонение:</span><strong class="${deviationClass}">${esc(data.deviation)}</strong></div>
                    </div>
                </div>
                <div class="metric-rich-source">Основание: ${esc(data.source)}</div>
            </div>`;
        },

        metricTooltipData(card, override) {
            const definitions = {
                cash: {
                    description: 'Денежные средства на расчётных и иных счетах по данным бухгалтерского учёта.',
                    detail: 'Счета 50, 51, 52 и 55. Счёт 52 уже отражён в рублёвой оценке, а средства счёта 55 могут иметь ограничения.',
                    required: 'Для оценки доступности средств нужны банковская аналитика и назначение специальных счетов 55.'
                },
                revenue: {
                    description: 'Выручка от продаж за выбранный период по данным счёта 90.',
                    detail: 'Нетто-выручка определяется только при наличии строк 90.03; иначе показывается брутто-выручка без вымышленного НДС.',
                    required: 'Для плана-факта нужны утверждённый план выручки, тарифы и аналитика 90.01/90.03 по видам услуг и объектам.'
                },
                collection: {
                    description: 'Бухгалтерский прокси собираемости по явным оборотам 62.01 и 62.02.',
                    detail: 'ОСВ не связывает платёж с конкретным начислением. Поэтому это расчётный показатель по субсчетам, а не подтверждённая сумма распределённых оплат.',
                    required: 'Нужны явные обороты ОбД и ОбК по 62.01 и 62.02 за полный выбранный период. Для самостоятельной проверки фактических оплат дополнительно нужны регистр начислений, платежей и их распределения.'
                },
                ar: {
                    description: 'Дебиторская задолженность покупателей по субсчёту 62.01.',
                    detail: 'Прочие дебетовые расчёты счёта 76 не добавляются без утверждённого соответствия аналитик дебиторской задолженности.',
                    required: 'Для полной ДЗ нужны остатки 62 и 76 по контрагентам, договорам, документам расчётов, датам возникновения и срокам оплаты.'
                },
                ap: {
                    description: 'Подтверждённая задолженность перед поставщиками по субсчёту 60.01.',
                    detail: 'Обязательства по налогам, персоналу и прочим расчётам не смешиваются с поставщиками без отдельной классификации.',
                    required: 'Для полной КЗ нужны остатки 60, 68, 69, 70 и 76 по контрагентам, документам, срокам погашения и признаку просрочки.'
                },
                cogs: {
                    description: 'Себестоимость продаж за выбранный период по дебетовому обороту 90.02.',
                    detail: 'Счета 20, 25, 26 и 44 повторно не прибавляются: их закрытие уже отражается на 90.02.',
                    required: 'Для структуры и плана-факта нужны аналитика 90.02 по видам услуг/объектам и утверждённый бюджет затрат.'
                },
                profit: {
                    description: 'Финансовый результат от продаж до отдельных прочих доходов, расходов и налога на прибыль.',
                    detail: 'Это не чистая прибыль. Расчёт использует выручку без НДС и себестоимость продаж без повторного добавления закрытых затрат.',
                    required: 'Для чистой прибыли нужны счета 91, 99 и налог на прибыль; для плана-факта — утверждённый финансовый план.'
                },
                credits: {
                    description: 'Бухгалтерская задолженность по краткосрочным и долгосрочным кредитам и займам.',
                    detail: 'Суммируются подтверждённые кредитовые сальдо счетов 66 и 67; отсутствующий счёт не принимается за ноль.',
                    required: 'Для долговой нагрузки нужны договор, ставка, валюта, график погашения, начисленные проценты и краткосрочная часть счёта 67.'
                },
                salary: {
                    description: 'Расчётная задолженность предприятия перед работниками по счёту 70.',
                    detail: 'Кредитовое сальдо не доказывает просрочку: на отчётную дату это может быть штатное начисление до дня выплаты.',
                    required: 'Для определения просрочки нужны ведомости начисления и выплаты, установленная дата зарплаты и фактические даты платежей.'
                },
                capex: {
                    description: 'Капитализируемые вложения за выбранный период по дебетовому обороту счёта 08.',
                    detail: 'Факт счёта 08 не содержит утверждённый план, готовность объектов и полный инвестиционный денежный поток.',
                    required: 'Нужны утверждённая инвестпрограмма и реестр объектов: план, факт, дата начала, срок ввода, готовность, источник финансирования и статус.'
                }
            };
            const definition = definitions[card.id] || {};
            const formatted = this.formatValue(card.value, card.unit);
            let deviation = 'Нет предыдущего сопоставимого периода';
            if (card.delta && card.delta.value !== null) {
                const delta = this.formatValue(card.delta.value, card.delta.unit || card.unit);
                deviation = `${Number(card.delta.value) > 0 ? '+' : ''}${delta.val} ${delta.u} · ${card.delta.label || 'к предыдущему периоду'}`;
            } else if (card.reason) {
                deviation = 'Расчёт невозможен';
            }
            return Object.assign({
                title: card.title,
                description: card.description || definition.description || 'Фактическое значение показателя по данным бухгалтерского учёта.',
                formula: card.formula || 'Формула не определена',
                detail: [definition.detail, ...(card.notes || [])].filter(Boolean).join(' '),
                required: card.value == null ? (definition.required || card.reason) : null,
                norm: 'Норматив или план предприятия не загружен',
                fact: card.value == null ? 'Н/Д' : `${formatted.val} ${formatted.u}`,
                deviation,
                deviationClass: card.value == null ? 'missing' :
                    (card.status === 'bad' ? 'missing' : 'good'),
                source: card.source || 'загруженные данные 1С'
            }, override || {});
        },

        initRichTooltip() {
            if (document.getElementById('ki-rich-tooltip-portal')) return;
            const portal = document.createElement('div');
            portal.id = 'ki-rich-tooltip-portal';
            portal.setAttribute('aria-hidden', 'true');
            document.body.appendChild(portal);
            this._richTooltipPortal = portal;
            this._richTooltipHost = null;
            const toggle = document.getElementById('ki-tooltip-toggle');
            if (toggle) {
                toggle.addEventListener('click', () => this.toggleTooltips());
            }

            const hostSelector = '.cash-tooltip-host, .cash-summary-card, .cash-analysis-card';
            document.addEventListener('pointerover', event => {
                const host = event.target.closest(hostSelector);
                if (!host || host.contains(event.relatedTarget)) return;
                const source = host.querySelector('.metric-rich-tooltip');
                if (!source) return;
                this._richTooltipHost = host;
                this.showRichTooltipHtml(source.outerHTML, event.clientX, event.clientY);
            });
            document.addEventListener('pointermove', event => {
                if (this._richTooltipHost) this.positionRichTooltip(event.clientX, event.clientY);
            });
            document.addEventListener('pointerout', event => {
                const host = event.target.closest(hostSelector);
                if (!host || host.contains(event.relatedTarget) || host !== this._richTooltipHost) return;
                this.hideRichTooltip();
            });
            document.addEventListener('focusin', event => {
                const host = event.target.closest(hostSelector);
                if (!host) return;
                const source = host.querySelector('.metric-rich-tooltip');
                if (!source) return;
                const rect = host.getBoundingClientRect();
                this._richTooltipHost = host;
                this.showRichTooltipHtml(source.outerHTML, rect.left + rect.width / 2, rect.bottom);
            });
            document.addEventListener('focusout', event => {
                const host = event.target.closest(hostSelector);
                if (host && host === this._richTooltipHost && !host.contains(event.relatedTarget)) {
                    this.hideRichTooltip();
                }
            });
            window.addEventListener('blur', () => this.hideRichTooltip());
            window.addEventListener('scroll', () => this.hideRichTooltip(), true);
        },

        showRichTooltip(data, clientX, clientY) {
            this.showRichTooltipHtml(this.cashTooltip(data), clientX, clientY);
        },

        showRichTooltipHtml(html, clientX, clientY) {
            if (!state.tooltipsEnabled) return;
            const portal = this._richTooltipPortal || document.getElementById('ki-rich-tooltip-portal');
            if (!portal) return;
            portal.innerHTML = html;
            portal.classList.add('visible');
            portal.setAttribute('aria-hidden', 'false');
            this.positionRichTooltip(clientX, clientY);
        },

        positionRichTooltip(clientX, clientY) {
            const portal = this._richTooltipPortal;
            if (!portal || !portal.classList.contains('visible')) return;
            const gap = 18;
            const margin = 8;
            const rect = portal.getBoundingClientRect();
            let left = clientX + gap;
            let top = clientY + gap;
            if (left + rect.width > window.innerWidth - margin) left = clientX - rect.width - gap;
            if (top + rect.height > window.innerHeight - margin) top = clientY - rect.height - gap;
            left = Math.max(margin, Math.min(left, window.innerWidth - rect.width - margin));
            top = Math.max(margin, Math.min(top, window.innerHeight - rect.height - margin));
            portal.style.left = `${left}px`;
            portal.style.top = `${top}px`;
        },

        hideRichTooltip() {
            const portal = this._richTooltipPortal;
            if (portal) {
                portal.classList.remove('visible');
                portal.setAttribute('aria-hidden', 'true');
                portal.innerHTML = '';
            }
            this._richTooltipHost = null;
        },

        cashMetricCard(label, value, sub, tooltip, needsData, seriesId = '',
                valueClass = '', chartMode = '') {
            const chartAttrs = chartMode
                ? ` role="button" aria-pressed="true" data-capex-chart-mode="${esc(chartMode)}"
                    onclick="window.KI_App.toggleCapexChartMode('${esc(chartMode)}')"
                    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCapexChartMode('${esc(chartMode)}')}"`
                : '';
            return `<div class="cash-analysis-card ${needsData ? 'needs-data' : ''} ${chartMode ? 'cash-chart-mode-card cash-chart-mode-selected' : ''}" tabindex="0"
                    ${seriesId ? `data-series-id="${esc(seriesId)}"` : ''}${chartAttrs}>
                <div class="cash-analysis-label">${esc(label)}</div>
                <div class="cash-analysis-value ${needsData ? 'cash-value-missing' : valueClass || 'cash-value-ok'}">${esc(value)}</div>
                <div class="cash-analysis-sub">${esc(sub)}</div>
                ${this.cashTooltip(tooltip)}
            </div>`;
        },

        revenueSegmentDefinitions() {
            return [
                {
                    id: 'population', label: 'Население', code: '90.01 / 90.03',
                    required: 'Аналитика счёта 90.01 с категорией потребителя «Население», документом реализации и суммой; для нетто-выручки — такая же аналитика 90.03.'
                },
                {
                    id: 'uk', label: 'УК/ТСЖ', code: '90.01 / 90.03',
                    required: 'Аналитика 90.01 и 90.03 с типом потребителя «УК/ТСЖ», контрагентом, договором и документом реализации.'
                },
                {
                    id: 'budget', label: 'Бюджет', code: '90.01 / 90.03',
                    required: 'Аналитика 90.01 и 90.03 с явным признаком бюджетного учреждения. Определение категории только по названию контрагента недостаточно.'
                },
                {
                    id: 'legal', label: 'Прочие юридические лица', code: '90.01 / 90.03',
                    required: 'Классификатор категорий покупателей и аналитика 90.01/90.03 по контрагентам и договорам, исключающая население, УК/ТСЖ и бюджет.'
                },
                {
                    id: 'subsidy', label: 'Субсидии', code: '90.01 / 90.03',
                    required: 'Регистр субсидий из 1С: вид субсидии, документ начисления, период признания дохода, сумма, плательщик и корреспондирующие счета. Остаток счёта 76 сам по себе не является выручкой.'
                }
            ];
        },

        revenueSegmentTooltip(segment, fact, share) {
            const missing = fact === null;
            const unavailableColumns = 'Для собираемости нужен регистр распределения оплат по начислениям; для отклонения от плана — утверждённый план группы; для АПГ — полные ОСВ за аналогичный период прошлого года с той же аналитикой.';
            return {
                title: `Выручка: ${segment.label}`,
                description: `Выручка по категории потребителей «${segment.label}».`,
                formula: 'Выручка группы = ОбК(90.01 группы) − ОбД(90.03 группы)',
                detail: 'Расчёт допустим только при однозначной и одинаковой аналитике группы на счетах 90.01 и 90.03.',
                required: `${missing ? `${segment.required} ` : ''}${unavailableColumns}`,
                norm: 'План группы не загружен',
                fact: missing ? 'Н/Д' : `${this.formatValue(fact, 'RUB').val} млн ₽ · ${share}`,
                deviation: missing ? 'Группа не подтверждена аналитикой ОСВ' : 'Отклонение от плана недоступно',
                deviationClass: missing ? 'missing' : 'good',
                source: missing ? 'требуется расширенная аналитика 1С' : 'аналитические строки ОСВ счёта 90'
            };
        },

        renderRevenueModal(card) {
            const container = document.getElementById('ki-revenue-modal-content');
            const grossDetail = (card.details || []).find(item => item.label === 'Выручка брутто');
            const vatDetail = (card.details || []).find(item => item.label === 'НДС');
            const gross = grossDetail && grossDetail.value !== null ? Number(grossDetail.value) : null;
            const vat = vatDetail && vatDetail.value !== null ? Number(vatDetail.value) : null;
            const total = card.value !== null ? Number(card.value) : null;
            const totalFormatted = this.formatValue(total, 'RUB');
            const grossFormatted = this.formatValue(gross, 'RUB');
            const vatFormatted = this.formatValue(vat, 'RUB');
            const definitions = this.revenueSegmentDefinitions();
            const segmentData = state.data.revenue_segments || {};
            const activityData = segmentData.activity_groups || {};
            const activityRows = activityData.reconciled ? (activityData.rows || []) : [];
            const showActivityRows = activityRows.length > 0;
            const yearOverYear = card.year_over_year || null;
            const selected = state.revenueSelection;
            const segmentRows = definitions.map(definition => {
                const saved = (segmentData.categories || []).find(row => row.id === definition.id);
                return {
                    ...definition,
                    value: saved && saved.value !== null ? Number(saved.value) : null
                };
            });
            const unclassified = segmentData.unclassified || [];
            const conflicts = segmentData.conflicts || [];
            const unknownAmounts = segmentData.unknown_amounts || [];
            const totalTooltip = this.metricTooltipData(card);
            const grossTooltip = this.metricTooltipData(card, {
                title: 'Выручка с НДС',
                description: 'Начисленная выручка до вычета НДС.',
                formula: 'Выручка с НДС = ОбК(90.01*)',
                detail: 'Суммируются только конечные аналитические строки 90.01, без одновременного сложения родительских итогов.',
                fact: gross === null ? 'Н/Д' : `${grossFormatted.val} ${grossFormatted.u}`,
                required: gross === null ? 'Суточная ОСВ счёта 90 с аналитическими строками точного префикса 90.01 и кредитовым оборотом.' : null,
                source: 'кредитовый оборот 90.01'
            });
            const vatTooltip = this.metricTooltipData(card, {
                title: 'НДС с выручки',
                description: 'НДС, уменьшающий брутто-выручку до бухгалтерской выручки без НДС.',
                formula: 'НДС = ОбД(90.03*)',
                detail: 'Для распределения НДС по группам аналитика 90.03 должна совпадать с аналитикой 90.01.',
                fact: vat === null ? 'Н/Д' : `${vatFormatted.val} ${vatFormatted.u}`,
                required: vat === null ? 'Суточная ОСВ счёта 90 с аналитическими строками 90.03, дебетовым оборотом и тем же разрезом по группам, что у 90.01.' : null,
                deviation: vat === null ? 'Нетто-выручка недоступна' : 'Учтён в расчёте нетто-выручки',
                deviationClass: vat === null ? 'missing' : 'good',
                source: 'дебетовый оборот 90.03'
            });
            const planTooltip = {
                title: 'Отклонение выручки от плана',
                description: 'Сравнение фактической выручки с утверждённым планом за тот же период и в той же базе НДС.',
                formula: 'Отклонение, % = (Факт / План − 1) × 100%',
                detail: 'План должен иметь период, версию утверждения и тот же состав групп, что фактическая выручка.',
                required: 'Утверждённый план выручки из 1С по месяцам и группам: сумма, база с НДС/без НДС, версия плана, дата утверждения и подразделение.',
                norm: 'Факт ≥ утверждённого плана',
                fact: 'Н/Д',
                deviation: 'План не загружен',
                deviationClass: 'missing',
                source: 'требуется регистр или документ финансового планирования 1С'
            };
            const collectionRequired = 'Регистр начислений и оплат: потребитель, договор, расчётный период, документ начисления, дата и сумма оплаты, распределение платежа, аванс, корректировка и сторно.';
            let rowsHtml = '';
            if (showActivityRows) {
                activityRows.forEach(row => {
                    const share = row.value !== null && total
                        ? `${(row.value / total * 100).toFixed(1)}%` : '—';
                    rowsHtml += `<tr>
                        <td>${esc(row.label)}</td>
                        <td>${row.gross === null ? 'Нужны данные' : esc(this.formatValue(row.gross, 'RUB').val)}</td>
                        <td>${row.vat === null ? 'Нужны данные' : esc(this.formatValue(row.vat, 'RUB').val)}</td>
                        <td>${row.value === null ? 'Нужны данные' : esc(this.formatValue(row.value, 'RUB').val)}</td>
                        <td>${esc(share)}</td>
                    </tr>`;
                });
            } else {
                segmentRows.forEach(segment => {
                    const share = segment.value !== null && total ? `${(segment.value / total * 100).toFixed(1)}%` : '—';
                    const tooltip = this.cashTooltip(this.revenueSegmentTooltip(segment, segment.value, share));
                    rowsHtml += `<tr class="cash-tooltip-host" tabindex="0">
                        <td><div class="obj-name-wrap"><span>${esc(segment.label)}</span><span class="obj-code">${esc(segment.code)}</span></div>${tooltip}</td>
                        <td>${segment.value === null ? 'Н/Д' : `${this.formatValue(segment.value, 'RUB').val}`}</td>
                        <td>${esc(share)}</td>
                    </tr>`;
                });
            }
            const consumerTableTooltip = {
                title: 'Итого выручка',
                description: 'Общая бухгалтерская выручка за выбранный период.',
                formula: vat === null ? 'ОбК(90.01*), брутто' : 'ОбК(90.01*) − ОбД(90.03*)',
                detail: unclassified.length
                    ? `Не сопоставлены: ${unclassified.map(row => `${row.account}: ${row.analytic_label}`).join(', ')}.`
                    : 'Все найденные аналитики сопоставлены с категориями.',
                required: !segmentData.reconciled
                    ? 'Нужен утверждённый классификатор соответствия аналитик 90.01 категориям потребителей.'
                    : null,
                norm: 'План не загружен',
                fact: `${totalFormatted.val} ${totalFormatted.u}`,
                deviation: card.delta ? `${this.formatValue(card.delta.value, 'RUB').val} млн ₽ к предыдущему сопоставимому периоду` : 'Нет сопоставимого периода',
                source: card.source
            };
            const activityTableTooltip = {
                title: 'Итого по видам деятельности',
                description: 'Подтверждённая выручка по совпадающим листовым путям аналитики 90.01 и 90.03.',
                formula: 'Выручка без НДС = Брутто 90.01 − НДС 90.03',
                detail: 'Каждый листовой путь 90.01 сопоставлен с тем же полным путём 90.03; служебные узлы счетов и «<...>» не образуют отдельные группы.',
                required: null,
                norm: 'Сумма строк должна совпадать с итогом периода',
                fact: `${grossFormatted.val} ${grossFormatted.u} − ${vatFormatted.val} ${vatFormatted.u} = ${totalFormatted.val} ${totalFormatted.u}`,
                deviation: 'Сверка выполнена',
                deviationClass: 'good',
                source: card.source
            };
            const tableTooltip = this.cashTooltip(
                showActivityRows ? activityTableTooltip : consumerTableTooltip);

            document.getElementById('ki-modal-title').textContent = 'Выручка по видам деятельности и группам';
            this.updateModalSubtitle();
            const toggle = (id, label, formatted, sub, tooltip, missing) => `
                <button type="button" class="cash-summary-card cash-chart-mode-card cash-tooltip-host revenue-metric-toggle ${selected.has(id) ? 'cash-chart-mode-selected' : ''}"
                    data-revenue-metric="${id}" aria-pressed="${selected.has(id) ? 'true' : 'false'}">
                    <span class="cash-summary-label">${label}</span>
                    <span class="cash-summary-value ${missing ? 'cash-value-missing' : ''}">${missing ? 'Нужны данные' : formatted}</span>
                    <span class="cash-summary-sub">${sub}</span>${this.cashTooltip(tooltip)}
                </button>`;
            const yoyTooltip = {
                title: 'Сравнение с АПГ',
                description: `Два подтверждённых итога за сопоставимые периоды в базе ${yearOverYear
                    ? (yearOverYear.basis === 'net' ? 'без НДС' : 'брутто')
                    : 'которая будет указана после получения данных'}.`,
                formula: 'Δ АПГ = текущий период − аналогичный период прошлого года',
                detail: `Текущий период: ${this.displayedPeriodLabel()}. Изменение является аннотацией, а не отдельной выручкой.`,
                required: yearOverYear ? null : 'Полные ОСВ счёта 90 за оба сопоставимых периода в одинаковой базе НДС.',
                fact: yearOverYear ? `${this.formatValue(yearOverYear.current, 'RUB').val} млн ₽ против ${this.formatValue(yearOverYear.previous, 'RUB').val} млн ₽` : 'Н/Д',
                source: 'сопоставимые итоговые обороты ОСВ счёта 90'
            };
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Фактическая выручка предприятия.</strong>
                    Выручка без НДС рассчитывается как ОбК(90.01*) − ОбД(90.03*). Если 90.03 отсутствует, факт показывается как брутто.</div>
                <div class="cash-summary-grid">
                    ${toggle('gross', 'Выручка с НДС', `${esc(grossFormatted.val)} <span class="metric-inline-unit">${esc(grossFormatted.u)}</span>`, 'ОбК 90.01', grossTooltip, gross === null)}
                    ${toggle('vat', 'НДС', `${esc(vatFormatted.val)} <span class="metric-inline-unit">${esc(vatFormatted.u)}</span>`, vat === null ? esc(vatDetail && vatDetail.reason || 'нет аналитики 90.03') : 'вычитается · ОбД 90.03', vatTooltip, vat === null)}
                    ${toggle('total', 'Выручка без НДС', `${esc(totalFormatted.val)} <span class="metric-inline-unit">${esc(totalFormatted.u)}</span>`, vat === null ? 'база не подтверждена' : 'ОбК 90.01 − ОбД 90.03', totalTooltip, total === null)}
                    ${toggle('yoy', 'Сравнение с АПГ', yearOverYear
                        ? (yearOverYear.percent === null ? '—' : `${yearOverYear.percent >= 0 ? '+' : ''}${esc(yearOverYear.percent.toFixed(2))}%`)
                        : '', 'два сопоставимых периода', yoyTooltip, !yearOverYear)}
                </div>
                <div id="ki-revenue-chart" class="revenue-interactive-chart" aria-live="polite"></div>
                <div id="ki-revenue-summary-table"></div>
                <div class="modal-section-title">Детализация выручки по группам</div>
                ${!showActivityRows && (unclassified.length || conflicts.length || unknownAmounts.length || !segmentData.reconciled) ? `<div class="revenue-classification-note">
                    ${unclassified.length ? `Новые аналитики без категории: ${esc(unclassified.map(row => `${row.account}: ${row.analytic_label}`).join(', '))}.` : ''}
                    ${conflicts.length ? ` Конфликты: ${esc(conflicts.map(row => row.message + ' — ' + row.analytic_label).join('; '))}.` : ''}
                    ${unknownAmounts.length ? ` Нет суммы по аналитикам: ${esc(unknownAmounts.map(row => `${row.account}: ${row.analytic_label}`).join(', '))}. Значения не заменены нулём.` : ''}
                    ${segmentData.reconciliation_difference !== null && segmentData.reconciliation_difference !== undefined && Math.abs(segmentData.reconciliation_difference) > 0.01 ? ` Расхождение с общей выручкой: ${esc(this.formatValue(segmentData.reconciliation_difference, 'RUB').val)} млн ₽.` : ''}
                </div>` : ''}
                ${config.canEditRevenueMappings && !showActivityRows ? this.renderRevenueMappingEditor(segmentData.display_analytics || []) : ''}
                <div class="revenue-table-wrap"><table class="cash-table revenue-table" data-ki-table-sync="revenue-analytics">
                    ${showActivityRows
                        ? `<thead><tr><th>Вид деятельности</th><th>Брутто, млн ₽</th><th>НДС, млн ₽</th><th>Без НДС, млн ₽</th><th>Доля</th></tr></thead>
                           <tbody>${rowsHtml}<tr class="cash-total-row cash-tooltip-host" tabindex="0"><td>ИТОГО${tableTooltip}</td><td>${esc(grossFormatted.val)}</td><td>${esc(vatFormatted.val)}</td><td>${esc(totalFormatted.val)}</td><td>100%</td></tr></tbody>`
                        : `<thead><tr><th>Группа</th><th>Выручка, млн ₽</th><th>Доля</th></tr></thead>
                           <tbody>${rowsHtml}<tr class="cash-tooltip-host" tabindex="0"><td>ИТОГО${tableTooltip}</td><td>${esc(totalFormatted.val)}</td><td>100%</td></tr></tbody>`}
                </table></div>
                <div class="revenue-missing-grid">
                    ${this.cashMetricCard('Уровень сбора', 'Нужны данные', 'оплаты / начисления', {
                        title: 'Уровень сбора платежей', description: 'Доля начислений выбранного периода, погашенных оплатами.',
                        formula: 'Сбор = Оплаты, распределённые на начисления / Начисления × 100%',
                        detail: 'Обороты счёта 62 без распределения платежей не являются достоверной собираемостью.',
                        required: collectionRequired, norm: 'Утверждённый норматив не загружен', fact: 'Н/Д',
                        deviation: 'Расчёт невозможен', deviationClass: 'missing', source: 'требуется регистр взаиморасчётов 1С'
                    }, true)}
                    ${this.cashMetricCard('План выручки', 'Нужны данные', 'утверждённый план по группам', planTooltip, true)}
                    ${this.cashMetricCard('Сравнение с АПГ',
                        yearOverYear ? `${yearOverYear.delta >= 0 ? '+' : ''}${this.formatValue(yearOverYear.delta, 'RUB').val} млн ₽` : 'Нужны данные',
                        yearOverYear ? `${yearOverYear.percent === null ? '—' : `${yearOverYear.percent >= 0 ? '+' : ''}${yearOverYear.percent.toFixed(1)}%`} · ${yearOverYear.basis === 'net' ? 'без НДС' : 'брутто'}` : 'аналогичный период прошлого года', {
                        title: 'Изменение к аналогичному периоду', description: 'Сравнение факта с тем же календарным периодом предыдущего года.',
                        formula: 'Δ АПГ = Факт текущего периода − Факт аналогичного периода прошлого года',
                        detail: 'Периоды должны быть полностью покрыты и рассчитаны в одинаковой базе НДС.',
                        required: yearOverYear ? null : 'Полные ОСВ счёта 90 за текущий и аналогичный период прошлого года с одинаковой аналитикой 90.01 и 90.03.',
                        norm: 'Индивидуальный норматив не задан',
                        fact: yearOverYear ? `${this.formatValue(yearOverYear.current, 'RUB').val} млн ₽ против ${this.formatValue(yearOverYear.previous, 'RUB').val} млн ₽` : 'Н/Д',
                        deviation: yearOverYear ? `${yearOverYear.delta >= 0 ? '+' : ''}${this.formatValue(yearOverYear.delta, 'RUB').val} млн ₽` : 'АПГ не рассчитан',
                        deviationClass: yearOverYear ? (yearOverYear.delta >= 0 ? 'good' : 'warning') : 'missing',
                        source: yearOverYear ? 'сопоставимые периоды ОСВ счёта 90' : 'требуются сопоставимые периоды ОСВ счёта 90'
                    }, !yearOverYear, '', yearOverYear && yearOverYear.delta < 0 ? 'cash-value-negative' : '')}
                </div>
                `;
            this.bindRevenueChart(card);
            this.renderRevenueChart(card);
        },

        bindRevenueChart(card) {
            const container = document.getElementById('ki-revenue-modal-content');
            if (!container) return;
            container.querySelectorAll('[data-revenue-metric]').forEach(button => {
                button.addEventListener('click', () => {
                    const id = button.dataset.revenueMetric;
                    if (state.revenueSelection.has(id)) state.revenueSelection.delete(id);
                    else state.revenueSelection.add(id);
                    button.setAttribute('aria-pressed', state.revenueSelection.has(id) ? 'true' : 'false');
                    button.classList.toggle('cash-chart-mode-selected', state.revenueSelection.has(id));
                    this.renderRevenueChart(card);
                });
            });
        },

        renderRevenueChart(card) {
            const chart = document.getElementById('ki-revenue-chart');
            const table = document.getElementById('ki-revenue-summary-table');
            if (!chart || !table) return;
            const details = Object.fromEntries((card.details || []).map(item => [item.label, item.value]));
            const values = {
                gross: hasNumericValue(details['Выручка брутто']) ? Number(details['Выручка брутто']) : null,
                vat: hasNumericValue(details['НДС']) ? Number(details['НДС']) : null,
                total: hasNumericValue(card.value) ? Number(card.value) : null
            };
            const yoy = card.year_over_year || null;
            const selected = state.revenueSelection;
            const million = value => `${this.formatValue(value, 'RUB').val} млн ₽`;
            const structureIds = ['gross', 'vat', 'total'].filter(id => selected.has(id));
            const max = Math.max(1, ...structureIds.map(id => values[id]).filter(hasNumericValue),
                ...(selected.has('yoy') && yoy ? [yoy.current, yoy.previous] : []));
            const bar = (label, value, kind, note = '') => value === null
                ? `<div class="revenue-chart-missing"><strong>${label}</strong><span>Нужны данные</span></div>`
                : `<div class="revenue-struct-row ${kind}"><div class="revenue-struct-label"><strong>${label}</strong><span>${note}</span></div>
                    <div class="revenue-bar-track"><span style="width:${Math.max(3, value / max * 100)}%"></span></div><b>${million(value)}</b></div>`;
            const structure = structureIds.length ? `<section class="revenue-chart-panel" aria-label="Структура текущей выручки">
                <h3>Структура текущей выручки</h3>
                ${structureIds.map(id => bar(
                    id === 'gross' ? 'Выручка с НДС' : id === 'vat' ? 'НДС' :
                        'Выручка без НДС',
                    values[id], id, id === 'vat' ? 'вычитается из брутто' : this.displayedPeriodLabel())).join('')}
                ${structureIds.length === 3 && values.gross !== null && values.vat !== null && values.total !== null
                    ? `<div class="revenue-equation">${million(values.gross)} − ${million(values.vat)} = ${million(values.total)}</div>` : ''}
                </section>` : '';
            const comparison = selected.has('yoy') ? `<section class="revenue-chart-panel revenue-yoy-panel" aria-label="Сравнение с аналогичным периодом">
                <h3>Сравнение периодов · ${yoy
                    ? (yoy.basis === 'net' ? 'без НДС' : 'брутто')
                    : 'база не подтверждена'}</h3>
                ${yoy ? `${bar('Текущий период', Number(yoy.current), 'current', this.displayedPeriodLabel())}
                    ${bar('Аналогичный период прошлого года', Number(yoy.previous), 'previous', `${yoy.start} — ${yoy.end}`)}
                    <div class="revenue-yoy-annotation">${yoy.delta >= 0 ? '+' : ''}${million(yoy.delta)} · ${yoy.percent === null
                        ? 'процент не рассчитывается при нулевой базе'
                        : `${yoy.percent >= 0 ? '+' : ''}${yoy.percent.toFixed(2)}%`}</div>`
                    : '<div class="revenue-chart-missing"><strong>Сравнение с АПГ</strong><span>Нужны данные</span></div>'}
                </section>` : '';
            chart.innerHTML = structure || comparison ? `<div class="revenue-chart-sections">${structure}${comparison}</div>`
                : '<div class="chart-empty">Выберите показатель для графика</div>';
            const rows = [];
            if (selected.has('gross')) rows.push(['Выручка с НДС', values.gross, null, null, null]);
            if (selected.has('vat')) rows.push(['НДС (вычитается)', values.vat, null, null, null]);
            if (selected.has('total')) rows.push([
                values.vat === null ? 'Выручка без НДС (база не подтверждена)' : 'Выручка без НДС',
                values.total, null, null, null]);
            if (selected.has('yoy')) rows.push([
                !yoy ? 'Сравнение с АПГ (база не подтверждена)' :
                    (yoy.basis === 'gross' ? 'Сравнение с АПГ (брутто)' : 'Сравнение с АПГ (без НДС)'),
                yoy ? yoy.current : null, yoy ? yoy.previous : null, yoy ? yoy.delta : null,
                yoy ? yoy.percent : null,
                yoy && yoy.percent === null && Number(yoy.previous) === 0
                    ? 'Не рассчитывается при нулевой базе' : null]);
            const cell = (value, percent = false, unavailableLabel = null) => value === null || value === undefined
                ? `<span class="cash-value-missing">${unavailableLabel || 'Нужны данные'}</span>`
                : `${value >= 0 && percent ? '+' : ''}${percent ? Number(value).toFixed(2) + '%' : million(value)}`;
            table.innerHTML = `<div class="modal-section-title">Подтверждённые итоги</div><div class="cash-table-scroll" tabindex="0">
                <table class="cash-table revenue-summary-table"><thead><tr><th>Показатель</th><th>Текущий период</th><th>АПГ</th><th>Изменение</th><th>Изменение, %</th></tr></thead>
                <tbody>${rows.map(row => `<tr><td>${row[0]}</td><td>${cell(row[1])}</td><td>${cell(row[2])}</td><td>${cell(row[3])}</td><td>${cell(row[4], true, row[5])}</td></tr>`).join('')}</tbody></table></div>`;
        },

        renderRevenueMappingEditor(analytics) {
            if (!analytics.length) return '';
            const rubAmount = value => new Intl.NumberFormat('ru-RU', {
                minimumFractionDigits: 2, maximumFractionDigits: 2
            }).format(value / 1000000);
            const percent = value => new Intl.NumberFormat('ru-RU', {
                minimumFractionDigits: 2, maximumFractionDigits: 2
            }).format(value) + '%';
            const knownTotal = analytics.reduce((sum, item) =>
                item.gross_amount == null ? sum : sum + Number(item.gross_amount), 0);
            const hasKnownAmount = analytics.some(item => item.gross_amount != null);
            const rows = analytics.map(item => `
                <tr><td>${esc(item.analytic_label)}</td>
                <td class="num">${item.gross_amount == null ? 'Нет данных' :
                    esc(rubAmount(Number(item.gross_amount)))}</td>
                <td class="num">${item.gross_amount == null ? 'Нет данных' :
                    esc(percent(knownTotal ? Number(item.gross_amount) / knownTotal * 100 : 0))}</td></tr>`).join('');
            return `<div class="modal-section-title">Сопоставление аналитик предприятия</div>
                <div class="revenue-table-wrap"><table class="cash-table revenue-mapping-table" data-ki-table-sync="revenue-analytics">
                <thead><tr><th>Вид деятельности</th><th>Сумма с НДС, млн ₽</th><th>Доля, %</th></tr></thead>
                <tbody>${rows}<tr class="revenue-mapping-total"><td>Итого</td>
                <td class="num">${hasKnownAmount ? esc(rubAmount(knownTotal)) : 'Нет данных'}</td>
                <td class="num">${hasKnownAmount ? '100,00%' : 'Нет данных'}</td></tr></tbody></table></div>`;
        },

        async saveRevenueMappings() {
            const companySwitchToken = state.companySwitchToken;
            const mappingsUrl = config.mappingsUrl;
            const status = document.getElementById('revenue-mapping-status');
            const mappings = Array.from(document.querySelectorAll('.revenue-mapping-select'))
                .map(select => ({
                    account: select.dataset.account,
                    analytic_key: select.dataset.key,
                    analytic_label: select.dataset.label,
                    category: select.value
                }));
            status.textContent = 'Сохранение...';
            try {
                const response = await fetch(mappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (companySwitchToken !== state.companySwitchToken) return;
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                if (companySwitchToken !== state.companySwitchToken) return;
                this.openModal('revenue');
            } catch (error) {
                if (companySwitchToken !== state.companySwitchToken) return;
                status.textContent = error.message;
            }
        },

        renderRevenueLineChart(card) {
            const container = document.getElementById('ki-revenue-line-chart');
            if (!container) return;
            const points = (card.series || []).map((item, index) => ({
                ...item, index, label: item.label,
                value: hasNumericValue(item.value)
                    ? Number(item.value) / 1000000 : null
            }));
            const valid = points.filter(item => hasNumericValue(item.value));
            if (!valid.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const width = 820, height = 220, left = 50, right = 15, top = 16, bottom = 36;
            let min = Math.min(0, ...valid.map(item => item.value));
            let max = Math.max(0, ...valid.map(item => item.value));
            if (min === max) max = min + 1;
            const timeline = chartTimeline(points);
            const plotWidth = width - left - right;
            const x = index => left + (timeline.calendar
                ? timeline.x(index) * plotWidth
                : timeline.x(index, plotWidth));
            const y = value => top + (max - value) * (height - top - bottom) / (max - min);
            let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика выручки">`;
            if (valid.length === 1) svg += '<text x="70" y="14" font-size="10" fill="#6B7280">Одна точка данных: для линии нужны данные внутри периода</text>';
            [min, (min + max) / 2, max].forEach(value => {
                const py = y(value);
                svg += `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#E5E7EB" stroke-dasharray="3 3"/>
                    <text x="${left-6}" y="${py+4}" text-anchor="end" font-size="10" fill="#6B7280">${esc(value.toFixed(1))}</text>`;
            });
            const paths = chartLinePaths(points, x, y, item => item.value, timeline);
            svg += `<path d="${paths.solid}" class="revenue-chart-line-solid" fill="none" stroke="#10B981" stroke-width="3"/>`;
            if (paths.dashed) {
                svg += `<path d="${paths.dashed}" class="revenue-chart-line-gap" fill="none" stroke="#10B981" stroke-width="3" stroke-dasharray="5 4"/>`;
                svg += `<text class="chart-gap-help" x="${width-right}" y="14" font-size="10" fill="#6B7280" text-anchor="end">Пунктир — пропуск данных, не факт</text>`;
            }
            points.forEach((item, index) => {
                if (hasNumericValue(item.value)) {
                    svg += `<circle class="revenue-chart-point" data-index="${index}" cx="${x(index)}" cy="${y(item.value)}" r="5" fill="#10B981" tabindex="0"/>`;
                }
                if (index % Math.max(1, Math.ceil(points.length / 8)) === 0 || index === points.length - 1)
                    svg += `<text x="${x(index)}" y="${height-10}" text-anchor="middle" font-size="9" fill="#6B7280">${esc(item.label)}</text>`;
            });
            container.innerHTML = svg + '</svg>';
            container.querySelectorAll('.revenue-chart-point').forEach(point => {
                const tooltipData = () => {
                    const item = points[Number(point.dataset.index)];
                    const previous = item.index > 0 ? points[item.index - 1] : null;
                    const change = previous && previous.value !== null ? item.value - previous.value : null;
                    return this.metricTooltipData(card, {
                        title: `Выручка — ${item.label}`,
                        fact: `${item.value.toFixed(2)} млн ₽`,
                        deviation: change === null ? 'Нет предыдущей точки' : `${change >= 0 ? '+' : ''}${change.toFixed(2)} млн ₽ к предыдущей точке`,
                        detail: 'Точка отражает фактическую выручку за интервал динамики. Пропущенные периоды не заменяются нулём.'
                    });
                };
                point.addEventListener('pointerenter', event => {
                    this._richTooltipHost = point;
                    this.showRichTooltip(tooltipData(), event.clientX, event.clientY);
                });
                point.addEventListener('pointermove', event => this.positionRichTooltip(event.clientX, event.clientY));
                point.addEventListener('pointerleave', () => this.hideRichTooltip());
                point.addEventListener('focus', () => {
                    const rect = point.getBoundingClientRect();
                    this._richTooltipHost = point;
                    this.showRichTooltip(tooltipData(), rect.left + rect.width / 2, rect.bottom);
                });
                point.addEventListener('blur', () => this.hideRichTooltip());
            });
        },

        collectionRequirements() {
            return {
                fact: 'ОСВ с явными дебетовыми и кредитовыми оборотами 62.01 и 62.02 за выбранный период; отсутствие любого из четырёх компонентов означает Н/Д.',
                linked: 'Для подтверждённой собираемости нужны регистр начислений и оплат, документы платежей и их распределение по начислениям. ОСВ этого связывания не содержит.',
                plan: 'Утверждённый план собираемости из 1С или системы бюджетирования: предприятие, сегмент потребителей, месяц, плановый процент, версия плана, дата утверждения, ответственное лицо и период действия.',
                previousYear: 'Те же регистры начислений и распределённых оплат за полный аналогичный период прошлого года, рассчитанные по тем же сегментам, услугам и правилам учёта авансов, корректировок и сторно.',
                segments: 'Справочник соответствия аналитик счёта 62 категориям потребителей: контрагент, договор, лицевой счёт, вид договора, категория «Население / УК-ТСЖ / Бюджет / Прочие юрлица / Субсидии», дата начала и окончания действия категории.',
                subsidy: 'Регистр субсидий и компенсаций: бюджет, программа, получатель, расчётный период, документ начисления дохода, заявленная сумма, подтверждённая сумма, дата поступления, платёжный документ и распределение поступления по начислениям.'
            };
        },

        collectionTooltip(overrides = {}) {
            const required = this.collectionRequirements();
            return Object.assign({
                title: 'Собираемость по ОСВ (бухгалтерский прокси)',
                description: 'Показатель по четырём явным компонентам оборотов 62.01 и 62.02.',
                formula: '(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%',
                detail: 'Все четыре компонента обязательны, а ОбД(62.01) не должен быть нулевым. Это не заменяет подтверждённые оплаты, распределённые по начислениям.',
                required: required.fact,
                norm: 'Норматив не применяется к бухгалтерскому прокси',
                fact: 'Н/Д',
                deviation: 'Недостаточно явных оборотов 62.01/62.02',
                deviationClass: 'missing',
                source: 'явные обороты ОСВ 62.01 и 62.02'
            }, overrides);
        },

        collectionCell(content, tooltip, className = '') {
            return `<td class="cash-tooltip-host ${className}" tabindex="0">${content}${this.cashTooltip(tooltip)}</td>`;
        },

        renderCollectionModal(card) {
            const container = document.getElementById('ki-collection-modal-content');
            if (!container) return;
            const modalTitle = document.getElementById('ki-modal-title');
            if (modalTitle) modalTitle.textContent = 'Уровень собираемости';
            this.updateModalSubtitle();

            const components = card.components || card.collection_components || [];
            const linked = card.consumer_collection || {};
            const proxyTurnover = card.accounting_proxy &&
                card.accounting_proxy.value !== undefined
                ? card.accounting_proxy.value : card.value;
            const turnover = linked.available ? linked.value : proxyTurnover;
            const rub = value => value === null || value === undefined ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined ? 'Н/Д' : `${Number(value).toFixed(1)}%`;

            const getComp = (acc, side) => components.find(c =>
                (String(c.account || '').includes(acc) || String(c.label || '').includes(acc)) && c.side === side
            );

            const getVal = c => c ? (c.value !== undefined && c.value !== null ? c.value : (c.side === 'debit' ? c.debit : c.credit)) : null;

            const obd6201 = getVal(getComp('62.01', 'debit'));
            const obk6201 = getVal(getComp('62.01', 'credit'));
            const obd6202 = getVal(getComp('62.02', 'debit'));
            const obk6202 = getVal(getComp('62.02', 'credit'));

            const net6202 = (obd6202 !== null && obk6202 !== null && obd6202 !== undefined && obk6202 !== undefined)
                ? (obk6202 - obd6202)
                : null;
            const proxyPaid = (obk6201 !== null && obk6201 !== undefined && net6202 !== null)
                ? obk6201 + net6202
                : null;
            const currentAccrual = linked.available ? linked.accrual : obd6201;
            const currentPaid = linked.available ? linked.payment : proxyPaid;
            const previousYearLevel = card.previous_year_value;
            const previousYearDelta = card.previous_year_delta;
            const deltaPp = previousYearDelta === null || previousYearDelta === undefined
                ? 'Н/Д'
                : `${previousYearDelta > 0 ? '+' : ''}${Number(previousYearDelta).toFixed(1)} п.п.`;

            const bannerHtml = `
                <div class="ki-modal-info-banner" style="background: #F8FAFC; border-radius: 8px; padding: 16px; margin-bottom: 16px; border: 1px solid #E5E7EB;">
                    <div style="font-size: 13px; color: #1E3A5F; font-weight: 500; margin-bottom: 16px; display: flex; align-items: flex-start; gap: 8px;">
                        <svg style="flex-shrink: 0; color: #3B82F6; margin-top: 2px;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
                        <div>
                            ${linked.available
                                ? 'Фактическая собираемость рассчитана по начислениям выбранной когорты и распределённым оплатам не позднее даты отсечения.'
                                : 'Собираемость по ОСВ рассчитана как бухгалтерский прокси.'}
                            <span style="color: #64748B;"> Контрольный прокси ОСВ:</span>
                            <span style="background: #EFF6FF; color: #1D4ED8; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 11px; margin-left: 4px;">(ОбК(62.01) + ОбК(62.02) − ОбД(62.02)) / ОбД(62.01) × 100%</span>
                            <div style="margin-top:6px;color:#64748B;">Корень 62 и отсутствующие компоненты не заменяют фактические обороты нулём.</div>
                        </div>
                    </div>
                    <div class="collection-summary">
                        ${this.cashMetricCard('УРОВЕНЬ СОБИРАЕМОСТИ', pct(turnover), linked.available ? 'по связанным оплатам' : 'из компонентов ОСВ', this.collectionTooltip({title: 'Уровень собираемости', fact: pct(turnover)}), turnover === null, '__main__')}
                        ${this.cashMetricCard('НАЧИСЛЕНО', rub(currentAccrual), linked.available ? 'когорта выбранного периода' : 'дебетовый оборот 62.01', this.collectionTooltip({title: 'Начислено', fact: rub(currentAccrual)}), currentAccrual === null)}
                        ${this.cashMetricCard('ОПЛАЧЕНО', rub(currentPaid), linked.available ? 'распределённые оплаты' : 'ОбК 62 − ОбД 62.02', this.collectionTooltip({title: 'Оплачено', fact: rub(currentPaid)}), currentPaid === null)}
                        ${this.cashMetricCard('УРОВЕНЬ АПГ', pct(previousYearLevel), deltaPp, this.collectionTooltip({title: 'Уровень собираемости АПГ', fact: pct(previousYearLevel), deviation: previousYearDelta === null || previousYearDelta === undefined ? 'Сравнение недоступно' : `${deltaPp} к АПГ`}), previousYearLevel === null || previousYearLevel === undefined, '__apg__')}
                    </div>
                </div>`;

            const segmentNoticeHtml = linked.available ? `
                <div style="background: ${linked.reconciled ? '#ECFDF5' : '#FFF7ED'}; border-left: 4px solid ${linked.reconciled ? '#10B981' : '#F97316'}; border-radius: 4px; padding: 12px 16px; margin-bottom: 16px; font-size: 12px; color: #374151;">
                    Фактическая собираемость по связанным начислениям: <strong>${esc(pct(linked.value))}</strong>.
                    Начислено ${esc(rub(linked.accrual))}, распределено оплат ${esc(rub(linked.payment))}.
                    ${linked.reconciliation_difference === null || linked.reconciliation_difference === undefined
                        ? 'Сверка с общим показателем ОСВ недоступна.'
                        : `Расхождение с общим показателем ОСВ: <strong>${esc(Number(linked.reconciliation_difference).toFixed(2))} п.п.</strong>`}
                    ${linked.rejected && linked.rejected.length ? ` Не включено строк: ${esc(linked.rejected.length)}.` : ''}
                </div>` : `
                <div style="background: #F9FAFB; border-left: 4px solid #9CA3AF; border-radius: 4px; padding: 12px 16px; margin-bottom: 24px; font-size: 12px; color: #4B5563; display: flex; align-items: center; gap: 8px;">
                    <svg style="flex-shrink: 0;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                    Детализация по сегментам потребителей недоступна: отсутствуют регистры начислений и распределения оплат.
                </div>`;

            const segmentRows = (linked.categories || []).map(row => {
                const plan = row.plan ? pct(row.plan.value) : 'Н/Д';
                const deviation = row.deviation === null || row.deviation === undefined
                    ? 'Н/Д' : `${row.deviation >= 0 ? '+' : ''}${Number(row.deviation).toFixed(1)} п.п.`;
                const status = row.status === 'normal' ? 'Норма'
                    : row.status === 'attention' ? 'Внимание' : 'Н/Д';
                return `<tr>
                    <td style="text-align:left;font-weight:600;">${esc(row.label)}</td>
                    <td style="text-align:right;">${esc(rub(row.accrual))}</td>
                    <td style="text-align:right;">${esc(rub(row.payment))}</td>
                    <td style="text-align:right;font-weight:700;">${esc(pct(row.value))}</td>
                    <td style="text-align:right;">${esc(plan)}</td>
                    <td style="text-align:right;">${esc(deviation)}</td>
                    <td style="text-align:center;">${esc(status)}</td>
                </tr>`;
            }).join('');
            const segmentTableHtml = linked.available ? `
                <div class="modal-section-title">СОБИРАЕМОСТЬ ПО КАТЕГОРИЯМ ПОТРЕБИТЕЛЕЙ</div>
                <div class="collection-table-wrap"><table class="collection-detail-table">
                    <thead><tr><th>Категория</th><th>Начислено</th><th>Оплачено</th><th>Факт</th><th>План</th><th>Отклонение</th><th>Статус</th></tr></thead>
                    <tbody>${segmentRows}</tbody>
                </table></div>` : '';

            const accrualSourceRows = (linked.accrual_sources || []).map(row => `
                <tr>
                    <td style="text-align:left;font-weight:600;">${esc(row.source)}</td>
                    <td style="text-align:left;">${esc(row.version)}</td>
                    <td style="text-align:left;">${esc(row.date_from)} — ${esc(row.date_to)}</td>
                    <td style="text-align:right;">${esc(row.rows)}</td>
                    <td style="text-align:right;font-weight:700;">${esc(rub(row.accrual))}</td>
                </tr>`).join('');
            const accrualSourcesHtml = accrualSourceRows ? `
                <div class="modal-section-title">ИСТОЧНИКИ НАЧИСЛЕНИЙ</div>
                <div class="collection-table-wrap">
                    <table class="collection-detail-table collection-source-table">
                        <thead>
                            <tr><th>Источник</th><th>Версия</th><th>Период записей</th><th>Строк</th><th>Начислено</th></tr>
                        </thead>
                        <tbody>${accrualSourceRows}</tbody>
                    </table>
                </div>` : '';

            const rawSeries = linked.available ? linked.series || [] :
                card.turnover_series || card.series || [];
            const previousYearSeries = linked.available
                ? card.previous_year_linked_series || []
                : card.previous_year_turnover_series || [];
            const collectionAmountsByMonth = inputSeries => {
                const result = new Map();
                (inputSeries || []).forEach(point => {
                    const key = String(point.date || '').slice(0, 7);
                    if (!key) return;
                    if (!result.has(key)) {
                        result.set(key, {
                            payment: 0,
                            paymentCount: 0,
                            credit6201: 0,
                            credit6201Count: 0,
                            credit6202: 0,
                            credit6202Count: 0,
                            debit6202: 0,
                            debit6202Count: 0
                        });
                    }
                    const month = result.get(key);
                    const add = (value, sumKey, countKey) => {
                        if (value !== null && value !== undefined &&
                                Number.isFinite(Number(value))) {
                            month[sumKey] += Number(value);
                            month[countKey] += 1;
                        }
                    };
                    add(point.payment, 'payment', 'paymentCount');
                    add(point.credit_6201, 'credit6201', 'credit6201Count');
                    add(point.credit_6202, 'credit6202', 'credit6202Count');
                    add(point.debit_6202, 'debit6202', 'debit6202Count');
                });
                return new Map([...result.entries()].map(([key, month]) => {
                    const value = month.paymentCount
                        ? month.payment
                        : month.credit6201Count && month.credit6202Count &&
                            month.debit6202Count
                        ? month.credit6201 + month.credit6202 - month.debit6202
                        : null;
                    return [key, value];
                }));
            };
            const currentMonthAmounts = collectionAmountsByMonth(rawSeries);
            const previousMonthAmounts = collectionAmountsByMonth(
                previousYearSeries);
            const monthLabel = key => {
                const monthDate = new Date(`${key}-01T00:00:00`);
                return Number.isNaN(monthDate.getTime()) ? key :
                    new Intl.DateTimeFormat('ru-RU', {
                        month: 'long', year: 'numeric'
                    }).format(monthDate);
            };
            const periodRows = [...currentMonthAmounts.entries()]
                .sort(([left], [right]) => left.localeCompare(right))
                .map(([key, value]) => {
                    const previous = previousMonthAmounts.has(key)
                        ? previousMonthAmounts.get(key) : null;
                    const delta = value !== null && previous !== null &&
                            previous !== 0
                        ? (value - previous) / Math.abs(previous) * 100
                        : null;
                    return `<tr>
                        <td style="text-align: left; font-weight: 600; color: #1F2937; text-transform: capitalize;">${esc(monthLabel(key))}</td>
                        <td style="font-family: ui-monospace, monospace; font-weight: 700; text-align: right;">${esc(rub(value))}</td>
                        <td style="font-family: ui-monospace, monospace; text-align: right;">${esc(rub(previous))}</td>
                        <td style="font-family: ui-monospace, monospace; font-weight: 700; text-align: right;">${esc(pct(delta))}</td>
                    </tr>`;
                }).join('');
            const periodTableHtml = `
                <div class="modal-section-title" style="display: flex; align-items: center; gap: 8px; border-bottom: none; padding-bottom: 0;">
                    <div style="width: 20px; height: 20px; background: #3B82F6; color: #fff; border-radius: 4px; display: flex; align-items: center; justify-content: center;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3zM3 9h18M9 21V9"/></svg>
                    </div>
                    СОБИРАЕМОСТЬ ЗА ПЕРИОД
                </div>
                <div class="collection-table-wrap" style="background: #fff; border-radius: 8px; border: 1px solid #E5E7EB; overflow: hidden; margin-bottom: 20px; margin-top: 10px;">
                    <table class="collection-detail-table collection-period-table collection-audit-table" style="min-width: 100%; width: 100%; border: none;">
                        <thead style="background: #1E3A8A;">
                            <tr>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: left; width: 25%;">МЕСЯЦ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 25%;">ЗНАЧЕНИЕ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 25%;">АПГ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 25%;">ДЕЛЬТА АПГ</th>
                            </tr>
                        </thead>
                        <tbody>${periodRows || '<tr><td colspan="4" style="text-align:center;color:#6B7280;padding:16px;">Нет данных за выбранный период</td></tr>'}</tbody>
                    </table>
                </div>`;

            const chartHtml = `
                <div class="ki-modal-dynamics-block" data-layout-key="dynamics" data-layout-title="Динамика" style="margin-bottom: 24px;">
                    <div class="modal-section-title" style="display: flex; align-items: center; gap: 8px; margin-top: 0; border-bottom: none; padding-bottom: 0;">
                        <div style="width: 20px; height: 20px; background: #3B82F6; color: #fff; border-radius: 4px; display: flex; align-items: center; justify-content: center;">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10M12 20V4M6 20v-4"/></svg>
                        </div>
                        <span>ДИНАМИКА СОБИРАЕМОСТИ</span>
                        <div class="collection-period-switch" role="group" aria-label="Период группировки графика">
                            <button type="button" data-collection-period="day">День</button>
                            <button type="button" data-collection-period="month">Месяц</button>
                            <button type="button" data-collection-period="year">Год</button>
                        </div>
                    </div>
                    <div class="modal-chart-wrap" style="height: 320px; background: #fff; border: 1px solid #F3F4F6; padding: 20px 10px 10px; margin-top: 10px;">
                        <div id="ki-collection-dynamics-chart" class="cash-modal-chart-plot" style="height: 100%;"></div>
                    </div>
                </div>`;

            const displayComponents = card.display_components || [
                {
                    account: '62.01',
                    label: 'Начисления',
                    value: obd6201,
                    previous_year_value: null,
                    previous_year_delta: null
                },
                {
                    account: '62 − 62.02',
                    label: 'Поступления',
                    value: proxyPaid,
                    previous_year_value: null,
                    previous_year_delta: null
                }
            ];
            const signedRub = value => value === null || value === undefined
                ? 'Н/Д'
                : `${value > 0 ? '+' : ''}${this.formatValue(value, 'RUB').val} млн ₽`;
            const componentRows = displayComponents.length ? displayComponents.map(row => {
                return `<tr>
                    <td style="text-align: left; font-weight: 600; color: #1F2937;">сч. ${esc(row.account || '62')}</td>
                    <td style="text-align: left; color: #4B5563;">${esc(row.label)}</td>
                    <td style="font-family: ui-monospace, monospace; font-weight: 700; text-align: right;">${esc(rub(row.value))}</td>
                    <td style="font-family: ui-monospace, monospace; text-align: right;">${esc(rub(row.previous_year_value))}</td>
                    <td style="font-family: ui-monospace, monospace; font-weight: 700; text-align: right;">${esc(signedRub(row.previous_year_delta))}</td>
                </tr>`;
            }).join('') : `<tr><td colspan="5" style="text-align: center; color: #6B7280; padding: 16px;">Компоненты собираемости отсутствуют</td></tr>`;

            const tableHtml = `
                <div class="modal-section-title" style="display: flex; align-items: center; gap: 8px; border-bottom: none; padding-bottom: 0;">
                    <div style="width: 20px; height: 20px; background: #3B82F6; color: #fff; border-radius: 4px; display: flex; align-items: center; justify-content: center;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3zM3 9h18M9 21V9"/></svg>
                    </div>
                    КОМПОНЕНТЫ РАСЧЁТА (ОСВ)
                </div>
                <div class="collection-table-wrap" style="background: #fff; border-radius: 8px; border: 1px solid #E5E7EB; overflow: hidden; margin-bottom: 20px; margin-top: 10px;">
                    <table class="collection-detail-table collection-audit-table" style="min-width: 100%; border: none;">
                        <thead style="background: #1E3A8A;">
                            <tr>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: left; width: 20%;">СЧЁТ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: left;">КОМПОНЕНТ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right;">ЗНАЧЕНИЕ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right;">АПГ</th>
                                <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right;">ДЕЛЬТА АПГ</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${componentRows}
                        </tbody>
                    </table>
                </div>`;

            container.innerHTML = bannerHtml + chartHtml + periodTableHtml + segmentNoticeHtml +
                segmentTableHtml + accrualSourcesHtml + tableHtml;
            if (!state.collectionTimelineMode) {
                state.collectionTimelineMode = 'month';
            }
            if (state.collectionShowMain === undefined) {
                state.collectionShowMain = true;
            }
            if (state.collectionShowApg === undefined) {
                state.collectionShowApg = false;
            }
            container.querySelectorAll('[data-collection-period]').forEach(button => {
                button.addEventListener('click', () => {
                    state.collectionTimelineMode = button.dataset.collectionPeriod;
                    this.renderCollectionDynamics(rawSeries, previousYearSeries);
                });
            });
            const mainCard = container.querySelectorAll(
                '[data-series-id="__main__"]')[0];
            if (mainCard) {
                mainCard.setAttribute('role', 'button');
                mainCard.setAttribute(
                    'aria-label', 'Показать или скрыть собираемость на графике');
                const toggleMain = () => {
                    state.collectionShowMain = !state.collectionShowMain;
                    this.renderCollectionDynamics(rawSeries, previousYearSeries);
                };
                mainCard.addEventListener('click', toggleMain);
                mainCard.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        toggleMain();
                    }
                });
            }
            const apgCard = container.querySelectorAll(
                '[data-series-id="__apg__"]')[0];
            if (apgCard && previousYearSeries.length) {
                apgCard.setAttribute('role', 'button');
                apgCard.setAttribute(
                    'aria-label', 'Показать или скрыть значения АПГ на графике');
                const toggleApg = () => {
                    state.collectionShowApg = !state.collectionShowApg;
                    this.renderCollectionDynamics(rawSeries, previousYearSeries);
                };
                apgCard.addEventListener('click', toggleApg);
                apgCard.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        toggleApg();
                    }
                });
            }
            this.renderCollectionDynamics(
                rawSeries, previousYearSeries);
        },

        renderCollectionDynamics(series, previousYearSeries = []) {
            const container = document.getElementById(
                'ki-collection-dynamics-chart');
            if (!container) return;
            const mode = state.collectionTimelineMode || 'day';
            document.querySelectorAll('[data-collection-period]').forEach(button => {
                const active = button.dataset.collectionPeriod === mode;
                button.classList.toggle('active', active);
                button.setAttribute('aria-pressed', String(active));
            });
            const mainCard = document.querySelectorAll(
                '[data-series-id="__main__"]')[0];
            if (mainCard) {
                mainCard.classList.toggle(
                    'collection-main-active', Boolean(state.collectionShowMain));
                mainCard.setAttribute(
                    'aria-pressed', String(Boolean(state.collectionShowMain)));
            }
            const apgCard = document.querySelectorAll(
                '[data-series-id="__apg__"]')[0];
            if (apgCard) {
                apgCard.classList.toggle(
                    'collection-apg-active', Boolean(state.collectionShowApg));
                apgCard.setAttribute(
                    'aria-pressed', String(Boolean(state.collectionShowApg)));
            }
            const groupedPoints = (inputSeries, grouping) => {
                const componentKeys = [
                    'debit_6201', 'credit_6201',
                    'debit_6202', 'credit_6202'
                ];
                const hasDailyComponents = (inputSeries || []).some(point =>
                    componentKeys.some(key =>
                        point[key] !== null && point[key] !== undefined));
                const groups = (inputSeries || []).reduce((result, point) => {
                    const key = String(point.date || '').slice(
                        0, grouping === 'year' ? 4 : 7);
                    if (!key) return result;
                    if (!result[key]) {
                        result[key] = {
                            ...point,
                            date: grouping === 'year'
                                ? `${key}-01-01` : `${key}-01`,
                            componentCounts: Object.fromEntries(
                                componentKeys.map(name => [name, 0])),
                            componentSums: Object.fromEntries(
                                componentKeys.map(name => [name, 0]))
                        };
                    }
                    const group = result[key];
                    group.lastPoint = point;
                    componentKeys.forEach(name => {
                        const value = point[name];
                        if (value !== null && value !== undefined &&
                                Number.isFinite(Number(value))) {
                            group.componentSums[name] += Number(value);
                            group.componentCounts[name] += 1;
                        }
                    });
                    return result;
                }, {});
                return Object.values(groups).map(group => {
                    const monthDate = new Date(`${group.date}T00:00:00`);
                    const label = grouping === 'year'
                        ? String(group.date).slice(0, 4)
                        : Number.isNaN(monthDate.getTime())
                        ? group.lastPoint.label
                        : new Intl.DateTimeFormat('ru-RU', {
                            month: 'short', year: '2-digit'
                        }).format(monthDate).replace('.', '');
                    if (!hasDailyComponents) {
                        return {...group.lastPoint, label};
                    }
                    const complete = componentKeys.every(name =>
                        group.componentCounts[name] > 0);
                    const debit = group.componentSums.debit_6201;
                    const value = complete && debit !== 0
                        ? (group.componentSums.credit_6201 +
                            group.componentSums.credit_6202 -
                            group.componentSums.debit_6202) / debit * 100
                        : null;
                    return {
                        ...group.lastPoint,
                        ...group.componentSums,
                        date: group.date,
                        label,
                        value: value === null ? null :
                            Math.round(value * 100) / 100,
                        reason: value === null
                            ? `Недостаточно компонентов оборотов за ${
                                grouping === 'year' ? 'год' : 'месяц'}`
                            : null
                    };
                });
            };
            const sourcePoints = mode === 'day'
                ? (series || []) : groupedPoints(series, mode);
            const apgSourcePoints = state.collectionShowApg
                ? (mode === 'day'
                    ? (previousYearSeries || [])
                    : groupedPoints(previousYearSeries, mode))
                : [];
            const points = sourcePoints.map((point, index) => ({
                ...point,
                index,
                value: point.value === null || point.value === undefined
                    ? null : Number(point.value)
            }));
            const apgPoints = apgSourcePoints.map((point, index) => ({
                ...point,
                index,
                value: point.value === null || point.value === undefined
                    ? null : Number(point.value)
            }));
            const mainPlotPoints = state.collectionShowMain ? points : [];
            if (![...mainPlotPoints, ...apgPoints].some(
                    point => Number.isFinite(point.value))) {
                container.innerHTML = '<div class="chart-empty">Нет данных для динамики выбранного периода</div>';
                return;
            }
            const width = 1000;
            const height = 300;
            const pad = {left: 58, right: 24, top: 18, bottom: 42};
            const plotWidth = width - pad.left - pad.right;
            const plotHeight = height - pad.top - pad.bottom;
            const values = [...mainPlotPoints, ...apgPoints]
                .filter(point => Number.isFinite(point.value))
                .map(point => point.value);
            // Percentages produced from a near-zero or negative denominator
            // can reach thousands or millions of percent. They must not
            // collapse the meaningful 93–95% threshold area to a pixel.
            const scaleValues = values.filter(value => value >= 0 && value <= 200);
            const scaleMin = scaleValues.length ? Math.min(...scaleValues) : 0;
            const scaleMax = scaleValues.length ? Math.max(...scaleValues) : 100;
            const yMin = Math.max(0, Math.min(
                50, Math.floor(scaleMin / 10) * 10));
            const yMax = Math.max(100, Math.min(
                200, Math.ceil(scaleMax / 10) * 10));
            const inScale = value => Number.isFinite(value) &&
                value >= yMin && value <= yMax;
            const outlierCount = [...mainPlotPoints, ...apgPoints].filter(point =>
                Number.isFinite(point.value) && !inScale(point.value)).length;
            const x = index => pad.left + (
                points.length <= 1 ? plotWidth / 2 :
                    index / (points.length - 1) * plotWidth);
            const y = value => pad.top + (yMax - value) /
                Math.max(1, yMax - yMin) * plotHeight;
            const band = (from, to, cssClass) => {
                const upper = Math.min(yMax, to);
                const lower = Math.max(yMin, from);
                if (upper <= lower) return '';
                const top = y(upper);
                return `<rect class="${cssClass}" x="${pad.left}" y="${top}" width="${plotWidth}" height="${y(lower) - top}"/>`;
            };
            const buildSegments = source => {
                const result = [];
                let path = '';
                source.forEach(point => {
                    if (!inScale(point.value)) {
                        if (path) result.push(path);
                        path = '';
                        return;
                    }
                    path += `${path ? ' L' : 'M'} ${x(point.index).toFixed(2)} ${y(point.value).toFixed(2)}`;
                });
                if (path) result.push(path);
                return result;
            };
            const segments = buildSegments(mainPlotPoints);
            const apgSegments = buildSegments(apgPoints);
            const ticks = [...new Set([yMin, 93, 95, 100, yMax])]
                .filter(value => value >= yMin && value <= yMax)
                .sort((a, b) => a - b);
            const tickSvg = ticks.map(value => `
                <line class="collection-dynamics-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}"/>
                <text class="collection-dynamics-axis" x="${pad.left - 10}" y="${y(value) + 4}" text-anchor="end">${value}%</text>`).join('');
            const labelStep = Math.max(1, Math.ceil(points.length / 8));
            const xLabels = points.map((point, index) => (
                index % labelStep === 0 || index === points.length - 1
                    ? `<text class="collection-dynamics-axis" x="${x(index)}" y="${height - 14}" text-anchor="middle">${esc(point.label || point.date || '')}</text>`
                    : '')).join('');
            const lines = segments.map(segment =>
                `<path class="collection-dynamics-line" d="${segment}"/>`).join('');
            const apgLines = apgSegments.map(segment =>
                `<path class="collection-dynamics-apg-line" d="${segment}"/>`).join('');
            const zoneClass = value => value < 93
                ? 'collection-point-red'
                : value < 95
                    ? 'collection-point-yellow'
                    : 'collection-point-green';
            const circles = mainPlotPoints.filter(point => inScale(point.value))
                .map(point => {
                    const accrual = point.accrual ?? point.debit_6201;
                    const paid = point.payment ?? (
                        [point.credit_6201, point.credit_6202, point.debit_6202]
                            .every(value => value !== null && value !== undefined)
                            ? point.credit_6201 + point.credit_6202 - point.debit_6202
                            : null);
                    const detail = [
                        point.label || point.date,
                        `Собираемость: ${point.value.toFixed(1)}%`,
                        accrual === null || accrual === undefined ? null :
                            `Начислено: ${this.formatValue(accrual, 'RUB').val} млн ₽`,
                        paid === null || paid === undefined ? null :
                            `Оплачено: ${this.formatValue(paid, 'RUB').val} млн ₽`
                    ].filter(Boolean).join(' · ');
                    return `<circle class="collection-dynamics-point ${zoneClass(point.value)}" data-detail="${esc(detail)}" data-series-label="Собираемость" cx="${x(point.index)}" cy="${y(point.value)}" r="6" tabindex="0"><title>${esc(detail)}</title></circle>`;
                }).join('');
            const apgCircles = apgPoints.filter(point => inScale(point.value))
                .map(point => {
                    const sourceDate = point.source_date
                        ? new Date(`${point.source_date}T00:00:00`) : null;
                    const sourcePeriod = sourceDate &&
                            !Number.isNaN(sourceDate.getTime())
                        ? new Intl.DateTimeFormat('ru-RU', {
                            month: 'long', year: 'numeric'
                        }).format(sourceDate).replace(/\s*г\.?$/, '')
                        : point.label || point.date;
                    const detail =
                        `${sourcePeriod} · АПГ: ${point.value.toFixed(1)}%`;
                    return `<circle class="collection-dynamics-apg-point ${zoneClass(point.value)}" data-detail="${esc(detail)}" data-series-label="АПГ" cx="${x(point.index)}" cy="${y(point.value)}" r="6" tabindex="0"><title>${esc(detail)}</title></circle>`;
                }).join('');
            const legendItems = [
                state.collectionShowMain
                    ? '<span><i class="collection-legend-main"></i>Собираемость</span>'
                    : '',
                state.collectionShowApg && apgPoints.length
                    ? '<span><i class="collection-legend-apg"></i>АПГ</span>'
                    : ''
            ].filter(Boolean).join('');
            const legend = legendItems
                ? `<div class="collection-chart-legend">${legendItems}</div>`
                : '';
            const scaleNotice = outlierCount
                ? `<div class="collection-scale-notice">Не соединено значений вне рабочей шкалы 0–${yMax}%: ${outlierCount}. Наведите на исходные данные в таблице для разбора.</div>`
                : '';
            container.innerHTML = `<svg class="collection-dynamics-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика собираемости за выбранный период">
                ${band(yMin, 93, 'collection-zone-red')}
                ${band(93, 95, 'collection-zone-yellow')}
                ${band(95, yMax, 'collection-zone-green')}
                ${tickSvg}${xLabels}${lines}${apgLines}${circles}${apgCircles}
            </svg>${legend}${scaleNotice}`;
            const showPointAnnotation = point => {
                container.querySelectorAll('.collection-point-annotation')
                    .forEach(item => item.remove());
                container.querySelectorAll(
                    '.collection-dynamics-point, .collection-dynamics-apg-point')
                    .forEach(item => item.classList.remove('selected'));
                point.classList.add('selected');
                const annotation = document.createElement('div');
                annotation.className = 'collection-point-annotation';
                annotation.innerHTML = `<strong>${esc(point.dataset.seriesLabel || '')}</strong><span>${esc(point.dataset.detail || '')}</span>`;
                annotation.style.left =
                    `${Math.min(82, Math.max(8, Number(point.getAttribute('cx')) / width * 100))}%`;
                annotation.style.top =
                    `${Math.min(76, Math.max(8, Number(point.getAttribute('cy')) / height * 100))}%`;
                container.appendChild(annotation);
            };
            container.querySelectorAll(
                '.collection-dynamics-point, .collection-dynamics-apg-point')
                .forEach(point => {
                    point.addEventListener('click', () =>
                        showPointAnnotation(point));
                    point.addEventListener('keydown', event => {
                        if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            showPointAnnotation(point);
                        }
                    });
                });
        },

        collectionProxyAggregate(series, mode) {
            const buckets = new Map();
            const bucketKey = raw => {
                const date = new Date(`${raw}T00:00:00Z`);
                if (mode === 'day') return raw;
                if (mode === 'month') return raw.slice(0, 7);
                const day = date.getUTCDay() || 7;
                date.setUTCDate(date.getUTCDate() - day + 1);
                return date.toISOString().slice(0, 10);
            };
            (series || []).forEach(point => {
                const key = bucketKey(point.date);
                if (!buckets.has(key)) buckets.set(key, {
                    date: point.date, label: point.label, points: [],
                    debit_6201: 0, credit_6201: 0, debit_6202: 0, credit_6202: 0
                });
                const bucket = buckets.get(key);
                bucket.date = point.date;
                bucket.label = mode === 'day' ? point.label :
                    mode === 'month' ? point.date.slice(0, 7) : `Неделя с ${key}`;
                bucket.points.push(point);
                ['debit_6201', 'credit_6201', 'debit_6202', 'credit_6202'].forEach(field => {
                    if (point[field] !== null && point[field] !== undefined) bucket[field] += Number(point[field]);
                });
            });
            return Array.from(buckets.values()).map(bucket => {
                const missing = ['debit_6201', 'credit_6201', 'debit_6202', 'credit_6202']
                    .filter(field => bucket.points.some(point => point[field] === null || point[field] === undefined));
                const denominator = missing.length ? null : bucket.debit_6201;
                const adjustment = missing.length ? null : bucket.credit_6202 - bucket.debit_6202;
                const numerator = missing.length ? null : bucket.credit_6201 + adjustment;
                const value = denominator === null || denominator === 0 ? null :
                    numerator / denominator * 100;
                return Object.assign(bucket, {
                    denominator, numerator, adjustment_6202: adjustment, value,
                    reason: missing.length
                        ? `Нет обязательных компонентов: ${missing.join(', ')}`
                        : denominator === 0 ? 'ОбД 62.01 равен нулю' : null,
                    source: bucket.points.map(point => point.source).find(Boolean) ||
                        'Явные обороты ОСВ 62.01 и 62.02',
                    coverage: {
                        state: missing.length ? 'incomplete' :
                            denominator === 0 ? 'zero_denominator' : 'complete',
                        known_components: 4 - missing.length,
                        required_components: 4
                    }
                });
            });
        },

        renderCollectionProxyModal(card, container) {
            const proxy = card.accounting_proxy || {};
            const components = card.components || card.collection_components || [];
            const val = (account, side) => {
                const item = components.find(row => String(row.account || row.label || '').includes(account) &&
                    row.side === side);
                return item && item.value !== undefined ? item.value : null;
            };
            const debit6201 = proxy.denominator !== undefined ? proxy.denominator : val('62.01', 'debit');
            const credit6201 = val('62.01', 'credit');
            const debit6202 = val('62.02', 'debit');
            const credit6202 = val('62.02', 'credit');
            const adjustment = proxy.adjustment_6202 !== undefined ? proxy.adjustment_6202 :
                credit6202 == null || debit6202 == null ? null : credit6202 - debit6202;
            const numerator = proxy.numerator !== undefined ? proxy.numerator :
                credit6201 == null || adjustment == null ? null : credit6201 + adjustment;
            const pct = value => value == null ? 'Н/Д' : `${Number(value).toFixed(2).replace('.', ',')}%`;
            const rub = value => value == null ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const exact = value => value == null ? 'Н/Д' :
                `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(value)} ₽`;
            const summary = [
                ['ПРОКСИ-ПРОЦЕНТ', pct(proxy.value !== undefined ? proxy.value : card.value), 'Итог отношения'],
                ['ЗНАМЕНАТЕЛЬ', rub(debit6201), 'ОбД 62.01'],
                ['РАСЧЁТНЫЙ ЧИСЛИТЕЛЬ', rub(numerator), 'ОбК 62.01 + ОбК 62.02 − ОбД 62.02'],
                ['КОРРЕКТИРОВКА 62.02', rub(adjustment), 'ОбК 62.02 − ОбД 62.02']
            ].map((item, index) => this.cashMetricCard(
                item[0], item[1], item[2],
                this.collectionTooltip({title: item[0], fact: item[1],
                    detail: `${item[2]}. Точное значение: ${index === 0 ? pct(card.value) : exact([null, debit6201, numerator, adjustment][index])}`}),
                item[1] === 'Н/Д', index === 0 ? '__main__' : `proxy:${index}`
            )).join('');
            container.innerHTML = `
                <section class="collection-proxy-view" aria-labelledby="collection-proxy-title">
                    <div class="collection-proxy-banner">
                        <h3 id="collection-proxy-title">Собираемость (прокси по ОСВ)</h3>
                        <p>Бухгалтерское отношение оборотов, а не фактические погашения начислений.</p>
                        <code>(ОбК 62.01 + ОбК 62.02 − ОбД 62.02) / ОбД 62.01 × 100%</code>
                    </div>
                    <div class="collection-summary">${summary}</div>
                    <section class="collection-proxy-dynamics" data-layout-key="dynamics" data-layout-title="Динамика прокси">
                        <div class="collection-proxy-toolbar">
                            <h4>Динамика прокси</h4>
                            <div class="collection-proxy-controls" role="group" aria-label="Режим графика">
                                <button type="button" data-collection-view="result" aria-pressed="true">Итог</button>
                                <button type="button" data-collection-view="components" aria-pressed="false">4 оборота</button>
                            </div>
                            <div class="collection-proxy-controls" role="group" aria-label="Период графика">
                                <button type="button" data-collection-period="day">День</button>
                                <button type="button" data-collection-period="week">Неделя</button>
                                <button type="button" data-collection-period="month" aria-pressed="true">Месяц</button>
                            </div>
                        </div>
                        <div id="ki-collection-proxy-chart" class="collection-proxy-chart" role="img"
                             aria-label="График собираемости прокси по ОСВ"></div>
                    </section>
                    <section>
                        <h4 class="modal-section-title">Периодная расшифровка</h4>
                        <div class="collection-table-wrap">
                            <table class="collection-detail-table collection-proxy-table">
                                <thead><tr><th>Период</th><th>ОбД 62.01<br><small>знаменатель</small></th>
                                <th>ОбК 62.01<br><small>+ числитель</small></th>
                                <th>ОбД 62.02<br><small>− числитель</small></th>
                                <th>ОбК 62.02<br><small>+ числитель</small></th>
                                <th>Числитель</th><th>Знаменатель</th><th>Прокси</th><th>Статус</th></tr></thead>
                                <tbody id="ki-collection-proxy-rows"></tbody>
                            </table>
                        </div>
                    </section>
                </section>`;
            const state = {view: 'result', period: 'month'};
            const render = () => this.drawCollectionProxy(card, state, rub, pct);
            Array.from(container.querySelectorAll('[data-collection-view]')).forEach(button =>
                button.addEventListener('click', () => {
                    state.view = button.dataset.collectionView;
                    Array.from(container.querySelectorAll('[data-collection-view]')).forEach(item =>
                        item.setAttribute('aria-pressed', String(item === button)));
                    render();
                }));
            Array.from(container.querySelectorAll('[data-collection-period]')).forEach(button =>
                button.addEventListener('click', () => {
                    state.period = button.dataset.collectionPeriod;
                    Array.from(container.querySelectorAll('[data-collection-period]')).forEach(item =>
                        item.setAttribute('aria-pressed', String(item === button)));
                    render();
                }));
            render();
        },

        drawCollectionProxy(card, viewState, rub, pct) {
            const points = this.collectionProxyAggregate(card.turnover_series || [], viewState.period);
            const chart = document.getElementById('ki-collection-proxy-chart');
            const tbody = document.getElementById('ki-collection-proxy-rows');
            if (!chart || !tbody) return;
            const status = point => point.coverage && point.coverage.state === 'complete'
                ? 'Полные данные' : point.reason || 'Недостаточно данных';
            tbody.innerHTML = points.length ? points.map(point => `<tr>
                <td>${esc(point.label || point.date)}</td><td>${esc(rub(point.debit_6201))}</td>
                <td>${esc(rub(point.credit_6201))}</td><td>${esc(rub(point.debit_6202))}</td>
                <td>${esc(rub(point.credit_6202))}</td><td>${esc(rub(point.numerator))}</td>
                <td>${esc(rub(point.denominator))}</td><td>${esc(pct(point.value))}</td>
                <td class="${point.value == null ? 'cash-value-missing' : ''}">${esc(status(point))}</td>
            </tr>`).join('') : '<tr><td colspan="9" class="collection-proxy-empty">Нет периодных данных для выбранного периода</td></tr>';
            if (!points.length) {
                chart.innerHTML = '<div class="collection-proxy-empty">Нет подтверждённых точек для графика</div>';
                return;
            }
            const width = 920, height = 330, left = 58, right = 58, top = 28, bottom = 52;
            const moneyFields = viewState.view === 'result'
                ? ['denominator', 'numerator']
                : ['debit_6201', 'credit_6201', 'debit_6202', 'credit_6202'];
            const values = points.flatMap(point => moneyFields.map(field => point[field])).filter(Number.isFinite);
            const percentages = points.map(point => point.value).filter(Number.isFinite);
            const moneyMin = Math.min(0, ...values), moneyMax = Math.max(0, ...values);
            const pctMin = Math.min(0, ...percentages), pctMax = Math.max(100, ...percentages);
            const plotW = width - left - right, plotH = height - top - bottom;
            const x = index => left + (index + .5) * plotW / points.length;
            const ym = value => top + (moneyMax - value) / Math.max(1, moneyMax - moneyMin) * plotH;
            const yp = value => top + (pctMax - value) / Math.max(1, pctMax - pctMin) * plotH;
            const colors = ['#2563EB', '#0F766E', '#D97706', '#7C3AED'];
            const barWidth = Math.min(30, plotW / points.length / (moneyFields.length + 1));
            const bars = points.map((point, index) => moneyFields.map((field, fieldIndex) => {
                const value = point[field];
                if (!Number.isFinite(value)) return '';
                const zero = ym(0), valueY = ym(value);
                const tooltip = `${point.label || point.date}; прокси ${pct(point.value)}; числитель ${rub(point.numerator)}; знаменатель ${rub(point.denominator)}; ОбД 62.01 ${rub(point.debit_6201)}; ОбК 62.01 ${rub(point.credit_6201)}; ОбД 62.02 ${rub(point.debit_6202)}; ОбК 62.02 ${rub(point.credit_6202)}; источник: ${point.source}; ${status(point)}`;
                return `<rect x="${x(index) + (fieldIndex - (moneyFields.length - 1) / 2) * barWidth - barWidth * .42}"
                    y="${Math.min(zero, valueY)}" width="${barWidth * .84}" height="${Math.max(1, Math.abs(zero - valueY))}"
                    fill="${colors[fieldIndex]}" tabindex="0"><title>${esc(tooltip)}</title></rect>`;
            }).join('')).join('');
            const linePoints = points.map((point, index) =>
                Number.isFinite(point.value) ? `${x(index)},${yp(point.value)}` : null);
            const lineSegments = [];
            let currentLineSegment = [];
            linePoints.forEach(point => {
                if (point) {
                    currentLineSegment.push(point);
                } else {
                    if (currentLineSegment.length > 1) {
                        lineSegments.push(currentLineSegment);
                    }
                    currentLineSegment = [];
                }
            });
            if (currentLineSegment.length > 1) {
                lineSegments.push(currentLineSegment);
            }
            const line = lineSegments.map(segment =>
                `<polyline class="collection-proxy-percent-line" points="${segment.join(' ')}" fill="none" stroke="#DC2626" stroke-width="3"/>`
            ).join('');
            const dots = points.map((point, index) => Number.isFinite(point.value)
                ? `<circle cx="${x(index)}" cy="${yp(point.value)}" r="4" fill="#DC2626"><title>${esc(`${point.label}: ${pct(point.value)}; ${status(point)}`)}</title></circle>` : '').join('');
            const labels = points.map((point, index) =>
                `<text x="${x(index)}" y="${height - 18}" text-anchor="middle">${esc(point.label || point.date)}</text>`).join('');
            chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">
                <line x1="${left}" y1="${ym(0)}" x2="${width-right}" y2="${ym(0)}" class="collection-axis"/>
                ${bars}${line}${dots}${labels}
                <text x="${left}" y="16">млн ₽</text><text x="${width-right}" y="16" text-anchor="end">%</text>
            </svg>
            <div class="collection-proxy-legend">${moneyFields.map((field, index) =>
                `<span><i style="--legend-color:${colors[index]}"></i>${esc({
                    denominator: 'ОбД 62.01', numerator: 'Расчётный числитель',
                    debit_6201: 'ОбД 62.01', credit_6201: 'ОбК 62.01',
                    debit_6202: 'ОбД 62.02', credit_6202: 'ОбК 62.02'
                }[field])}</span>`).join('')}<span><i class="collection-proxy-percent"></i>Прокси, %</span></div>`;
        },
        arRequirements() {
            return {
                fullDebt: 'Остатки из 1С по счетам 62 и 76: предприятие, контрагент, ИНН/КПП, договор, лицевой счёт, документ расчётов, субсчёт, аналитика, дебетовое и кредитовое сальдо, дата снимка и признак включения в дебиторскую задолженность.',
                segments: 'Справочник категорий должников из 1С: контрагент, ИНН/КПП, договор, лицевой счёт, категория «Население / УК-ТСЖ / Бюджет / Прочие юрлица / Субсидии», дата начала и окончания действия категории.',
                aging: 'Регистр задолженности из 1С: документ начисления, дата возникновения долга, расчётный период, договорный срок оплаты, дата наступления просрочки, исходная сумма, распределённые оплаты, корректировки, сторно, непогашенный остаток и количество дней просрочки на отчётную дату.',
                overdue: 'Для просрочки свыше 180 дней нужны по каждому документу: дата возникновения, срок оплаты, непогашенный остаток, дата последнего погашения, корректировки и количество календарных дней просрочки.',
                subsidy: 'Регистр субсидий и компенсаций из 1С: бюджет и программа, получатель, расчётный период, документ начисления дохода, подтверждённая сумма, ожидаемая дата компенсации, поступления, распределение поступлений и непогашенный остаток.',
                status: 'Утверждённые правила оценки из 1С или финансовой политики: показатель, категория должника, пороги «Норма / Внимание / Критично», единица измерения, дата утверждения и период действия.'
            };
        },

        arTooltip(overrides = {}) {
            const required = this.arRequirements();
            return Object.assign({
                title: 'Дебиторская задолженность покупателей',
                description: 'Дебетовое сальдо расчётов с покупателями по субсчёту 62.01 на выбранную дату.',
                formula: 'ДЗ покупателей = max(СКд(62.01) − СКк(62.01), 0)',
                detail: 'Счёт 76 не добавляется автоматически: он содержит разнородные расчёты. Отсутствующий счёт или аналитика не принимаются за ноль.',
                required: required.fullDebt,
                norm: 'Индивидуальный норматив не задан',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'конечное сальдо ОСВ 62.01'
            }, overrides);
        },

        renderArModal(card) {
            const container = document.getElementById('ki-ar-modal-content');
            if (!container) return;
            const required = this.arRequirements();
            const revenueCard = state.data.cards.find(item => item.id === 'revenue') || {};
            const current = card.value;
            const consumerAdvances = card.consumer_advances;
            const opening = card.opening_value;
            const change = current !== null && current !== undefined &&
                opening !== null && opening !== undefined ? current - opening : null;
            const revenueIsNet = revenueCard.value && !String(revenueCard.title || '').includes('брутто');
            const ratio = current !== null && current !== undefined && revenueIsNet
                ? current / revenueCard.value * 100 : null;
            const period = state.data.period || {};
            const startDate = period.start ? new Date(`${period.start}T00:00:00`) : null;
            const endDate = period.end ? new Date(`${period.end}T00:00:00`) : null;
            const days = startDate && endDate ? Math.round((endDate - startDate) / 86400000) + 1 : null;
            const dso = current !== null && current !== undefined &&
                opening !== null && opening !== undefined && revenueIsNet && days
                ? ((opening + current) / 2) / revenueCard.value * days : null;
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;

            const totalTip = this.arTooltip({
                fact: rub(current),
                required: current === null || current === undefined
                    ? 'ОСВ счёта 62 с конечным дебетовым и кредитовым сальдо субсчёта 62.01 на выбранную дату.'
                    : required.fullDebt,
                deviation: 'Фактическое подтверждённое сальдо 62.01 и разрешённых аналитик 76',
                deviationClass: current === null || current === undefined ? 'missing' : 'neutral'
            });
            const advancesTip = this.arTooltip({
                title: 'Авансы потребителей',
                description: 'Кредитовое сальдо расчётов по авансам потребителей на выбранную дату.',
                formula: 'Авансы потребителей = max(СКк(62.02) − СКд(62.02), 0)',
                detail: 'Рассчитывается отдельно от дебиторской задолженности по 62.01.',
                required: consumerAdvances === null || consumerAdvances === undefined
                    ? 'ОСВ счёта 62 с начальным сальдо и дневными оборотами 62.02.'
                    : null,
                fact: rub(consumerAdvances),
                deviation: 'Кредитовое сальдо 62.02',
                deviationClass: consumerAdvances === null || consumerAdvances === undefined
                    ? 'missing' : 'neutral',
                source: 'сальдо ОСВ 62.02'
            });
            const changeTip = this.arTooltip({
                title: 'Изменение дебиторской задолженности',
                description: 'Изменение подтверждённого сальдо 62.01 за выбранный период.',
                formula: 'Δ ДЗ = ДЗ на конец периода − ДЗ на начало периода',
                detail: 'Начальное и конечное сальдо должны иметь одинаковый состав субсчетов и аналитик.',
                required: change === null ? 'ОСВ 62.01 с начальным и конечным дебетовым/кредитовым сальдо за один полный период.' : null,
                fact: change === null ? 'Н/Д' : `${change >= 0 ? '+' : ''}${rub(change)}`,
                deviation: change === null ? 'Нет сопоставимых границ периода' :
                    (change > 0 ? 'Задолженность выросла' : change < 0 ? 'Задолженность снизилась' : 'Без изменения'),
                deviationClass: change === null ? 'missing' : (change > 0 ? 'bad' : 'good'),
                source: 'начальное и конечное сальдо ОСВ 62.01'
            });
            const dsoTip = this.arTooltip({
                title: 'Оценочный DSO',
                description: 'Бухгалтерская оценка числа дней выручки, находящихся в дебиторской задолженности.',
                formula: 'DSO = [(ДЗ начало + ДЗ конец) / 2] / Выручка без НДС × дней в периоде',
                detail: 'DSO не является фактическим средним сроком оплаты документов. При неполном периоде результат требует отдельного предупреждения.',
                required: dso === null ? 'ОСВ 62.01 с начальным и конечным сальдо и ОСВ 90.01/90.03 за тот же полный период. Для фактического срока оплаты дополнительно нужен регистр распределённых оплат по документам.' : null,
                fact: dso === null ? 'Н/Д' : `${dso.toFixed(0)} дней`,
                deviation: dso === null ? 'Недостаточно сопоставимых данных' : 'Оценочный бухгалтерский показатель',
                deviationClass: dso === null ? 'missing' : 'neutral',
                source: 'ОСВ 62.01, 90.01 и 90.03'
            });
            const ratioTip = this.arTooltip({
                title: 'Дебиторская задолженность к выручке',
                description: 'Доля выручки выбранного периода, находящаяся в конечной задолженности покупателей.',
                formula: 'ДЗ / Выручка = ДЗ 62.01 на конец / [ОбК(90.01) − ОбД(90.03)] × 100%',
                detail: 'Период выручки всегда показывается явно. Брутто-выручка при отсутствии 90.03 не используется скрыто.',
                required: ratio === null ? 'ОСВ 62.01 с конечным сальдо и ОСВ 90.01/90.03 за один сопоставимый период.' : null,
                fact: pct(ratio), deviation: ratio === null ? 'Недостаточно данных' : 'Фактическое соотношение',
                deviationClass: ratio === null ? 'missing' : 'neutral',
                source: 'ОСВ 62.01, 90.01 и 90.03'
            });

            const analytics = card.analytics || [];
            const debtor = card.debtor_segments || {};
            const rows = analytics.length ? analytics.map(row => {
                const share = current ? row.value / current * 100 : null;
                const analyticTip = this.arTooltip({
                    title: `ДЗ по аналитике — ${row.label}`,
                    description: `Дебетовое сальдо ${row.account || '62.01'} по фактическому полному пути аналитики из ОСВ.`,
                    formula: `max(СКд − СКк, 0) по аналитике ${row.account || '62.01'}`,
                    detail: String(row.account || '').startsWith('76.') && !row.included_in_total
                        ? 'Аналитика 76 не включена в подтверждённую ДЗ без явного сопоставления.'
                        : 'Категория применяется только в указанном периоде действия.',
                    required: required.segments, fact: rub(row.value),
                    deviation: 'Фактическое сальдо ОСВ', deviationClass: 'neutral',
                    source: 'аналитическое сальдо ОСВ 62.01'
                });
                const shareTip = this.arTooltip({
                    title: `Доля в подтверждённой ДЗ — ${row.label}`,
                    description: 'Доля аналитики в общей подтверждённой задолженности 62.01.',
                    formula: 'Доля = ДЗ аналитики / общая ДЗ 62.01 × 100%',
                    detail: 'Доля не включает несопоставленные дебетовые расчёты счёта 76.',
                    required: null, fact: pct(share), deviation: 'Фактическая структура 62.01',
                    deviationClass: 'neutral', source: 'ОСВ 62.01'
                });
                const overdueTip = this.arTooltip({
                    title: `Просрочка — ${row.label}`,
                    description: 'Непогашенная задолженность после наступления договорного срока оплаты.',
                    formula: 'Просрочка = Σ непогашенных остатков документов с датой оплаты раньше отчётной даты',
                    detail: 'Возраст бухгалтерского сальдо нельзя восстановить из одной итоговой строки ОСВ.',
                    required: `${required.aging} ${required.segments}`,
                    source: 'требуется регистр задолженности по документам'
                });
                const statusTip = this.arTooltip({
                    title: `Статус задолженности — ${row.label}`,
                    description: 'Оценка задолженности по утверждённым порогам предприятия.',
                    formula: 'Статус определяется по доле, сроку просрочки и утверждённым порогам.',
                    detail: 'Демонстрационные отраслевые значения макета не считаются нормативом предприятия.',
                    required: `${required.aging} ${required.status} ${required.segments}`,
                    source: 'требуются сроки задолженности и утверждённые пороги'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.account || '62.01')} · ${esc(row.label)}</strong>`, analyticTip)}
                    ${this.collectionCell(esc(rub(row.value)), analyticTip)}
                    ${this.collectionCell(esc(pct(share)), shareTip)}
                    ${this.collectionCell('Нужны данные', overdueTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', overdueTip, 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', statusTip, 'cash-value-missing')}
                </tr>`;
            }).join('') : `<tr><td colspan="6" class="chart-empty">В ОСВ 62.01 нет аналитических сальдо на выбранную дату. ${esc(required.segments)}</td></tr>`;

            const categoryRows = (debtor.categories || []).map(item =>
                `<tr><td>${esc(item.label)}</td><td>${esc(rub(item.value))}</td></tr>`
            ).join('');
            const issueRows = [
                ...(debtor.new || []).map(item => ({
                    label: `${item.account} · ${item.label}`,
                    status: String(item.account || '').startsWith('76.')
                        ? 'Новая аналитика — не включена в итог'
                        : 'Новая аналитика — не распределена по категории'
                })),
                ...(debtor.unmapped || []).map(item => ({
                    label: `${item.account} · ${item.label}`,
                    status: String(item.account || '').startsWith('76.')
                        ? 'Нет действующего сопоставления — не включено в итог'
                        : 'Нет действующего сопоставления на отчётную дату'
                })),
                ...(debtor.conflicts || []).map(item => ({
                    label: `${item.account} · ${item.label}`,
                    status: `Конфликт: ${(item.categories || []).join(', ')}`
                }))
            ];
            const issueHtml = issueRows.length
                ? `<div class="revenue-classification-note"><strong>Новые, несопоставленные и конфликтующие аналитики:</strong><ul>${
                    issueRows.map(item => `<li>${esc(item.label)} — ${esc(item.status)}</li>`).join('')
                }</ul></div>` : '';
            const reconciliation = debtor.confirmed_total === null || debtor.confirmed_total === undefined
                ? 'Сверка недоступна'
                : `Категории: ${rub(debtor.classified_total)} · Подтверждённая ДЗ: ${rub(debtor.confirmed_total)} · Разница: ${rub(debtor.reconciliation_difference)} · ${debtor.reconciled ? 'сверено' : 'не сверено'}`;
            const mappingEditor = config.canEditRevenueMappings
                ? this.renderDebtorMappingEditor(analytics, debtor) : '';

            const overdueTip = this.arTooltip({
                title: 'Просроченная ДЗ свыше 180 дней',
                description: 'Непогашенная задолженность, просроченная более чем на 180 календарных дней.',
                formula: 'Σ непогашенных остатков, где отчётная дата − договорный срок оплаты > 180 дней',
                detail: 'Дата бухгалтерской проводки не заменяет договорный срок оплаты.',
                required: required.overdue, source: 'требуется регистр задолженности по документам'
            });
            const agingTip = this.arTooltip({
                title: 'Старение дебиторской задолженности',
                description: 'Распределение непогашенного долга по интервалам 0–30, 31–90, 91–180, 181–365 и более 365 дней.',
                formula: 'Возраст = отчётная дата − договорный срок оплаты документа',
                detail: 'График не строится из демонстрационных значений и не заполняется нулями.',
                required: required.aging, source: 'требуется регистр задолженности по документам'
            });
            const subsidyTip = this.arTooltip({
                title: 'Задолженность по субсидиям и компенсациям',
                description: 'Подтверждённые, но не компенсированные бюджетом суммы.',
                formula: 'ДЗ субсидий = подтверждённая сумма − распределённые поступления',
                detail: 'Весь дебет счёта 76 нельзя считать субсидиями.',
                required: required.subsidy, source: 'требуется регистр субсидий и подтверждённая аналитика 76'
            });

            container.innerHTML = `
                <div class="cash-info-banner"><strong>ДЗ потребителей равна дебетовому сальдо 62.01.</strong>
                    Аналитики счёта 76 показываются отдельно и не прибавляются к этой метрике.</div>
                <div class="collection-summary ar-summary">
                    ${this.cashMetricCard('ДЗ ПОТРЕБИТЕЛЕЙ', rub(current), 'дебетовое сальдо 62.01', totalTip, current === null || current === undefined, '__main__')}
                    ${this.cashMetricCard('АВАНСЫ ПОТРЕБИТЕЛЕЙ', rub(consumerAdvances), 'кредитовое сальдо 62.02', advancesTip, consumerAdvances === null || consumerAdvances === undefined, 'detail:consumer-advances')}
                    ${this.cashMetricCard('Изменение ДЗ', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'за выбранный период', changeTip, change === null)}
                    ${this.cashMetricCard('Оценочный DSO', dso === null ? 'Нужны данные' : `${dso.toFixed(0)} дней`, 'бухгалтерская оценка', dsoTip, dso === null)}
                    ${this.cashMetricCard('ДЗ / Выручка', pct(ratio), period.label || 'выбранный период', ratioTip, ratio === null)}
                </div>
                <div class="modal-section-title">Категории и фактические аналитики 62.01 / 76</div>
                <div class="revenue-classification-note">${esc(reconciliation)}</div>
                <div class="collection-table-wrap"><table class="cash-table debtor-category-table">
                    <thead><tr><th>Категория должников</th><th>Сумма</th></tr></thead>
                    <tbody>${categoryRows}</tbody></table></div>
                ${issueHtml}
                <div class="collection-table-wrap"><table class="collection-detail-table ar-detail-table">
                    <thead><tr><th>Аналитика 1С</th><th>ДЗ</th><th>Доля</th><th>Просрочка</th><th>Возраст долга</th><th>Статус</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                ${mappingEditor}
                <div class="modal-section-title">Недоступные показатели макета</div>
                <div class="revenue-missing-grid ar-missing-grid">
                    ${this.cashMetricCard('Просрочка >180 дней', 'Нужны данные', 'документы и сроки оплаты', overdueTip, true)}
                    ${this.cashMetricCard('Старение задолженности', 'Нужны данные', 'непогашенные остатки по документам', agingTip, true)}
                    ${this.cashMetricCard('ДЗ по сегментам', 'Нужны данные', 'категории должников', this.arTooltip({title:'ДЗ по сегментам потребителей', required: required.segments}), true)}
                    ${this.cashMetricCard('ДЗ по субсидиям', 'Нужны данные', 'отдельный регистр компенсаций', subsidyTip, true)}
                </div>`;
        },

        renderDebtorMappingEditor(analytics, debtor) {
            if (!analytics.length) return '';
            const definitions = [
                ['population', 'Население'], ['uk', 'УК/ТСЖ'],
                ['budget', 'Бюджет'], ['legal', 'Прочие юридические лица'],
                ['subsidy', 'Субсидии']
            ];
            const records = debtor.mapping_records || [];
            const editorRows = [];
            analytics.forEach(item => {
                const matched = records.filter(record =>
                    record.account === item.account &&
                    record.analytic_key === item.analytic_key);
                (matched.length ? matched : [{
                    id: '', category: '', effective_from: debtor.effective_from,
                    effective_to: debtor.effective_to
                }]).forEach(record => editorRows.push({item, record}));
            });
            const rows = editorRows.map(({item, record}) => {
                const options = definitions.map(definition =>
                    `<option value="${definition[0]}" ${record.category === definition[0] ? 'selected' : ''}>${definition[1]}</option>`).join('');
                return `<tr>
                <td>${esc(item.account)}<br><small>${esc(record.effective_from)} — ${esc(record.effective_to)}</small></td>
                <td>${esc(item.label)}</td>
                <td><select class="debtor-mapping-select"
                    data-id="${esc(record.id || '')}"
                    data-account="${esc(item.account)}" data-key="${esc(item.analytic_key)}"
                    data-label="${esc(item.label)}"
                    data-from="${esc(record.effective_from)}"
                    data-to="${esc(record.effective_to)}">
                    <option value="">Не сопоставлено / отозвать</option>${options}</select></td>
                </tr>`;
            }).join('');
            return `<div class="modal-section-title">Сопоставление категорий должников</div>
                <div class="revenue-mapping-help">Настройка действует только с ${esc(debtor.effective_from)} по ${esc(debtor.effective_to)}. Аналитика 76 включается в итог только при явном выборе категории.</div>
                <div class="revenue-table-wrap"><table class="cash-table revenue-mapping-table">
                <thead><tr><th>Счёт</th><th>Полный путь аналитики</th><th>Категория</th></tr></thead>
                <tbody>${rows}</tbody></table></div>
                <button type="button" class="revenue-mapping-save" onclick="window.KI_App.saveDebtorMappings()">Сохранить категории ДЗ</button>
                <span id="debtor-mapping-status" class="revenue-mapping-status"></span>`;
        },

        async saveDebtorMappings() {
            const companySwitchToken = state.companySwitchToken;
            const mappingsUrl = config.debtorMappingsUrl;
            const status = document.getElementById('debtor-mapping-status');
            const period = state.data.period || {};
            const mappings = Array.from(document.querySelectorAll('.debtor-mapping-select'))
                .map(select => ({
                    id: select.dataset.id ? Number(select.dataset.id) : undefined,
                    account: select.dataset.account,
                    analytic_key: select.dataset.key,
                    analytic_label: select.dataset.label,
                    category: select.value,
                    effective_from: select.dataset.from || period.start,
                    effective_to: select.dataset.to || period.end
                }));
            status.textContent = 'Сохранение...';
            try {
                const response = await fetch(mappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (companySwitchToken !== state.companySwitchToken) return;
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                if (companySwitchToken !== state.companySwitchToken) return;
                this.openModal('ar');
            } catch (error) {
                if (companySwitchToken !== state.companySwitchToken) return;
                status.textContent = error.message;
            }
        },

        apRequirements() {
            return {
                fullDebt: 'Остатки из 1С по счетам 60, 68, 69, 70 и 76: предприятие, счёт, субсчёт, аналитика, контрагент, ИНН/КПП, договор, документ расчётов, дебетовое и кредитовое сальдо, дата снимка и признак включения в кредиторскую задолженность.',
                categories: 'Справочник категорий обязательств из 1С: контрагент, договор, вид закупки, номенклатура или ресурс, объект, категория «Топливо / Электроэнергия / Подрядчики / Прочее», дата начала и окончания действия категории.',
                terms: 'Регистр обязательств из 1С: документ поступления или начисления, дата возникновения, договорный срок оплаты, сумма, НДС, аванс, зачёт, корректировка, сторно, распределённые платежи, непогашенный остаток и количество дней просрочки на отчётную дату.',
                dpo: 'Для фактического DPO нужны документы поступления и оплаты из 1С: дата признания обязательства, сумма закупки в кредит без НДС, договорный срок, документ и дата оплаты, распределение оплаты по документам, авансы, зачёты, возвраты, корректировки и непогашенный остаток.',
                taxes: 'Налоговые обязательства из 1С: вид налога или взноса, налоговый период, начисление, установленный срок уплаты, платёж, дата платежа, остаток, пени и состояние ЕНС.',
                salary: 'Данные расчётов с персоналом из 1С: расчётный период, ведомость, дата начисления, установленная дата выплаты, сумма к выплате, удержания, депонирование, фактическая дата и сумма выплаты, непогашенный остаток.',
                priority: 'Утверждённая платёжная политика: вид обязательства, правила очередности, вес срочности и критичности, допустимая просрочка, минимальный платёж, дата утверждения и период действия.',
                risk: 'Договорные и операционные риски из 1С или договорного реестра: штрафы и пени, условия приостановки поставки, критичность поставщика, заменяемость, претензионный и судебный статус.',
                status: 'Утверждённые пороги статусов: показатель, категория обязательства, значения «Норма / Внимание / Критично», единица измерения, дата утверждения и период действия.'
            };
        },

        apTooltip(overrides = {}) {
            const required = this.apRequirements();
            return Object.assign({
                title: 'Подтверждённая задолженность поставщикам',
                description: 'Кредитовое сальдо расчётов с поставщиками и подрядчиками по субсчёту 60.01.',
                formula: 'КЗ поставщикам = max(СКк(60.01) − СКд(60.01), 0)',
                detail: 'Авансы 60.02, налоги, персонал и несопоставленный счёт 76 не смешиваются с задолженностью поставщикам.',
                required: required.fullDebt,
                norm: 'Индивидуальный норматив не задан',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'конечное сальдо ОСВ 60.01'
            }, overrides);
        },

        renderApModal(card) {
            const container = document.getElementById('ki-ap-modal-content');
            if (!container) return;
            const required = this.apRequirements();
            const arCard = state.data.cards.find(item => item.id === 'ar') || {};
            const current = card.value;
            const opening = card.opening_value;
            const knownTotal = card.known_total;
            const change = current !== null && current !== undefined &&
                opening !== null && opening !== undefined ? current - opening : null;
            const arApRatio = current && arCard.value !== null && arCard.value !== undefined
                ? arCard.value / current : null;
            const period = state.data.period || {};
            const startDate = period.start ? new Date(`${period.start}T00:00:00`) : null;
            const endDate = period.end ? new Date(`${period.end}T00:00:00`) : null;
            const days = startDate && endDate ? Math.round((endDate - startDate) / 86400000) + 1 : null;
            const dpo = opening !== null && opening !== undefined &&
                current !== null && current !== undefined &&
                card.credit_turnover && days
                ? ((opening + current) / 2) / card.credit_turnover * days : null;
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;

            const supplierTip = this.apTooltip({
                fact: rub(current),
                deviation: current === null || current === undefined
                    ? 'Нет точного сальдо 60.01' : 'Фактическое подтверждённое сальдо',
                deviationClass: current === null || current === undefined ? 'missing' : 'neutral'
            });
            const changeTip = this.apTooltip({
                title: 'Изменение задолженности поставщикам',
                description: 'Изменение подтверждённого кредитового сальдо 60.01 за выбранный период.',
                formula: 'Δ КЗ = КЗ на конец − КЗ на начало',
                detail: 'Сравниваются одинаковые субсчета и аналитики на точных границах одного периода.',
                required: change === null ? 'ОСВ 60.01 с начальным и конечным дебетовым/кредитовым сальдо за один полный период.' : null,
                fact: change === null ? 'Н/Д' : `${change >= 0 ? '+' : ''}${rub(change)}`,
                deviation: change === null ? 'Нет сопоставимых границ периода' :
                    (change > 0 ? 'Задолженность выросла' : change < 0 ? 'Задолженность снизилась' : 'Без изменения'),
                deviationClass: change === null ? 'missing' : 'neutral',
                source: 'начальное и конечное сальдо ОСВ 60.01'
            });
            const ratioTip = this.apTooltip({
                title: 'Узкое соотношение ДЗ / КЗ',
                description: 'Соотношение подтверждённой задолженности покупателей и задолженности поставщикам.',
                formula: 'ДЗ / КЗ = ДЗ 62.01 / КЗ 60.01',
                detail: 'Показатель имеет узкий охват: не включает несопоставленный счёт 76, налоги, взносы и расчёты с персоналом.',
                required: arApRatio === null ? 'ОСВ 62.01 и 60.01 с конечными сальдо на одну отчётную дату. Для расширенного коэффициента — утверждённый состав обязательств и сопоставленные аналитики 76.' : null,
                fact: arApRatio === null ? 'Н/Д' : arApRatio.toFixed(2),
                deviation: arApRatio === null ? 'Недостаточно данных' : 'Фактическое узкое соотношение',
                deviationClass: arApRatio === null ? 'missing' : 'neutral',
                source: 'ОСВ 62.01 и 60.01'
            });
            const dpoTip = this.apTooltip({
                title: 'Оценочный DPO по начислениям поставщиков',
                description: 'Бухгалтерская оценка числа дней обязательств перед поставщиками.',
                formula: 'Оценочный DPO = [(КЗ начало + КЗ конец) / 2] / ОбК(60.01) × дней в периоде',
                detail: 'Кредитовый оборот 60.01 используется как приближение начислений поставщиков. Это не фактический средний срок оплаты и не формула по оборотам 20, 26 и 44.',
                required: dpo === null ? `ОСВ 60.01 с начальным и конечным сальдо и кредитовым оборотом за полный период. ${required.dpo}` : required.dpo,
                fact: dpo === null ? 'Н/Д' : `${dpo.toFixed(0)} дней`,
                deviation: dpo === null ? 'Недостаточно сопоставимых данных' : 'Оценочный показатель',
                deviationClass: dpo === null ? 'missing' : 'neutral',
                source: 'ОСВ 60.01; для точного DPO нужен регистр документов'
            });

            const componentTips = {
                '60.01': supplierTip,
                '68': this.apTooltip({
                    title: 'Расчётные обязательства по налогам и сборам',
                    description: 'Кредитовое сальдо счёта 68 на выбранную дату.',
                    formula: 'Обязательство 68 = max(СКк(68) − СКд(68), 0)',
                    detail: 'Кредитовое сальдо не подтверждает наступление срока или налоговую просрочку.',
                    required: required.taxes, source: 'конечное сальдо ОСВ 68'
                }),
                '69': this.apTooltip({
                    title: 'Расчётные обязательства по страховым взносам',
                    description: 'Кредитовое сальдо счёта 69 на выбранную дату.',
                    formula: 'Обязательство 69 = max(СКк(69) − СКд(69), 0)',
                    detail: 'Срок уплаты и просрочка не определяются из итогового сальдо.',
                    required: required.taxes, source: 'конечное сальдо ОСВ 69'
                }),
                '70': this.apTooltip({
                    title: 'Расчётная задолженность перед персоналом',
                    description: 'Кредитовое сальдо счёта 70 на выбранную дату.',
                    formula: 'Обязательство 70 = max(СКк(70) − СКд(70), 0)',
                    detail: 'Это расчётный остаток, а не автоматически просроченная заработная плата.',
                    required: required.salary, source: 'конечное сальдо ОСВ 70'
                })
            };

            const components = card.components || [];
            const componentRows = components.map(item => {
                const tip = Object.assign({}, componentTips[item.account] || this.apTooltip(), {
                    fact: rub(item.value),
                    deviation: item.value === null || item.value === undefined
                        ? (item.reason || 'Нет точного сальдо') : 'Фактическое расчётное обязательство',
                    deviationClass: item.value === null || item.value === undefined ? 'missing' : 'neutral'
                });
                const share = knownTotal && item.value !== null && item.value !== undefined
                    ? item.value / knownTotal * 100 : null;
                const apgTip = this.apTooltip({
                    title: `АПГ — ${item.label}`,
                    description: 'Сальдо того же обязательства на сопоставимую дату прошлого года.',
                    formula: 'АПГ = кредитовое сальдо того же счёта на конец аналогичного периода прошлого года',
                    fact: rub(item.previous_year_value),
                    deviation: item.previous_year_value === null ||
                        item.previous_year_value === undefined
                        ? 'Нет сопоставимого прошлогоднего сальдо'
                        : 'Сопоставимый факт прошлого года',
                    source: 'ОСВ аналогичного периода прошлого года'
                });
                const apgDeltaTip = this.apTooltip({
                    title: `Дельта АПГ — ${item.label}`,
                    description: 'Процентное изменение обязательства относительно аналогичного периода прошлого года.',
                    formula: 'Дельта АПГ = (текущее значение − АПГ) / |АПГ| × 100%',
                    fact: pct(item.previous_year_delta_percent),
                    deviation: item.previous_year_delta_percent === null ||
                        item.previous_year_delta_percent === undefined
                        ? 'Сравнение недоступно'
                        : 'Изменение к АПГ',
                    source: 'текущая ОСВ и ОСВ аналогичного периода прошлого года'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(item.label)}</strong><br><span class="ki-muted">сч. ${esc(item.account)}</span>`, tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(pct(share)), tip)}
                    ${this.collectionCell(esc(rub(item.previous_year_value)), apgTip, item.previous_year_value === null || item.previous_year_value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(pct(item.previous_year_delta_percent)), apgDeltaTip, item.previous_year_delta_percent === null || item.previous_year_delta_percent === undefined ? 'cash-value-missing' : '')}
                </tr>`;
            }).join('');

            const analytics = card.analytics || [];
            const analyticRows = analytics.length ? analytics.map(row => {
                const share = current ? row.value / current * 100 : null;
                const tip = this.apTooltip({
                    title: `КЗ по аналитике — ${row.label}`,
                    description: 'Кредитовое сальдо 60.01 по фактическому пути аналитики из ОСВ.',
                    formula: 'max(СКк − СКд, 0) по аналитике 60.01',
                    detail: 'Категория закупки не определяется автоматически по названию.',
                    required: required.categories, fact: rub(row.value),
                    deviation: 'Фактическое сальдо ОСВ', deviationClass: 'neutral'
                });
                const categoryTip = this.apTooltip({
                    title: `Категория обязательства — ${row.label}`,
                    description: 'Классификация поставщика или договора как топлива, электроэнергии, подрядных работ или прочего.',
                    formula: 'Категория берётся из утверждённого справочника сопоставлений',
                    detail: 'Без справочника приложение не угадывает категорию по названию аналитики.',
                    required: required.categories, source: 'требуется справочник категорий обязательств'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, tip)}
                    ${this.collectionCell(esc(rub(row.value)), tip)}
                    ${this.collectionCell(esc(pct(share)), tip)}
                    ${this.collectionCell('Нужны данные', categoryTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', this.apTooltip({title:`Срок оплаты — ${row.label}`, required: required.terms}), 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', this.apTooltip({title:`Статус — ${row.label}`, required:`${required.terms} ${required.status}`}), 'cash-value-missing')}
                </tr>`;
            }).join('') : `<tr><td colspan="6" class="chart-empty">В ОСВ 60.01 нет аналитических сальдо на выбранную дату. ${esc(required.categories)}</td></tr>`;

            const knownComponents = components.filter(item => item.value !== null && item.value !== undefined);
            const structure = knownComponents.length ? knownComponents.map((item, index) => {
                const share = knownTotal ? item.value / knownTotal * 100 : 0;
                return `<div class="ap-structure-row">
                    <div class="ap-structure-label"><span>${esc(item.label)} · сч. ${esc(item.account)}</span><strong>${esc(rub(item.value))} · ${esc(pct(share))}</strong></div>
                    <div class="ap-structure-track"><span class="ap-structure-fill ap-color-${index % 4}" style="width:${Math.max(0, Math.min(100, share))}%"></span></div>
                </div>`;
            }).join('') : '<div class="chart-empty">Нет точных кредитовых сальдо для построения структуры.</div>';

            const priorityRows = [
                ['Расчёты с персоналом', '70', required.salary],
                ['Налоги и страховые взносы', '68, 69', required.taxes],
                ['Поставщики и подрядчики', '60.01', required.terms],
                ['Прочие обязательства', '76', `${required.fullDebt} ${required.terms}`]
            ].map(([label, account, dataRequired]) => {
                const taxComponents = components.filter(item =>
                    ['68', '69'].includes(item.account) &&
                    item.value !== null && item.value !== undefined);
                const amount = account === '68, 69'
                    ? (taxComponents.length
                        ? taxComponents.reduce((sum, item) => sum + item.value, 0)
                        : null)
                    : (components.find(item => item.account === account) || {}).value;
                const priorityTip = this.apTooltip({
                    title: `Приоритет платежа — ${label}`,
                    description: 'Расчётная очередность оплаты с учётом срока, санкций и операционной критичности.',
                    formula: 'Приоритет определяется утверждённой платёжной политикой',
                    detail: 'Тип бухгалтерского счёта сам по себе не задаёт номер приоритета.',
                    required: `${dataRequired} ${required.priority} ${required.risk}`,
                    source: 'требуется платёжный календарь и утверждённая политика'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(label)}</strong><br><span class="ki-muted">сч. ${esc(account)}</span>`, priorityTip)}
                    ${this.collectionCell(esc(rub(amount)), priorityTip)}
                    ${this.collectionCell('Нужны данные', priorityTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', priorityTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', priorityTip, 'cash-value-missing')}
                </tr>`;
            }).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Основной факт — задолженность поставщикам 60.01.</strong>
                    Суммы 68, 69 и 70 показаны отдельно. Счёт 76 не включён без утверждённого сопоставления аналитик.</div>
                <div class="collection-summary ap-summary">
                    ${this.cashMetricCard('КЗ поставщикам', rub(current), 'сальдо 60.01', supplierTip, current === null || current === undefined, '__main__')}
                    ${this.cashMetricCard('Изменение КЗ', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'за выбранный период', changeTip, change === null)}
                    ${this.cashMetricCard('ДЗ 62.01 / КЗ 60.01', arApRatio === null ? 'Нужны данные' : arApRatio.toFixed(2), 'узкий бухгалтерский охват', ratioTip, arApRatio === null)}
                    ${this.cashMetricCard('Оценочный DPO', dpo === null ? 'Нужны данные' : `${dpo.toFixed(0)} дней`, 'по обороту Кт 60.01', dpoTip, dpo === null)}
                </div>
                <div class="modal-section-title" style="display: flex; align-items: center; gap: 8px; border-bottom: none; padding-bottom: 0;">
                    <div style="width: 20px; height: 20px; background: #3B82F6; color: #fff; border-radius: 4px; display: flex; align-items: center; justify-content: center;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3zM3 9h18M9 21V9"/></svg>
                    </div>
                    СТРУКТУРА ИЗВЕСТНЫХ РАСЧЁТНЫХ ОБЯЗАТЕЛЬСТВ
                </div>
                <div class="ap-structure">${structure}</div>
                <div class="collection-table-wrap" style="background: #fff; border-radius: 8px; border: 1px solid #E5E7EB; overflow: hidden; margin-bottom: 20px; margin-top: 10px;">
                    <table class="collection-detail-table ap-detail-table" style="min-width: 100%; width: 100%; table-layout: fixed; border: none;">
                    <thead style="background: #1E3A8A;"><tr>
                        <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: left; width: 32%;">ВИД ОБЯЗАТЕЛЬСТВА</th>
                        <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 17%;">СУММА</th>
                        <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 15%;">ДОЛЯ</th>
                        <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 18%;">АПГ</th>
                        <th style="color: #fff; border: none; padding: 12px 16px; font-weight: 600; text-align: right; width: 18%;">ДЕЛЬТА АПГ</th>
                    </tr></thead>
                    <tbody>${componentRows}</tbody>
                </table></div>
                <div class="ap-known-total-note">
                    <span><strong>Известная сумма: ${esc(rub(knownTotal))}.</strong> Это не полная КЗ, пока не сопоставлены разрешённые кредитовые аналитики счёта 76. Отсутствующие счета не принимаются за ноль.</span>
                </div>
                <div class="modal-section-title">Фактические аналитики 60.01</div>
                <div class="revenue-classification-note">До загрузки справочника категории «Топливо», «Электроэнергия» и «Подрядчики» не назначаются автоматически.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table ap-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>КЗ</th><th>Доля 60.01</th><th>Категория</th><th>Срок оплаты</th><th>Статус</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Недоступная классификация и сроки</div>
                <div class="revenue-missing-grid ar-missing-grid">
                    ${this.cashMetricCard('КЗ за топливо', 'Нужны данные', 'газ, уголь, мазут, дизель', this.apTooltip({
                        title:'Кредиторская задолженность за топливо',
                        description:'Непогашенные обязательства перед поставщиками топлива.',
                        formula:'Σ КЗ 60.01 по договорам категории «Топливо»',
                        detail:'Название поставщика само по себе не подтверждает вид ресурса.',
                        required:`${required.categories} Для топлива нужны вид топлива, поставщик, договор, номенклатура, объект потребления и документ поступления.`,
                        source:'требуется справочник категорий обязательств'
                    }), true)}
                    ${this.cashMetricCard('КЗ за электроэнергию', 'Нужны данные', 'собственные нужды', this.apTooltip({
                        title:'КЗ за электроэнергию на собственные нужды',
                        description:'Непогашенные обязательства по договорам энергоснабжения.',
                        formula:'Σ КЗ 60.01 по договорам категории «Электроэнергия»',
                        detail:'Электроэнергия не выделяется автоматически по названию аналитики.',
                        required:`${required.categories} Дополнительно нужны договор энергоснабжения, объект и назначение потребления.`,
                        source:'требуется справочник категорий обязательств'
                    }), true)}
                    ${this.cashMetricCard('КЗ подрядчикам', 'Нужны данные', 'ремонт и техническое обслуживание', this.apTooltip({
                        title:'Кредиторская задолженность подрядчикам',
                        description:'Обязательства по договорам ремонта, обслуживания и другим подрядным работам.',
                        formula:'Σ КЗ 60.01 по договорам категории «Подрядчики»',
                        detail:'Для классификации нужен вид договора и работ.',
                        required:`${required.categories} Дополнительно нужны вид работ, объект ремонта, заказ или этап работ.`,
                        source:'требуется справочник категорий обязательств'
                    }), true)}
                    ${this.cashMetricCard('Прочая КЗ 76', 'Нужны данные', 'только разрешённые аналитики', this.apTooltip({
                        title:'Прочая кредиторская задолженность',
                        description:'Кредитовые остатки утверждённых аналитик счёта 76.',
                        formula:'Σ max(СКк − СКд, 0) по разрешённым аналитикам 76',
                        detail:'Весь кредит счёта 76 не включается автоматически из-за разнородных расчётов.',
                        required:required.fullDebt,
                        source:'требуется сопоставление аналитик 76'
                    }), true)}
                    ${this.cashMetricCard('Просроченная КЗ', 'Нужны данные', 'документы и сроки оплаты', this.apTooltip({
                        title:'Просроченная кредиторская задолженность',
                        description:'Непогашенные обязательства после наступления установленного срока оплаты.',
                        formula:'Σ остатков документов, где срок оплаты раньше отчётной даты',
                        detail:'Кредитовое сальдо ОСВ не подтверждает просрочку.',
                        required:required.terms,
                        source:'требуется регистр обязательств и платежей'
                    }), true)}
                    ${this.cashMetricCard('Фактический DPO', 'Нужны данные', 'распределение оплат по документам', this.apTooltip({
                        title:'Фактический средний срок оплаты',
                        description:'Среднее время между признанием обязательства и его фактическим погашением.',
                        formula:'Средневзвешенное число дней от документа поступления до распределённой оплаты',
                        detail:'Оценочный DPO по обороту 60.01 не заменяет этот показатель.',
                        required:required.dpo,
                        source:'требуется регистр документов поступления и оплаты'
                    }), true)}
                </div>
                <div class="modal-section-title">Приоритизация платежей</div>
                <div class="collection-table-wrap"><table class="collection-detail-table ap-priority-table">
                    <thead><tr><th>Вид обязательства</th><th>Сумма</th><th>Срочность</th><th>Риск неоплаты</th><th>Приоритет</th></tr></thead>
                    <tbody>${priorityRows}</tbody>
                </table></div>`;
        },

        toggleCogsMetric(mode) {
            if (!state.modalChart) return;
            const group = String(mode).includes('ratio') ? 'ratio' : 'cost';
            const defaults = group === 'ratio'
                ? ['cogs-ratio-current', 'cogs-ratio-apg']
                : ['cogs-current', 'cogs-apg'];
            if (state.modalChart.cogsGroup !== group) {
                state.modalChart.cogsGroup = group;
                state.modalChart.cogsModes = new Set(defaults);
            } else {
                const selected = state.modalChart.cogsModes || new Set(defaults);
                if (selected.has(mode) && selected.size > 1) selected.delete(mode);
                else selected.add(mode);
                state.modalChart.cogsModes = selected;
            }
            const card = state.data.cards.find(item => item.id === 'cogs');
            this.updateCogsMetricCards();
            this.renderCogsDynamics(card);
        },

        updateCogsMetricCards() {
            const selected = state.modalChart && state.modalChart.cogsModes
                ? state.modalChart.cogsModes : new Set();
            document.querySelectorAll('[data-cogs-chart-mode]').forEach(card => {
                const active = selected.has(card.dataset.cogsChartMode);
                card.classList.toggle('cash-chart-mode-selected', active);
                card.setAttribute('aria-pressed', String(active));
            });
        },

        cogsMonthlyRows(card) {
            const groups = new Map();
            (card.monthly_comparison || []).forEach(row => {
                const key = String(row.date || '').slice(0, 7);
                if (!key) return;
                if (!groups.has(key)) {
                    groups.set(key, {
                        date: `${key}-01`,
                        current_cost: 0, previous_year_cost: 0,
                        current_revenue: 0, previous_year_revenue: 0,
                        counts: {
                            current_cost: 0, previous_year_cost: 0,
                            current_revenue: 0, previous_year_revenue: 0
                        }
                    });
                }
                const group = groups.get(key);
                ['current_cost', 'previous_year_cost',
                    'current_revenue', 'previous_year_revenue'].forEach(name => {
                    if (row[name] !== null && row[name] !== undefined &&
                            Number.isFinite(Number(row[name]))) {
                        group[name] += Number(row[name]);
                        group.counts[name] += 1;
                    }
                });
            });
            return [...groups.values()].sort((a, b) => a.date.localeCompare(b.date))
                .map(group => {
                    ['current_cost', 'previous_year_cost',
                        'current_revenue', 'previous_year_revenue'].forEach(name => {
                        if (!group.counts[name]) group[name] = null;
                    });
                    const delta = group.current_cost !== null &&
                        group.previous_year_cost !== null
                        ? group.current_cost - group.previous_year_cost : null;
                    const currentRatio = group.current_cost !== null &&
                        group.current_revenue
                        ? group.current_cost / group.current_revenue * 100 : null;
                    const previousRatio = group.previous_year_cost !== null &&
                        group.previous_year_revenue
                        ? group.previous_year_cost /
                            group.previous_year_revenue * 100 : null;
                    const date = new Date(`${group.date}T00:00:00`);
                    return {
                        ...group,
                        label: new Intl.DateTimeFormat('ru-RU', {
                            month: 'long', year: 'numeric'
                        }).format(date),
                        delta,
                        percent_delta: delta !== null && group.previous_year_cost
                            ? delta / Math.abs(group.previous_year_cost) * 100 : null,
                        current_ratio: currentRatio,
                        previous_year_ratio: previousRatio,
                        ratio_delta_pp: currentRatio !== null &&
                            previousRatio !== null
                            ? currentRatio - previousRatio : null
                    };
                });
        },

        renderCogsDynamics(card) {
            const container = document.getElementById('ki-cogs-dynamics-chart');
            if (!container || !card) return;
            const rows = this.cogsMonthlyRows(card);
            const group = state.modalChart.cogsGroup || 'cost';
            const selected = state.modalChart.cogsModes || new Set(
                group === 'ratio'
                    ? ['cogs-ratio-current', 'cogs-ratio-apg']
                    : ['cogs-current', 'cogs-apg']);
            const definitions = group === 'ratio' ? [
                ['cogs-ratio-current', 'С/с / выручка', 'current_ratio', '#2563EB'],
                ['cogs-ratio-apg', 'С/с / выручка АПГ', 'previous_year_ratio', '#DB2777']
            ] : [
                ['cogs-current', 'Себестоимость', 'current_cost', '#2563EB'],
                ['cogs-apg', 'Себестоимость АПГ', 'previous_year_cost', '#DB2777']
            ];
            const visible = definitions.filter(item => selected.has(item[0]));
            const values = visible.flatMap(item => rows.map(row => row[item[2]]))
                .filter(value => Number.isFinite(Number(value))).map(Number);
            if (!rows.length || !values.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const width = 1000, height = 300, left = 70, right = 24, top = 24, bottom = 42;
            const minValue = Math.min(0, ...values);
            const maxValue = Math.max(...values);
            const span = maxValue - minValue || 1;
            const x = index => rows.length === 1 ? width / 2 :
                left + index * (width - left - right) / (rows.length - 1);
            const y = value => top + (maxValue - value) / span * (height - top - bottom);
            let svg = `<svg class="cogs-dynamics-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика себестоимости продаж">`;
            [0, .25, .5, .75, 1].forEach(step => {
                const value = maxValue - span * step;
                const cy = top + (height - top - bottom) * step;
                const label = group === 'ratio'
                    ? `${value.toFixed(1)}%`
                    : `${(value / 1000000).toFixed(0)} млн`;
                svg += `<line x1="${left}" y1="${cy}" x2="${width-right}" y2="${cy}" stroke="#E5E7EB"/>`;
                svg += `<text x="${left-8}" y="${cy+4}" text-anchor="end" font-size="10" fill="#64748B">${esc(label)}</text>`;
            });
            rows.forEach((row, index) => {
                svg += `<text x="${x(index)}" y="${height-12}" text-anchor="middle" font-size="10" fill="#475569">${esc(row.label || row.date || '')}</text>`;
            });
            visible.forEach((definition) => {
                const [, label, key, color] = definition;
                let path = '';
                rows.forEach((row, index) => {
                    const value = row[key];
                    if (!Number.isFinite(Number(value))) return;
                    path += `${path ? ' L' : 'M'} ${x(index).toFixed(1)} ${y(Number(value)).toFixed(1)}`;
                });
                if (path) svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
                rows.forEach((row, index) => {
                    const value = row[key];
                    if (!Number.isFinite(Number(value))) return;
                    const title = group === 'ratio'
                        ? `${label}: ${Number(value).toFixed(1)}%`
                        : `${label}: ${(Number(value) / 1000000).toFixed(2)} млн ₽`;
                    svg += `<circle cx="${x(index)}" cy="${y(Number(value))}" r="5" fill="${color}" stroke="#fff" stroke-width="2"><title>${esc(row.label)} — ${esc(title)}</title></circle>`;
                });
            });
            svg += '</svg>';
            const legend = visible.map(item =>
                `<span><i class="cash-chart-dot" style="background:${item[3]}"></i>${esc(item[1])}</span>`).join('');
            container.innerHTML = svg + `<div class="cash-chart-legend">${legend}</div>`;
        },

        cogsRequirements() {
            return {
                fullCost: 'Учётная политика 1С по закрытию счетов 20, 25, 26 и 44: счета и субсчета закрытия, наличие 90.07 и 90.08, порядок распределения косвенных расходов, база распределения, незавершённое производство и период действия настройки.',
                articles: 'Справочник статей затрат из 1С: статья, группа «Топливо / Электроэнергия / ФОТ / Амортизация / Ремонт / Реагенты / Прочее», счёт затрат, подразделение, объект, услуга, дата начала и окончания действия классификации.',
                fuel: 'Данные топлива из 1С: вид топлива, номенклатура, склад, котельная или объект, документ поступления и списания, дата, количество, единица измерения, цена, сумма, статья затрат, подразделение, услуга и выпуск тепловой энергии.',
                electricity: 'Данные электроэнергии из 1С: договор, поставщик, точка поставки, объект, расчётный период, кВт·ч, мощность, тариф, сумма, НДС, статья затрат, подразделение и назначение потребления.',
                payroll: 'Данные ФОТ из 1С: подразделение, категория персонала, должность, расчётный период, вид начисления, сумма, страховые взносы, счёт и статья затрат, объект или услуга и распределение начисления по объектам.',
                depreciation: 'Данные амортизации из 1С: основное средство, инвентарный номер, группа ОС, объект эксплуатации, первоначальная стоимость, накопленная и периодическая амортизация, счёт и статья затрат, услуга и база распределения.',
                repair: 'Данные ремонта из 1С: вид ремонта, плановый/текущий/аварийный признак, объект, заказ, подрядчик, договор, акт, материалы, собственные трудозатраты, дата, сумма, статья и счёт затрат, источник финансирования.',
                reagents: 'Данные материалов ХВО из 1С: номенклатура, категория реагента или воды, склад, объект, документ списания, дата, количество, единица, цена, сумма, статья затрат и услуга.',
                tariff: 'Тарифные данные: услуга, тарифная группа, единица измерения, тариф без НДС и с НДС, период действия, орган и документ утверждения; натуральные данные: фактический объём реализации, выпуск, потери и собственные нужды.',
                plan: 'Утверждённый план РЭК или бюджет затрат: версия, сценарий, период, предприятие, услуга, объект, статья затрат, плановая сумма и натуральный объём, дата утверждения и статус актуальности.',
                status: 'Утверждённые нормативы: показатель, статья или услуга, пороги «Норма / Внимание / Критично», единица измерения, источник, дата утверждения и период действия.'
            };
        },

        cogsTooltip(overrides = {}) {
            const required = this.cogsRequirements();
            return Object.assign({
                title: 'Себестоимость продаж',
                description: 'Расходы, признанные по дебету субсчёта 90.02 за выбранный период.',
                formula: 'Себестоимость продаж = ОбД(90.02*)',
                detail: 'Обороты 20, 25, 26 и 44 не прибавляются: их суммы могут уже быть включены в 90 после закрытия периода.',
                required: required.fullCost,
                norm: 'Индивидуальный норматив не задан',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'дебетовый оборот ОСВ 90.02'
            }, overrides);
        },

        costCategoryDefinitions() {
            return [
                ['fuel', 'Топливо'], ['electricity', 'Электроэнергия'],
                ['payroll', 'ФОТ'], ['depreciation', 'Амортизация'],
                ['repair', 'Ремонт'], ['reagents', 'Реагенты'],
                ['other', 'Прочее']
            ];
        },

        renderCostMappingEditor(structure) {
            const analytics = structure.analytics || [];
            if (!config.canEditRevenueMappings || !analytics.length) return '';
            const categories = this.costCategoryDefinitions().map(([id, label]) =>
                `<option value="${id}">${esc(label)}</option>`).join('');
            const targets = [
                ['90.02', '90.02 — себестоимость продаж'],
                ['90.07', '90.07 — расходы на продажу'],
                ['90.08', '90.08 — управленческие расходы']
            ].map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join('');
            const rows = analytics.map(item => `<tr>
                <td>${esc(item.account)}</td><td>${esc(item.analytic_label)}</td>
                <td><select class="cost-mapping-category" data-account="${esc(item.account)}"
                    data-key="${esc(item.analytic_key)}" data-label="${esc(item.analytic_label)}"
                    data-from="${esc(item.valid_from)}" data-to="${esc(item.valid_to)}">
                    <option value="">Не классифицировано</option>${categories}</select></td>
                <td><select class="cost-mapping-target"><option value="">Выберите итог</option>${targets}</select></td>
                <td><input class="cost-mapping-from" type="date" value="${esc(item.valid_from)}"></td>
                <td><input class="cost-mapping-to" type="date" value="${esc(item.valid_to)}"></td>
            </tr>`).join('');
            setTimeout(() => {
                document.querySelectorAll('.cost-mapping-category').forEach(select => {
                    const item = analytics.find(row => row.account === select.dataset.account &&
                        row.analytic_key === select.dataset.key);
                    select.value = item && item.category ? item.category : '';
                    const tr = select.closest('tr');
                    tr.querySelector('.cost-mapping-target').value =
                        item && item.target_account ? item.target_account : '';
                });
            }, 0);
            return `<div class="modal-section-title">Сопоставление формирующих затрат</div>
                <div class="revenue-mapping-help">Правила действуют для указанного периода. Каждая аналитика относится к одной статье и одному признанному итогу; счета 20/25/26/44 с итогами 90 повторно не складываются.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table">
                    <thead><tr><th>Счёт</th><th>Аналитика</th><th>Статья</th><th>Сверять с</th><th>С</th><th>По</th></tr></thead>
                    <tbody>${rows}</tbody></table></div>
                <button type="button" class="revenue-mapping-save" onclick="window.KI_App.saveCostMappings()">Сохранить сопоставления</button>
                <span id="cost-mapping-status" class="revenue-mapping-status"></span>`;
        },

        async saveCostMappings() {
            const companySwitchToken = state.companySwitchToken;
            const mappingsUrl = config.costMappingsUrl;
            const status = document.getElementById('cost-mapping-status');
            const mappings = Array.from(document.querySelectorAll('.cost-mapping-category'))
                .map(select => {
                    const tr = select.closest('tr');
                    return {
                        account: select.dataset.account,
                        analytic_key: select.dataset.key,
                        analytic_label: select.dataset.label,
                        category: select.value,
                        target_account: tr.querySelector('.cost-mapping-target').value,
                        valid_from: tr.querySelector('.cost-mapping-from').value,
                        valid_to: tr.querySelector('.cost-mapping-to').value
                    };
                });
            status.textContent = 'Сохранение...';
            try {
                const response = await fetch(mappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (companySwitchToken !== state.companySwitchToken) return;
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                if (companySwitchToken !== state.companySwitchToken) return;
                this.openModal('cogs');
            } catch (error) {
                if (companySwitchToken !== state.companySwitchToken) return;
                status.textContent = error.message;
            }
        },

        renderCogsModal(card) {
            const container = document.getElementById('ki-cogs-modal-content');
            if (!container) return;
            const required = this.cogsRequirements();
            const revenueCard = state.data.cards.find(item => item.id === 'revenue') || {};
            const profitCard = state.data.cards.find(item => item.id === 'profit') || {};
            const cogsGrossProfit = profitCard.gross_profit !== null &&
                profitCard.gross_profit !== undefined
                ? profitCard.gross_profit : profitCard.value;
            const current = card.value;
            const netRevenue = revenueCard.value !== null && revenueCard.value !== undefined &&
                !String(revenueCard.title || '').includes('брутто') ? revenueCard.value : null;
            const costRatio = current !== null && current !== undefined && netRevenue
                ? current / netRevenue * 100 : null;
            const previousYearCost = card.previous_year_value;
            const previousYearRatio = card.previous_year_ratio;
            const change = card.delta && card.delta.value !== null &&
                card.delta.value !== undefined ? card.delta.value : null;
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const structure = card.cost_structure || {};
            if (!state.modalChart.cogsGroup) state.modalChart.cogsGroup = 'cost';
            if (!state.modalChart.cogsModes) {
                state.modalChart.cogsModes = new Set(['cogs-current', 'cogs-apg']);
            }
            const cogsMetricCard = (title, value, subtitle, mode, missing) => `
                <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card ${
                    state.modalChart.cogsModes.has(mode) ? 'cash-chart-mode-selected' : ''}"
                     tabindex="0" role="button"
                     aria-pressed="${state.modalChart.cogsModes.has(mode)}"
                     data-layout-key="${mode}"
                     data-cogs-chart-mode="${mode}"
                     onclick="window.KI_App.toggleCogsMetric('${mode}')"
                     onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCogsMetric('${mode}')}">
                    <div class="cash-summary-label">${esc(title)}</div>
                    <div class="cash-summary-value ${missing ? 'cash-value-missing' : 'cash-value-ok'}">${esc(value)}</div>
                    <div class="cash-summary-sub">${esc(subtitle)}</div>
                </div>`;
            const formatSigned = (value, suffix) =>
                value === null || value === undefined || !Number.isFinite(Number(value))
                    ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}${suffix}`;
            const periodRows = this.cogsMonthlyRows(card).map(row => {
                const values = [
                    row.current_cost, row.previous_year_cost, row.delta,
                    row.percent_delta, row.current_revenue,
                    row.previous_year_revenue, row.current_ratio,
                    row.previous_year_ratio, row.ratio_delta_pp
                ];
                const missing = value => value === null || value === undefined ||
                    !Number.isFinite(Number(value));
                return `<tr>
                    <td>${esc(row.label || row.date || '—')}</td>
                    <td class="cash-number-cell">${missing(values[0]) ? '—' : esc(rub(values[0]))}</td>
                    <td class="cash-number-cell">${missing(values[1]) ? '—' : esc(rub(values[1]))}</td>
                    <td class="cash-number-cell">${missing(values[2]) ? '—' : esc(`${values[2] > 0 ? '+' : ''}${rub(values[2])}`)}</td>
                    <td>${esc(formatSigned(values[3], '%'))}</td>
                    <td class="cash-number-cell">${missing(values[4]) ? '—' : esc(rub(values[4]))}</td>
                    <td class="cash-number-cell">${missing(values[5]) ? '—' : esc(rub(values[5]))}</td>
                    <td>${missing(values[6]) ? '—' : esc(pct(values[6]))}</td>
                    <td>${missing(values[7]) ? '—' : esc(pct(values[7]))}</td>
                    <td>${esc(formatSigned(values[8], ' п.п.'))}</td>
                </tr>`;
            }).join('');

            const totalTip = this.cogsTooltip({
                fact: rub(current),
                deviation: current === null || current === undefined
                    ? 'Нет точного оборота 90.02' : 'Фактический оборот 90.02',
                deviationClass: current === null || current === undefined ? 'missing' : 'neutral'
            });
            const changeTip = this.cogsTooltip({
                title: 'Изменение себестоимости продаж',
                description: 'Изменение дебетового оборота 90.02 к предыдущему полностью сопоставимому периоду.',
                formula: 'Δ себестоимости = текущий ОбД(90.02) − предыдущий ОбД(90.02)',
                detail: 'Неполные и несопоставимые периоды не сравниваются, пропуски не заменяются нулями.',
                required: change === null ? 'ОСВ 90.02 за два последовательных полных периода с одинаковым составом аналитик.' : null,
                fact: change === null ? 'Н/Д' : `${change >= 0 ? '+' : ''}${rub(change)}`,
                deviation: change === null ? 'Нет сопоставимого периода' :
                    (change > 0 ? 'Себестоимость выросла' : change < 0 ? 'Себестоимость снизилась' : 'Без изменения'),
                deviationClass: change === null ? 'missing' : 'neutral'
            });
            const ratioTip = this.cogsTooltip({
                title: 'Себестоимость продаж к выручке',
                description: 'Доля нетто-выручки, приходящаяся на себестоимость продаж.',
                formula: 'ОбД(90.02) / [ОбК(90.01) − ОбД(90.03)] × 100%',
                detail: 'Выручка с НДС при отсутствии 90.03 не используется скрыто.',
                required: costRatio === null ? 'ОСВ 90.01, 90.02 и 90.03 за один сопоставимый период.' : null,
                fact: pct(costRatio),
                deviation: costRatio === null ? 'Недостаточно данных' :
                    (costRatio > 100 ? 'Себестоимость выше нетто-выручки' : 'Фактическое соотношение'),
                deviationClass: costRatio === null ? 'missing' : (costRatio > 100 ? 'bad' : 'neutral'),
                source: 'ОСВ 90.01, 90.02 и 90.03'
            });
            const profitTip = this.cogsTooltip({
                title: 'Прибыль от продаж до отдельных расходов',
                description: 'Разница между нетто-выручкой и себестоимостью 90.02.',
                formula: 'Прибыль = [ОбК(90.01) − ОбД(90.03)] − ОбД(90.02)',
                detail: 'Это не чистая прибыль и не полный EBIT: 90.07, 90.08, 91 и налог на прибыль учитываются отдельно.',
                required: profitCard.value === null || profitCard.value === undefined
                    ? 'ОСВ 90.01, 90.02 и 90.03 за один период; для полного результата — 90.07, 90.08, 91 и налог на прибыль.' : null,
                fact: rub(cogsGrossProfit),
                deviation: cogsGrossProfit === null || cogsGrossProfit === undefined
                    ? 'Недостаточно данных' : (cogsGrossProfit < 0 ? 'Валовой убыток' : 'Валовая прибыль'),
                deviationClass: cogsGrossProfit === null || cogsGrossProfit === undefined
                    ? 'missing' : (cogsGrossProfit < 0 ? 'bad' : 'good'),
                source: 'ОСВ 90.01, 90.02 и 90.03'
            });

            const recognizedRows = (card.recognized_costs || []).map(item => {
                const recognized = item.account === '90.02';
                const tip = this.cogsTooltip({
                    title: item.label,
                    description: recognized ? 'Себестоимость продаж за период.' :
                        `Расходы, признанные на субсчёте ${item.account}.`,
                    formula: `ОбД(${item.account})`,
                    detail: recognized ? 'Основной подтверждённый показатель окна.' :
                        'Показывается отдельно и не смешивается с первоначальными оборотами 26 или 44.',
                    required: item.value === null || item.value === undefined
                        ? `ОСВ счёта 90 со строкой ${item.account} и дебетовым оборотом за выбранный период. ${required.fullCost}` : required.fullCost,
                    fact: rub(item.value),
                    deviation: item.value === null || item.value === undefined ? 'Субсчёт не представлен' : 'Фактический признанный расход',
                    deviationClass: item.value === null || item.value === undefined ? 'missing' : 'neutral',
                    source: `дебетовый оборот ОСВ ${item.account}`
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(item.label)}</strong><br><span class="ki-muted">сч. ${esc(item.account)}</span>`, tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(recognized ? 'Основной показатель' : 'Показывается отдельно', tip)}
                </tr>`;
            }).join('');

            const movementRows = (card.cost_movements || []).map(item => {
                const tip = this.cogsTooltip({
                    title: `Движение затрат по счёту ${item.account}`,
                    description: `${item.label}: дебетовый оборот за выбранный период.`,
                    formula: `Движение затрат = ОбД(${item.account})`,
                    detail: 'Это движение по счёту формирования затрат, а не дополнительная сумма к 90.02.',
                    required: item.value === null || item.value === undefined
                        ? `ОСВ счёта ${item.account} с дебетовым оборотом и аналитикой статей, подразделений, объектов и услуг.` : required.articles,
                    fact: rub(item.value),
                    deviation: item.value === null || item.value === undefined ? 'Данные не представлены' : 'Справочный оборот, не суммируется',
                    deviationClass: item.value === null || item.value === undefined ? 'missing' : 'neutral',
                    source: `дебетовый оборот ОСВ ${item.account}`
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(item.label)}</strong><br><span class="ki-muted">сч. ${esc(item.account)}</span>`, tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell('Не прибавляется к 90.02', tip)}
                </tr>`;
            }).join('');

            const analyticRows = (card.analytics || []).length ? card.analytics.map(row => {
                const share = current && row.value !== null && row.value !== undefined
                    ? row.value / current * 100 : null;
                const tip = this.cogsTooltip({
                    title: `Себестоимость — ${row.label}`,
                    description: 'Дебетовый оборот 90.02 по фактическому пути аналитики из ОСВ.',
                    formula: 'Σ ОбД(90.02) по аналитике',
                    detail: 'Статья затрат не определяется автоматически по тексту аналитики.',
                    required: required.articles,
                    fact: rub(row.value),
                    deviation: row.value === null || row.value === undefined ? 'Оборот неизвестен' : 'Фактическая аналитика 90.02',
                    deviationClass: row.value === null || row.value === undefined ? 'missing' : 'neutral'
                });
                const categoryTip = this.cogsTooltip({
                    title: `Статья затрат — ${row.label}`,
                    description: 'Утверждённая классификация расхода по экономической статье.',
                    formula: 'Категория берётся из справочника статей затрат',
                    detail: 'Без справочника аналитика не относится автоматически к топливу, энергии, ФОТ или ремонту.',
                    required: required.articles,
                    source: 'требуется справочник статей затрат'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, tip)}
                    ${this.collectionCell(esc(rub(row.value)), tip)}
                    ${this.collectionCell(esc(pct(share)), tip)}
                    ${this.collectionCell('Нужны данные', categoryTip, 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', this.cogsTooltip({title:`Статус — ${row.label}`, required:`${required.plan} ${required.status}`}), 'cash-value-missing')}
                </tr>`;
            }).join('') : `<tr><td colspan="5" class="chart-empty">В ОСВ нет аналитических оборотов 90.02. ${esc(required.articles)}</td></tr>`;

            const structureRows = (structure.categories || []).map(row => `<tr>
                <td><strong>${esc(row.label)}</strong></td>
                <td>${esc(rub(row.value))}</td>
                <td class="${row.share === null ? 'cash-value-missing' : ''}">${esc(pct(row.share))}</td>
            </tr>`).join('');
            const structureProblems = [
                ...(structure.unclassified || []).map(row =>
                    `Не сопоставлено: ${row.account} — ${row.analytic_label}`),
                ...(structure.unknown_amounts || []).map(row =>
                    `Нет суммы: ${row.account} — ${row.analytic_label}`),
                ...(structure.conflicts || []).map(row =>
                    `${row.message}${row.analytic_label ? ` — ${row.account}: ${row.analytic_label}` : ''}`)
            ];

            const objectRows = ((state.data.objects || {}).rows || []).map(row => {
                const factTip = this.cogsTooltip({
                    title: `Финансовый результат — ${row.label}`,
                    description: 'Выручка и себестоимость по совпадающей аналитике счёта 90.',
                    formula: 'Прибыль = нетто-выручка − себестоимость 90.02',
                    detail: 'Результат доступен только при точном сопоставлении 90.01, 90.02 и 90.03.',
                    required: 'Единый аналитический ключ услуги или объекта в 90.01, 90.02 и 90.03.',
                    source: 'аналитические обороты счёта 90'
                });
                const tariffTip = this.cogsTooltip({
                    title: `Себестоимость единицы и тариф — ${row.label}`,
                    description: 'Сравнение фактической себестоимости единицы услуги с утверждённым тарифом.',
                    formula: 'С/с единицы = затраты / натуральный объём; покрытие = тариф / с/с единицы × 100%',
                    detail: 'Финансовая ОСВ не содержит Гкал, м³, кВт·ч или тарифный справочник.',
                    required: required.tariff,
                    source: 'требуются тарифный справочник и натуральные объёмы'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, factTip)}
                    ${this.collectionCell(esc(rub(row.cost)), factTip)}
                    ${this.collectionCell(esc(rub(row.revenue)), factTip)}
                    ${this.collectionCell(esc(rub(row.profit)), factTip)}
                    ${this.collectionCell(esc(pct(row.margin)), factTip)}
                    ${this.collectionCell('Нужны данные', tariffTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', tariffTip, 'cash-value-missing')}
                </tr>`;
            }).join('') || `<tr><td colspan="7" class="chart-empty">Нет сопоставимых аналитик 90.01 и 90.02.</td></tr>`;

            const unavailable = [
                ['Топливо', 'газ, уголь, мазут, дизель', required.fuel],
                ['Электроэнергия', 'собственные нужды', required.electricity],
                ['ФОТ производства', 'начисления по объектам', required.payroll],
                ['Амортизация', 'основные средства и объекты', required.depreciation],
                ['Ремонт и ТО', 'материалы и подрядные работы', required.repair],
                ['Реагенты, вода, ХВО', 'натуральное списание материалов', required.reagents],
                ['План РЭК', 'план-факт по статьям', required.plan],
                ['Тарифное покрытие', 'тариф и себестоимость единицы', required.tariff]
            ].map(([label, sub, dataRequired]) => this.cashMetricCard(
                label, 'Нужны данные', sub, this.cogsTooltip({
                    title: label,
                    description: `Детализация себестоимости: ${label.toLowerCase()}.`,
                    formula: 'Расчёт выполняется после точной классификации первичных затрат',
                    detail: 'Показатель не восстанавливается из похожего оборота и не заполняется демонстрационными значениями.',
                    required: `${required.articles} ${dataRequired}`,
                    source: 'требуется детальная выгрузка затрат из 1С'
                }), true)).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Подтверждённый показатель — ОбД(90.02).</strong>
                    Обороты 20, 25, 26 и 44 показываются отдельно и не прибавляются к себестоимости продаж.</div>
                <div class="cash-summary-grid cogs-summary">
                    ${cogsMetricCard('Себестоимость продаж', rub(current), 'дебет 90.02', 'cogs-current', current === null || current === undefined)}
                    ${this.cashMetricCard('Изменение', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'к сопоставимому периоду', changeTip, change === null)}
                    ${cogsMetricCard('Себестоимость / Выручка', pct(costRatio), 'по нетто-выручке', 'cogs-ratio-current', costRatio === null)}
                    ${this.cashMetricCard('Валовая прибыль', rub(cogsGrossProfit), 'нетто-выручка − 90.02', profitTip, cogsGrossProfit === null || cogsGrossProfit === undefined)}
                    ${cogsMetricCard('Себестоимость продаж АПГ', rub(previousYearCost), 'аналогичный период прошлого года', 'cogs-apg', previousYearCost === null || previousYearCost === undefined)}
                    ${cogsMetricCard('Себестоимость / Выручка АПГ', pct(previousYearRatio), 'аналогичный период прошлого года', 'cogs-ratio-apg', previousYearRatio === null || previousYearRatio === undefined)}
                </div>
                <div class="ki-modal-dynamics-block" data-layout-key="dynamics" data-layout-title="Динамика">
                    <div class="modal-section-title">ДИНАМИКА СЕБЕСТОИМОСТИ ПРОДАЖ</div>
                    <div id="ki-cogs-dynamics-chart" class="cash-line-chart"></div>
                </div>
                <div class="modal-section-title">СЕБЕСТОИМОСТЬ ПО ПЕРИОДАМ</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-period-table">
                    <thead><tr>
                        <th>Период</th><th>Себестоимость</th><th>АПГ</th>
                        <th>Δ, ₽</th><th>Δ, %</th><th>Выручка</th>
                        <th>Выручка АПГ</th><th>С/с / выручка</th>
                        <th>АПГ</th><th>Δ, п.п.</th>
                    </tr></thead>
                    <tbody>${periodRows || '<tr><td colspan="10">Нет сопоставимых данных</td></tr>'}</tbody>
                </table></div>
                <div class="modal-section-title">Признанные расходы счёта 90</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-recognized-table">
                    <thead><tr><th>Показатель</th><th>Сумма</th><th>Использование</th></tr></thead>
                    <tbody>${recognizedRows}</tbody>
                </table></div>
                <div class="modal-section-title">Структура затрат по утверждённым статьям</div>
                ${structureProblems.length ? `<div class="revenue-classification-note">${esc(structureProblems.join('; '))}.</div>` : ''}
                <div class="cash-info-banner"><strong>Контроль без повторного учёта.</strong>
                    Классифицировано: ${esc(rub(structure.classified_total))};
                    выбранный признанный итог (${esc((structure.selected_targets || []).join(', ') || 'не выбран')}):
                    ${esc(rub(structure.recognized_total))};
                    расхождение: ${esc(rub(structure.difference))}.
                    ${structure.reconciled ? 'Итог сверён.' : 'Структура не подтверждена; доли не считаются корректными.'}</div>
                <div class="collection-table-wrap"><table class="collection-detail-table">
                    <thead><tr><th>Статья</th><th>Формирующие затраты</th><th>Доля признанного итога</th></tr></thead>
                    <tbody>${structureRows}</tbody></table></div>
                ${this.renderCostMappingEditor(structure)}
                <div class="modal-section-title">Фактические аналитики 90.02</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Себестоимость</th><th>Доля 90.02</th><th>Статья затрат</th><th>Статус</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Движение затрат — без прибавления к 90.02</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-movement-table">
                    <thead><tr><th>Счёт</th><th>Дебетовый оборот</th><th>Методическое использование</th></tr></thead>
                    <tbody>${movementRows}</tbody>
                </table></div>
                <div class="modal-section-title">Себестоимость и финансовый результат по аналитикам</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-object-table">
                    <thead><tr><th>Услуга / объект</th><th>Себестоимость</th><th>Выручка</th><th>Прибыль</th><th>Рентабельность</th><th>С/с единицы</th><th>Тариф</th></tr></thead>
                    <tbody>${objectRows}</tbody>
                </table></div>
                <div class="modal-section-title">Дополнительные тарифные показатели</div>
                <div class="revenue-missing-grid cogs-missing-grid">${unavailable}</div>`;
            this.updateCogsMetricCards();
            this.renderCogsDynamics(card);
        },

        profitRequirements() {
            return {
                sales: 'ОСВ счёта 90 за один период: полный путь аналитики, 90.01, 90.02, 90.03, 90.07 и 90.08, дебетовый и кредитовый оборот, услуга, объект, подразделение и учётная политика закрытия 20, 25, 26 и 44.',
                other: 'ОСВ счёта 91: отдельные субсчета 91.01 и 91.02 без повторного учёта закрывающего 91.09; полный путь аналитики, статья прочих доходов/расходов, документ, дата, контрагент, сумма и корреспондирующий счёт.',
                classification: 'Классификатор статей 91 из 1С: операционная/неоперационная статья, процентная/непроцентная, штрафы, пени, курсовая разница, выбытие ОС, резерв, субсидия, RAB-доход, налоговая статья и период действия классификации.',
                interest: 'Кредитный регистр 1С: договор, кредитор, вид займа, валюта, основной долг, ставка, период начисления, начисленные, уплаченные, просроченные и капитализированные проценты, документ начисления и статья 91.02.',
                rab: 'Данные RAB и субсидий: программа и бюджет, документ назначения, документ и дата признания дохода, счёт и аналитика, период дохода, сумма, услуга, НВВ, база инвестированного капитала, ставка доходности и поступление денег.',
                tax: 'Данные налога на прибыль: аналитика 99 и 68.04, текущий налог, отложенный налог, постоянный налоговый расход/доход, документ, период, обороты закрытия 99 на 84 и дата реформации баланса.',
                depreciation: 'Регистр амортизации 1С: основное средство, инвентарный номер, объект эксплуатации, период, начисленная амортизация, счёт и статья затрат, услуга, капитализированная часть и распределение по производственным, коммерческим и управленческим расходам.',
                assets: 'Бухгалтерский баланс или полный набор счетов активов: активы на начало и конец периода, состав и валюта баланса, корректировки, чистая прибыль того же периода.',
                objects: 'Единый аналитический ключ услуги или объекта в 90.01, 90.02 и 90.03; для полной прибыли — распределение 90.07, 90.08 и статей 91 по тем же ключам и база распределения.',
                plan: 'Утверждённый финансовый план или план РЭК: версия, сценарий, период, предприятие, услуга, объект, статья, плановая выручка, затраты и прибыль, дата утверждения и статус актуальности.',
                status: 'Утверждённые пороги: показатель, услуга, границы «Норма / Внимание / Критично», единица измерения, документ-основание, дата утверждения и период действия.'
            };
        },

        profitTooltip(overrides = {}) {
            return Object.assign({
                title: 'Валовая прибыль',
                description: 'Финансовый результат после себестоимости продаж, до отдельных коммерческих, управленческих, прочих расходов и налога.',
                formula: 'Валовая прибыль = [ОбК(90.01) − ОбД(90.03)] − ОбД(90.02)',
                detail: 'Счета 20, 26 и 44 повторно не вычитаются.',
                required: this.profitRequirements().sales,
                norm: 'Индивидуальный норматив не задан',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'ОСВ субсчетов 90'
            }, overrides);
        },

        renderProfitModal(card) {
            const container = document.getElementById('ki-profit-modal-content');
            if (!container) return;
            const req = this.profitRequirements();
            const revenue = state.data.cards.find(item => item.id === 'revenue') || {};
            const cogs = state.data.cards.find(item => item.id === 'cogs') || {};
            const objects = (state.data.objects || {}).rows || [];
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const margin = value => value !== null && value !== undefined && revenue.value
                ? value / revenue.value * 100 : null;
            const metricTip = (title, formula, value, detail, required, source = 'ОСВ субсчетов 90') =>
                this.profitTooltip({
                    title, formula, detail, required,
                    fact: rub(value),
                    deviation: value === null || value === undefined
                        ? 'Нужны данные' : (value < 0 ? 'Отрицательный результат' : 'Положительный результат'),
                    deviationClass: value === null || value === undefined
                        ? 'missing' : (value < 0 ? 'bad' : 'good'),
                    source
                });
            const grossTip = metricTip(
                'Валовая прибыль',
                '[ОбК(90.01) − ОбД(90.03)] − ОбД(90.02)',
                card.gross_profit,
                'Это не прибыль от продаж и не чистая прибыль.',
                req.sales);
            const salesTip = metricTip(
                'Прибыль от продаж',
                'Валовая прибыль − ОбД(90.07) − ОбД(90.08)',
                card.profit_from_sales,
                'Используются признанные расходы 90.07 и 90.08. Обороты 26 и 44 повторно не вычитаются.',
                req.sales);
            const otherTip = metricTip(
                'Сальдо прочих доходов и расходов',
                'ОбК(91.01) − ОбД(91.02)',
                card.other_result,
                'Закрывающий субсчёт 91.09 не включается.',
                `${req.other} ${req.classification}`,
                'ОСВ 91.01 и 91.02');
            const pretaxTip = metricTip(
                'Расчётный результат до налога',
                'Прибыль от продаж + сальдо 91.01/91.02',
                card.profit_before_tax,
                'Результат показан до выделения процентов и налога; это не подтверждённая чистая прибыль.',
                `${req.sales} ${req.other}`,
                'ОСВ 90 и 91');

            const decomposition = [
                ['Нетто-выручка', 'ОбК(90.01) − ОбД(90.03)', revenue.value, 1, req.sales, 'Выручка после выделения НДС.'],
                ['Себестоимость продаж', 'ОбД(90.02)', cogs.value, -1, req.sales, 'Признанная себестоимость продаж.'],
                ['Валовая прибыль', 'Нетто-выручка − 90.02', card.gross_profit, 1, req.sales, 'Результат до отдельных расходов.'],
                ['Коммерческие расходы', 'ОбД(90.07)', card.selling_expense, -1, req.sales, 'Признанные расходы на продажу.'],
                ['Управленческие расходы', 'ОбД(90.08)', card.admin_expense, -1, req.sales, 'Признанные управленческие расходы.'],
                ['Прибыль от продаж', 'Валовая прибыль − 90.07 − 90.08', card.profit_from_sales, 1, req.sales, 'Результат основной деятельности.'],
                ['Прочие доходы', 'ОбК(91.01)', card.other_income, 1, req.other, 'Доходы счёта 91 без 91.09.'],
                ['Прочие расходы', 'ОбД(91.02)', card.other_expense, -1, req.other, 'Расходы счёта 91 без 91.09.'],
                ['Результат до налога', 'Прибыль от продаж + 91.01 − 91.02', card.profit_before_tax, 1, `${req.sales} ${req.other}`, 'Не является чистой прибылью.'],
                ['Налог на прибыль', 'Текущий + отложенный налог', null, -1, req.tax, 'Не выводится из общего сальдо счёта 68.'],
                ['Чистая прибыль', 'Результат до налога − налог', null, 1, req.tax, 'Нужны точные данные 99 и 68.04.']
            ].map(([label, formula, value, sign, required, detail]) => {
                const tip = metricTip(label, formula, value, detail, required,
                    label.includes('Проч') ? 'ОСВ 91' : 'ОСВ 90/91/99');
                const shown = value === null || value === undefined
                    ? 'Нужны данные' : `${sign < 0 ? '− ' : value > 0 ? '+ ' : ''}${rub(Math.abs(value))}`;
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(label)}</strong><br><span class="ki-muted">${esc(formula)}</span>`, tip)}
                    ${this.collectionCell(esc(shown), tip, value === null || value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(detail), tip)}
                </tr>`;
            }).join('');

            const otherRows = (card.other_analytics || []).length
                ? card.other_analytics.map(row => {
                    const net = row.income !== null && row.income !== undefined &&
                        row.expense !== null && row.expense !== undefined
                        ? row.income - row.expense : null;
                    const tip = metricTip(
                        `Статья 91 — ${row.label}`,
                        'Доход 91.01 − расход 91.02',
                        net,
                        'Статья показывается по фактическому пути аналитики; её вид не угадывается по названию.',
                        `${req.other} ${req.classification}`,
                        'аналитические обороты 91.01/91.02');
                    return `<tr>
                        ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, tip)}
                        ${this.collectionCell(esc(rub(row.income)), tip, row.income === null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub(row.expense)), tip, row.expense === null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub(net)), tip, net === null ? 'cash-value-missing' : '')}
                        ${this.collectionCell('Нужна классификация', tip, 'cash-value-missing')}
                    </tr>`;
                }).join('')
                : `<tr><td colspan="5" class="chart-empty">Аналитики 91.01/91.02 не представлены. ${esc(req.other)}</td></tr>`;

            const objectRows = objects.length ? objects.map(row => {
                const tip = this.profitTooltip({
                    title: `Валовой результат — ${row.label}`,
                    description: 'Выручка и себестоимость по совпадающему аналитическому ключу счёта 90.',
                    formula: 'Нетто-выручка аналитики − себестоимость 90.02',
                    detail: 'Без распределения 90.07, 90.08 и статей 91 это валовой, а не полный результат услуги.',
                    required: req.objects,
                    fact: rub(row.profit),
                    deviation: row.profit === null || row.profit === undefined
                        ? 'Нужны данные' : (row.profit < 0 ? 'Валовой убыток' : 'Валовая прибыль'),
                    deviationClass: row.profit === null || row.profit === undefined
                        ? 'missing' : (row.profit < 0 ? 'bad' : 'good')
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, tip)}
                    ${this.collectionCell(esc(rub(row.revenue)), tip)}
                    ${this.collectionCell(esc(rub(row.cost)), tip)}
                    ${this.collectionCell(esc(rub(row.profit)), tip)}
                    ${this.collectionCell(esc(pct(row.margin)), tip)}
                    ${this.collectionCell(row.profit === null || row.profit === undefined ? 'Нужны данные' : row.profit < 0 ? 'Валовой убыток' : 'Валовая прибыль', tip, row.profit === null || row.profit === undefined ? 'cash-value-missing' : '')}
                </tr>`;
            }).join('') : '<tr><td colspan="6" class="chart-empty">Нет сопоставимых аналитик 90.01/90.02/90.03.</td></tr>';

            const series = card.gross_series || [];
            const salesSeries = card.sales_profit_series || [];
            const otherSeries = card.other_result_series || [];
            const pretaxSeries = card.pretax_series || [];
            const dynamics = series.length ? series.map((item, index) => {
                const tip = metricTip(
                    `Финансовый результат — ${item.label}`,
                    'См. значения отдельных уровней результата',
                    item.value,
                    'Пропуски не заменяются нулями.',
                    `${req.sales} ${req.other}`);
                return `<tr>
                    ${this.collectionCell(esc(item.label), tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub((salesSeries[index] || {}).value)), tip, (salesSeries[index] || {}).value === null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub((otherSeries[index] || {}).value)), tip, (otherSeries[index] || {}).value === null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub((pretaxSeries[index] || {}).value)), tip, (pretaxSeries[index] || {}).value === null ? 'cash-value-missing' : '')}
                </tr>`;
            }).join('') : '<tr><td colspan="5" class="chart-empty">Нет дневной серии.</td></tr>';

            const unavailable = [
                ['EBIT', 'до процентов и налога', `${req.classification} ${req.interest}`],
                ['EBITDA', 'EBIT + амортизация', `${req.classification} ${req.interest} ${req.depreciation}`],
                ['Чистая прибыль', 'после налога на прибыль', req.tax],
                ['ROA', 'чистая прибыль / средние активы', `${req.tax} ${req.assets}`],
                ['Процентные расходы', 'начисленные проценты', req.interest],
                ['RAB-доход и субсидии', 'без повторного учёта', req.rab],
                ['Плановая прибыль', 'финансовый план / план РЭК', req.plan],
                ['Нормативный статус', 'утверждённые пороги', req.status]
            ].map(([label, sub, required]) => this.cashMetricCard(
                label, 'Нужны данные', sub, this.profitTooltip({
                    title: label,
                    description: `Показатель «${label}» не восстанавливается из похожего оборота ОСВ.`,
                    formula: sub,
                    detail: 'Демонстрационные нормативы и значения макета не используются.',
                    required,
                    source: 'требуется дополнительная выгрузка из 1С'
                }), true)).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Уровни результата разделены.</strong>
                    Валовая прибыль, прибыль от продаж и результат до налога не называются чистой прибылью.</div>
                <div class="collection-summary profit-summary">
                    ${this.cashMetricCard('Валовая прибыль', rub(card.gross_profit), `Рв = ${pct(margin(card.gross_profit))}`, grossTip, card.gross_profit === null || card.gross_profit === undefined, '__main__')}
                    ${this.cashMetricCard('Прибыль от продаж', rub(card.profit_from_sales), `Рп = ${pct(margin(card.profit_from_sales))}`, salesTip, card.profit_from_sales === null || card.profit_from_sales === undefined)}
                    ${this.cashMetricCard('Сальдо счёта 91', rub(card.other_result), '91.01 − 91.02', otherTip, card.other_result === null || card.other_result === undefined)}
                    ${this.cashMetricCard('Результат до налога', rub(card.profit_before_tax), 'не чистая прибыль', pretaxTip, card.profit_before_tax === null || card.profit_before_tax === undefined)}
                </div>
                <div class="modal-section-title">Декомпозиция финансового результата</div>
                <div class="collection-table-wrap"><table class="collection-detail-table profit-waterfall-table">
                    <thead><tr><th>Уровень</th><th>Сумма</th><th>Комментарий</th></tr></thead>
                    <tbody>${decomposition}</tbody>
                </table></div>
                <div class="collection-table-wrap"><table class="collection-detail-table profit-dynamics-table">
                    <thead><tr><th>Дата / период</th><th>Валовая прибыль</th><th>Прибыль от продаж</th><th>Сальдо 91</th><th>До налога</th></tr></thead>
                    <tbody>${dynamics}</tbody>
                </table></div>
                <div class="modal-section-title">Фактические аналитики счёта 91</div>
                <div class="collection-table-wrap"><table class="collection-detail-table profit-other-table">
                    <thead><tr><th>Аналитика 1С</th><th>Доходы</th><th>Расходы</th><th>Сальдо</th><th>Классификация</th></tr></thead>
                    <tbody>${otherRows}</tbody>
                </table></div>
                <div class="modal-section-title">Валовая рентабельность по аналитикам счёта 90</div>
                <div class="revenue-classification-note">Полная прибыль услуги требует распределения 90.07, 90.08 и статей 91 по тому же аналитическому ключу.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table profit-object-table">
                    <thead><tr><th>Услуга / объект</th><th>Выручка</th><th>Себестоимость</th><th>Валовая прибыль</th><th>Рентабельность</th><th>Статус</th></tr></thead>
                    <tbody>${objectRows}</tbody>
                </table></div>
                <div class="modal-section-title">Показатели, требующие дополнительных данных 1С</div>
                <div class="revenue-missing-grid profit-missing-grid">${unavailable}</div>`;
        },

        creditsRequirements() {
            return {
                balances: 'ОСВ счетов 66 и 67 на одну отчётную дату: счёт, субсчёт, полный путь аналитики, контрагент, договор, транш, валюта, начальное и конечное дебетовое/кредитовое сальдо, дебетовый и кредитовый оборот.',
                contract: 'Справочник кредитных договоров 1С: банк или заимодавец, номер и дата договора, транш, назначение, валюта, лимит, основной долг, фиксированная/плавающая ставка, маржа, период действия ставки, дата окончательного погашения, обеспечение и статус договора.',
                schedule: 'Актуальный график 1С по каждому договору: номер платежа, плановая дата, тело долга, проценты, комиссия, валюта, остаток после платежа, фактическая дата и сумма, просрочка, реструктуризация и досрочное погашение.',
                movement: 'Для денежных привлечений и погашений: документ, дата, сумма, договор, транш, вид операции и корреспондирующий счёт 51/52; отдельно тело долга, проценты, комиссии, переоценка и перевод между 66 и 67.',
                interest: 'Регистр начисления процентов: договор и транш, период, ставка, база начисления, начисленные, уплаченные, просроченные и капитализированные проценты, комиссия, документ, статья 91.02 и счёт задолженности.',
                ebitda: 'Договорный расчёт EBITDA: период, прибыль от продаж, классификация статей 91, начисленная амортизация по регистру ОС, разрешённые корректировки, аннуализация и подтверждённая итоговая EBITDA.',
                covenants: 'Регистр ковенантов по договору: название, точная формула, состав числителя и знаменателя, допустимые корректировки, порог, оператор сравнения, период и частота проверки, дата действия, льготный период, waiver и последствия нарушения.',
                liquidity: 'Бухгалтерский баланс и расшифровка оборотных активов и краткосрочных обязательств, включая текущую часть счёта 67, исключаемые договором статьи и отчётную дату.',
                netAssets: 'Полный бухгалтерский баланс, все активы и обязательства, исключаемые статьи по утверждённой методике, отчётная дата и договорный минимальный порог чистых активов.',
                overdueAr: 'Регистр дебиторской задолженности: контрагент, договор, документ начисления, дата, срок оплаты, сумма, распределённые платежи, остаток, дата начала просрочки, реструктуризация и исключения по методике банка.'
            };
        },

        creditsTooltip(overrides = {}) {
            return Object.assign({
                title: 'Кредиты и займы',
                description: 'Подтверждённая бухгалтерская задолженность по кредитам и займам.',
                formula: 'max(СКк − СКд, 0) по счетам 66 и 67',
                detail: 'Остаток может включать основной долг, начисленные проценты, переоценку и другие начисления.',
                required: this.creditsRequirements().balances,
                norm: 'Договорный норматив не загружен',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'конечные сальдо ОСВ 66 и 67'
            }, overrides);
        },

        renderCreditsModal(card) {
            const container = document.getElementById('ki-credits-modal-content');
            if (!container) return;
            const req = this.creditsRequirements();
            const accounts = card.accounts || [];
            const byAccount = Object.fromEntries(accounts.map(item => [item.account, item]));
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const change = card.delta && card.delta.value !== null &&
                card.delta.value !== undefined ? card.delta.value : null;
            const debtTip = (title, value, formula, detail, required = req.balances) =>
                this.creditsTooltip({
                    title, formula, detail, required,
                    fact: rub(value),
                    deviation: value === null || value === undefined
                        ? 'Нужны данные' : 'Подтверждённое бухгалтерское сальдо',
                    deviationClass: value === null || value === undefined ? 'missing' : 'neutral'
                });
            const totalTip = debtTip(
                'Подтверждённый общий долг', card.value,
                'max(СКк−СКд, 0) сч. 66 + max(СКк−СКд, 0) сч. 67',
                card.coverage && card.coverage.complete
                    ? 'Оба счёта представлены.' : 'Показана сумма доступных счетов; отсутствующий счёт не принят за ноль.');
            const shortTip = debtTip(
                'Краткосрочные кредиты и займы', (byAccount['66'] || {}).value,
                'max(СКк−СКд, 0) счёта 66',
                'Бухгалтерская классификация счёта 66 не заменяет договорный график платежей.');
            const longTip = debtTip(
                'Долгосрочные кредиты и займы', (byAccount['67'] || {}).value,
                'max(СКк−СКд, 0) счёта 67',
                'Без графика нельзя разделить остаток на интервалы 1–3 года и более трёх лет.');
            const changeTip = this.creditsTooltip({
                title: 'Изменение задолженности',
                description: 'Изменение подтверждённых сальдо 66/67 между сопоставимыми отчётными датами.',
                formula: 'Долг на текущую дату − долг на предыдущую дату',
                detail: 'Это изменение бухгалтерского остатка, а не сумма полученных или погашенных денежных траншей.',
                required: change === null ? `${req.balances} Нужны минимум две сопоставимые отчётные даты.` : req.movement,
                fact: change === null ? 'Н/Д' : `${change >= 0 ? '+' : ''}${rub(change)}`,
                deviation: change === null ? 'Нет предыдущей сопоставимой даты' :
                    (change > 0 ? 'Задолженность выросла' : change < 0 ? 'Задолженность снизилась' : 'Без изменения'),
                deviationClass: change === null ? 'missing' : 'neutral'
            });

            const accountRows = accounts.map(item => {
                const tip = debtTip(
                    `Счёт ${item.account} — ${item.label}`, item.value,
                    `max(СКк−СКд, 0) счёта ${item.account}`,
                    'Дебетовый и кредитовый обороты показываются как бухгалтерские движения и не называются погашением или привлечением без корреспонденции.',
                    `${req.balances} ${req.movement}`);
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(item.label)}</strong><br><span class="ki-muted">сч. ${esc(item.account)}</span>`, tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(pct(item.share)), tip)}
                    ${this.collectionCell(esc(rub(item.debit_turnover)), tip, item.debit_turnover === null || item.debit_turnover === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub(item.credit_turnover)), tip, item.credit_turnover === null || item.credit_turnover === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(item.value === null || item.value === undefined ? 'Нет точного сальдо' : 'Подтверждено', tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                </tr>`;
            }).join('');

            const analyticRows = (card.analytics || []).length
                ? card.analytics.map(row => {
                    const tip = debtTip(
                        `Аналитика ${row.account} — ${row.label}`, row.value,
                        'max(СКк−СКд, 0) по аналитической строке',
                        'Банк, договор, основной долг, ставка и срок не угадываются по тексту аналитики.',
                        `${req.balances} ${req.contract}`);
                    const contractTip = this.creditsTooltip({
                        title: `Договорные условия — ${row.label}`,
                        description: 'Условия кредитного договора, связанные с аналитической строкой ОСВ.',
                        formula: 'Данные берутся из карточки договора и актуального графика',
                        detail: 'Демонстрационные банки, ставки и сроки макета не используются.',
                        required: `${req.contract} ${req.schedule}`,
                        source: 'требуется справочник кредитных договоров 1С'
                    });
                    return `<tr>
                        ${this.collectionCell(`<strong>${esc(row.label)}</strong><br><span class="ki-muted">сч. ${esc(row.account)}</span>`, tip)}
                        ${this.collectionCell(esc(rub(row.value)), tip)}
                        ${this.collectionCell(esc(rub(row.debit_turnover)), tip)}
                        ${this.collectionCell(esc(rub(row.credit_turnover)), tip)}
                        ${this.collectionCell('Нужны данные', contractTip, 'cash-value-missing')}
                        ${this.collectionCell('Нужны данные', contractTip, 'cash-value-missing')}
                        ${this.collectionCell('Нужны данные', contractTip, 'cash-value-missing')}
                    </tr>`;
                }).join('')
                : `<tr><td colspan="7" class="chart-empty">Аналитические сальдо 66/67 не представлены. ${esc(req.balances)}</td></tr>`;

            const dynamics = (card.series || []).length
                ? card.series.map(item => {
                    const tip = debtTip(
                        `Общий долг — ${item.label}`, item.value,
                        'Сальдо 66 + 67 на точную отчётную дату',
                        'Пропущенные календарные дни не заменяются нулями. Нужны отдельные точные сальдо на каждую дату.');
                    return `<tr>
                        ${this.collectionCell(esc(item.label), tip)}
                        ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                        ${this.collectionCell(item.value === null || item.value === undefined ? 'Нет точного сальдо' : 'Подтверждено', tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    </tr>`;
                }).join('')
                : '<tr><td colspan="3" class="chart-empty">Нет дневной серии сальдо.</td></tr>';

            const unavailable = [
                ['Основной долг', 'без процентов и переоценки', `${req.balances} ${req.contract}`],
                ['Средняя ставка', 'взвешенная по остаткам', req.contract],
                ['График на 12 месяцев', 'тело, проценты и комиссии', req.schedule],
                ['Структура по срокам', 'до года / 1–3 / свыше 3 лет', `${req.contract} ${req.schedule}`],
                ['Процентные расходы', 'начислено за период', req.interest],
                ['Долг / EBITDA', 'по формуле договора', `${req.contract} ${req.ebitda} ${req.covenants}`],
                ['ICR', 'покрытие процентов', `${req.interest} ${req.ebitda} ${req.covenants}`],
                ['Текущая ликвидность', 'по договорной методике', `${req.liquidity} ${req.covenants}`],
                ['Чистые активы', 'по утверждённой методике', `${req.netAssets} ${req.covenants}`],
                ['Просроченная ДЗ', 'доля по методике банка', `${req.overdueAr} ${req.covenants}`],
                ['Обеспечение', 'залог, гарантия, поручительство', req.contract],
                ['Статус ковенант', 'факт, порог и запас', req.covenants]
            ].map(([label, sub, required]) => this.cashMetricCard(
                label, 'Нужны данные', sub, this.creditsTooltip({
                    title: label,
                    description: `Показатель «${label}» не определяется из общего сальдо 66/67.`,
                    formula: sub,
                    detail: 'Демонстрационные ставки, графики и банковские нормативы макета не используются.',
                    required,
                    source: 'требуется дополнительная выгрузка из 1С'
                }), true)).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Показан бухгалтерский долг.</strong>
                    Остаток 66/67 не считается автоматически телом долга и не подтверждает ставку, срок или просрочку.</div>
                <div class="collection-summary credits-summary">
                    ${this.cashMetricCard('Подтверждённый долг', rub(card.value), card.coverage && card.coverage.complete ? 'полный охват 66/67' : 'частичный охват', totalTip, card.value === null || card.value === undefined, '__main__')}
                    ${this.cashMetricCard('Краткосрочный сч. 66', rub((byAccount['66'] || {}).value), `доля ${pct((byAccount['66'] || {}).share)}`, shortTip, (byAccount['66'] || {}).value === null || (byAccount['66'] || {}).value === undefined)}
                    ${this.cashMetricCard('Долгосрочный сч. 67', rub((byAccount['67'] || {}).value), `доля ${pct((byAccount['67'] || {}).share)}`, longTip, (byAccount['67'] || {}).value === null || (byAccount['67'] || {}).value === undefined)}
                    ${this.cashMetricCard('Изменение долга', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'к сопоставимой дате', changeTip, change === null)}
                </div>
                <div class="modal-section-title">Счета и бухгалтерские обороты</div>
                <div class="collection-table-wrap"><table class="collection-detail-table credits-account-table">
                    <thead><tr><th>Счёт</th><th>Сальдо</th><th>Доля</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th><th>Статус</th></tr></thead>
                    <tbody>${accountRows}</tbody>
                </table></div>
                <div class="collection-table-wrap"><table class="collection-detail-table credits-dynamics-table">
                    <thead><tr><th>Дата / период</th><th>Общий долг</th><th>Качество данных</th></tr></thead>
                    <tbody>${dynamics}</tbody>
                </table></div>
                <div class="modal-section-title">Фактические аналитики 66/67</div>
                <div class="revenue-classification-note">Банк и договор можно подтвердить только после связи аналитики ОСВ с карточкой кредитного договора.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table credits-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th><th>Банк / договор</th><th>Ставка</th><th>Срок</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Показатели, требующие договорных данных 1С</div>
                <div class="revenue-missing-grid credits-missing-grid">${unavailable}</div>`;
        },

        salaryRequirements() {
            return {
                balances: 'ОСВ счетов 70 и 69 на одну отчётную дату: счёт, субсчёт, полный путь аналитики, начальное и конечное дебетовое/кредитовое сальдо, дебетовый и кредитовый оборот. Для счёта 70 — обезличенный идентификатор сотрудника, подразделение и документ расчёта; для 69 — вид обязательства и расчётный период.',
                payroll: 'Регистр начислений зарплаты 1С: обезличенный кадровый ID, подразделение, должность, категория персонала, вид начисления, признак включения в ФОТ, период регистрации и начисления, документ, сумма до удержаний, дни/часы, ставка, сторно, перерасчёт, счёт и статья затрат, объект и услуга.',
                payments: 'Ведомости и платежи 1С: ведомость, сотрудник или обезличенный ID, расчётный период, сумма к выплате, установленная дата выплаты, фактическая дата и сумма, банк/касса, платёжный документ, корреспондирующий счёт 50/51, распределение платежа по ведомостям, остаток и депонирование.',
                withholdings: 'Регистр удержаний 1С: сотрудник или обезличенный ID, период, вид удержания, НДФЛ, алименты, исполнительные листы и прочие удержания, сумма, документ и корреспондирующий счёт 68/73/76.',
                staff: 'Кадровый регистр 1С: обезличенный кадровый ID, дата приёма и увольнения, подразделение, должность, категория персонала, основное место/совместительство, ставка занятости, календарные и отработанные дни/часы, часы по норме и период действия записи.',
                insurance: 'Регистр расчёта страховых взносов 1С: вид взноса, субсчёт 69, расчётный период, облагаемая база, необлагаемая сумма, предельная база, фактический тариф, пониженный/дополнительный тариф, начислено, уменьшено, сторно, пени, штрафы и документ.',
                insurancePayments: 'Регистр обязательств и платежей по взносам: вид обязательства, расчётный период, начислено, установленный срок уплаты, платёжный документ, фактическая дата и сумма, распределение платежа, зачёт, возврат, переплата, непогашенный остаток, пени и статус.',
                categories: 'Справочник классификации персонала: кадровый ID или должность/подразделение, категория персонала, объект, услуга, дата начала и окончания классификации. Категории «производственный», «ремонтный», «АУП» и другие нельзя определять по тексту автоматически.',
                budget: 'Утверждённый бюджет ФОТ: версия бюджета, предприятие, подразделение, категория персонала, вид начисления, месяц, плановая численность/FTE, плановый ФОТ, дата утверждения и сценарий.',
                comparable: 'Для сравнения с АПГ нужны те же поля за сопоставимый период прошлого года, одинаковый состав предприятия, единая классификация начислений и персонала, а также признаки реорганизации, переводов и корректировок прошлых периодов.'
            };
        },

        salaryTooltip(overrides = {}) {
            return Object.assign({
                title: 'Расчёты по оплате труда',
                description: 'Бухгалтерские расчёты с работниками и по страховым взносам.',
                formula: 'Сальдо и обороты счетов 70 и 69',
                detail: 'Кредитовое сальдо 70 не доказывает просрочку, а обороты 70 и 69 без классификации не равны ФОТ, выплатам или уплаченным взносам.',
                required: this.salaryRequirements().balances,
                norm: 'Норматив не загружен',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'ОСВ счетов 70 и 69'
            }, overrides);
        },

        renderSalaryModal(card) {
            const container = document.getElementById('ki-salary-modal-content');
            if (!container) return;
            const req = this.salaryRequirements();
            const ins = card.insurance || {};
            const salaryMainId = metricSeriesIdForItem(card, card);
            const salaryDebitId = metricSeriesIdForLabel(card, 'details', 'Дебетовое сальдо 70');
            const salaryAccrualId = metricSeriesItems(card).find(item =>
                item.data === card.accrual_series)?.id || '';
            const seriesKind = state.data?.period?.series_kind;
            const granularity = seriesKind === 'day' ? 'Дневная' : seriesKind === 'month' ? 'Месячная' : 'По периодам источника';
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const tip = (title, formula, value, detail, required, source = 'ОСВ 70/69') =>
                this.salaryTooltip({
                    title, formula, detail, required, source,
                    fact: rub(value),
                    deviation: value === null || value === undefined
                        ? 'Нужны данные' : 'Подтверждено бухгалтерскими данными',
                    deviationClass: value === null || value === undefined ? 'missing' : 'neutral'
                });
            const debtTip = tip(
                'Расчётная задолженность работникам',
                'max(СКк(70) − СКд(70), 0)', card.value,
                'Это бухгалтерское кредитовое сальдо на дату, а не подтверждённая просроченная зарплата.',
                `${req.balances} Для просрочки: ${req.payments}`);
            const debitTip = tip(
                'Дебетовое сальдо счёта 70',
                'max(СКд(70) − СКк(70), 0)', card.debit_balance,
                'Может отражать переплаты и расчёты с отдельными работниками; природа суммы требует аналитики.',
                req.balances);
            const accrualTip = tip(
                'Начисления работникам',
                'ОбК(70) за выбранный период', card.credit_turnover,
                'Это бухгалтерский кредитовый оборот. Он называется ФОТ только после исключения выплат, не входящих в утверждённый состав ФОТ.',
                `${req.balances} Для подтверждённого ФОТ: ${req.payroll}`);
            const insuranceDebtTip = tip(
                'Кредитовое сальдо счёта 69',
                'max(СКк(69) − СКд(69), 0)', ins.credit_balance,
                'Сальдо не подтверждает просрочку и не разделяется на виды взносов без субсчетов и обязательств.',
                `${req.balances} Для сроков: ${req.insurancePayments}`);
            const movementTip = (account, side, value) => tip(
                `${side === 'debit' ? 'Дебетовый' : 'Кредитовый'} оборот счёта ${account}`,
                `Об${side === 'debit' ? 'Д' : 'К'}(${account}) за выбранный период`, value,
                account === '70'
                    ? (side === 'debit'
                        ? 'Включает выплаты, НДФЛ и другие удержания; не называется выплатой зарплаты без корреспонденции.'
                        : 'Включает бухгалтерские начисления работникам; не весь оборот обязательно входит в ФОТ.')
                    : (side === 'debit'
                        ? 'Может включать платежи, зачёты, возвраты и корректировки; не называется уплаченными взносами.'
                        : 'Может включать начисления, корректировки, пени и штрафы; не называется начисленными взносами без классификации.'),
                account === '70'
                    ? `${req.balances} ${side === 'debit' ? `${req.payments} ${req.withholdings}` : req.payroll}`
                    : `${req.balances} ${req.insurance} ${req.insurancePayments}`, `ОСВ счёта ${account}: обороты`);

            const balanceTip = (account, side, value, label = '') => tip(
                `${side === 'debit' ? 'Дебетовое' : 'Кредитовое'} сальдо ${account}${label ? ` — ${label}` : ''}`,
                side === 'debit' ? `max(СКд(${account}) − СКк(${account}), 0)` : `max(СКк(${account}) − СКд(${account}), 0)`,
                value, 'Конечное бухгалтерское сальдо на дату; не подтверждает платёжный статус.', req.balances,
                `ОСВ счёта ${account}${label ? `, аналитика: ${label}` : ''}`);
            const accountRows = [
                ['70', 'Расчёты с персоналом', card.debit_balance, card.value,
                    card.debit_turnover, card.credit_turnover],
                ['69', 'Социальное страхование и обеспечение', ins.debit_balance,
                    ins.credit_balance, ins.debit_turnover, ins.credit_turnover]
            ].map(([account, label, debitBalance, creditBalance, debitTurnover, creditTurnover]) => {
                const rowTip = tip(
                    `Счёт ${account} — ${label}`, 'Сальдо и обороты по данным ОСВ',
                    creditBalance,
                    'Дебетовые и кредитовые показатели показываются раздельно и не получают платёжный статус без первичных регистров.',
                    account === '70' ? `${req.balances} ${req.payroll} ${req.payments}` :
                        `${req.balances} ${req.insurance} ${req.insurancePayments}`);
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(label)}</strong><br><span class="ki-muted">сч. ${account}</span>`, Object.assign({}, rowTip, {fact: label}))}
                    ${this.collectionCell(esc(rub(debitBalance)), balanceTip(account, 'debit', debitBalance), debitBalance == null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub(creditBalance)), balanceTip(account, 'credit', creditBalance), creditBalance == null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub(debitTurnover)), movementTip(account, 'debit', debitTurnover), debitTurnover == null ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(rub(creditTurnover)), movementTip(account, 'credit', creditTurnover), creditTurnover == null ? 'cash-value-missing' : '')}
                </tr>`;
            }).join('');

            const analyticTable = (rows, account) => rows && rows.length
                ? rows.map(row => {
                    const rowTip = tip(
                        `Аналитика ${account} — ${row.label}`,
                        'Сальдо и обороты аналитической строки ОСВ',
                        row.credit_balance,
                        account === '70'
                            ? 'Категория персонала и вид начисления не угадываются по названию аналитики.'
                            : 'Вид взноса, тариф и срок уплаты не угадываются по названию аналитики.',
                        account === '70' ? `${req.balances} ${req.payroll} ${req.categories}` :
                            `${req.balances} ${req.insurance} ${req.insurancePayments}`);
                    return `<tr>
                        ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, Object.assign({}, rowTip, {fact: row.label}))}
                        ${this.collectionCell(esc(rub(row.debit_balance)), balanceTip(account, 'debit', row.debit_balance, row.label))}
                        ${this.collectionCell(esc(rub(row.credit_balance)), balanceTip(account, 'credit', row.credit_balance, row.label))}
                        ${this.collectionCell(esc(rub(row.debit_turnover)), Object.assign(movementTip(account, 'debit', row.debit_turnover), {source: `ОСВ ${account}, аналитика: ${row.label}`}))}
                        ${this.collectionCell(esc(rub(row.credit_turnover)), Object.assign(movementTip(account, 'credit', row.credit_turnover), {source: `ОСВ ${account}, аналитика: ${row.label}`}))}
                    </tr>`;
                }).join('')
                : `<tr><td colspan="5" class="chart-empty">Аналитики счёта ${account} не представлены. ${esc(req.balances)}</td></tr>`;

            const accrualSeries = card.accrual_series || [];
            const insuranceSeries = card.insurance_series || [];
            const balanceSeries = card.series || [];
            const dynamics = accrualSeries.length
                ? accrualSeries.map((item, index) => {
                    const dynTip = (data) => Object.assign({}, data, {
                        title: `${data.title} — ${item.label}`,
                        description: `${granularity} динамика бухгалтерских показателей.`,
                        detail: `${data.detail} Пропущенные периоды не заменяются нулями.`
                    });
                    return `<tr>
                        <td>${esc(item.label)}</td>
                        ${this.collectionCell(esc(rub(item.value)), dynTip(movementTip('70', 'credit', item.value)), item.value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub((balanceSeries[index] || {}).value)), dynTip(balanceTip('70', 'credit', (balanceSeries[index] || {}).value)), (balanceSeries[index] || {}).value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub((insuranceSeries[index] || {}).value)), dynTip(movementTip('69', 'credit', (insuranceSeries[index] || {}).value)), (insuranceSeries[index] || {}).value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell('Нужны данные', this.salaryTooltip({
                            title: `Средняя зарплата — ${item.label}`,
                            description: 'Среднее начисление на одного работника или FTE.',
                            formula: 'Подтверждённый ФОТ / утверждённая численность',
                            detail: 'Количество строк ОСВ не является численностью.',
                            required: `${req.payroll} ${req.staff} Нужно также указать методику: среднесписочная численность, средняя численность или FTE.`,
                            source: 'требуются зарплатный и кадровый регистры 1С'
                        }), 'cash-value-missing')}
                    </tr>`;
                }).join('')
                : '<tr><td colspan="5" class="chart-empty">Нет точной серии оборотов счёта 70 за выбранный период.</td></tr>';

            const unavailable = [
                ['Подтверждённый ФОТ', 'начисления, включаемые в ФОТ', req.payroll],
                ['Просроченная зарплата', 'остаток после установленной даты выплаты', `${req.payments} ${req.withholdings}`],
                ['Численность', 'среднесписочная / средняя / FTE', req.staff],
                ['Средняя зарплата', 'ФОТ на работника или FTE', `${req.payroll} ${req.staff}`],
                ['Категории персонала', 'производственный, ремонтный, АУП и другие', `${req.payroll} ${req.categories}`],
                ['Структура начислений', 'оклад, премии, отпускные, больничные', req.payroll],
                ['Выплачено работникам', 'платежи без удержаний и зачётов', `${req.payments} ${req.withholdings}`],
                ['Начислено взносов', 'по видам и расчётным периодам', req.insurance],
                ['Уплачено взносов', 'платежи, распределённые по обязательствам', req.insurancePayments],
                ['Фактические тарифы', 'база, предельная база и применённый тариф', req.insurance],
                ['План-факт ФОТ', 'утверждённая версия бюджета', `${req.budget} ${req.payroll}`],
                ['Изменение к АПГ', 'сопоставимые периоды и классификация', req.comparable]
            ].map(([label, sub, required]) => this.cashMetricCard(
                label, 'Нужны данные', sub, this.salaryTooltip({
                    title: label,
                    description: `Показатель «${label}» не восстанавливается из общего сальдо или оборота 70/69.`,
                    formula: sub,
                    detail: 'Демонстрационные численность, ставки, тарифы и статусы макета не используются.',
                    required,
                    source: 'требуется дополнительная выгрузка из 1С'
                }), true)).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Бухгалтерское сальдо не равно просроченной зарплате.</strong>
                    Обороты 70 и 69 показываются как движения ОСВ без неподтверждённых платёжных статусов.</div>
                <div class="collection-summary salary-summary">
                    ${this.cashMetricCard('Задолженность работникам', rub(card.value), 'кредитовое сальдо 70', debtTip, card.value == null, salaryMainId)}
                    ${this.cashMetricCard('Дебетовое сальдо 70', rub(card.debit_balance), 'переплаты и другие расчёты', debitTip, card.debit_balance == null, salaryDebitId)}
                    ${this.cashMetricCard('Начисления работникам', rub(card.credit_turnover), 'ОбК 70 · не подтверждённый ФОТ', accrualTip, card.credit_turnover == null, salaryAccrualId)}
                    ${this.cashMetricCard('Сальдо счёта 69', rub(ins.credit_balance), 'не статус просрочки', insuranceDebtTip, ins.credit_balance == null)}
                </div>
                <div class="modal-section-title">Сальдо и бухгалтерские обороты</div>
                <div class="collection-table-wrap"><table class="collection-detail-table salary-account-table">
                    <thead><tr><th>Счёт</th><th>Дебетовое сальдо</th><th>Кредитовое сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th></tr></thead>
                    <tbody>${accountRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика начислений и расчётных остатков</div>
                <p class="ki-muted">${granularity} динамика. Единственная мера графика — ОбК70 (кредитовый оборот), млн ₽. Остатки и ОбК69 приведены только в таблице. Оборот не равен денежным выплатам или подтверждённому ФОТ.</p>
                <div class="collection-table-wrap"><table class="collection-detail-table salary-dynamics-table">
                    <thead><tr><th>Дата / период</th><th>ОбК 70</th><th>Кредитовое сальдо 70</th><th>ОбК 69</th><th>Средняя зарплата</th></tr></thead>
                    <tbody>${dynamics}</tbody>
                </table></div>
                <div class="modal-section-title">Фактические аналитики счёта 70</div>
                <div class="collection-table-wrap"><table class="collection-detail-table salary-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Дебетовое сальдо</th><th>Кредитовое сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th></tr></thead>
                    <tbody>${analyticTable(card.analytics || [], '70')}</tbody>
                </table></div>
                <div class="modal-section-title">Фактические аналитики счёта 69</div>
                <div class="collection-table-wrap"><table class="collection-detail-table salary-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Дебетовое сальдо</th><th>Кредитовое сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th></tr></thead>
                    <tbody>${analyticTable(ins.analytics || [], '69')}</tbody>
                </table></div>
                <div class="modal-section-title">Показатели, требующие дополнительных данных 1С</div>
                <div class="revenue-missing-grid salary-missing-grid">${unavailable}</div>`;
        },

        capexRequirements() {
            return {
                balances: 'ОСВ счёта 08 на одну отчётную дату: счёт, субсчёт, полный путь аналитики, начальное и конечное дебетовое/кредитовое сальдо, дебетовый и кредитовый оборот. Для каждой строки нужны дата, документ, организация, подразделение и стабильный идентификатор объекта вложений.',
                objects: 'Справочник объектов капитальных вложений 1С: уникальный ID, название, аналитический ключ счёта 08, организация, подразделение, вид строительства, ответственный, дата открытия и закрытия объекта, объект основных средств и статус.',
                program: 'Утверждённая инвестпрограмма: ID и версия программы, решение об утверждении, регулирующий орган, мероприятие, объект, направление, период действия, годовой и помесячный план, план на отчётную дату, НДС, источник финансирования и признак включения расходов.',
                fact: 'Регистр факта инвестпрограммы: объект и мероприятие, документ, дата, сумма, НДС, вид затрат, договор, подрядчик, этап, признак включения в программу, сумма, принятая РЭК, и причина исключения. Должно быть указано, используется оборот 08, принятые работы или оплаченная сумма.',
                commissioning: 'Документы ввода в эксплуатацию 1С: объект вложений, объект ОС, дата и номер документа принятия к учёту, первоначальная стоимость, проводка Дт 01 — Кт 08, акт приёмки, введённая мощность и оставшиеся затраты.',
                progress: 'Реестр физической готовности: объект, этап, вид и единица работ, плановый и фактический объём, плановый процент на отчётную дату, фактический процент, подтверждающий акт, дата обновления и ответственное лицо.',
                schedule: 'Календарный график объекта: плановое и фактическое начало, контрольные точки, плановое и прогнозное завершение, плановая стоимость по месяцам, допустимое отклонение и утверждённые правила статусов «в графике», «отставание» и «критично».',
                delays: 'Реестр отклонений: объект, код и описание причины, дата возникновения, ответственное лицо, блокирующий фактор, влияние на стоимость и срок, ожидаемая дата устранения, подтверждающий документ и актуальный комментарий.',
                payments: 'Для денежного CAPEX нужны платежи 1С: дата, документ, сумма, валюта, контрагент, договор, объект, мероприятие, назначение, статья ДДС, счёт 50/51/52/55, аванс, возврат и распределение платежа по объектам.',
                financing: 'Регистр источников финансирования: объект и мероприятие, вид источника, лимит, сумма, период, документ распределения, тарифные средства, кредит, бюджетное финансирование, спецсчёт или собственные средства.',
                passports: 'Реестр паспортов готовности: отчётный сезон, перечень обязанных объектов, ID объекта, статус и дата проверки, замечания, срок устранения, номер и дата выдачи паспорта, выдавший орган, отказ и основание исключения.',
                repairs: 'Ремонтная программа: ID мероприятия, объект, вид ремонта, плановые и фактические даты, физический объём и единица, плановая и фактическая стоимость, акт выполнения, подрядчик, статус и связь со статьёй затрат 20/25/26/44 либо капитализируемым объектом 08.',
                rab: 'Для оценки влияния на RAB нужны метод регулирования, утверждённая база инвестированного капитала, решение РЭК, перечень принятых мероприятий и расходов, условия включения в НВВ, период, фактический ввод и причины исключения.'
            };
        },

        capexTooltip(overrides = {}) {
            return Object.assign({
                title: 'Капитальные вложения',
                description: 'Бухгалтерские движения и остатки счёта 08.',
                formula: 'ОбД(08) за выбранный период',
                detail: 'Дебетовый оборот 08 не равен денежному CAPEX, физической готовности или исполнению утверждённой инвестпрограммы.',
                required: this.capexRequirements().balances,
                norm: 'Утверждённый план не загружен',
                fact: 'Н/Д',
                deviation: 'Недостаточно данных',
                deviationClass: 'missing',
                source: 'ОСВ счёта 08'
            }, overrides);
        },

        renderCapexModal(card) {
            const container = document.getElementById('ki-capex-modal-content');
            if (!container) return;
            const req = this.capexRequirements();
            const capexMainId = metricSeriesIdForItem(card, card);
            const capexApgId = metricSeriesIdForLabel(
                card, 'details', 'Дебетовый оборот 08 АПГ');
            const capexWipId = metricSeriesIdForLabel(card, 'details', 'Дебетовое сальдо 08');
            const capexCreditId = metricSeriesIdForLabel(card, 'details', 'Кредитовый оборот 08');
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const balanceChange = card.opening_balance != null && card.closing_balance != null
                ? card.closing_balance - card.opening_balance : null;
            const tip = (title, formula, value, detail, required, source = 'ОСВ счёта 08') =>
                this.capexTooltip({
                    title, formula, detail, required, source,
                    fact: rub(value),
                    deviation: value == null ? 'Нужны данные' : 'Подтверждено бухгалтерскими данными',
                    deviationClass: value == null ? 'missing' : 'neutral'
                });
            const debitTip = tip(
                'Формирование капитальных вложений',
                'ОбД(08) за выбранный период', card.debit_turnover,
                'Показатель отражает бухгалтерское формирование стоимости. Он не доказывает оплату, включение в инвестпрограмму или принятие РЭК.',
                `${req.balances} Для факта инвестпрограммы: ${req.fact}`);
            const previousYearPeriod = card.previous_year_period || {};
            const previousYearPeriodText = previousYearPeriod.start && previousYearPeriod.end
                ? `${previousYearPeriod.start} — ${previousYearPeriod.end}` : 'тот же период прошлого года';
            const apgTip = tip(
                'Формирование капитальных вложений АПГ',
                'ОбД(08) за тот же период прошлого года', card.previous_year_value,
                'АПГ сравнивает дебетовый оборот счёта 08 за сопоставимый календарный период. Это не денежный CAPEX и не подтверждённый ввод.',
                req.balances);
            const wipTip = tip(
                'Бухгалтерская незавершёнка',
                'max(СКд(08) − СКк(08), 0)', card.closing_balance,
                'Дебетовое сальдо 08 не подтверждает возраст, просрочку или физическую незавершённость объекта.',
                `${req.balances} Для возраста и статуса: ${req.objects} ${req.schedule}`);
            const creditTip = tip(
                'Кредитовый оборот счёта 08',
                'ОбК(08) за выбранный период', card.credit_turnover,
                'Может включать ввод, списание, перенос и исправление; без корреспонденции не называется вводом в эксплуатацию.',
                `${req.balances} Для подтверждённого ввода: ${req.commissioning}`);
            const changeTip = tip(
                'Изменение бухгалтерской незавершёнки',
                'Конечное дебетовое сальдо 08 − начальное дебетовое сальдо 08',
                balanceChange,
                'Изменение остатка не равно дебетовому обороту: кредитовые движения могут отражать ввод, списания и переносы.',
                `${req.balances} Нужны точные начальное и конечное сальдо одной отчётной границы.`);

            const accountRowTip = this.capexTooltip({
                title: 'Счёт 08 — сводные бухгалтерские данные',
                description: 'Сальдо и обороты счёта 08 за выбранный период.',
                formula: 'СНд, ОбД, ОбК и СКд счёта 08',
                detail: 'Показатели показываются раздельно и не получают проектный или платёжный статус.',
                required: req.balances,
                fact: rub(card.closing_balance),
                deviation: card.closing_balance == null ? 'Нужны данные' : 'Подтверждено',
                deviationClass: card.closing_balance == null ? 'missing' : 'neutral'
            });
            const accountRows = `<tr>
                ${this.collectionCell('<strong>Счёт 08</strong><br><span class="ki-muted">Вложения во внеоборотные активы</span>', accountRowTip)}
                ${this.collectionCell(esc(rub(card.opening_balance)), accountRowTip, card.opening_balance == null ? 'cash-value-missing' : '')}
                ${this.collectionCell(esc(rub(card.debit_turnover)), debitTip, card.debit_turnover == null ? 'cash-value-missing' : '')}
                ${this.collectionCell(esc(rub(card.credit_turnover)), creditTip, card.credit_turnover == null ? 'cash-value-missing' : '')}
                ${this.collectionCell(esc(rub(card.closing_balance)), wipTip, card.closing_balance == null ? 'cash-value-missing' : '')}
            </tr>`;

            const analyticRows = (card.analytics || []).length
                ? card.analytics.map(row => {
                    const rowTip = this.capexTooltip({
                        title: `Аналитика 08 — ${row.label}`,
                        description: 'Фактическая аналитическая строка счёта 08.',
                        formula: 'Сальдо и обороты аналитической строки',
                        detail: 'Аналитика не считается объектом инвестпрограммы и не классифицируется по направлению без утверждённого сопоставления.',
                        required: `${req.balances} ${req.objects} ${req.program}`,
                        fact: rub(row.debit_balance),
                        deviation: 'Бухгалтерская аналитика',
                        deviationClass: 'neutral'
                    });
                    const classTip = this.capexTooltip({
                        title: `Связь с инвестпрограммой — ${row.label}`,
                        description: 'Сопоставление бухгалтерской аналитики с утверждённым мероприятием.',
                        formula: 'Аналитический ключ 08 → объект → мероприятие → направление → версия программы',
                        detail: 'Название строки не используется для автоматического определения направления или статуса.',
                        required: `${req.objects} ${req.program} ${req.fact}`,
                        source: 'требуется справочник объектов и инвестпрограмма 1С'
                    });
                    return `<tr>
                        ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, rowTip)}
                        ${this.collectionCell(esc(rub(row.debit_balance)), rowTip)}
                        ${this.collectionCell(esc(rub(row.debit_turnover)), rowTip)}
                        ${this.collectionCell(esc(rub(row.credit_turnover)), rowTip)}
                        ${this.collectionCell(esc(pct(row.turnover_share)), rowTip)}
                        ${this.collectionCell(esc(pct(row.balance_share)), rowTip)}
                        ${this.collectionCell('Нужны данные', classTip, 'cash-value-missing')}
                        ${this.collectionCell('Нужны данные', classTip, 'cash-value-missing')}
                    </tr>`;
                }).join('')
                : `<tr><td colspan="8" class="chart-empty">Аналитики счёта 08 не представлены. ${esc(req.balances)}</td></tr>`;

            const debitSeries = card.series || [];
            const creditSeries = card.credit_series || [];
            const balanceSeries = card.balance_series || [];
            const previousYearSeries = card.previous_year_series || [];
            const dynamics = debitSeries.length
                ? debitSeries.map((item, index) => {
                    const dynTip = this.capexTooltip({
                        title: `Динамика счёта 08 — ${item.label}`,
                        description: 'Бухгалтерские обороты и точное сальдо счёта 08 за интервал динамики.',
                        formula: 'ОбД(08), ОбК(08), СКд(08)',
                        detail: 'Пропущенные календарные дни не заменяются нулями. Плановая линия не строится без утверждённого календарного плана.',
                        required: `${req.balances} Для план-факта: ${req.program}`,
                        fact: rub(item.value),
                        deviation: item.value == null ? 'Нужны данные' : 'Подтверждённый оборот',
                        deviationClass: item.value == null ? 'missing' : 'neutral'
                    });
                    return `<tr>
                        ${this.collectionCell(esc(item.label), dynTip)}
                        ${this.collectionCell(esc(rub(item.value)), dynTip, item.value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub((previousYearSeries[index] || {}).value)), apgTip, (previousYearSeries[index] || {}).value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub((creditSeries[index] || {}).value)), dynTip, (creditSeries[index] || {}).value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell(esc(rub((balanceSeries[index] || {}).value)), dynTip, (balanceSeries[index] || {}).value == null ? 'cash-value-missing' : '')}
                        ${this.collectionCell('Нужны данные', this.capexTooltip({
                            title: `План инвестпрограммы — ${item.label}`,
                            description: 'Утверждённый план на ту же отчётную дату.',
                            formula: 'Факт на дату / план на ту же дату × 100%',
                            detail: 'Годовой план нельзя сравнивать с промежуточным фактом без календарного графика.',
                            required: `${req.program} ${req.fact}`,
                            source: 'требуется утверждённая версия инвестпрограммы 1С'
                        }), 'cash-value-missing')}
                    </tr>`;
                }).join('')
                : '<tr><td colspan="6" class="chart-empty">Нет точной периодной серии счёта 08.</td></tr>';

            const unavailable = [
                ['План инвестпрограммы', 'утверждённый план на отчётную дату', req.program],
                ['Исполнение плана', 'сопоставимый факт / план', `${req.program} ${req.fact}`],
                ['Физическая готовность', 'выполненный объём по этапам', req.progress],
                ['Срок и статус объекта', 'план, прогноз и правила статуса', `${req.schedule} ${req.progress}`],
                ['Просроченные объекты', 'возраст и нарушение графика', `${req.objects} ${req.schedule} ${req.delays}`],
                ['Причины задержки', 'подтверждённый реестр отклонений', req.delays],
                ['Введено в эксплуатацию', 'документы Дт 01 — Кт 08', req.commissioning],
                ['Денежный CAPEX', 'фактические внешние платежи', req.payments],
                ['Источник финансирования', 'распределение средств по объектам', req.financing],
                ['Принято РЭК', 'принятые и исключённые расходы', `${req.program} ${req.fact}`],
                ['Паспорта готовности', 'объекты, проверки и паспорта', req.passports],
                ['Ремонтная программа', 'финансовое и физическое выполнение', req.repairs],
                ['Влияние на RAB', 'условия включения в НВВ', req.rab]
            ].map(([label, sub, required]) => this.cashMetricCard(
                label, 'Нужны данные', sub, this.capexTooltip({
                    title: label,
                    description: `Показатель «${label}» не восстанавливается из общего оборота или сальдо счёта 08.`,
                    formula: sub,
                    detail: 'Демонстрационные объекты, планы, проценты готовности, нормативы и статусы макета не используются.',
                    required,
                    source: 'требуется дополнительная выгрузка из 1С'
                }), true)).join('');

            container.innerHTML = `
                <div class="cash-info-banner"><strong>Бухгалтерские вложения отделены от исполнения инвестпрограммы.</strong>
                    Дебетовый оборот 08 не называется денежным CAPEX, а сальдо 08 не подтверждает просрочку объекта.</div>
                <div class="capex-metrics-strip capex-summary">
                    ${this.cashMetricCard('Формирование вложений', rub(card.debit_turnover), 'ОбД 08 за выбранный период', debitTip, card.debit_turnover == null, '', '', 'current')}
                    ${this.cashMetricCard('Формирование вложений АПГ', rub(card.previous_year_value), previousYearPeriodText, apgTip, card.previous_year_value == null, '', '', 'apg')}
                    ${this.cashMetricCard('Кредитовый оборот', rub(card.credit_turnover), 'не подтверждённый ввод', creditTip, card.credit_turnover == null, capexCreditId)}
                </div>
                <section class="capex-overview ki-modal-dynamics-block" data-layout-key="dynamics"
                         data-layout-title="Динамика капитальных вложений">
                    <div id="ki-capex-chart-title" class="modal-section-title">Формирование вложений и Формирование вложений АПГ</div>
                    <div id="ki-capex-line-chart" class="cash-line-chart capex-cash-style-chart"></div>
                    <div id="ki-capex-chart-legend" class="cash-chart-legend"
                         aria-label="Легенда графика капитальных вложений"></div>
                </section>
                <button type="button" class="modal-section-title cash-details-toggle capex-details-toggle"
                        aria-expanded="false" aria-controls="ki-capex-details-panel"
                        onclick="window.KI_App.toggleCashDetails(this)">
                    <span>Детализация по счёту 08</span>
                    <span class="cash-details-chevron" aria-hidden="true">⌄</span>
                </button>
                <div id="ki-capex-details-panel" class="capex-details-panel" hidden>
                    <div class="cash-table-scroll"><table class="cash-table capex-account-table">
                        <thead><tr><th>Счёт</th><th>Начальное сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th><th>Конечное сальдо</th></tr></thead>
                        <tbody>${accountRows}</tbody>
                    </table></div>
                    <div class="cash-table-scroll"><table class="cash-table capex-dynamics-table">
                        <thead><tr><th>Дата / период</th><th>ОбД 08</th><th>ОбД 08 АПГ</th><th>ОбК 08</th><th>СКд 08</th><th>План на дату</th></tr></thead>
                        <tbody>${dynamics}</tbody>
                    </table></div>
                </div>
                <div class="modal-section-title">Фактические аналитики счёта 08</div>
                <div class="revenue-classification-note">Аналитика становится объектом инвестпрограммы только после явного сопоставления со справочником объектов и утверждённой версией программы.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table capex-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Остаток</th><th>ОбД</th><th>ОбК</th><th>Доля оборота</th><th>Доля остатка</th><th>Объект</th><th>Мероприятие</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Показатели, требующие дополнительных данных 1С</div>
                <div class="revenue-missing-grid capex-missing-grid">${unavailable}</div>`;
        },

        renderCapexChartModes(card) {
            if (!card) return;
            if (!state.modalChart.capexModes || !state.modalChart.capexModes.size) {
                state.modalChart.capexModes = new Set(['current', 'apg']);
            }
            const selected = state.modalChart.capexModes;
            const root = document.getElementById('ki-capex-modal-content');
            if (root && root.querySelectorAll) {
                root.querySelectorAll('[data-capex-chart-mode]').forEach(tile => {
                    const active = selected.has(tile.dataset.capexChartMode);
                    tile.classList.toggle('cash-chart-mode-selected', active);
                    tile.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
            }
            const title = document.getElementById('ki-capex-chart-title');
            const labels = [];
            if (selected.has('current')) labels.push('Формирование вложений');
            if (selected.has('apg')) labels.push('Формирование вложений АПГ');
            if (title) title.textContent = labels.join(' и ');
            const legend = document.getElementById('ki-capex-chart-legend');
            if (legend) {
                legend.innerHTML = [
                    selected.has('current')
                        ? '<span><i class="cash-chart-dot" style="background:#111827"></i>Формирование вложений</span>'
                        : '',
                    selected.has('apg')
                        ? '<span><i class="cash-chart-dot" style="background:#DB2777"></i>Формирование вложений АПГ</span>'
                        : ''
                ].join('');
            }
            const current = (card.series || []).map(point => ({
                ...point, accounts: ['08'], missing_accounts: [],
                coverage: 'complete'
            }));
            const previous = (card.previous_year_series || []).map(point => ({
                ...point, accounts: ['08'], missing_accounts: [],
                coverage: 'complete'
            }));
            this.renderCashLineChart(
                [], [],
                selected.has('current') ? current : [],
                selected.has('apg') ? previous : [],
                'ki-capex-line-chart');
        },

        toggleCapexChartMode(mode) {
            const card = state.data && Array.isArray(state.data.cards)
                ? state.data.cards.find(item => item.id === 'capex') : null;
            if (!card || !['current', 'apg'].includes(mode)) return;
            if (!state.modalChart.capexModes || !state.modalChart.capexModes.size) {
                state.modalChart.capexModes = new Set(['current', 'apg']);
            }
            if (state.modalChart.capexModes.has(mode)) {
                if (state.modalChart.capexModes.size > 1) {
                    state.modalChart.capexModes.delete(mode);
                }
            } else {
                state.modalChart.capexModes.add(mode);
            }
            this.renderCapexChartModes(card);
        },

        cashCoverageText(item) {
            const accounts = item.accounts || [];
            if (item.coverage === 'missing') return 'Нет точных остатков';
            if (!accounts.length) return 'Охват счетов не подтверждён';
            return `${item.coverage === 'complete' ? 'Полный' : 'Частичный'} охват: сч. ${accounts.join(', ')}` +
                (item.missing_accounts && item.missing_accounts.length ? `; нет данных: ${item.missing_accounts.join(', ')}` : '');
        },

        cashComparisonText(comparison) {
            if (!comparison) return 'Нет подтверждённого сравнения';
            const dates = comparison.previous_date && comparison.date
                ? `${comparison.previous_date} → ${comparison.date}` : '';
            return [dates, comparison.comparable ? 'одинаковый состав счетов' : comparison.reason || 'Нет сопоставимых остатков']
                .filter(Boolean).join(' · ');
        },
        /* Legacy icon fragment removed; shared renderer follows.
                    : '<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" style="color:#9CA3AF; margin-right:6px; opacity:0.3;"><path d="M21.1 3.9a3.5 3.5 0 0 0-4.95 0L14 6l-5.5-1.5L7 6l3.5 4.5L5 16l-3 3 3-3 5.5-5.5L18 17l1.5-1.5L18 10l2.1-2.15a3.5 3.5 0 0 0 0-4.95z"/></svg>';
        */
        updateMetricTileStates(root) {
            if (!root || !root.querySelectorAll) return;
            root.querySelectorAll('[data-series-id]').forEach(tile => {
                const id = tile.dataset && tile.dataset.seriesId;
                if (!id) return;
                const active = state.modalChart.activeId === id;
                const pinned = state.modalChart.pinnedIds.has(id);
                if (tile.classList) {
                    tile.classList.toggle('ki-metric-tile-active', active);
                    tile.classList.toggle('ki-metric-tile-pinned', pinned);
                }
                if (tile.style) {
                    const color = metricChartColor(id);
                    if (tile.style.setProperty) {
                        tile.style.setProperty('--ki-selected-series-color', color);
                    } else {
                        tile.style['--ki-selected-series-color'] = color;
                    }
                }
                if (tile.setAttribute) {
                    tile.setAttribute('aria-pressed', active ? 'true' : 'false');
                }
            });
        },

        scheduleMetricModalChart(card, container, delay = 0) {
            const target = container || state.modalChart.chartContainer;
            if (!target) return;
            this.scheduleChart(target, () => this.drawMetricModalSvg(card, target), delay);
        },

        bindMetricTiles(card, root) {
            if (!root || !root.querySelectorAll) return;
            const series = metricSeriesItems(card);
            if (!series.length) return;
            const validIds = new Set(series.map(item => item.id));
            const tiles = root.querySelectorAll(
                '[data-series-id]');
            Array.from(tiles).forEach(tile => {
                if (!tile.dataset) tile.dataset = {};
                let id = tile.dataset.seriesId;
                if (!id || !validIds.has(id) || tile.dataset.kiMetricBound === 'true') return;
                tile.dataset.kiMetricBound = 'true';
                tile.setAttribute('role', 'button');
                tile.setAttribute('aria-pressed',
                    state.modalChart.activeId === id ? 'true' : 'false');
                if (!tile.getAttribute('tabindex')) tile.setAttribute('tabindex', '0');
                let clickTimer = null;
                const select = () => {
                    state.modalChart.activeId = id;
                    this.updateMetricTileStates(root);
                    this.scheduleMetricModalChart(card);
                };
                const pin = () => {
                    if (state.modalChart.pinnedIds.has(id)) {
                        state.modalChart.pinnedIds.delete(id);
                    } else {
                        state.modalChart.pinnedIds.add(id);
                    }
                    this.updateMetricTileStates(root);
                    this.scheduleMetricModalChart(card);
                };
                tile.addEventListener('click', event => {
                    if (event.target && event.target.closest &&
                        event.target.closest('.pin-btn')) {
                        pin();
                        return;
                    }
                    if (event.detail === 1) clickTimer = setTimeout(select, 200);
                });
                tile.addEventListener('dblclick', () => {
                    clearTimeout(clickTimer);
                    pin();
                });
                tile.addEventListener('keydown', event => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        select();
                    } else if (event.key === ' ' || event.code === 'Space') {
                        event.preventDefault();
                        pin();
                    }
                });
            });
            this.updateMetricTileStates(root);
        },

        mountSharedDynamics(card, activeContainer) {
            if (!activeContainer || !activeContainer.appendChild) return;
            const metricItems = metricSeriesItems(card);
            const validIds = new Set(metricItems.map(item => item.id));
            if (!validIds.has(state.modalChart.activeId)) {
                state.modalChart.activeId = metricItems.length ? metricItems[0].id : null;
            }
            state.modalChart.pinnedIds = new Set(
                Array.from(state.modalChart.pinnedIds).filter(id => validIds.has(id)));
            let block = activeContainer.querySelector &&
                activeContainer.querySelector('[data-layout-key="dynamics"]');
            if (!block && typeof document.createElement === 'function') {
                block = document.createElement('div');
                block.className = 'ki-modal-dynamics-block';
                block.dataset.layoutKey = 'dynamics';
                block.dataset.layoutTitle = 'Динамика';
                block.innerHTML = `
                    <div class="modal-section-title">Динамика</div>
                    <div class="modal-chart-wrap"></div>`;
                activeContainer.appendChild(block);
            }
            if (!block || !block.querySelector) return;
            const chart = block.querySelector('.modal-chart-wrap');
            const title = block.querySelector('.modal-section-title');
            if (!chart || !title) return;
            const holder = document.createElement('div');
            const ignoredTitle = document.createElement('div');
            holder.style.display = 'none';
            this.renderMetricModalInteractive(card, holder, ignoredTitle, chart, title);
            this.bindMetricTiles(card, activeContainer);
        },

        renderMetricModalInteractive(card, bdCont, bdTitle, chCont, chTitle) {
            bdTitle.style.display = 'block';
            bdTitle.textContent = 'Состав показателя';
            chTitle.style.display = 'block';
            chCont.style.display = 'block';
            if (chTitle.parentElement && chTitle.parentElement.dataset &&
                chTitle.parentElement.dataset.layoutKey === 'dynamics') {
                chTitle.parentElement.style.display = 'block';
            }
            bdCont.style.display = 'block';

            const baseTooltip = this.metricTooltipData(card);
            const rowUnit = (item) => (item && item.unit) ||
                ((item && item.series || []).find(point => point && point.unit) || {}).unit ||
                card.unit;

            const togglePin = (id) => {
                if (state.modalChart.pinnedIds.has(id)) {
                    state.modalChart.pinnedIds.delete(id);
                } else {
                    state.modalChart.pinnedIds.add(id);
                }
                this.updateMetricTileStates(bdCont);
                this.updateMetricTileStates(chCont.parentElement);
                this.scheduleMetricModalChart(card);
            };

            const setActive = (id) => {
                state.modalChart.activeId = id;
                this.updateMetricTileStates(bdCont);
                this.updateMetricTileStates(chCont.parentElement);
                this.scheduleMetricModalChart(card);
            };

            const handleKey = (e, id) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    setActive(id);
                }
                if (e.key === ' ' || e.code === 'Space') {
                    e.preventDefault();
                    togglePin(id);
                }
            };

            // Metric components are cards, not a second metric table.
            let bdHtml = '<div class="cash-analysis-grid ki-metric-tile-grid" role="list">';
            const tileItems = metricTileItems(card);
            const renderRow = (id, label, value, hasSeries, isMain, row) => {
                const unit = rowUnit(row);
                const f = this.formatValue(value, unit);
                const isActive = state.modalChart.activeId === id;
                const isPinned = state.modalChart.pinnedIds.has(id);
                const pinIcon = isPinned
                    ? '<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="currentColor" stroke-width="2" style="color:#F59E0B; margin-right:6px;"><path d="M21.1 3.9a3.5 3.5 0 0 0-4.95 0L14 6l-5.5-1.5L7 6l3.5 4.5L5 16l-3 3 3-3 5.5-5.5L18 17l1.5-1.5L18 10l2.1-2.15a3.5 3.5 0 0 0 0-4.95z"/></svg>'
                    : '<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" style="color:#9CA3AF; margin-right:6px; opacity:0.3;"><path d="M21.1 3.9a3.5 3.5 0 0 0-4.95 0L14 6l-5.5-1.5L7 6l3.5 4.5L5 16l-3 3 3-3 5.5-5.5L18 17l1.5-1.5L18 10l2.1-2.15a3.5 3.5 0 0 0 0-4.95z"/></svg>';

                const trStyle = isActive ? 'background:#EFF6FF; border-left:3px solid #3B82F6;' : (hasSeries ? 'cursor:pointer;' : 'opacity:0.6;');

                const tooltip = this.cashTooltip(this.metricTooltipData(card, {
                    title: `${card.title}: ${label}`,
                    fact: `${f.val} ${f.u}`,
                    detail: `${baseTooltip.detail} Детализация: ${label}.`,
                    deviation: isMain ? 'Итоговое значение' : 'Составная часть общего показателя'
                }));

                bdHtml += `<div class="cash-analysis-card cash-tooltip-host ki-metric-interactive-tile"
                    role="listitem" style="${trStyle}" tabindex="${hasSeries ? '0' : '-1'}"
                    data-series-id="${hasSeries ? id : ''}" data-series-label="${esc(label)}">
                    <div class="cash-analysis-label">${esc(label)}</div>
                    <div class="cash-analysis-value">${esc(f.val)} ${f.u ? `<span class="metric-inline-unit">${esc(f.u)}</span>` : ''}</div>
                    ${isPinned ? '<span class="ki-metric-pinned">Закреплено</span>' : ''}
                    ${tooltip}
                </div>`;
            };

            tileItems.forEach(series => {
                const item = series.item;
                renderRow(series.id, series.label, series.isMain ? card.value : item.value,
                    series.hasSeries, series.isMain, item);
            });
            if (!tileItems.length) bdHtml += '<div class="chart-empty">Нет доступных рядов для графика</div>';
            bdHtml += '</div>';
            bdCont.innerHTML = bdHtml;

            // Attach events
            Array.from(bdCont.querySelectorAll('tr[data-series-id], [data-series-id]')).forEach(tr => {
                const id = tr.dataset.seriesId;
                if (!id) return;
                let clickTimer = null;
                tr.addEventListener('click', (e) => {
                    if (e.target.closest('.pin-btn')) {
                        togglePin(id);
                        return;
                    }
                    if (e.detail === 1) {
                        clickTimer = setTimeout(() => { try { setActive(id); } catch (err) { console.error(err); } }, 200);
                    }
                });
                tr.addEventListener('dblclick', () => {
                    clearTimeout(clickTimer);
                    togglePin(id);
                });
                tr.addEventListener('keydown', (e) => handleKey(e, id));
            });

            const gapMode1 = state.modalChart.gapMode === 1;
            const gapMode2 = state.modalChart.gapMode === 2;
            const timelineMode = state.modalChart.timelineMode || 'week';
            const chartHtml = `
                <div class="cash-chart-controls">
                    <div class="cash-chart-scale" role="group" aria-label="Масштаб времени">
                        <label><input type="radio" name="timelineMode" value="day" ${timelineMode === 'day' ? 'checked' : ''}>День</label>
                        <label><input type="radio" name="timelineMode" value="week" ${timelineMode === 'week' ? 'checked' : ''}>Неделя</label>
                        <label><input type="radio" name="timelineMode" value="month" ${timelineMode === 'month' ? 'checked' : ''}>Месяц</label>
                    </div>
                    <div class="cash-chart-gap-mode" role="group" aria-label="Отображение пропусков">
                    <label>
                        <input type="radio" name="gapMode" value="1" ${gapMode1 ? 'checked' : ''} style="margin:0;">
                        Соединить
                    </label>
                    <label>
                        <input type="radio" name="gapMode" value="2" ${gapMode2 ? 'checked' : ''} style="margin:0;">
                        Продлить
                    </label>
                    </div>
                </div>
                <div class="cash-line-chart ki-metric-modal-svg"></div>
            `;
            chCont.innerHTML = chartHtml;
            const chartContainer = chCont.querySelector
                ? chCont.querySelector('.ki-metric-modal-svg')
                : document.getElementById('ki-metric-modal-svg');
            state.modalChart.chartContainer = chartContainer;

            Array.from(chCont.querySelectorAll('input[name="gapMode"]')).forEach(radio => {
                radio.addEventListener('change', (e) => {
                    state.modalChart.gapMode = Number(e.target.value);
                    this.drawMetricModalSvg(card, chartContainer);
                });
            });
            Array.from(chCont.querySelectorAll('input[name="timelineMode"]')).forEach(radio => {
                radio.addEventListener('change', (e) => {
                    state.modalChart.timelineMode = e.target.value;
                    this.drawMetricModalSvg(card, chartContainer);
                });
            });

            this.scheduleMetricModalChart(card, chartContainer, 50);
        },

        drawCashMetricModalSvg(card, chartContainer) {
            const container = chartContainer || state.modalChart.chartContainer ||
                document.getElementById('ki-metric-modal-svg');
            if (!container) return;

            const metricItems = metricSeriesItems(card);
            const active = metricItems.find(item =>
                item.id === state.modalChart.activeId) || metricItems[0];
            if (!active) {
                container.innerHTML = '<div class="chart-empty">Нет данных для отображения</div>';
                return;
            }
            const selectedUnit = (active.item && active.item.unit) ||
                ((active.data || []).find(point => point && point.unit) || {}).unit ||
                card.unit;
            const rawSelected = metricItems.filter(item => {
                const unit = (item.item && item.item.unit) ||
                    ((item.data || []).find(point => point && point.unit) || {}).unit ||
                    card.unit;
                return (item.id === active.id ||
                    state.modalChart.pinnedIds.has(item.id)) && unit === selectedUnit;
            });
            if (!rawSelected.length) {
                container.innerHTML = '<div class="chart-empty">Нет данных для отображения</div>';
                return;
            }

            const rawDates = rawSelected.flatMap(item => (item.data || [])
                .map(point => chartDate(point)).filter(value => value !== null));
            if (!rawDates.length) {
                container.innerHTML = '<div class="chart-empty">Нет дат для отображения</div>';
                return;
            }
            const timelineMode = state.modalChart.timelineMode || 'week';
            const dayMs = 86400000;
            const bucketStart = (value) => {
                const date = new Date(value);
                let result = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
                if (timelineMode === 'week') {
                    const weekday = new Date(result).getUTCDay();
                    result -= ((weekday + 6) % 7) * dayMs;
                } else if (timelineMode === 'month') {
                    result = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
                }
                return result;
            };
            const nextBucket = (value) => {
                if (timelineMode === 'week') return value + 7 * dayMs;
                if (timelineMode === 'month') {
                    const date = new Date(value);
                    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
                }
                return value + dayMs;
            };
            const isoDate = value => new Date(value).toISOString().slice(0, 10);
            const selectedBounds = chartPeriodBounds(rawDates);
            const minDate = selectedBounds.start;
            const maxDate = selectedBounds.end;
            const rangeStart = bucketStart(minDate);
            const rangeEnd = bucketStart(maxDate);
            const aggregateSeries = (data) => {
                const buckets = new Map();
                (data || []).forEach(point => {
                    const timestamp = chartDate(point);
                    if (timestamp === null || timestamp < minDate ||
                            timestamp > maxDate ||
                            !hasNumericValue(point.value)) return;
                    const key = bucketStart(timestamp);
                    const previous = buckets.get(key);
                    if (!previous || timestamp >= previous.timestamp) {
                        buckets.set(key, {timestamp, point});
                    }
                });
                const result = [];
                for (let cursor = rangeStart; cursor <= rangeEnd; cursor = nextBucket(cursor)) {
                    const known = buckets.get(cursor);
                    const displayDate = known
                        ? known.timestamp
                        : Math.min(maxDate, Math.max(minDate, cursor));
                    result.push(known
                        ? {...known.point, date: isoDate(displayDate),
                            bucket_date: isoDate(cursor), bucket_source_date:
                                known.point.date || known.point.label}
                        : {date: isoDate(displayDate), bucket_date: isoDate(cursor),
                            label: isoDate(displayDate), value: null,
                            granularity: timelineMode});
                }
                return result;
            };
            const selected = rawSelected.map(item => ({
                ...item, data: aggregateSeries(item.data)
            }));
            const span = Math.max(1, (maxDate - minDate) / dayMs);
            const width = 900, height = 270, left = 52, right = 18,
                top = 38, bottom = 48;
            const plotWidth = width - left - right;
            const plotHeight = height - top - bottom;
            const x = item => left + Math.round(
                (chartDate(item) - minDate) / 86400000) / span * plotWidth;
            const numericValues = [];
            selected.forEach(series => (series.data || []).forEach(point => {
                if (hasNumericValue(point.value)) numericValues.push(Number(point.value));
            }));
            let minValue = Math.min(0, ...numericValues);
            let maxValue = Math.max(0, ...numericValues);
            if (minValue === maxValue) maxValue = minValue + 1;
            const y = value => top + (maxValue - Number(value)) /
                (maxValue - minValue) * plotHeight;
            const baseline = y(Math.max(0, minValue));
            const gapMode = state.modalChart.gapMode;
            const formatDate = value => {
                const date = new Date(value);
                const options = span > 365
                    ? {day: 'numeric', month: 'short', year: 'numeric'}
                    : {day: 'numeric', month: 'short'};
                return new Intl.DateTimeFormat('ru-RU', options).format(date)
                    .replace(/\s*г\.$/, '');
            };
            const colors = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6',
                '#EC4899', '#06B6D4', '#F43F5E'];
            const stableColor = id => {
                if (id === '__main__') return '#2563EB';
                let hash = 0;
                String(id).split('').forEach(char => {
                    hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
                });
                return colors[Math.abs(hash) % colors.length];
            };
            const pathsFor = series => {
                const known = (series.data || []).map((point, index) =>
                    hasNumericValue(point.value)
                        ? {point, index, value: Number(point.value)} : null)
                    .filter(Boolean);
                let solid = '', dashed = '', area = '';
                let areaSegment = [];
                const flushArea = () => {
                    if (!areaSegment.length) return;
                    const first = areaSegment[0];
                    const last = areaSegment[areaSegment.length - 1];
                    area += ` M ${x(first.point).toFixed(1)} ${baseline.toFixed(1)}`;
                    areaSegment.forEach(item => {
                        area += ` L ${x(item.point).toFixed(1)} ${y(item.value).toFixed(1)}`;
                    });
                    area += ` L ${x(last.point).toFixed(1)} ${baseline.toFixed(1)} Z`;
                    areaSegment = [];
                };
                known.forEach((current, index) => {
                    if (!index) {
                        areaSegment.push(current);
                        return;
                    }
                    const previous = known[index - 1];
                    const previousX = x(previous.point).toFixed(1);
                    const previousY = y(previous.value).toFixed(1);
                    const currentX = x(current.point).toFixed(1);
                    const currentY = y(current.value).toFixed(1);
                    const previousBucket = chartDate({
                        date: previous.point.bucket_date || previous.point.date});
                    const currentBucket = chartDate({
                        date: current.point.bucket_date || current.point.date});
                    const consecutive = nextBucket(previousBucket) === currentBucket;
                    if (consecutive) {
                        solid += ` M ${previousX} ${previousY} L ${currentX} ${currentY}`;
                        areaSegment.push(current);
                    } else if (gapMode === 2) {
                        solid += ` M ${previousX} ${previousY} L ${currentX} ${previousY}`;
                        solid += ` L ${currentX} ${currentY}`;
                        areaSegment.push(current);
                    } else {
                        dashed += ` M ${previousX} ${previousY} L ${currentX} ${currentY}`;
                        flushArea();
                        areaSegment.push(current);
                    }
                });
                flushArea();
                return {solid, dashed, area, known};
            };

            let svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" `
                + `role="img" aria-label="Динамика денежных средств">`;
            [0, 0.25, 0.5, 0.75, 1].forEach(tick => {
                const py = top + plotHeight * tick;
                const value = maxValue - (maxValue - minValue) * tick;
                svg += `<line x1="${left}" y1="${py}" x2="${width - right}" y2="${py}" `
                    + `stroke="#E5E7EB" stroke-width="1"/>`;
                svg += `<text x="${left - 7}" y="${py + 4}" fill="#6B7280" `
                    + `font-size="10" text-anchor="end">${esc(this.formatValue(value, selectedUnit).val)}</text>`;
            });

            const overlays = [];
            selected.forEach(series => {
                const paths = pathsFor(series);
                const color = stableColor(series.id);
                const isActive = series.id === active.id;
                const widthPx = isActive ? 3.5 : 2;
                if (series.id === '__main__' && isActive && paths.area) {
                    svg += `<path d="${paths.area}" fill="#3B82F6" opacity="0.12" `
                        + `stroke="none" class="cash-modal-area"/>`;
                }
                svg += `<path d="${paths.solid}" fill="none" stroke="${color}" `
                    + `stroke-width="${widthPx}" stroke-linejoin="round" `
                    + `opacity="${isActive ? 1 : 0.65}" class="cash-modal-line"/>`;
                if (paths.dashed) {
                    svg += `<path d="${paths.dashed}" fill="none" stroke="${color}" `
                        + `stroke-width="${widthPx}" stroke-dasharray="5 4" `
                        + `stroke-linecap="round" class="cash-modal-line-gap"/>`;
                }
                paths.known.forEach(item => {
                    const cx = x(item.point);
                    const cy = y(item.value);
                    svg += `<circle class="cash-modal-point" data-series-id="${esc(series.id)}" `
                        + `cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${isActive ? 4 : 3}" `
                        + `fill="${isActive ? color : '#fff'}" stroke="${color}" stroke-width="2"/>`;
                    overlays.push(`<div class="cash-chart-point-overlay" tabindex="0" `
                        + `style="left:${((cx - 8) / width) * 100}%;top:${((cy - 8) / height) * 100}%;"`
                        + ` aria-label="${esc(`${series.label} — ${item.point.date || item.point.label}: ${this.formatValue(item.value, selectedUnit).val}`)}">`
                        + `${esc(series.label)} · ${esc(item.point.date || item.point.label)}: `
                        + `${esc(this.formatValue(item.value, selectedUnit).val)}</div>`);
                });
            });
            const activeDisplay = selected.find(series => series.id === active.id) || selected[0];
            const tickSeries = activeDisplay ? activeDisplay.data : [];
            const tickStep = Math.max(1, Math.ceil(tickSeries.length / 8));
            tickSeries.forEach((point, index) => {
                if (!chartDate(point) ||
                    (index % tickStep !== 0 && index !== tickSeries.length - 1)) return;
                const cx = x(point);
                svg += `<text class="cash-date-tick" x="${cx.toFixed(1)}" y="${height - 13}" `
                    + `text-anchor="middle" font-size="10" fill="#4B5563">${esc(formatDate(chartDate(point)))}</text>`;
            });
            if (minDate !== maxDate) {
                svg += `<text class="cash-date-tick" x="${left}" y="${height - 1}" `
                    + `text-anchor="start" font-size="10" fill="#334155">${esc(formatDate(minDate))}</text>`;
                svg += `<text class="cash-date-tick" x="${width - right}" y="${height - 1}" `
                    + `text-anchor="end" font-size="10" fill="#334155">${esc(formatDate(maxDate))}</text>`;
            }
            svg += '</svg>';
            const mainLegend = selected.some(series => series.id === '__main__')
                ? '<span class="cash-modal-chart-legend-main"><i></i>Денежные средства '
                    + '(сч. 50, 51, 52, 55)</span>'
                : '';
            const legend = '<div class="cash-modal-chart-legend" role="group" '
                + 'aria-label="Легенда графика">'
                + mainLegend
                + selected.filter(series => series.id !== '__main__').map(series =>
                    `<span><i style="background:${stableColor(series.id)}"></i>${esc(series.label)}</span>`
                ).join('')
                + '</div>';
            container.innerHTML = legend
                + `<div class="cash-modal-chart-plot">${svg}`
                + `<div class="cash-chart-point-overlays">${overlays.join('')}</div></div>`;
        },

        drawMetricModalSvg(card, chartContainer) {
            const container = chartContainer || state.modalChart.chartContainer ||
                document.getElementById('ki-metric-modal-svg');
            if (!container) return;
            if (isCashMetric(card.id)) {
                this.drawCashMetricModalSvg(card, container);
                return;
            }

            const unitFor = (item, data) => (item && item.unit) ||
                ((data || []).find(point => point && point.unit) || {}).unit ||
                card.unit;
            const availableSeries = [];
            const seenSeriesItems = new Set();
            const addSeries = (id, label, data, isMain, item, category) => {
                if (!data || !data.length) return;
                const identity = metricSelectableIdentity(item, category);
                if (seenSeriesItems.has(identity)) return;
                seenSeriesItems.add(identity);
                availableSeries.push({
                    id, label, data, isMain, unit: unitFor(item, data)
                });
            };
            if (card.series) addSeries(
                '__main__', 'Итого', card.series, true, card, 'main');
            if (card.breakdown) {
                card.breakdown.forEach((b, i) => {
                    addSeries(metricSeriesId(b, 'breakdown', i), b.label,
                        b.series, false, b, 'breakdown');
                });
            }
            const addCollectionSeries = (items, prefix) => {
                (items || []).forEach((item, i) => {
                    addSeries(metricSeriesId(item, prefix, i), item && item.label,
                        item && item.series, false, item, prefix);
                });
            };
            addCollectionSeries(card.components, 'component');
            addCollectionSeries(card.details, 'detail');
            addCollectionSeries(card.analytics, 'analytics');
            if (card.accrual_series && card.accrual_series.length) {
                const salarySeries = {
                    label: 'Начисления работникам',
                    series: card.accrual_series,
                    series_id: 'credit-turnover'
                };
                addSeries(metricSeriesId(salarySeries, 'salary', 0),
                    salarySeries.label, salarySeries.series, false,
                    salarySeries, 'salary');
            }

            const activeSeries = availableSeries.find(
                series => series.id === state.modalChart.activeId);
            const selectedUnit = activeSeries ? activeSeries.unit : card.unit;
            const chartUnit = selectedUnit;
            const compatible = availableSeries.filter(
                series => series.unit === selectedUnit);
            const seriesToDraw = compatible.filter(series => {
                const isActive = state.modalChart.activeId === series.id;
                const isPinned = state.modalChart.pinnedIds.has(series.id);
                if (isActive || isPinned) {
                    series.isActive = isActive;
                    series.isPinned = isPinned;
                    return true;
                }
                return false;
            });
            const incompatiblePinned = availableSeries.some(series =>
                state.modalChart.pinnedIds.has(series.id) &&
                series.unit !== selectedUnit);
            if (incompatiblePinned) {
                state.modalChart.unitNotice =
                    `Закреплённые серии ${selectedUnit} не смешиваются с сериями другой единицы измерения.`;
            } else {
                state.modalChart.unitNotice = '';
            }

            seriesToDraw.sort((a, b) => (a.isActive === b.isActive) ? 0 : a.isActive ? 1 : -1);

            if (seriesToDraw.length === 0) {
                container.innerHTML = '<div class="chart-empty">Нет данных для отображения</div>';
                return;
            }

            let allDates = [];
            compatible.forEach(s => {
                s.data.forEach(item => {
                    const d = chartDate(item);
                    if (d) allDates.push(d);
                });
            });
            const selectedBounds = chartPeriodBounds(allDates);
            let minDate = selectedBounds.start;
            let maxDate = selectedBounds.end;
            const dayMs = 86400000;
            const timelineMode = state.modalChart.timelineMode || 'week';
            const bucketStart = value => {
                const date = new Date(value);
                let result = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
                if (timelineMode === 'week') {
                    result -= ((new Date(result).getUTCDay() + 6) % 7) * dayMs;
                } else if (timelineMode === 'month') {
                    result = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
                }
                return result;
            };
            const nextBucket = value => {
                if (timelineMode === 'week') return value + 7 * dayMs;
                if (timelineMode === 'month') {
                    const date = new Date(value);
                    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
                }
                return value + dayMs;
            };
            const bucketRangeStart = bucketStart(minDate);
            const bucketRangeEnd = bucketStart(maxDate);
            const aggregateSeries = (data, isCollectionMain = false) => {
                const buckets = new Map();
                (data || []).forEach(point => {
                    const timestamp = chartDate(point);
                    if (timestamp === null || timestamp < minDate ||
                            timestamp > maxDate) return;
                    const key = bucketStart(timestamp);
                    if (card.id === 'collection' &&
                            card.measurement_basis === 'accounting_collection_proxy') {
                        const bucket = buckets.get(key) || {
                            timestamp, points: []
                        };
                        bucket.timestamp = Math.max(bucket.timestamp, timestamp);
                        bucket.points.push(point);
                        buckets.set(key, bucket);
                        return;
                    }
                    if (!hasNumericValue(point.value)) return;
                    const previous = buckets.get(key);
                    if (!previous || timestamp >= previous.timestamp) {
                        buckets.set(key, {timestamp, point});
                    }
                });
                const result = [];
                for (let cursor = bucketRangeStart;
                        cursor <= bucketRangeEnd;
                        cursor = nextBucket(cursor)) {
                    const known = buckets.get(cursor);
                    const displayDate = known
                        ? known.timestamp
                        : Math.min(selectedBounds.end,
                            Math.max(selectedBounds.start, cursor));
                    if (known && known.points) {
                        if (isCollectionMain) {
                            const keys = [
                                'debit_6201', 'credit_6201',
                                'debit_6202', 'credit_6202'
                            ];
                            const totals = {};
                            keys.forEach(component => {
                                const values = known.points
                                    .map(point => point[component])
                                    .filter(hasNumericValue)
                                    .map(Number);
                                totals[component] = values.length
                                    ? values.reduce((sum, value) => sum + value, 0)
                                    : null;
                            });
                            const complete = keys.every(component =>
                                totals[component] !== null);
                            const value = complete && totals.debit_6201 !== 0
                                ? (totals.credit_6201 + totals.credit_6202 -
                                    totals.debit_6202) /
                                    totals.debit_6201 * 100
                                : null;
                            result.push({
                                ...totals,
                                date: new Date(displayDate).toISOString().slice(0, 10),
                                bucket_date: new Date(cursor).toISOString().slice(0, 10),
                                label: '', value, granularity: timelineMode
                            });
                        } else {
                            const values = known.points
                                .map(point => point.value)
                                .filter(hasNumericValue)
                                .map(Number);
                            result.push({
                                date: new Date(displayDate).toISOString().slice(0, 10),
                                bucket_date: new Date(cursor).toISOString().slice(0, 10),
                                label: '',
                                value: values.length
                                    ? values.reduce((sum, value) => sum + value, 0)
                                    : null,
                                granularity: timelineMode
                            });
                        }
                        continue;
                    }
                    result.push(known
                        ? {...known.point,
                            date: new Date(displayDate).toISOString().slice(0, 10),
                            bucket_date: new Date(cursor).toISOString().slice(0, 10)}
                        : {date: new Date(displayDate).toISOString().slice(0, 10),
                            bucket_date: new Date(cursor).toISOString().slice(0, 10),
                            label: '', value: null, granularity: timelineMode});
                }
                return result;
            };
            const displaySeries = seriesToDraw.map(series => ({
                ...series, data: aggregateSeries(
                    series.data, series.id === '__main__')
            }));
            const baseSeries = displaySeries.find(s => s.isActive) || displaySeries[0];
            const span = Math.max(1, (maxDate - minDate) / dayMs);
            const masterTimeline = {
                calendar: true,
                x: dateMs => (dateMs - minDate) / dayMs / span
            };

            let min = 0, max = 0;
            displaySeries.forEach(s => {
                s.data.forEach(item => {
                    if (hasNumericValue(item.value)) {
                        const v = Number(item.value);
                        if (v < min) min = v;
                        if (v > max) max = v;
                    }
                });
            });
            if (min === max) max = min + 1;

            const width = 820, height = 220, left = 48, right = 15, top = 16, bottom = 36;
            const plotWidth = width - left - right;
            const plotHeight = height - top - bottom;

            const getY = val => top + plotHeight - ((val - min) / (max - min)) * plotHeight;
            const getX = (item, index, sData) => {
                if (masterTimeline.calendar) {
                    const d = chartDate(item);
                    return d ? left + masterTimeline.x(d) * plotWidth : left;
                }
                return left + masterTimeline.rawX(index, plotWidth);
            };

            const colors = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4', '#F43F5E'];
            const stableColor = id => {
                const text = String(id);
                let hash = 0;
                for (let i = 0; i < text.length; i++) {
                    hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
                }
                return colors[Math.abs(hash) % colors.length];
            };

            let svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="width:100%;height:100%;">`;

            [0, 0.25, 0.5, 0.75, 1].forEach(tick => {
                const y = top + plotHeight * tick;
                const val = max - (max - min) * tick;
                svg += `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" stroke="#E5E7EB" stroke-width="1"/>`;
                svg += `<text x="${left - 6}" y="${y + 4}" fill="#6B7280" font-size="10" text-anchor="end">${this.formatValue(val, chartUnit).val}</text>`;
            });

            const gapMode = state.modalChart.gapMode;

            let htmlOverlay = '<div style="position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none;">';

            displaySeries.forEach(s => {
                const color = metricChartColor(s.id);
                const strokeWidth = s.isActive ? 3 : 2;
                const opacity = s.isActive ? 1 : 0.6;

                let pathSolid = '';
                let pathDashed = '';

                const validPoints = [];
                s.data.forEach((p, i) => {
                    if (!hasNumericValue(p.value)) return;
                    const v = Number(p.value);
                    validPoints.push({...p, index: i, v});
                });

                validPoints.forEach((curr, vi) => {
                    const cx = getX(curr, curr.index, s.data);
                    const cy = getY(curr.v);

                    if (vi === 0) {
                        pathSolid += ` M ${cx.toFixed(1)} ${cy.toFixed(1)}`;
                    } else {
                        const prev = validPoints[vi - 1];
                        const px = getX(prev, prev.index, s.data);
                        const py = getY(prev.v);

                        const previousBucket = chartDate({
                            date: prev.bucket_date || prev.date});
                        const currentBucket = chartDate({
                            date: curr.bucket_date || curr.date});
                        const isConsecutive = nextBucket(previousBucket) === currentBucket;
                        if (isConsecutive) {
                            pathSolid += ` L ${cx.toFixed(1)} ${cy.toFixed(1)}`;
                        } else {
                            if (gapMode === 2) {
                                pathSolid += ` L ${cx.toFixed(1)} ${py.toFixed(1)} L ${cx.toFixed(1)} ${cy.toFixed(1)}`;

                                const tooltipData = this.metricTooltipData(card, {
                                    title: `${s.label} (Синтетическое значение)`,
                                    fact: `${this.formatValue(prev.v, chartUnit).val} ${chartUnit || ''}`,
                                    detail: `Продлено с ${prev.label || prev.date}. Точные данные за этот день отсутствуют.`,
                                    deviation: 'Замещающее значение', deviationClass: 'missing'
                                });
                                const tooltip = this.cashTooltip(tooltipData);

                                const rectW = Math.max(cx - px, 1);
                                const leftPct = (px / width) * 100;
                                const topPct = ((py - 10) / height) * 100;
                                const widthPct = (rectW / width) * 100;
                                const heightPct = (20 / height) * 100;

                                htmlOverlay += `
                                <div class="cash-tooltip-host gap-transform-rect" tabindex="0" style="position:absolute; left:${leftPct}%; top:${topPct}%; width:${widthPct}%; height:${heightPct}%; pointer-events:auto; cursor:help;">
                                    ${tooltip}
                                </div>`;
                            } else {
                                pathSolid += ` M ${cx.toFixed(1)} ${cy.toFixed(1)}`;
                                pathDashed += ` M ${px.toFixed(1)} ${py.toFixed(1)} L ${cx.toFixed(1)} ${cy.toFixed(1)}`;
                            }
                        }
                    }
                });

                svg += `<path d="${pathSolid}" stroke="${color}" stroke-width="${strokeWidth}" fill="none" stroke-linejoin="round" opacity="${opacity}"/>`;
                if (pathDashed) {
                    svg += `<path d="${pathDashed}" stroke="${color}" stroke-width="${strokeWidth}" fill="none" stroke-linejoin="round" stroke-dasharray="4,4" opacity="${opacity}"/>`;
                }

                validPoints.forEach(curr => {
                    const cx = getX(curr, curr.index, s.data);
                    const cy = getY(curr.v);
                    svg += `<circle class="metric-chart-point-svg" cx="${cx.toFixed(1)}" `
                        + `cy="${cy.toFixed(1)}" r="${s.isActive ? 4 : 3}" `
                        + `fill="${s.isActive ? color : '#fff'}" stroke="${color}" stroke-width="2"/>`;

                    const sourceLabels = {
                        direct_daily_osv: 'Прямая суточная ОСВ',
                        reconciled_turnovers: 'Восстановлено из сверенных датированных оборотов',
                        confirmed_zero: 'Подтверждённый нулевой источник',
                        unknown: 'Происхождение старой точки не определено'
                    };
                    let provenance = curr.source_type
                        ? sourceLabels[curr.source_type]
                        : '';
                    if (!provenance && curr.sources) {
                        provenance = Object.entries(curr.sources)
                            .map(([account, sourceType]) =>
                                `счёт ${account}: ${sourceLabels[sourceType] || sourceLabels.unknown}`)
                            .join('; ');
                    }
                    const pointDetail = provenance
                        ? `Источник: ${provenance}`
                        : (curr.reason || 'Фактическое значение');
                    const tooltipData = this.metricTooltipData(card, {
                        title: `${s.label}: ${curr.label || curr.date}`,
                        fact: `${this.formatValue(curr.v, chartUnit).val} ${chartUnit || ''}`,
                        detail: pointDetail,
                        deviation: (curr.comparison && curr.comparison.comparable && curr.comparison.value) ?
                            (curr.comparison.value > 0 ? '+' : '') + this.formatValue(curr.comparison.value, chartUnit).val : ''
                    });
                    const tooltip = this.cashTooltip(tooltipData);

                    const leftPct = ((cx - 6) / width) * 100;
                    const topPct = ((cy - 6) / height) * 100;
                    const wPct = (12 / width) * 100;
                    const hPct = (12 / height) * 100;

                    htmlOverlay += `
                    <div class="cash-tooltip-host chart-point-marker" tabindex="0" style="position:absolute; left:${leftPct}%; top:${topPct}%; width:${wPct}%; height:${hPct}%; border-radius:50%; background:transparent; pointer-events:auto; cursor:help; display:flex; align-items:center; justify-content:center;">
                        ${tooltip}
                    </div>`;
                });
            });

            const tickStep = Math.max(1, Math.ceil(baseSeries.data.length / 8));
            const dateFormatter = new Intl.DateTimeFormat('ru-RU',
                timelineMode === 'month'
                    ? {month: 'short', year: '2-digit'}
                    : {day: 'numeric', month: 'short'});
            baseSeries.data.forEach((point, index) => {
                if (index % tickStep !== 0 && index !== baseSeries.data.length - 1) return;
                const dateMs = chartDate(point);
                if (dateMs === null) return;
                if (minDate !== maxDate &&
                        (dateMs === minDate || dateMs === maxDate)) return;
                const cx = getX(point, index, baseSeries.data);
                const label = dateFormatter.format(new Date(dateMs)).replace(/\s*г\.$/, '');
                svg += `<line x1="${cx.toFixed(1)}" y1="${height - bottom}" `
                    + `x2="${cx.toFixed(1)}" y2="${height - bottom + 4}" stroke="#94A3B8"/>`;
                svg += `<text class="metric-date-tick" x="${cx.toFixed(1)}" y="${height - 10}" `
                    + `text-anchor="middle" font-size="10" fill="#475569">${esc(label)}</text>`;
            });
            if (minDate !== maxDate) {
                const firstLabel = dateFormatter.format(new Date(minDate))
                    .replace(/\s*г\.$/, '');
                const lastLabel = dateFormatter.format(new Date(maxDate))
                    .replace(/\s*г\.$/, '');
                svg += `<text class="metric-date-tick" x="${left}" y="${height - 1}" `
                    + `text-anchor="start" font-size="10" fill="#334155">${esc(firstLabel)}</text>`;
                svg += `<text class="metric-date-tick" x="${width - right}" y="${height - 1}" `
                    + `text-anchor="end" font-size="10" fill="#334155">${esc(lastLabel)}</text>`;
            }

            svg += '</svg>';
            htmlOverlay += '</div>';

            const unitNotice = state.modalChart.unitNotice
                ? `<div style="font-size:11px;color:#92400E;margin-bottom:6px;">${esc(state.modalChart.unitNotice)}</div>`
                : '';
            container.innerHTML = unitNotice
                + `<div class="metric-modal-chart-plot">${svg}${htmlOverlay}</div>`;
        },
        renderCashDzModal(card) {
            const container = document.getElementById('ki-cash-modal-content');
            const currentDz = card.consumer_series || card.series || [];
            const advances = card.advances_series || [];
            const apgDz = card.previous_year_consumer_series || [];
            const apgAdvances = card.previous_year_advances_series || [];
            const formatAmount = value => this.formatValue(
                value !== null && value !== undefined && Number.isFinite(Number(value))
                    ? Number(value) : null, card.unit || 'RUB');
            const amount = formatAmount(card.value);
            const periodChange = card.period_change || {};
            const changeValue = periodChange.comparable &&
                Number.isFinite(Number(periodChange.value))
                ? Number(periodChange.value) : null;
            const change = formatAmount(changeValue);
            const changeText = changeValue === null ? 'Нужны данные' :
                `${changeValue > 0 ? '+' : ''}${change.val} ${change.u}`;
            const coverage = this.cashCoverageText(card);
            const analytics = card.analytics || card.breakdown ||
                card.debtor_segments || card.details || [];
            const total = card.value !== null && card.value !== undefined &&
                Number.isFinite(Number(card.value)) ? Number(card.value) : null;
            const rows = analytics.map((item, index) => {
                const value = item && item.value !== null && item.value !== undefined &&
                    Number.isFinite(Number(item.value))
                    ? Number(item.value) : null;
                const formatted = formatAmount(value);
                const share = value !== null && total !== null && total !== 0
                    ? `${(value / total * 100).toFixed(1)}%` : '—';
                const label = item && (item.label || item.name || item.account ||
                    `Аналитика ${index + 1}`);
                const comment = item && (item.comment || item.description ||
                    item.reason || 'Дебиторская задолженность потребителя');
                return `<tr class="cash-tooltip-host" tabindex="0">
                    <td class="cash-account-cell"><div><strong>${esc(label)}</strong><span class="cash-account-name">62.01</span></div></td>
                    <td class="cash-number-cell"><span class="cash-number-value">${esc(formatted.val)}</span> <span class="cash-number-unit">${esc(formatted.u)}</span></td>
                    <td class="cash-share-cell"><span class="cash-share-value">${esc(share)}</span>${share !== '—'
                        ? `<span class="cash-share-track" aria-hidden="true"><span class="cash-share-fill" style="width:${Math.max(0, Math.min(100, Number(share.slice(0, -1)))).toFixed(1)}%"></span></span>`
                        : ''}</td>
                    <td class="cash-comment-cell">${esc(comment)}</td>
                    <td class="cash-status-cell"><span class="cash-data-state ${value === null ? 'missing' : 'ok'}">${value === null ? 'Нет данных' : 'Загружен'}</span></td>
                </tr>`;
            }).join('');
            const monthly = Array.isArray(card.monthly_comparison)
                ? card.monthly_comparison : [];
            const formatPercent = value => value !== null && value !== undefined &&
                Number.isFinite(Number(value))
                ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)}%` : '—';
            const monthlyRows = monthly.map(item => {
                const current = formatAmount(item.current_dz);
                const apgValue = item.previous_year_dz ?? item.apg_dz;
                const apg = formatAmount(apgValue);
                const currentMissing = !Number.isFinite(Number(item.current_dz));
                const apgMissing = !Number.isFinite(Number(apgValue));
                return `<tr>
                    <td>${esc(item.label || item.month || '—')}</td>
                    <td class="cash-number-cell">${currentMissing ? '—' : `${esc(current.val)} <span class="cash-number-unit">${esc(current.u)}</span>`}</td>
                    <td class="cash-number-cell">${apgMissing ? '—' : `${esc(apg.val)} <span class="cash-number-unit">${esc(apg.u)}</span>`}</td>
                    <td class="${Number(item.percent_delta) < 0 ? 'cash-value-negative' : ''}">${esc(formatPercent(item.percent_delta))}</td>
                </tr>`;
            }).join('');
            const changeClass = changeValue === null ? 'cash-value-missing' :
                changeValue < 0 ? 'cash-value-negative' : 'cash-value-ok';
            document.getElementById('ki-modal-title').textContent = 'ДЗ';
            this.updateModalSubtitle();
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Дебиторская задолженность потребителей.</strong>
                    Расчёт выполнен по счёту 62.01. Авансы потребителей по счёту 62.02 показываются отдельно.</div>
                <div class="cash-summary-grid">
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="debtor-current"
                         onclick="window.KI_App.toggleCashChartMode('debtor-current')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('debtor-current')}">
                        <div class="cash-summary-label">ДЗ потребителей</div>
                        <div class="cash-summary-value ${card.value == null ? 'cash-value-missing' : 'cash-value-ok'}">${card.value == null ? 'Нужны данные' : `${esc(amount.val)} <span class="metric-inline-unit">${esc(amount.u)}</span>`}</div>
                        <div class="cash-summary-sub ${changeClass}">Изменение за период: ${esc(changeText)}</div>
                    </div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="advances-current"
                         onclick="window.KI_App.toggleCashChartMode('advances-current')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('advances-current')}">
                        <div class="cash-summary-label">Авансы потребителей</div>
                        <div class="cash-summary-value ${advances.length ? 'cash-value-ok' : 'cash-value-missing'}">${advances.length ? esc(formatAmount(advances[advances.length - 1].value).val) : 'Нужны данные'}${advances.length ? ` <span class="metric-inline-unit">${esc(formatAmount(advances[advances.length - 1].value).u)}</span>` : ''}</div>
                        <div class="cash-summary-sub">${esc(coverage)}</div>
                    </div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="debtor-apg"
                         onclick="window.KI_App.toggleCashChartMode('debtor-apg')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('debtor-apg')}">
                        <div class="cash-summary-label">ДЗ потребителей АПГ</div>
                        <div class="cash-summary-value ${apgDz.length ? 'cash-value-ok' : 'cash-value-missing'}">${apgDz.length ? esc(formatAmount(apgDz[apgDz.length - 1].value).val) : 'Нужны данные'}${apgDz.length ? ` <span class="metric-inline-unit">${esc(formatAmount(apgDz[apgDz.length - 1].value).u)}</span>` : ''}</div>
                        <div class="cash-summary-sub">${esc(coverage)}</div>
                    </div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="advances-apg"
                         onclick="window.KI_App.toggleCashChartMode('advances-apg')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('advances-apg')}">
                        <div class="cash-summary-label">Авансы потребителей АПГ</div>
                        <div class="cash-summary-value ${apgAdvances.length ? 'cash-value-ok' : 'cash-value-missing'}">${apgAdvances.length ? esc(formatAmount(apgAdvances[apgAdvances.length - 1].value).val) : 'Нужны данные'}${apgAdvances.length ? ` <span class="metric-inline-unit">${esc(formatAmount(apgAdvances[apgAdvances.length - 1].value).u)}</span>` : ''}</div>
                        <div class="cash-summary-sub">${esc(coverage)}</div>
                    </div>
                </div>
                <button type="button" class="modal-section-title cash-details-toggle"
                        aria-expanded="false" aria-controls="ki-cash-details-table"
                        onclick="window.KI_App.toggleCashDetails(this)">
                    <span>Детализация ДЗ потребителей</span><span class="cash-details-chevron" aria-hidden="true">⌄</span>
                </button>
                <div id="ki-cash-details-table" class="cash-table-scroll" tabindex="0" role="region"
                     aria-label="Детализация ДЗ потребителей" hidden><table class="cash-table">
                    <thead><tr><th>Потребитель / аналитика</th><th>Сумма</th><th>Доля</th><th>Комментарий</th><th>Данные</th></tr></thead>
                    <tbody>${rows}<tr class="cash-total-row"><td><div>ИТОГО</div></td><td class="cash-number-cell"><span class="cash-number-value">${esc(amount.val)}</span> <span class="cash-number-unit">${esc(amount.u)}</span></td><td class="cash-share-cell"><span class="cash-share-value">${total ? '100%' : '—'}</span></td><td></td><td></td></tr></tbody>
                </table></div>
                <div class="modal-section-title">ДЗ по месяцам</div>
                <div class="cash-table-scroll"><table class="cash-table">
                    <thead><tr><th>Месяц</th><th>ДЗ</th><th>АПГ</th><th>Дельта АПГ</th></tr></thead>
                    <tbody>${monthlyRows || '<tr><td colspan="4">Нет сопоставимых данных</td></tr>'}</tbody>
                </table></div>
                <div id="ki-cash-chart-title" class="modal-section-title">ДЗ потребителей и Авансы потребителей</div>
                <div id="ki-cash-line-chart" class="cash-line-chart"></div>
                <div class="cash-chart-legend" aria-label="Легенда графика">
                    <span><i class="cash-chart-dot" style="background:#111827"></i>ДЗ потребителей</span>
                    <span><i class="cash-chart-dot" style="background:#DB2777"></i>ДЗ потребителей АПГ</span>
                    <span><i class="cash-chart-dot" style="background:#2563EB"></i>Авансы потребителей</span>
                    <span><i class="cash-chart-dot" style="background:#059669"></i>Авансы потребителей АПГ</span>
                </div>`;
        },
renderCashModal(card) {
            const isCashDz = card.id === 'cash_dz';
            if (isCashDz) {
                this.renderCashDzModal(card);
                return;
            }
            const container = document.getElementById('ki-cash-modal-content');
            const accounts = ['50', '51', '52', '55'];
            const names = {
                '50': 'Касса',
                '51': 'Расчётные счета',
                '52': 'Валютные счета',
                '55': 'Специальные счета'
            };
            const comments = {
                '50': 'Наличные денежные средства',
                '51': 'Рублёвые банковские счета',
                '52': 'Балансовая стоимость валютных средств в рублях',
                '55': 'Аккредитивы, депозиты и иные специальные счета'
            };
            const accountDetails = {
                '50': 'Наличные деньги в кассах предприятия. Подотчётные суммы учитываются отдельно и в остаток счёта 50 не включаются.',
                '51': 'Денежные средства на расчётных счетах в рублях. Назначение конкретного банковского счёта определяется только из банковской аналитики 1С.',
                '52': 'Валютные денежные средства в рублёвой бухгалтерской оценке. Повторное умножение остатка ОСВ на курс валюты не выполняется.',
                '55': 'Аккредитивы, депозиты, чековые книжки и специальные счета. Часть средств может быть ограничена или иметь целевое назначение.'
            };
            const parts = {};
            const breakdownSeriesIds = {};
            (card.breakdown || []).forEach(item => {
                const match = String(item.label || '').match(/(\d{2})/);
                if (match) {
                    parts[match[1]] = item.value;
                    breakdownSeriesIds[match[1]] = metricSeriesId(
                        item, 'breakdown', (card.breakdown || []).indexOf(item));
                }
            });
            const cashFormatted = this.formatValue(card.value, card.unit);
            const delta = card.comparison && card.comparison.comparable && Number.isFinite(card.comparison.value)
                ? card.comparison.value : null;
            const deltaFormatted = this.formatValue(delta, 'RUB');
            const deltaText = delta === null ? 'Нужны данные' :
                `${delta > 0 ? '+' : ''}${deltaFormatted.val} ${deltaFormatted.u}`;
            const total = Number.isFinite(card.value) ? card.value : null;
            const coverageText = this.cashCoverageText(card);
            const comparisonText = this.cashComparisonText(card.comparison);
            const dateText = card.date ? `на ${card.date}` : 'дата не подтверждена';
            let rows = '';
            accounts.forEach(account => {
                const value = Object.prototype.hasOwnProperty.call(parts, account) ? parts[account] : null;
                const formatted = this.formatValue(value, card.unit);
                const shareValue = value !== null && total !== null && total !== 0
                    ? value / total * 100 : null;
                const share = shareValue !== null ? `${shareValue.toFixed(1)}%` : '—';
                const shareBarWidth = shareValue !== null
                    ? Math.max(0, Math.min(100, shareValue)) : null;
                const required = value === null
                    ? `ОСВ по счёту ${account} на выбранную дату с начальным и конечным дебетовым/кредитовым сальдо.`
                    : null;
                const rowTooltip = this.cashTooltip({
                    title: `${names[account]} — счёт ${account}`,
                    description: comments[account] + '.',
                    formula: `Остаток счёта ${account} = СКд(${account}) − СКк(${account})`,
                    detail: accountDetails[account],
                    required,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${formatted.val} ${formatted.u} · ${dateText}${share !== '—' ? ` · ${share}` : ''}`,
                    deviation: required ? 'Расчёт невозможен' : 'Оценивается после задания норматива',
                    source: required ? `требуется ОСВ по счёту ${account}` : `ОСВ по счёту ${account}`
                });
                rows += `<tr class="cash-tooltip-host" tabindex="0"${breakdownSeriesIds[account]
                    ? ` data-series-id="${esc(breakdownSeriesIds[account])}"` : ''}>
                    <td class="cash-account-cell"><div><strong>Счёт ${account}</strong><span class="cash-account-name">${esc(names[account])}</span>${rowTooltip}</div></td>
                    <td class="cash-number-cell"><span class="cash-number-value">${esc(formatted.val)}</span> <span class="cash-number-unit">${esc(formatted.u)}</span></td>
                    <td class="cash-share-cell"><span class="cash-share-value">${esc(share)}</span>${shareBarWidth !== null
                        ? `<span class="cash-share-track" aria-hidden="true"><span class="cash-share-fill" style="width:${shareBarWidth.toFixed(1)}%"></span></span>`
                        : ''}</td>
                    <td class="cash-comment-cell">${esc(comments[account])}</td>
                    <td class="cash-status-cell"><span class="cash-data-state ${value === null ? 'missing' : 'ok'}">${value === null ? 'Нет данных' : 'Загружен'}</span></td>
                </tr>`;
            });

            const fullNcfTip = 'Для полного NCF нужна выгрузка движений по счетам 50, 51, 52 и 55 с датой, суммой, корреспондирующим счётом, статьёй движения денежных средств и признаком внутреннего перевода.';
            const currentLiquidityTip = 'Нужен бухгалтерский баланс либо ОСВ по всем счетам оборотных активов и краткосрочных обязательств, включая 41, 43, 45, 60, 62, 66, 68, 69, 70, 76, а также сроки погашения по счетам 67 и 76.';
            const absoluteLiquidityTip = 'Нужна аналитика сроков погашения по счетам 60, 66, 67, 68, 69, 70 и 76, чтобы отделить обязательства, подлежащие погашению в ближайшие 12 месяцев.';
            const runwayTip = 'Нужна банковская выписка и кассовые операции минимум за 90 дней: дата, сумма, назначение платежа, контрагент, статья ДДС и корреспондирующий счёт. Внутренние переводы между счетами должны быть отмечены.';
            const fuelTip = 'Нужен складской отчёт 1С по топливу в натуральных единицах, вид топлива, доступный остаток, среднесуточный фактический расход и производственный план либо норматив потребления.';
            const changeTip = delta === null
                ? comparisonText + '. Нужны точные конечные сальдо на две даты с одинаковым составом доступных счетов.'
                : 'Показано изменение бухгалтерского остатка между двумя сопоставимыми датами. Это не классифицированный отчёт о движении денежных средств. ' + fullNcfTip;
            const cashTipData = {
                title: 'Денежные средства предприятия',
                description: 'Денежные средства на расчётных и иных счетах по данным бухгалтерского учёта. Средства на счёте 55 могут иметь ограничения по использованию.',
                formula: 'Денежные средства = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                detail: `${coverageText}. Отсутствующие счета не приняты за ноль. Счёт 50 — касса; 51 — расчётные счета; 52 — валютные счета в рублёвой оценке; 55 — специальные счета.`,
                norm: 'Индивидуальный норматив предприятия не задан',
                fact: `${cashFormatted.val} ${cashFormatted.u} · ${dateText}`,
                deviation: delta === null ? comparisonText : `${deltaText} · ${comparisonText}`,
                source: 'конечные сальдо загруженных ОСВ по счетам 50, 51, 52 и 55'
            };
            const cashApgLatest = [...(card.previous_year_series || [])]
                .reverse().find(point => Number.isFinite(point.value));
            const cashApgFormatted = this.formatValue(
                cashApgLatest ? cashApgLatest.value : null, card.unit);
            const cashApgCoverageText = this.cashCoverageText(
                cashApgLatest || {coverage: 'missing', accounts: []});
            const cashApgDate = cashApgLatest &&
                (cashApgLatest.date || cashApgLatest.label);
            const cashApgTipData = {
                title: 'Денежные средства АПГ',
                description: 'Остаток денежных средств на конечную дату аналогичного периода прошлого года.',
                formula: 'Денежные средства АПГ = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                detail: `${cashApgCoverageText}. Отсутствующие счета не приняты за ноль.`,
                required: cashApgLatest ? null : 'Нужны точные конечные сальдо за аналогичный период прошлого года.',
                norm: 'Индивидуальный норматив предприятия не задан',
                fact: cashApgLatest
                    ? `${cashApgFormatted.val} ${cashApgFormatted.u} · на ${cashApgDate}`
                    : 'Н/Д',
                deviation: cashApgLatest
                    ? 'Фактический остаток АПГ' : 'Расчёт невозможен',
                source: 'конечные сальдо ОСВ по счетам 50, 51, 52 и 55 за прошлый год'
            };
            const cumulativeLatest = [...(card.series || [])].reverse().find(point =>
                Number.isFinite(point.debit_value) &&
                Number.isFinite(point.credit_value));
            const compactMillions = value => Number.isFinite(value)
                ? new Intl.NumberFormat('ru-RU', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                }).format(value / 1000000)
                : '—';
            const cumulativeDebit = compactMillions(
                cumulativeLatest && cumulativeLatest.debit_value);
            const cumulativeCredit = compactMillions(
                cumulativeLatest && cumulativeLatest.credit_value);
            const cumulativeCoverage = {
                accounts: cumulativeLatest ? cumulativeLatest.cumulative_accounts || [] : [],
                missing_accounts: cumulativeLatest
                    ? cumulativeLatest.cumulative_missing_accounts || [] : accounts,
                coverage: cumulativeLatest
                    ? cumulativeLatest.cumulative_coverage || 'missing' : 'missing'
            };
            const cumulativeCoverageText = this.cashCoverageText(cumulativeCoverage);
            const cumulativeDate = cumulativeLatest &&
                (cumulativeLatest.date || cumulativeLatest.label);
            const cumulativeTipData = {
                title: 'Поступления / выплаты',
                description: 'Поступления и выплаты денежных средств.',
                formula: 'Д = Σ(СНд + накопленный ОД); К = Σ(СНк + накопленный ОК)',
                detail: `${cumulativeCoverageText}. Отсутствующие счета не приняты за ноль.${cumulativeDate ? ` Значения приведены на ${cumulativeDate}.` : ''}`,
                required: cumulativeLatest ? null : 'Нужны точные начальные сальдо и дневные обороты по денежным счетам.',
                norm: 'Норматив не установлен',
                fact: cumulativeLatest
                    ? `${cumulativeDebit} / ${cumulativeCredit} млн ₽`
                    : 'Н/Д',
                deviation: cumulativeLatest ? 'Информационный показатель' : 'Расчёт невозможен',
                source: 'счета 50, 51, 52 и 55 по данным ОСВ'
            };
            const cumulativeApgLatest = [...(card.previous_year_series || [])]
                .reverse().find(point =>
                    Number.isFinite(point.debit_value) &&
                    Number.isFinite(point.credit_value));
            const cumulativeApgDebit = compactMillions(
                cumulativeApgLatest && cumulativeApgLatest.debit_value);
            const cumulativeApgCredit = compactMillions(
                cumulativeApgLatest && cumulativeApgLatest.credit_value);
            const cumulativeApgCoverage = {
                accounts: cumulativeApgLatest
                    ? cumulativeApgLatest.cumulative_accounts || [] : [],
                missing_accounts: cumulativeApgLatest
                    ? cumulativeApgLatest.cumulative_missing_accounts || [] : accounts,
                coverage: cumulativeApgLatest
                    ? cumulativeApgLatest.cumulative_coverage || 'missing' : 'missing'
            };
            const cumulativeApgCoverageText = this.cashCoverageText(
                cumulativeApgCoverage);
            const cumulativeApgDate = cumulativeApgLatest &&
                (cumulativeApgLatest.date || cumulativeApgLatest.label);
            const cumulativeApgTipData = {
                title: 'Поступления / выплаты АПГ',
                description: 'Поступления и выплаты за аналогичный период прошлого года.',
                formula: 'Д = Σ(СНд + накопленный ОД); К = Σ(СНк + накопленный ОК)',
                detail: `${cumulativeApgCoverageText}. Период сдвинут на один год без изменения его продолжительности. Отсутствующие счета не приняты за ноль.`,
                required: cumulativeApgLatest ? null : 'Нужны точные начальные сальдо и дневные обороты за аналогичный период прошлого года.',
                norm: 'Норматив не установлен',
                fact: cumulativeApgLatest
                    ? `${cumulativeApgDebit} / ${cumulativeApgCredit} млн ₽`
                    : 'Н/Д',
                deviation: cumulativeApgLatest
                    ? 'Фактические значения АПГ' : 'Расчёт невозможен',
                source: 'счета 50, 51, 52 и 55 по данным ОСВ за прошлый год'
            };
            const changeTipData = {
                title: 'Изменение бухгалтерского остатка',
                description: 'Изменение бухгалтерского остатка денежных средств между двумя сопоставимыми датами. Без статей ДДС это не полный отчёт о денежных потоках.',
                formula: 'Изменение = Остаток на текущую дату − Остаток на предыдущую дату',
                detail: `${comparisonText}. ${coverageText}. Разница остатков не является NCF. ${fullNcfTip}`,
                required: delta === null ? changeTip : null,
                norm: 'Положительное значение не является универсальной нормой',
                fact: `${deltaText} · ${comparisonText}`,
                deviation: delta === null ? 'Расчёт невозможен' : 'Фактическое изменение остатка',
                source: 'сопоставимые ОСВ; для полного NCF требуется аналитика движения денежных средств'
            };
            const currentLiquidityData = {
                title: 'Коэффициент текущей ликвидности',
                description: 'Показывает способность предприятия погасить краткосрочные обязательства за счёт оборотных активов.',
                formula: 'КТЛ = Оборотные активы / Краткосрочные обязательства',
                detail: 'Текущие 20 счетов не содержат полного состава оборотных активов и не разделяют все обязательства по срокам погашения.',
                required: currentLiquidityTip,
                norm: 'Задаётся утверждённой методикой предприятия',
                fact: 'Н/Д',
                deviation: 'Расчёт невозможен',
                source: 'требуется бухгалтерский баланс или расширенная ОСВ'
            };
            const absoluteLiquidityData = {
                title: 'Коэффициент абсолютной ликвидности',
                description: 'Доля краткосрочных обязательств, которую можно погасить денежными средствами немедленно.',
                formula: 'КАЛ = Денежные средства / Краткосрочные обязательства',
                detail: 'Сумма денежных средств доступна, но краткосрочная часть обязательств без сроков погашения не определена.',
                required: absoluteLiquidityTip,
                norm: 'Задаётся утверждённой методикой предприятия',
                fact: 'Н/Д',
                deviation: 'Расчёт невозможен',
                source: 'требуется аналитика сроков погашения обязательств из 1С'
            };
            const runwayData = {
                title: 'Cash Runway',
                description: 'Расчётное число дней, на которое хватит текущего денежного остатка при сохранении среднего темпа внешних платежей.',
                formula: 'Cash Runway = Остаток денежных средств / Среднедневные внешние платежи',
                detail: 'Обороты ОСВ включают внутренние переводы и не позволяют надёжно выделить внешний расход.',
                required: runwayTip,
                norm: 'Устанавливается финансовой политикой предприятия',
                fact: 'Н/Д',
                deviation: 'Расчёт невозможен',
                source: 'требуются банковские и кассовые операции из 1С'
            };
            const fuelData = {
                title: 'Обеспеченность топливом',
                description: 'Количество дней работы, обеспеченное фактическим доступным запасом топлива.',
                formula: 'Дни запаса = Натуральный остаток топлива / Среднесуточный расход',
                detail: 'Денежное сальдо счёта 10 не заменяет натуральное количество топлива и производственный расход.',
                required: fuelTip,
                norm: 'Зависит от вида топлива, сезона и утверждённого норматива',
                fact: 'Н/Д',
                deviation: 'Расчёт невозможен',
                source: 'требуется складская и производственная аналитика 1С'
            };

            document.getElementById('ki-modal-title').textContent = isCashDz
                ? 'ДЗ'
                : 'Ликвидность и денежные средства (сч. 50, 51, 52, 55)';
            this.updateModalSubtitle();
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Денежные средства по данным бухгалтерского учёта.</strong>
                    Итог рассчитан как сумма чистых конечных сальдо: СКд − СКк по счетам 50, 51, 52 и 55.
                    Средства счёта 55 могут иметь ограничения по использованию.</div>
                <div class="cash-summary-grid">
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="cash-current"
                         onclick="window.KI_App.toggleCashChartMode('cash-current')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('cash-current')}"><div class="cash-summary-label">Денежные средства</div>
                        <div class="cash-summary-value cash-value-ok">${esc(cashFormatted.val)} <span class="metric-inline-unit">${esc(cashFormatted.u)}</span></div>
                        <div class="cash-summary-sub">${isCashDz ? '' : `${esc(dateText)}<br>`}${esc(coverageText)}</div>${this.cashTooltip(cashTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card cash-chart-mode-selected"
                         tabindex="0" role="button" aria-pressed="true" data-cash-chart-mode="cash-apg"
                         onclick="window.KI_App.toggleCashChartMode('cash-apg')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('cash-apg')}"><div class="cash-summary-label">Денежные средства АПГ</div>
                        <div class="cash-summary-value ${cashApgLatest ? 'cash-value-ok' : 'cash-value-missing'}">${cashApgLatest
                            ? `${esc(cashApgFormatted.val)} <span class="metric-inline-unit">${esc(cashApgFormatted.u)}</span>`
                            : 'Нужны данные'}</div>
                        <div class="cash-summary-sub">${esc(cashApgCoverageText)}${!isCashDz && cashApgDate ? `<br>на ${esc(cashApgDate)}` : ''}</div>${this.cashTooltip(cashApgTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card"
                         tabindex="0" role="button" aria-pressed="false" data-cash-chart-mode="cumulative-current"
                         onclick="window.KI_App.toggleCashChartMode('cumulative-current')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('cumulative-current')}">
                        <div class="cash-summary-label">Поступления / выплаты</div>
                        <div class="cash-summary-value ${cumulativeLatest ? 'cash-value-ok' : 'cash-value-missing'}">${cumulativeLatest
                            ? `${esc(cumulativeDebit)} / ${esc(cumulativeCredit)} <span class="metric-inline-unit">млн ₽</span>`
                            : 'Нужны данные'}</div>
                        <div class="cash-summary-sub">${esc(cumulativeCoverageText)}${!isCashDz && cumulativeDate ? `<br>на ${esc(cumulativeDate)}` : ''}</div>
                        ${this.cashTooltip(cumulativeTipData)}
                    </div>
                    <div class="cash-summary-card cash-tooltip-host cash-chart-mode-card"
                         tabindex="0" role="button" aria-pressed="false" data-cash-chart-mode="cumulative-apg"
                         onclick="window.KI_App.toggleCashChartMode('cumulative-apg')"
                         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.KI_App.toggleCashChartMode('cumulative-apg')}">
                        <div class="cash-summary-label">Поступления / выплаты АПГ</div>
                        <div class="cash-summary-value ${cumulativeApgLatest ? 'cash-value-ok' : 'cash-value-missing'}">${cumulativeApgLatest
                            ? `${esc(cumulativeApgDebit)} / ${esc(cumulativeApgCredit)} <span class="metric-inline-unit">млн ₽</span>`
                            : 'Нужны данные'}</div>
                        <div class="cash-summary-sub">${esc(cumulativeApgCoverageText)}${!isCashDz && cumulativeApgDate ? `<br>на ${esc(cumulativeApgDate)}` : ''}</div>
                        ${this.cashTooltip(cumulativeApgTipData)}
                    </div>
                    ${isCashDz ? '' : `<div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Изменение остатка</div>
                        <div class="cash-summary-value ${delta === null ? 'cash-value-missing' : delta < 0 ? 'cash-value-negative' : 'cash-value-ok'}">${esc(deltaText)}</div>
                        <div class="cash-summary-sub">${esc(comparisonText)}</div>${this.cashTooltip(changeTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Текущая ликвидность</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет полного состава оборотных активов</div>${this.cashTooltip(currentLiquidityData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Cash Runway</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет очищенного платёжного потока</div>${this.cashTooltip(runwayData)}</div>`}
                </div>
                <button type="button" class="modal-section-title cash-details-toggle"
                        aria-expanded="false" aria-controls="ki-cash-details-table"
                        onclick="window.KI_App.toggleCashDetails(this)">
                    <span>Детализация по счетам денежных средств</span>
                    <span class="cash-details-chevron" aria-hidden="true">⌄</span>
                </button>
                <div id="ki-cash-details-table" class="cash-table-scroll" tabindex="0" role="region"
                     aria-label="Детализация по счетам денежных средств" hidden><table class="cash-table">
                    <thead><tr><th>Счёт / назначение</th><th>Сумма</th><th>Доля</th><th>Комментарий</th><th>Данные</th></tr></thead>
                    <tbody>${rows}<tr class="cash-total-row cash-tooltip-host" tabindex="0"><td><div>ИТОГО${this.cashTooltip(cashTipData)}</div></td><td class="cash-number-cell"><span class="cash-number-value">${esc(cashFormatted.val)}</span> <span class="cash-number-unit">${esc(cashFormatted.u)}</span></td><td class="cash-share-cell"><span class="cash-share-value">${total ? '100%' : '—'}</span>${total ? '<span class="cash-share-track" aria-hidden="true"><span class="cash-share-fill" style="width:100%"></span></span>' : ''}${card.coverage === 'partial' ? '<span class="cash-coverage-note">Частичный охват</span>' : ''}</td><td></td><td></td></tr></tbody>
                </table></div>
                <div id="ki-cash-chart-title" class="modal-section-title">Поступления / выплаты</div>
                <div id="ki-cash-line-chart" class="cash-line-chart"></div>
                <div class="cash-chart-legend" aria-label="Легенда графика">
                    <span><i class="cash-chart-dot" style="background:#111827"></i>Денежные средства</span>
                    <span><i class="cash-chart-dot" style="background:#DB2777"></i>Денежные средства АПГ</span>
                    <span><i class="cash-chart-dot" style="background:#2563EB"></i>Поступления: СНд + ОД</span>
                    <span><i class="cash-chart-dot" style="background:#059669"></i>Выплаты: СНк + ОК</span>
                    <span><i class="cash-chart-dot" style="background:rgba(34,197,94,.2)"></i>Интервал между значениями</span>
                    <span><i class="cash-chart-dot" style="background:#7C3AED"></i>Поступления АПГ</span>
                    <span><i class="cash-chart-dot" style="background:#D97706"></i>Выплаты АПГ</span>
                </div>
                ${isCashDz ? '' : `<div class="modal-section-title">Показатели ликвидности — наведите для методики и требований к данным</div>
                <div class="cash-metrics-grid">
                    ${this.cashMetricCard('Денежные средства', `${cashFormatted.val} ${cashFormatted.u}`, `${dateText} · ${coverageText}`, cashTipData, card.value == null, '__main__')}
                    ${this.cashMetricCard('Изменение остатка', deltaText, comparisonText, changeTipData, delta === null, '', delta < 0 ? 'cash-value-negative' : '')}
                    ${this.cashMetricCard('Коэффициент текущей ликвидности', 'Нужны данные', 'оборотные активы / краткосрочные обязательства', currentLiquidityData, true)}
                    ${this.cashMetricCard('Коэффициент абсолютной ликвидности', 'Нужны данные', 'деньги / краткосрочные обязательства', absoluteLiquidityData, true)}
                    ${this.cashMetricCard('Cash Runway', 'Нужны данные', 'остаток / среднедневные внешние платежи', runwayData, true)}
                    ${this.cashMetricCard('Обеспеченность топливом', 'Нужны данные', 'натуральный запас / среднедневной расход', fuelData, true)}
                </div>`}
                `;
        },

        toggleCashDetails(button) {
            const targetId = button && button.getAttribute('aria-controls');
            const table = targetId && document.getElementById(targetId);
            if (!button || !table) return;
            const expanded = button.getAttribute('aria-expanded') === 'true';
            button.setAttribute('aria-expanded', String(!expanded));
            table.hidden = expanded;
        },

        renderCashChartModes(card) {
            if (!card) return;
            const isCashDz = card.id === 'cash_dz';
            const defaultModes = isCashDz
                ? ['debtor-current', 'advances-current', 'debtor-apg', 'advances-apg']
                : ['cash-current', 'cash-apg'];
            if (!state.modalChart.cashModes || !state.modalChart.cashModes.size) {
                state.modalChart.cashModes = new Set(defaultModes);
            } else if (state.modalChart.cashModes.has('current') ||
                    state.modalChart.cashModes.has('apg')) {
                state.modalChart.cashModes = new Set(
                    [...state.modalChart.cashModes].map(mode =>
                        mode === 'current' ? 'cumulative-current'
                            : mode === 'apg' ? 'cumulative-apg' : mode));
            }
            const selected = state.modalChart.cashModes;
            const modeLabels = isCashDz ? {
                'debtor-current': 'ДЗ потребителей',
                'advances-current': 'Авансы потребителей',
                'debtor-apg': 'ДЗ потребителей АПГ',
                'advances-apg': 'Авансы потребителей АПГ'
            } : {
                'cash-current': 'Денежные средства',
                'cash-apg': 'Денежные средства АПГ',
                'cumulative-current': 'Поступления / выплаты',
                'cumulative-apg': 'Поступления / выплаты АПГ'
            };
            const title = document.getElementById('ki-cash-chart-title');
            if (title) title.textContent = [...selected]
                .map(mode => modeLabels[mode]).filter(Boolean).join(' и ');
            const root = document.getElementById('ki-cash-modal-content');
            if (root && root.querySelectorAll) {
                root.querySelectorAll('[data-cash-chart-mode]').forEach(tile => {
                    const active = tile.dataset &&
                        selected.has(tile.dataset.cashChartMode);
                    if (tile.classList) {
                        tile.classList.toggle('cash-chart-mode-selected', active);
                    }
                    if (tile.setAttribute) {
                        tile.setAttribute('aria-pressed', active ? 'true' : 'false');
                    }
                });
            }
            if (isCashDz) {
                this.renderCashLineChart(
                    [],
                    [],
                    [],
                    [],
                    'ki-cash-line-chart',
                    [
                        {
                            id: 'debtor-current',
                            series: selected.has('debtor-current')
                                ? card.consumer_series || card.series || [] : [],
                            color: '#111827',
                            label: 'ДЗ потребителей',
                            className: 'cash-chart-balance',
                            connectWithoutAccounts: true
                        },
                        {
                            id: 'debtor-apg',
                            series: selected.has('debtor-apg')
                                ? card.previous_year_consumer_series || [] : [],
                            color: '#DB2777',
                            dash: ' stroke-dasharray="6 4"',
                            label: 'ДЗ потребителей АПГ',
                            suffix: ' АПГ',
                            className: 'cash-chart-balance-apg',
                            connectWithoutAccounts: true
                        },
                        {
                            id: 'advances-current',
                            series: selected.has('advances-current')
                                ? card.advances_series || [] : [],
                            color: '#2563EB',
                            label: 'Авансы потребителей',
                            className: 'cash-chart-advances',
                            connectWithoutAccounts: true
                        },
                        {
                            id: 'advances-apg',
                            series: selected.has('advances-apg')
                                ? card.previous_year_advances_series || [] : [],
                            color: '#059669',
                            dash: ' stroke-dasharray="6 4"',
                            label: 'Авансы потребителей АПГ',
                            suffix: ' АПГ',
                            className: 'cash-chart-advances-apg',
                            connectWithoutAccounts: true
                        }
                    ]);
            } else {
                this.renderCashLineChart(
                    selected.has('cumulative-current') ? card.series || [] : [],
                    selected.has('cumulative-apg') ? card.previous_year_series || [] : [],
                    selected.has('cash-current') ? card.series || [] : [],
                    selected.has('cash-apg') ? card.previous_year_series || [] : []);
            }
        },

        toggleCashChartMode(mode) {
            const card = state.data && Array.isArray(state.data.cards)
                ? state.data.cards.find(item => item.id === state.modalCardId &&
                    isCashMetric(item.id)) : null;
            const allowedModes = [
                'cash-current', 'cash-apg',
                'cumulative-current', 'cumulative-apg'
            ];
            if (card && card.id === 'cash_dz') {
                const dzModes = [
                    'debtor-current', 'advances-current',
                    'debtor-apg', 'advances-apg'
                ];
                if (!dzModes.includes(mode)) return;
                if (!state.modalChart.cashModes ||
                        !dzModes.some(item => state.modalChart.cashModes.has(item))) {
                    state.modalChart.cashModes = new Set(dzModes);
                }
                if (state.modalChart.cashModes.has(mode)) {
                    if (state.modalChart.cashModes.size > 1) {
                        state.modalChart.cashModes.delete(mode);
                    }
                } else {
                    state.modalChart.cashModes.add(mode);
                }
                this.renderCashChartModes(card);
                return;
            }
            if (!card || !allowedModes.includes(mode)) return;
            if (!state.modalChart.cashModes) {
                state.modalChart.cashModes = new Set(['cash-current', 'cash-apg']);
            }
            if (state.modalChart.cashModes.has(mode)) {
                if (state.modalChart.cashModes.size > 1) {
                    state.modalChart.cashModes.delete(mode);
                }
            } else {
                state.modalChart.cashModes.add(mode);
            }
            this.renderCashChartModes(card);
        },

        renderCashLineChart(series, apgSeries = [], cashSeries = [], cashApgSeries = [],
                containerId = 'ki-cash-line-chart', extraBalanceSeries = []) {
            const container = document.getElementById(containerId);
            if (!container) return;
            const mapPoints = items => items.map((item, index) => ({
                ...item, index,
                debit: Number.isFinite(item.debit_value) ? item.debit_value / 1000000 : null,
                credit: Number.isFinite(item.credit_value) ? item.credit_value / 1000000 : null,
                accounts: item.cumulative_accounts || [],
                missing_accounts: item.cumulative_missing_accounts || ['50', '51', '52', '55'],
                coverage: item.cumulative_coverage || 'missing',
                sources: item.cumulative_sources || {},
                reasons: item.cumulative_reasons || []
            }));
            const mapBalancePoints = items => items.map((item, index) => ({
                ...item, index,
                balance: Number.isFinite(item.value) ? item.value / 1000000 : null,
                accounts: item.accounts || [],
                missing_accounts: item.missing_accounts || ['50', '51', '52', '55'],
                coverage: item.coverage || 'missing',
                sources: item.sources || {},
                reasons: []
            }));
            const groups = [
                {
                    id: 'current',
                    points: mapPoints(series),
                    debitColor: '#2563EB',
                    creditColor: '#059669',
                    areaColor: '#22C55E',
                    dash: '',
                    suffix: ''
                },
                {
                    id: 'apg',
                    points: mapPoints(apgSeries),
                    debitColor: '#7C3AED',
                    creditColor: '#D97706',
                    areaColor: '#A78BFA',
                    dash: ' stroke-dasharray="6 4"',
                    suffix: ' АПГ'
                }
            ].filter(group => group.points.length);
            const balanceGroups = [
                {
                    id: 'cash-current',
                    points: mapBalancePoints(cashSeries),
                    color: '#111827',
                    dash: '',
                    suffix: ''
                },
                {
                    id: 'cash-apg',
                    points: mapBalancePoints(cashApgSeries),
                    color: '#DB2777',
                    dash: ' stroke-dasharray="6 4"',
                    suffix: ' АПГ'
                },
                ...extraBalanceSeries.map(group => ({
                    ...group,
                    points: mapBalancePoints(group.series || []),
                    dash: group.dash || '',
                    suffix: group.suffix || ''
                }))
            ].filter(group => group.points.length);
            const allGroups = [...groups, ...balanceGroups];
            const valid = [
                ...groups.flatMap(group => group.points).flatMap(item =>
                    [item.debit, item.credit]),
                ...balanceGroups.flatMap(group => group.points).map(item =>
                    item.balance)
            ].filter(Number.isFinite);
            const validPointCount = groups.reduce((count, group) =>
                count + group.points.filter(item =>
                    Number.isFinite(item.debit) || Number.isFinite(item.credit)).length, 0)
                + balanceGroups.reduce((count, group) =>
                    count + group.points.filter(item =>
                        Number.isFinite(item.balance)).length, 0);
            if (!allGroups.length || !valid.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const values = valid;
            const width = 820, height = 220, left = 48, right = 15, top = 16, bottom = 36;
            let min = Math.min(0, ...values), max = Math.max(0, ...values);
            if (min === max) max = min + 1;
            const plotWidth = width - left - right;
            const xFor = (index, pointCount) => left + (
                pointCount > 1 ? index / (pointCount - 1) : 0.5) * plotWidth;
            const y = value => top + (max - value) * (height - top - bottom) / (max - min);
            const sameAccounts = (first, second) =>
                first && second && first.accounts && first.accounts.length &&
                second.accounts && second.accounts.length &&
                [...first.accounts].sort().join(',') === [...second.accounts].sort().join(',');
            const segmentsFor = (points, keys = ['debit', 'credit'],
                    connectWithoutAccounts = false) => {
                const timeline = chartTimeline(points);
                const consecutive = (previous, current) => (
                    timeline.calendar && containerId === 'ki-cash-line-chart'
                        ? timeline.day(current.index) - timeline.day(previous.index) === 1
                        : current.index - previous.index === 1
                );
                const segments = [];
                points.forEach((point, index) => {
                    if (!keys.every(key => Number.isFinite(point[key]))) return;
                    const entry = {point, index};
                    const segment = segments[segments.length - 1];
                    const previous = segment && segment[segment.length - 1];
                    if (!previous || !consecutive(previous, entry) ||
                            (!connectWithoutAccounts &&
                                !sameAccounts(previous.point, point))) {
                        segments.push([entry]);
                    } else {
                        segment.push(entry);
                    }
                });
                return segments;
            };
            const curve = (segment, key, x, reverse = false, move = true) => {
                const entries = reverse ? [...segment].reverse() : segment;
                if (!entries.length) return '';
                const first = entries[0];
                let path = `${move ? 'M' : 'L'} ${x(first.index).toFixed(1)} ${y(first.point[key]).toFixed(1)}`;
                for (let index = 1; index < entries.length; index += 1) {
                    const previous = entries[index - 1];
                    const current = entries[index];
                    const x0 = x(previous.index), x1 = x(current.index);
                    const y0 = y(previous.point[key]), y1 = y(current.point[key]);
                    const control = (x1 - x0) / 3;
                    path += ` C ${(x0 + control).toFixed(1)} ${y0.toFixed(1)}, ${(x1 - control).toFixed(1)} ${y1.toFixed(1)}, ${x1.toFixed(1)} ${y1.toFixed(1)}`;
                }
                return path;
            };
            let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика денежных средств">`;
            if (validPointCount === 1) svg += '<text x="70" y="14" font-size="10" fill="#6B7280">Одна точка данных: для линии нужны данные внутри периода</text>';
            [min, (min + max) / 2, max].forEach(value => {
                const py = y(value);
                svg += `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#E5E7EB" stroke-dasharray="3 3"/>
                    <text x="${left-6}" y="${py+4}" text-anchor="end" font-size="10" fill="#6B7280">${esc(value.toFixed(1))}</text>`;
            });
            const labelGroup = allGroups.find(group =>
                group.id === 'current' || group.id === 'cash-current') || allGroups[0];
            const labelStep = Math.max(1, Math.ceil(labelGroup.points.length / 8));
            labelGroup.points.forEach((item, index) => {
                if (index % labelStep === 0 || index === labelGroup.points.length - 1) {
                    const px = xFor(index, labelGroup.points.length);
                    svg += `<line class="cash-chart-grid-vertical" x1="${px}" y1="${top}" x2="${px}" y2="${height-bottom}" stroke="#E5E7EB"/>`;
                }
            });
            groups.forEach(group => {
                const x = index => xFor(index, group.points.length);
                const segments = segmentsFor(group.points);
                const debitPath = segments.map(segment =>
                    curve(segment, 'debit', x)).join(' ');
                const creditPath = segments.map(segment =>
                    curve(segment, 'credit', x)).join(' ');
                segments.filter(segment => segment.length > 1).forEach(segment => {
                    const area = `${curve(segment, 'debit', x)} ${curve(segment, 'credit', x, true, false)} Z`;
                    svg += `<path d="${area}" class="cash-chart-area cash-chart-period-${group.id}" fill="${group.areaColor}" fill-opacity="${group.id === 'apg' ? '0.10' : '0.16'}"/>`;
                });
                svg += `<path d="${debitPath}" class="cash-chart-debit${group.id === 'apg' ? '-apg' : ''}" fill="none" stroke="${group.debitColor}" stroke-width="1.5"${group.dash} stroke-linecap="round" stroke-linejoin="round"/>`;
                svg += `<path d="${creditPath}" class="cash-chart-credit${group.id === 'apg' ? '-apg' : ''}" fill="none" stroke="${group.creditColor}" stroke-width="1.5"${group.dash} stroke-linecap="round" stroke-linejoin="round"/>`;
                group.points.forEach((item, index) => {
                    if (Number.isFinite(item.debit)) {
                        svg += `<circle class="cash-chart-hit-target" data-kind="debit" data-index="${index}" data-period="${group.id}" cx="${x(index)}" cy="${y(item.debit)}" r="7" fill="transparent" stroke="none" tabindex="0" aria-label="${esc(`Поступления${group.suffix} на ${item.date || item.label}. ${this.cashCoverageText(item)}`)}"/>`;
                    }
                    if (Number.isFinite(item.credit)) {
                        svg += `<circle class="cash-chart-hit-target" data-kind="credit" data-index="${index}" data-period="${group.id}" cx="${x(index)}" cy="${y(item.credit)}" r="7" fill="transparent" stroke="none" tabindex="0" aria-label="${esc(`Выплаты${group.suffix} на ${item.date || item.label}. ${this.cashCoverageText(item)}`)}"/>`;
                    }
                });
            });
            balanceGroups.forEach(group => {
                const x = index => xFor(index, group.points.length);
                const segments = segmentsFor(
                    group.points, ['balance'], group.connectWithoutAccounts);
                const balancePath = segments.map(segment =>
                    curve(segment, 'balance', x)).join(' ');
                const balanceClass = group.className ||
                    `cash-chart-balance${group.id === 'cash-apg' ? '-apg' : ''}`;
                svg += `<path d="${balancePath}" class="${balanceClass}" fill="none" stroke="${group.color}" stroke-width="2"${group.dash} stroke-linecap="round" stroke-linejoin="round"/>`;
                group.points.forEach((item, index) => {
                    if (!Number.isFinite(item.balance)) return;
                    const balanceLabel = group.label || `Денежные средства${group.suffix}`;
                    svg += `<circle class="cash-chart-hit-target" data-kind="balance" data-index="${index}" data-period="${group.id}" cx="${x(index)}" cy="${y(item.balance)}" r="7" fill="transparent" stroke="none" tabindex="0" aria-label="${esc(`${balanceLabel} на ${item.date || item.label}. ${this.cashCoverageText(item)}`)}"/>`;
                });
            });
            labelGroup.points.forEach((item, index) => {
                if (index % labelStep === 0 || index === labelGroup.points.length - 1)
                    svg += `<text x="${xFor(index, labelGroup.points.length)}" y="${height-10}" text-anchor="middle" font-size="9" fill="#6B7280">${esc(item.label)}</text>`;
            });
            container.innerHTML = svg + '</svg>';
            const pointTipData = point => {
                const index = Number(point.dataset.index);
                const isCredit = point.dataset.kind === 'credit';
                const isBalance = point.dataset.kind === 'balance';
                const group = allGroups.find(item =>
                    item.id === (point.dataset.period || 'current')) || allGroups[0];
                const item = group.points[index];
                const suffix = group.suffix;
                const coverageText = this.cashCoverageText(item);
                const missingText = item.reasons && item.reasons.length
                    ? ` ${item.reasons.join('; ')}.` : '';
                if (isBalance) return {
                    title: `${group.label || `Денежные средства${suffix}`} — ${item.date || item.label}`,
                    description: group.label
                        ? `Фактическое значение «${group.label}» на точную дату.`
                        : `Фактический остаток денежных средств${suffix} на точную дату.`,
                    formula: group.label
                        ? (group.id.startsWith('advances')
                            ? 'Авансы потребителей = max(СКк(62.02) − СКд(62.02), 0)'
                            : 'ДЗ потребителей = max(СКд(62.01) − СКк(62.01), 0)')
                        : 'Денежные средства = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                    detail: `${coverageText}. Отсутствующие счета не принимаются за ноль.${missingText}`,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.balance.toFixed(2)} млн ₽`,
                    deviation: group.label
                        ? `Фактическое значение «${group.label}»`
                        : `Фактический остаток денежных средств${suffix}`,
                    source: group.label
                        ? (group.id.startsWith('advances')
                            ? 'счёт 62.02 по данным ОСВ'
                            : 'счёт 62.01 по данным ОСВ')
                        : 'точные дневные конечные сальдо ОСВ по счетам 50, 51, 52 и 55'
                };
                return isCredit ? {
                    title: `Выплаты${suffix} — ${item.date}`,
                    description: 'Кредитовое сальдо на начало выбранного периода и кредитовые обороты нарастающим итогом.',
                    formula: 'Кредит = Σ(СНк + накопленный ОК) по счетам 50, 51, 52, 55',
                    detail: `${coverageText}. Отсутствующие счета не принимаются за ноль.${missingText}`,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.credit.toFixed(2)} млн ₽`,
                    deviation: 'Фактическое накопленное кредитовое значение',
                    source: 'начальные кредитовые сальдо и точные дневные кредитовые обороты ОСВ'
                } : {
                    title: `Поступления${suffix} — ${item.date || item.label}`,
                    description: 'Дебетовое сальдо на начало выбранного периода и дебетовые обороты нарастающим итогом.',
                    formula: 'Дебет = Σ(СНд + накопленный ОД) по счетам 50, 51, 52, 55',
                    detail: `${coverageText}. Отсутствующие счета не принимаются за ноль.${missingText}`,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.debit.toFixed(2)} млн ₽`,
                    deviation: 'Фактическое накопленное дебетовое значение',
                    source: 'начальные дебетовые сальдо и точные дневные дебетовые обороты ОСВ'
                };
            };
            container.querySelectorAll('.cash-chart-hit-target').forEach(point => {
                point.addEventListener('pointerenter', event => {
                    this._richTooltipHost = point;
                    this.showRichTooltip(pointTipData(point), event.clientX, event.clientY);
                });
                point.addEventListener('pointermove', event => this.positionRichTooltip(event.clientX, event.clientY));
                point.addEventListener('pointerleave', () => this.hideRichTooltip());
                point.addEventListener('focus', () => {
                    const rect = point.getBoundingClientRect();
                    this._richTooltipHost = point;
                    this.showRichTooltip(pointTipData(point), rect.left + rect.width / 2, rect.bottom);
                });
                point.addEventListener('blur', () => this.hideRichTooltip());
            });
        },

        openModal(cardId) {
            let card = state.data && Array.isArray(state.data.cards) ?
                state.data.cards.find(c => c.id === cardId) : null;
            if (!card) return;

            // A modal can be reused without an intervening close (for example
            // keyboard navigation between cards). Never let a delayed render
            // target the next modal's chart container.
            this.clearChartJobs();
            state.modalCardId = cardId;
            // Chart selection is local to the card.  Breakdown indexes are
            // intentionally reused by different cards, so a single global
            // pinned set would leak a pin from one modal into another.
            if (!state.modalCharts[cardId]) {
                state.modalCharts[cardId] = {
                    activeId: null, pinnedIds: new Set(), gapMode: 1,
                    timelineMode: 'week',
                    chartContainer: null
                };
            }
            state.modalChart = state.modalCharts[cardId];
            if (!state.modalChart.timelineMode) state.modalChart.timelineMode = 'week';
            this.hideModalSwitchStatus();
            
            state.lastFocused = document.activeElement;
            
            document.getElementById('ki-modal-title').textContent = card.title;
            this.updateModalSubtitle();
            
            const def = cardDefinitions.find(d => d.id === cardId);
            document.getElementById('ki-modal-icon').innerHTML = def ? def.icon : '<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';

            const cashContent = document.getElementById('ki-cash-modal-content');
            const revenueContent = document.getElementById('ki-revenue-modal-content');
            const collectionContent = document.getElementById('ki-collection-modal-content');
            const arContent = document.getElementById('ki-ar-modal-content');
            const apContent = document.getElementById('ki-ap-modal-content');
            const cogsContent = document.getElementById('ki-cogs-modal-content');
            const profitContent = document.getElementById('ki-profit-modal-content');
            const creditsContent = document.getElementById('ki-credits-modal-content');
            const salaryContent = document.getElementById('ki-salary-modal-content');
            const capexContent = document.getElementById('ki-capex-modal-content');
            const genericContent = document.getElementById('ki-generic-modal-content');
            const commonHasBreakdownSeries = card.breakdown &&
                card.breakdown.some(b => b.series && b.series.length > 0);
            const commonHasDetailsSeries = card.details &&
                card.details.some(d => d.series && d.series.length > 0);
            const commonHasAnalyticsSeries = card.analytics &&
                card.analytics.some(d => d.series && d.series.length > 0);
            const commonHasComponentsSeries = card.components &&
                card.components.some(d => d.series && d.series.length > 0);
            const commonHasMainSeries = card.series && card.series.length > 0;
            salaryContent.style.display = 'none';
            capexContent.style.display = 'none';
            if (isCashMetric(cardId)) {
                genericContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                cashContent.style.display = 'block';
                this.renderCashModal(card);
                this.renderCashChartModes(card);
            } else if (cardId === 'revenue') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                revenueContent.style.display = 'block';
                this.renderRevenueModal(card);
            } else if (cardId === 'collection') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                collectionContent.style.display = 'block';
                this.renderCollectionModal(card);
            } else if (cardId === 'ar') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                arContent.style.display = 'block';
                this.renderArModal(card);
            } else if (cardId === 'ap') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                apContent.style.display = 'block';
                this.renderApModal(card);
            } else if (cardId === 'cogs') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                cogsContent.style.display = 'block';
                this.renderCogsModal(card);
            } else if (cardId === 'profit') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                creditsContent.style.display = 'none';
                profitContent.style.display = 'block';
                this.renderProfitModal(card);
            } else if (cardId === 'credits') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'block';
                this.renderCreditsModal(card);
            } else if (cardId === 'salary') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                salaryContent.style.display = 'block';
                this.renderSalaryModal(card);
            } else if (cardId === 'capex') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                salaryContent.style.display = 'none';
                capexContent.style.display = 'block';
                this.renderCapexModal(card);
                this.renderCapexChartModes(card);
            } else {
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cogsContent.style.display = 'none';
                profitContent.style.display = 'none';
                creditsContent.style.display = 'none';
                genericContent.style.display = 'block';
            const baseTooltip = this.metricTooltipData(card);
            const rb = document.getElementById('ki-modal-reason-banner');
            if (card.reason) {
                rb.style.display = 'block';
                rb.innerHTML = `<strong>Внимание:</strong> ${esc(card.reason)}`;
            } else {
                rb.style.display = 'none';
            }
            
            document.getElementById('ki-modal-formula').textContent = card.formula || 'Нет данных';
            document.getElementById('ki-modal-source').textContent = card.source ? `Источник: ${card.source}` : '';


            const bdCont = document.getElementById('ki-modal-breakdown-container');
            const bdTitle = document.getElementById('ki-modal-breakdown-title');
            const chCont = document.getElementById('ki-modal-chart-container');
            const chTitle = document.getElementById('ki-modal-chart-title');

            const hasBreakdownSeries = card.breakdown && card.breakdown.some(b => b.series && b.series.length > 0);
            const hasDetailsSeries = card.details && card.details.some(d => d.series && d.series.length > 0);
            const hasMainSeries = card.series && card.series.length > 0;

            if (state.mode === 'operational' && (hasMainSeries || hasBreakdownSeries ||
                hasDetailsSeries || (card.analytics || []).some(item => item.series && item.series.length) ||
                (card.components || []).some(item => item.series && item.series.length))) {
                const metricItems = metricSeriesItems(card);
                const validIds = new Set(metricItems.map(item => item.id));

                if (!validIds.has(state.modalChart.activeId)) {
                    state.modalChart.activeId = metricItems.length ? metricItems[0].id : null;
                }

                const newPinned = new Set();
                state.modalChart.pinnedIds.forEach(id => {
                    if (validIds.has(id)) newPinned.add(id);
                });
                state.modalChart.pinnedIds = newPinned;
                this.renderMetricModalInteractive(card, bdCont, bdTitle, chCont, chTitle);

            } else {
                bdTitle.style.display = 'none';
                bdCont.style.display = 'none';
                if (hasMainSeries) {
                    chTitle.style.display = 'block';
                    chCont.style.display = 'block';
                    this.scheduleChart('ki-modal-chart-container',
                        () => this.renderBarChart(card.series, card.unit, 'ki-modal-chart-container', card), 50);
                } else {
                    chTitle.style.display = 'none';
                    chCont.style.display = 'none';
                }
            }

            const dtCont = document.getElementById('ki-modal-details-container');
            const dtTitle = document.getElementById('ki-modal-details-title');
            if (card.details && card.details.length > 0) {
                dtTitle.style.display = 'block';
                let dtHtml = '<div style="display:flex; flex-direction:column; gap:8px;">';
                card.details.forEach(d => {
                    const u = d.unit || card.unit;
                    const f = this.formatValue(d.value, u);
                    const tooltip = this.cashTooltip(this.metricTooltipData(card, {
                        title: `${card.title}: ${d.label}`,
                        fact: d.value == null ? 'Н/Д' : `${f.val} ${f.u}`,
                        detail: d.reason || baseTooltip.detail,
                        required: d.value == null ? (d.reason || baseTooltip.required) : null,
                        deviation: d.value == null ? 'Расчёт невозможен' : 'Дополнительный факт',
                        deviationClass: d.value == null ? 'missing' : 'good'
                    }));
                    dtHtml += `<div class="waterfall-bar cash-tooltip-host" tabindex="0">
                        <div class="waterfall-label">${esc(d.label)}</div>
                        <div class="waterfall-value">${esc(f.val)} ${esc(f.u)}</div>
                        ${d.reason ? `<div style="flex:1;text-align:right;font-size:10px; color:#EF4444;">${esc(d.reason)}</div>` : ''}
                        ${tooltip}
                    </div>`;
                });
                dtHtml += '</div>';
                dtCont.innerHTML = dtHtml;
                dtCont.style.display = 'block';
            } else {
                dtTitle.style.display = 'none';
                dtCont.style.display = 'none';
            }
            }

            const activeContainer = [
                cashContent, revenueContent, collectionContent, arContent,
                apContent, cogsContent, profitContent, creditsContent,
                salaryContent, capexContent, genericContent
            ].find(el => el.style.display === 'block');
            if (activeContainer && state.mode === 'operational') {
                const validIds = new Set(metricSeriesItems(card).map(item => item.id));
                if (!validIds.has(state.modalChart.activeId)) {
                    state.modalChart.activeId = validIds.values().next().value || null;
                }
                state.modalChart.pinnedIds = new Set(
                    [...state.modalChart.pinnedIds].filter(id => validIds.has(id)));
                if (activeContainer === genericContent) {
                    this.updateMetricTileStates(genericContent);
                } else if (activeContainer !== cashContent &&
                        activeContainer !== revenueContent &&
                        activeContainer !== collectionContent &&
                        activeContainer !== capexContent) {
                    this.mountSharedDynamics(card, activeContainer);
                    this.bindMetricTiles(card, activeContainer);
                }
            }
            if (window.KI_Layout) {
                if (activeContainer) {
                    window.KI_Layout.onModalOpen(cardId, activeContainer, 'osv');
                }
            }

            const overlay = document.getElementById('ki-modal');
            this.enhanceTables(overlay);
            overlay.style.visibility = 'visible';
            overlay.setAttribute('aria-hidden', 'false');
            overlay.classList.add('visible');
            requestAnimationFrame(() => {
                if (overlay.classList.contains('visible')) {
                    overlay.querySelector('.modal-close').focus();
                }
            });
        },
        
        closeModal(force = false) {
            if (window.KI_Layout) {
                if (typeof window.KI_Layout.canClose === 'function' &&
                    !window.KI_Layout.canClose() && !force) {
                    return;
                }
                if (!force && window.KI_Layout.isDirty()) {
                    if (!confirm('Есть несохраненные изменения раскладки. Закрыть и потерять изменения?')) {
                        return;
                    }
                }
                window.KI_Layout.onModalClose(force);
            }
            // A close during an in-place switch stops modal reopening, but
            // deliberately leaves the current-company data request running.
            state.companySwitching = false;
            const overlay = document.getElementById('ki-modal');
            overlay.classList.remove('visible');
            overlay.style.visibility = 'hidden';
            overlay.setAttribute('aria-hidden', 'true');
            state.modalCardId = null;
            state.pendingModalCardId = null;
            state.companySwitchPeriodFallback = null;
            if (window.KI_AnnualApp && window.KI_AnnualApp.state) {
                window.KI_AnnualApp.state.modalCardId = null;
            }
            this.hideModalSwitchStatus();
            this.clearChartJobs();
            if (state.modalChart) state.modalChart.chartContainer = null;
            if (state.lastFocused) state.lastFocused.focus();
        },
        
        renderBarChart(series, unit, containerId, card) {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            const points = series.map(s => {
                let v = s.value;
                if (hasNumericValue(v) && unit === 'RUB') v = v / 1000000;
                return { ...s, scaledValue: v };
            });
            const valid = points.filter(s => hasNumericValue(s.scaledValue));
            
            if(valid.length === 0) {
                container.innerHTML = '<div class="chart-empty">Нет данных для графика<br>(недостаточно периодов)</div>';
                return;
            }
            
            const width = container.clientWidth || 600;
            const height = container.clientHeight || 260;
            const padT = 20, padB = 30, padL = 40, padR = 10;
            const w = width - padL - padR;
            const h = height - padT - padB;
            
            const vals = valid.map(s => s.scaledValue);
            let minVal = Math.min(0, ...vals);
            let maxVal = Math.max(0, ...vals);
            if (maxVal === minVal) {
                if (maxVal === 0) maxVal = 1;
                else { maxVal += Math.abs(maxVal)*0.1; minVal -= Math.abs(minVal)*0.1; }
            }
            const range = maxVal - minVal;
            const timeline = chartTimeline(points);
            const x = index => padL + (timeline.calendar
                ? timeline.x(index) * w
                : timeline.x(index, w));
            
            let svg = `<svg viewBox="0 0 ${width} ${height}" style="width:100%; height:100%; display:block;">`;
            if (valid.length === 1) svg += '<text x="60" y="14" font-size="10" fill="#6B7280">Одна точка данных: для линии нужны данные внутри периода</text>';
            
            const zeroY = padT + h - ((0 - minVal) / range) * h;
            
            [minVal, minVal + range/2, maxVal].forEach(val => {
                const y = padT + h - ((val - minVal) / range) * h;
                const fmt = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(val);
                svg += `<text x="${padL-5}" y="${y+4}" font-size="10" fill="#9CA3AF" text-anchor="end">${esc(fmt)}</text>`;
                svg += `<line x1="${padL}" y1="${y}" x2="${width-padR}" y2="${y}" stroke="#E5E7EB" stroke-dasharray="2,2"/>`;
            });
            
            svg += `<line x1="${padL}" y1="${zeroY}" x2="${width-padR}" y2="${zeroY}" stroke="#9CA3AF" stroke-width="1"/>`;
            const y = value => padT + h - ((Number(value) - minVal) / range) * h;
            const paths = chartLinePaths(
                points, x, y, point => point.scaledValue, timeline);
            svg += `<path d="${paths.solid}" class="metric-chart-line-solid" fill="none" stroke="#3B82F6" stroke-width="2" stroke-linejoin="round"/>`;
            if (paths.dashed) {
                svg += `<path d="${paths.dashed}" class="metric-chart-line-gap" fill="none" stroke="#3B82F6" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"/>`;
                svg += `<text class="chart-gap-help" x="${width-padR}" y="14" font-size="10" fill="#6B7280" text-anchor="end">Пунктир — пропуск данных, не факт</text>`;
            }
            const labelStep = Math.max(1, Math.ceil(series.length / Math.max(2, Math.floor(w / 95))));
            points.forEach((s, i) => {
                const cx = x(i);
                let v = s.value;
                if (v !== null && unit === 'RUB') v = v / 1000000;
                
                if(hasNumericValue(v)) {
                    const py = y(v);
                    const fill = v >= 0 ? '#3B82F6' : '#EF4444';
                    svg += `<circle class="metric-chart-point" data-index="${i}" cx="${cx}" cy="${py}" r="4" fill="${fill}" tabindex="0" aria-label="${esc(s.label)}: ${esc(v)}"/>`;
                    
                    const fmtVal = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(v);
                    if (series.length <= 12) svg += `<text x="${cx}" y="${py - 8}" font-size="10" fill="#4B5563" text-anchor="middle" font-weight="bold">${esc(fmtVal)}</text>`;
                }
                if (i % labelStep === 0 || i === series.length - 1) svg += `<text x="${cx}" y="${height - 5}" font-size="10" fill="#6B7280" text-anchor="middle">${esc(s.label)}</text>`;
            });
            
            svg += '</svg>';
            container.innerHTML = svg;
            container.querySelectorAll('.metric-chart-point').forEach(point => {
                const tooltipData = () => {
                    const item = series[Number(point.dataset.index)];
                    const formatted = this.formatValue(item.value, unit);
                    return this.metricTooltipData(card, {
                        title: `${card.title} — ${item.label}`,
                        fact: `${formatted.val} ${formatted.u}`,
                        deviation: 'Факт за выбранный период динамики'
                    });
                };
                point.addEventListener('pointerenter', event => {
                    this._richTooltipHost = point;
                    this.showRichTooltip(tooltipData(), event.clientX, event.clientY);
                });
                point.addEventListener('pointermove', event => this.positionRichTooltip(event.clientX, event.clientY));
                point.addEventListener('pointerleave', () => this.hideRichTooltip());
                point.addEventListener('focus', () => {
                    const rect = point.getBoundingClientRect();
                    this._richTooltipHost = point;
                    this.showRichTooltip(tooltipData(), rect.left + rect.width / 2, rect.bottom);
                });
                point.addEventListener('blur', () => this.hideRichTooltip());
            });
        },
        
        showLoading() {
            document.getElementById('ki-loading').classList.add('active');
        },
        
        hideLoading() {
            document.getElementById('ki-loading').classList.remove('active');
        }
    };

    window.KI_App = App;
    if (typeof window.addEventListener === 'function') {
        window.addEventListener('message', event => {
            if (!event || event.origin !== window.location.origin ||
                event.source !== window.parent || !event.data ||
                event.data.type !== 'ki-company-switch') return;
            App.switchCompany(event.data.company);
        });
    }
    document.addEventListener('DOMContentLoaded', () => App.init());
})();
