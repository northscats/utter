/* annual_key_indicators.js */
(function() {
    const config = window.KI_CONFIG || (typeof KI_CONFIG !== 'undefined' ? KI_CONFIG : null);
    const missingYearMessage = 'Запрошенный год отсутствует среди принятых данных';
    
    const state = {
        active: false,
        year: null,
        selectionStart: null,
        rangeStart: null,
        rangeEnd: null,
        data: null,
        modalCardId: null,
        companySwitchYearFallback: null,
        reqId: 0,
        chartJob: null
    };

    let abortController = null;

    const esc = (str) => {
        if (str === null || str === undefined) return '';
        return String(str).replace(/[&<>'"]/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        })[m]);
    };

    const AnnualApp = {
        state: state,

        hasRange() {
            return Number.isInteger(Number(state.rangeStart)) &&
                Number.isInteger(Number(state.rangeEnd)) &&
                Number(state.rangeStart) !== Number(state.rangeEnd);
        },

        payloadRange(data = state.data) {
            const from = data && Number(data.year_from);
            const to = data && Number(data.year_to);
            if (!Number.isInteger(from) || !Number.isInteger(to)) return null;
            return {
                start: Math.min(from, to),
                end: Math.max(from, to)
            };
        },

        requestedSelection() {
            return this.hasRange() ? {
                year: Number(state.year),
                rangeStart: Number(state.rangeStart),
                rangeEnd: Number(state.rangeEnd)
            } : {
                year: Number(state.year),
                rangeStart: null,
                rangeEnd: null
            };
        },

        syncLocation() {
            if (!window.location || !window.history ||
                typeof window.history.replaceState !== 'function') return;
            const locationUrl = new URL(window.location.href || window.location);
            if (state.rangeStart !== null && state.rangeEnd !== null &&
                this.hasRange()) {
                locationUrl.searchParams.set('year_from', state.rangeStart);
                locationUrl.searchParams.set('year_to', state.rangeEnd);
                locationUrl.searchParams.set('year', state.rangeEnd);
            } else if (Number.isInteger(Number(state.year))) {
                locationUrl.searchParams.set('year', state.year);
                locationUrl.searchParams.delete('year_from');
                locationUrl.searchParams.delete('year_to');
            } else {
                locationUrl.searchParams.delete('year');
                locationUrl.searchParams.delete('year_from');
                locationUrl.searchParams.delete('year_to');
            }
            window.history.replaceState({}, '', locationUrl);
        },
        
        init() {
            const urlParams = new URLSearchParams(window.location.search);
            const year = parseInt(urlParams.get('year'), 10);
            const yearFrom = parseInt(urlParams.get('year_from'), 10);
            const yearTo = parseInt(urlParams.get('year_to'), 10);
            if (Number.isInteger(yearFrom) && Number.isInteger(yearTo) &&
                yearFrom !== yearTo) {
                state.rangeStart = Math.min(yearFrom, yearTo);
                state.rangeEnd = Math.max(yearFrom, yearTo);
                // The legacy `year` parameter is always the range end.
                state.year = state.rangeEnd;
            } else if (Number.isInteger(year)) {
                state.year = year;
            } else if (Number.isInteger(yearFrom)) {
                state.year = yearFrom;
            }
            if (urlParams.get('mode') === 'annual') {
                this.activate();
            }
            this.bindEvents();
        },
        
        bindEvents() {
            document.getElementById('ki-annual-cards-grid').addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') {
                    const card = e.target.closest('.kpi-card.clickable');
                    if (card) {
                        e.preventDefault();
                        this.openModal(card.dataset.metric);
                    }
                }
            });
        },

        activate() {
            state.active = true;
            state.data = null;
            state.chartJob = null;
            this.fetchData();
        },

        deactivate() {
            state.active = false;
            state.reqId++;
            state.chartJob = null;
            if (abortController) abortController.abort();
            if (window.KI_App) window.KI_App.invalidateAiReport('loading');
        },

        switchCompany(next, companySwitchToken) {
            if (!state.active) return;
            state.reqId += 1;
            if (abortController) abortController.abort();
            state.data = null;
            state.chartJob = null;
            state.companySwitchYearFallback = null;
            this.fetchData(companySwitchToken);
        },

        cancelCompanySwitch() {
            state.reqId += 1;
            if (abortController) abortController.abort();
        },

        onYearChange(yearStr) {
            const year = parseInt(yearStr, 10);
            if (!Number.isInteger(year)) return;

            if (state.rangeStart !== null && state.rangeEnd !== null) {
                state.rangeStart = null;
                state.rangeEnd = null;
                state.selectionStart = year;
                state.year = year;
            } else if (state.selectionStart === null) {
                state.selectionStart = year;
                state.rangeStart = null;
                state.rangeEnd = null;
                state.year = year;
            } else if (state.selectionStart === year) {
                state.year = year;
            } else {
                state.rangeStart = Math.min(state.selectionStart, year);
                state.rangeEnd = Math.max(state.selectionStart, year);
                state.selectionStart = null;
                state.year = state.rangeEnd;
            }

            this.syncLocation();
            // Do not leave cards, charts, or the AI tab showing the previous
            // selection while the new interval is being calculated.
            if (window.KI_App &&
                    typeof window.KI_App.prepareOpenModalRefresh === 'function') {
                window.KI_App.prepareOpenModalRefresh(
                    'Загрузка годовых данных за выбранный период…');
            }
            state.data = null;
            state.chartJob = null;
            this.fetchData();
        },

        isYearSelected(year) {
            if (state.rangeStart !== null && state.rangeEnd !== null) {
                return year >= state.rangeStart && year <= state.rangeEnd;
            }
            return year === state.year;
        },

        selectedTimelineYears() {
            const timeline = (state.data && state.data.timeline_years) || [];
            if (state.rangeStart !== null && state.rangeEnd !== null) {
                return timeline.filter(year =>
                    year >= state.rangeStart && year <= state.rangeEnd
                );
            }
            return timeline.filter(year => year === state.year);
        },

        retry() {
            this.fetchData();
        },

        consumeCompanySwitchYearFallback() {
            const fallback = state.companySwitchYearFallback;
            state.companySwitchYearFallback = null;
            return fallback;
        },

        async fetchData(companySwitchToken = null, skipRequestedYear = false,
                        fallbackRequestedSelection = null) {
            if (!state.active) return;
            if (window.KI_App) window.KI_App.invalidateAiReport('loading');
            if (window.KI_App) window.KI_App.showLoading();
            document.getElementById('ki-error').style.display = 'none';
            
            if (abortController) abortController.abort();
            abortController = new AbortController();
            const reqId = ++state.reqId;
            
            try {
                if (!config || !config.annualDataUrl) throw new Error('Не настроен адрес источника годовых показателей.');
                let url = config.annualDataUrl;
                const requestedYear = state.year;
                const requestedYearNumber = Number(requestedYear);
                const requestedRange = this.hasRange() ? {
                    start: Number(state.rangeStart),
                    end: Number(state.rangeEnd)
                } : null;
                // A first click on a year is an anchor for the next click.
                // Keep it through the settled single-year response so two
                // ordinary button clicks can form a range.
                const pendingSelectionStart = Number.isInteger(
                    Number(state.selectionStart)) ? Number(state.selectionStart) : null;
                const requestedSelection = {
                    year: requestedYearNumber,
                    rangeStart: requestedRange ? requestedRange.start : null,
                    rangeEnd: requestedRange ? requestedRange.end : null
                };
                if (!skipRequestedYear && requestedRange) {
                    url += `?year=${encodeURIComponent(requestedRange.end)}` +
                        `&year_from=${encodeURIComponent(requestedRange.start)}` +
                        `&year_to=${encodeURIComponent(requestedRange.end)}`;
                } else if (requestedYear && !skipRequestedYear) {
                    url += `?year=${encodeURIComponent(requestedYear)}`;
                }
                
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
                const isMissingRequestedYear = data &&
                    (data.error === missingYearMessage || data.message === missingYearMessage);
                if (!response.ok && companySwitchToken !== null &&
                    !skipRequestedYear && Number.isInteger(requestedYearNumber) &&
                    isMissingRequestedYear) {
                    if (reqId !== state.reqId ||
                        (window.KI_App &&
                         companySwitchToken !== window.KI_App.state.companySwitchToken)) return;
                    return this.fetchData(companySwitchToken, true, requestedSelection);
                }
                
                if (!response.ok) {
                    throw new Error(responseError || `Ошибка сервера: ${response.status}`);
                }
                
                if (reqId !== state.reqId ||
                    (companySwitchToken !== null && window.KI_App &&
                     companySwitchToken !== window.KI_App.state.companySwitchToken)) return;
                
                state.data = data;
                const responseRange = this.payloadRange(data);
                const fallback = fallbackRequestedSelection === null ? null :
                    (typeof fallbackRequestedSelection === 'object'
                        ? fallbackRequestedSelection
                        : {year: Number(fallbackRequestedSelection)});
                if (requestedRange && responseRange) {
                    state.rangeStart = responseRange.start;
                    state.rangeEnd = responseRange.end;
                    state.year = Number.isInteger(Number(data.year))
                        ? Number(data.year) : responseRange.end;
                    state.selectionStart = null;
                } else if (requestedRange) {
                    // Keep the requested interval when talking to an older
                    // endpoint that has not started returning range metadata.
                    state.rangeStart = requestedRange.start;
                    state.rangeEnd = requestedRange.end;
                    state.year = Number.isInteger(Number(data.year))
                        ? Number(data.year) : requestedRange.end;
                    state.selectionStart = null;
                } else {
                    state.rangeStart = null;
                    state.rangeEnd = null;
                    state.selectionStart = pendingSelectionStart;
                    state.year = Number.isInteger(Number(data.year))
                        ? Number(data.year) : requestedYear;
                }
                if (fallback &&
                    Number.isInteger(Number(data.year)) &&
                    (fallback.rangeStart !== null ||
                     Number(data.year) !== Number(fallback.year)) &&
                    window.KI_App && window.KI_App.state.companySwitching) {
                    const actualYear = Number(data.year);
                    state.rangeStart = null;
                    state.rangeEnd = null;
                    state.selectionStart = actualYear;
                    state.year = actualYear;
                    state.companySwitchYearFallback = {
                        requestedYear: Number(fallback.year),
                        actualYear
                    };
                    if (fallback.rangeStart !== null &&
                        fallback.rangeEnd !== null) {
                        state.companySwitchYearFallback.requestedRange = {
                            yearFrom: Number(fallback.rangeStart),
                            yearTo: Number(fallback.rangeEnd)
                        };
                    }
                }
                if (requestedRange && responseRange) this.syncLocation();
                if (fallback) this.syncLocation();
                if (window.KI_App) window.KI_App.setAiReport('annual', data);
                this.render();
                if (companySwitchToken !== null && window.KI_App) {
                    window.KI_App.finishCompanySwitch(companySwitchToken);
                }
            } catch (e) {
                if (reqId !== state.reqId || e.name === 'AbortError' ||
                    (companySwitchToken !== null && window.KI_App &&
                     companySwitchToken !== window.KI_App.state.companySwitchToken)) return;
                if (companySwitchToken !== null && window.KI_App &&
                    window.KI_App.state.companySwitching) {
                    window.KI_App.invalidateAiReport('error', e.message);
                    window.KI_App.showCompanySwitchError(companySwitchToken, e.message);
                    return;
                }
                if (window.KI_App) window.KI_App.invalidateAiReport('error', e.message);
                document.getElementById('ki-error-text').textContent = e.message;
                document.getElementById('ki-error').style.display = 'flex';
            } finally {
                if (reqId === state.reqId && window.KI_App &&
                    (companySwitchToken === null ||
                     companySwitchToken === window.KI_App.state.companySwitchToken)) {
                    window.KI_App.hideLoading();
                }
            }
        },

        formatValue(v, unit, isUpperBound = false) {
            if (v === null || v === undefined) return { val: 'Н/Д', u: '' };
            let num = v;
            let u = unit;
            if (unit === 'RUB') {
                num = v / 1000000;
                u = 'млн ₽';
            } else if (unit === 'ratio') {
                u = 'коэф.';
            } else if (unit === 'percent') {
                u = '%';
            }
            if (num === 0) return { val: isUpperBound ? '< 0' : '0', u: u };
            let formatted = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(num);
            return { 
                val: isUpperBound ? `< ${formatted}` : formatted, 
                u: u 
            };
        },

        periodLabel(data = state.data) {
            if (data && data.period_label) return String(data.period_label);
            const responseRange = this.payloadRange(data);
            if (responseRange && responseRange.start !== responseRange.end) {
                return `${responseRange.start}–${responseRange.end}`;
            }
            if (data && Number.isInteger(Number(data.year))) {
                return String(Number(data.year));
            }
            return this.displayedPeriodLabel();
        },

        displayedPeriodLabel() {
            if (state.data && state.data.period_label) {
                return String(state.data.period_label);
            }
            const responseRange = this.payloadRange();
            if (responseRange && responseRange.start !== responseRange.end) {
                return `${responseRange.start}–${responseRange.end}`;
            }
            if (state.rangeStart !== null && state.rangeEnd !== null) {
                return state.rangeStart === state.rangeEnd
                    ? String(state.rangeStart)
                    : `${state.rangeStart}–${state.rangeEnd}`;
            }
            return state.year == null ? 'Период не указан' : String(state.year);
        },

        previousPeriodLabel() {
            const data = state.data || {};
            const previous = data.previous_range || data.previous_period || {};
            if (data.previous_period_label) return String(data.previous_period_label);
            if (data.previous_range_label) return String(data.previous_range_label);
            if (previous.period_label) return String(previous.period_label);
            if (previous.label) return String(previous.label);
            const from = Number(previous.year_from !== undefined
                ? previous.year_from : data.previous_year_from);
            const to = Number(previous.year_to !== undefined
                ? previous.year_to : data.previous_year_to);
            if (Number.isInteger(from) && Number.isInteger(to)) {
                return from === to ? String(from) : `${Math.min(from, to)}–${Math.max(from, to)}`;
            }
            return null;
        },

        cardPeriodLabel(card) {
            const raw = card && card.period_label ? String(card.period_label) : '';
            const normalized = raw.trim().toLowerCase();
            const interval = this.periodLabel();
            const endYear = Number((state.data || {}).year_to ||
                (state.data || {}).year);
            const kind = String((card && card.kind) || '').toLowerCase();
            const genericFlow = normalized === 'за год' ||
                normalized === 'за календарный год' ||
                (kind === 'flow' && !raw);
            const genericBalance = normalized === 'на 31.12' ||
                normalized === 'на конец года' ||
                normalized === 'на конец периода' ||
                (kind === 'balance' && !/\b(?:19|20)\d{2}\b/.test(raw));
            const genericRatio = normalized === 'по итогам года' ||
                normalized === 'по итогам периода' ||
                (kind === 'ratio' && !raw);
            const intervalIsRange = this.hasRange() || !!this.payloadRange();

            // Keep the legacy single-year presentation intact when the
            // server still sends kind-only labels.
            if (!intervalIsRange && raw && Number.isInteger(endYear) &&
                (genericFlow || genericBalance || genericRatio)) {
                return `${raw} ${endYear}`;
            }

            // New payloads carry the complete label.  Older payloads carry
            // only the kind marker, which must be expanded without placing a
            // multi-year interval after a balance's closing date.
            if (genericBalance && Number.isInteger(endYear)) {
                return `на 31.12.${endYear}`;
            }
            if (genericFlow) return `за ${interval}`;
            if (genericRatio) return `по итогам ${interval}`;
            return raw || interval;
        },

        provenanceText(card) {
            if (!card || !card.provenance) return 'Нет данных';

            const flatten = value => {
                if (Array.isArray(value)) return value.reduce(
                    (all, item) => all.concat(flatten(item)), []);
                return value && typeof value === 'object' ? [value] : [];
            };
            let sources = flatten(card.provenance);
            const byYear = [];
            if (card.provenance_by_year &&
                typeof card.provenance_by_year === 'object') {
                Object.keys(card.provenance_by_year).sort().forEach(year => {
                    flatten(card.provenance_by_year[year]).forEach(source => {
                        byYear.push(source.year === undefined &&
                            source.year_from === undefined
                            ? Object.assign({year}, source) : source);
                    });
                });
            }
            // Aggregated range payloads may flatten `provenance` while
            // retaining the year association in provenance_by_year.
            if (byYear.length && sources.every(source =>
                source.year === undefined && source.year_from === undefined)) {
                sources = byYear;
            }
            if (!sources.length) return 'Нет данных';

            const normalize = (source, fallbackYear = null) => {
                if (!source || typeof source !== 'object') return null;
                const nested = source.provenance &&
                    typeof source.provenance === 'object' &&
                    !Array.isArray(source.provenance)
                    ? source.provenance : source;
                const year = source.year !== undefined ? source.year :
                    (source.year_from !== undefined ? source.year_from :
                        (nested.year !== undefined ? nested.year :
                            (nested.year_from !== undefined
                                ? nested.year_from : fallbackYear)));
                const component = source.component !== undefined
                    ? source.component : nested.component;
                const normalized = Object.assign({}, nested);
                normalized.source_label = source.source_label || nested.source_label;
                if (year !== null && year !== undefined) normalized.year = year;
                if (component !== null && component !== undefined) {
                    normalized.component = component;
                }
                return normalized;
            };
            const seen = new Set();
            const lines = sources.map(source => {
                source = normalize(source);
                if (!source) return '';
                const year = source.year !== undefined ? source.year :
                    source.year_from;
                const filename = source.filename || source.file ||
                    source.original_filename;
                const sheet = source.sheet || source.sheet_name;
                const cell = source.cell || source.cell_ref;
                const component = source.component;
                const details = [
                    source.source_label || (component ? String(component) : ''),
                    filename ? `Файл: ${filename}` : '',
                    sheet ? `Лист: ${sheet}` : '',
                    cell ? `Ячейка: ${cell}` : ''
                ].filter(Boolean).join(', ') || 'Нет данных';
                const line = year === undefined || year === null
                    ? details : `${year} год: ${details}`;
                const marker = `${year === undefined ? '' : year}|${details}`;
                if (seen.has(marker)) return '';
                seen.add(marker);
                return line;
            }).filter(Boolean);
            return lines.length ? lines.join('\n') : 'Нет данных';
        },

        render() {
            if (!state.data || !state.active) return;
            
            const subtitle = document.getElementById('ki-topbar-subtitle');
            subtitle.textContent = `${config.companyName} · ${this.displayedPeriodLabel()}`;
            
            this.renderYearSelector();
            
            const grid = document.getElementById('ki-annual-cards-grid');
            const emptyState = document.getElementById('ki-annual-empty-state');
            const warnings = document.getElementById('ki-annual-warnings');
            const quality = document.getElementById('ki-annual-quality');
            
            if (!state.data.years || state.data.years.length === 0) {
                grid.style.display = 'none';
                warnings.style.display = 'none';
                quality.style.display = 'none';
                emptyState.style.display = 'block';
                
                let importUrl = config.historicalImportUrl || '/';
                emptyState.innerHTML = `
                    <div class="ki-empty-annual">
                        <h3>Годовые данные 1С не загружены</h3>
                        <p>Загрузите годовую XLSX финансовую аналитику 1С и подтвердите предварительный просмотр для активации.</p>
                        <a href="${esc(importUrl)}" class="btn-import">Перейти к годовой аналитике 1С</a>
                    </div>
                `;
                return;
            } else {
                emptyState.style.display = 'none';
                grid.style.display = 'grid';
                quality.style.display = 'block';
            }
            
            this.renderWarnings();
            this.renderCards();
            this.renderQuality();
            if (window.KI_App &&
                    typeof window.KI_App.refreshOpenModalAfterData === 'function') {
                window.KI_App.refreshOpenModalAfterData('annual');
            }
        },

        renderYearSelector() {
            const sel = document.getElementById('ki-annual-year-select');
            const buttons = document.getElementById('ki-annual-year-buttons');
            if (!sel && !buttons) return;
            if (state.data.years && state.data.years.length > 0) {
                // Keep the old select hidden for compatibility with cached pages;
                // the visible control is a direct button for every available year.
                if (sel) sel.style.display = 'none';
                if (buttons) {
                    buttons.style.display = 'flex';
                    buttons.innerHTML = state.data.years.map(y => {
                        const selected = this.isYearSelected(y);
                        const edge = y === state.rangeStart || y === state.rangeEnd;
                        return `<button type="button" class="annual-year-btn${selected ? ' active' : ''}${edge ? ' range-edge' : ''}" ` +
                            `data-year="${y}" aria-pressed="${selected ? 'true' : 'false'}" ` +
                            `onclick="window.KI_AnnualApp.onYearChange(${y})">${y}</button>`;
                    }).join('');
                } else if (sel) {
                    sel.style.display = 'inline-block';
                    sel.innerHTML = state.data.years.map(y =>
                        `<option value="${y}" ${y === state.data.year ? 'selected' : ''}>${y} год</option>`
                    ).join('');
                }
            } else {
                if (sel) sel.style.display = 'none';
                if (buttons) {
                    buttons.style.display = 'none';
                    buttons.innerHTML = '';
                }
            }
        },

        renderWarnings() {
            const c = document.getElementById('ki-annual-warnings');
            if (!state.data.warnings || state.data.warnings.length === 0) {
                c.style.display = 'none';
                return;
            }
            let html = `<details class="warnings-details">
                <summary>Предупреждения и ограничения (${state.data.warnings.length})</summary>
                <div class="warnings-content"><ul>`;
            
            state.data.warnings.forEach(w => {
                html += `<li>${esc(w)}</li>`;
            });
            html += `</ul></div></details>`;
            
            c.innerHTML = html;
            c.style.display = 'block';
        },

        reportComparisonHtml(card, detailed = false) {
            const comparison = card.report_comparison;
            if (!comparison) return '';
            // More precision than the headline is essential: two rounded
            // headline values may look equal while their difference is not.
            const number = value => typeof value === 'number' && Number.isFinite(value)
                ? new Intl.NumberFormat('ru-RU', {maximumSignificantDigits: 15}).format(value)
                : 'Нет данных';
            const unit = comparison.unit === 'percent' ? '%' : 'коэф.';
            const deltaUnit = comparison.unit === 'percent' ? 'п.п.' : unit;
            let html = `<div style="font-size:12px;line-height:1.5;margin-top:12px">
                <strong>Сверка с годовой книгой${comparison.year ? ` · ${esc(comparison.year)}` : ''}</strong>
                <div>Независимый расчёт: ${esc(number(comparison.calculated_value))} ${esc(unit)}</div>
                <div>В годовой книге: ${esc(number(comparison.reported_value))} ${esc(unit)}</div>`;
            if (typeof comparison.delta === 'number' && Number.isFinite(comparison.delta)) {
                html += `<div>Разница (расчёт − книга): ${esc(number(comparison.delta))} ${esc(deltaUnit)}</div>`;
            }
            html += `<div>${esc(comparison.message || 'Сравнение недоступно')}</div>`;
            if (detailed) {
                html += `<div style="margin-top:8px"><strong>Источник коэффициента книги:</strong> ${esc(this.provenanceText({provenance: comparison.reported_provenance}))}</div>
                    <div><strong>Источники независимого расчёта:</strong> ${esc(this.provenanceText({provenance: comparison.calculated_provenance}))}</div>
                    <div>Сверка справочная: значения, рейтинг и принятые версии не изменяются.</div>`;
            } else {
                html += '<div>Источники и ограничения — в детализации.</div>';
            }
            return html + '</div>';
        },

        renderCards() {
            const grid = document.getElementById('ki-annual-cards-grid');
            if (!state.data.cards) return;
            
            let html = '';
            
            // Preserve the original order; additional annual indicators follow it.
            const order = ['revenue', 'cogs', 'net_profit', 'profitability', 'cash', 'ar', 'ap', 'equity', 'assets', 'current_liquidity'];
            (state.data.cards || []).forEach(card => {
                if (card && card.id && !order.includes(card.id)) order.push(card.id);
            });
            
            order.forEach(id => {
                const card = state.data.cards.find(c => c.id === id);
                if (!card) return;
                
                let isNull = (card.value === null || card.value === undefined);
                let formatted = this.formatValue(card.value, card.unit, card.upper_bound);
                
                let deltaHtml = '';
                let hasDelta = card.delta !== null && card.delta !== undefined;
                const rangePayload = this.hasRange() || !!this.payloadRange();
                let comparisonNote = card.comparison_note ||
                    card.comparison_label ||
                    (rangePayload ? card.previous_period_label : '') || '';
                if (!comparisonNote && hasDelta &&
                    rangePayload) {
                    const previousLabel = this.previousPeriodLabel();
                    if (previousLabel) comparisonNote = `к ${previousLabel}`;
                }
                comparisonNote = comparisonNote ? ` · ${comparisonNote}` : '';

                if (!isNull && hasDelta) {
                    let dClass = 'neutral';
                    // A movement in debt or liquidity is not a risk assessment.
                    if (!card.calculation_note && card.delta > 0) dClass = 'up';
                    if (!card.calculation_note && card.delta < 0) dClass = 'down';
                    
                    let displayVal = card.delta;
                    let numVal = parseFloat(card.delta);
                    let u = card.unit;
                    
                    if (card.unit === 'RUB') {
                        if (!isNaN(numVal)) {
                            displayVal = (numVal > 0 ? '+' : '') + new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(numVal / 1000000);
                        }
                        u = 'млн ₽';
                    } else if (card.unit === 'ratio') {
                        if (!isNaN(numVal)) {
                            displayVal = (numVal > 0 ? '+' : '') + new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(numVal);
                        }
                        u = 'коэф.';
                    } else if (card.unit === 'percent') {
                         if (!isNaN(numVal)) {
                            displayVal = (numVal > 0 ? '+' : '') + new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 2}).format(numVal);
                        }
                        u = 'п.п.';
                    }
                    
                    if (card.previous_upper_bound) {
                        displayVal = '≈ ' + displayVal;
                    }
                    
                    let deltaPct = card.delta_percent !== null && card.delta_percent !== undefined
                        ? ` (${card.delta_percent > 0 ? '+' : ''}${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(card.delta_percent)}%)` : '';
                    
                    deltaHtml = `<div class="kpi-delta ${dClass}">
                        ${esc(displayVal)} ${esc(u)}${esc(deltaPct)}${esc(comparisonNote)}
                    </div>`;
                } else if (isNull && card.missing_reason) {
                     deltaHtml = `<div class="kpi-delta neutral">${esc(card.missing_reason)}</div>`;
                } else if (!isNull && card.comparison_note) {
                     deltaHtml = `<div class="kpi-delta neutral">${esc(card.comparison_note)}</div>`;
                } else if (!isNull) {
                    const previousLabel = this.previousPeriodLabel();
                    const hasRange = this.hasRange() || !!this.payloadRange();
                    if (previousLabel) {
                        deltaHtml = `<div class="kpi-delta neutral">Нет данных за ${esc(previousLabel)}</div>`;
                    } else if (!hasRange && state.data.previous_year) {
                        // Keep the single-year wording for legacy payloads;
                        // never describe an interval as only its last year.
                        deltaHtml = `<div class="kpi-delta neutral">Нет данных за ${state.data.previous_year}</div>`;
                    }
                }
                
                const ariaLabel = `${card.title}. Значение: ${formatted.val} ${formatted.u}.`;
                const statusClass = 'grey';
                
                html += `
                <div class="kpi-card status-${statusClass} clickable" data-metric="${esc(id)}" tabindex="0" onclick="window.KI_AnnualApp.openModal('${esc(id)}')" role="button" aria-label="${esc(ariaLabel)}">
                  <div class="kpi-label">${esc(card.title)} <span class="status-dot ${statusClass}" style="opacity:0.3"></span></div>
                  <div class="kpi-value">${esc(formatted.val)}<span class="kpi-unit">${esc(formatted.u)}</span></div>
                  <div style="font-size:12px;color:#6B7280;margin-bottom:8px">${esc(this.cardPeriodLabel(card))}</div>
                  ${deltaHtml}
                  ${this.reportComparisonHtml(card)}
                </div>
                `;
            });
            grid.innerHTML = html;
        },

        renderQuality() {
            const container = document.getElementById('ki-annual-quality');
            let html = '';
            
            if (state.data.sources && state.data.sources.length > 0) {
                let qHtml = '<div class="quality-box"><div class="quality-title">Загруженные источники (годовые)</div>';
                const selectedYears = new Set(this.selectedTimelineYears());
                state.data.sources.filter(src => selectedYears.has(src.year)).forEach(src => {
                    qHtml += `<div style="font-size:12px; margin-bottom:4px;">
                        <strong>${esc(src.year)} год:</strong> ${esc(src.filename)} (версия ${esc(src.version)}, загружено ${esc(src.uploaded_at)})
                    </div>`;
                });
                qHtml += '</div>';
                html += qHtml;
            }
            
            if (state.data.methodology && state.data.methodology.length > 0) {
                let mHtml = '<div class="quality-box"><div class="quality-title">Методология расчета</div>';
                state.data.methodology.forEach(m => {
                    mHtml += `<div style="margin-bottom:12px;">
                        <div style="font-size:12px; font-weight:bold; margin-bottom:4px;">Методология</div>
                        <div style="font-size:12px; color:#4B5563; line-height:1.4;">${esc(m)}</div>
                    </div>`;
                });
                mHtml += '</div>';
                html += mHtml;
            }
            
            container.innerHTML = html;
        },

        openModal(cardId) {
            const card = state.data && Array.isArray(state.data.cards) ?
                state.data.cards.find(c => c.id === cardId) : null;
            if (!card) return;

            state.modalCardId = cardId;
            if (window.KI_App && typeof window.KI_App.hideModalSwitchStatus === 'function') {
                window.KI_App.hideModalSwitchStatus();
            }
            
            if (window.KI_App) {
                window.KI_App.state.lastFocused = document.activeElement;
            }
            
            document.getElementById('ki-modal-title').textContent = card.title;
            document.getElementById('ki-modal-subtitle').textContent =
                `Детализация по данным Отчета · ${this.displayedPeriodLabel()}`;
            
            const defIcon = '<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';
            document.getElementById('ki-modal-icon').innerHTML = defIcon;

            const rb = document.getElementById('ki-modal-reason-banner');
            if (card.missing_reason) {
                rb.style.display = 'block';
                rb.innerHTML = `<strong>Внимание:</strong> ${esc(card.missing_reason)}`;
            } else {
                rb.style.display = 'none';
            }
            
            const provText = this.provenanceText(card);
            document.getElementById('ki-modal-formula').textContent = [
                card.formula || 'Показатель из годовой отчетности 1С',
                card.calculation_note,
                card.correction_note
            ].filter(Boolean).join('. ');
            document.getElementById('ki-modal-source').textContent = provText;
            
            const breakdownTitle = document.getElementById('ki-modal-breakdown-title');
            const breakdownContainer = document.getElementById('ki-modal-breakdown-container');
            breakdownTitle.style.display = card.report_comparison ? 'block' : 'none';
            breakdownTitle.textContent = 'Сверка коэффициента с годовой книгой';
            breakdownContainer.style.display = card.report_comparison ? 'block' : 'none';
            breakdownContainer.innerHTML = this.reportComparisonHtml(card, true);
            
            const chCont = document.getElementById('ki-modal-chart-container');
            const chTitle = document.getElementById('ki-modal-chart-title');
            state.chartJob = null;
            const selectedYears = this.selectedTimelineYears();
            if (card.series && card.series.length > 0 && selectedYears.length > 0) {
                chTitle.style.display = 'block';
                this.renderModalChart(card.series, selectedYears, card.unit, 'ki-modal-chart-container');
                chCont.style.display = 'block';
            } else {
                chTitle.style.display = 'none';
                chCont.style.display = 'none';
            }
            
            document.getElementById('ki-modal-details-title').style.display = 'none';
            document.getElementById('ki-modal-details-container').style.display = 'none';
            
            if (window.KI_Layout) {
                const activeContainer = document.getElementById('ki-generic-modal-content');
                window.KI_Layout.onModalOpen(cardId, activeContainer, 'annual');
            }

            const modal = document.getElementById('ki-modal');
            if (window.KI_App &&
                    typeof window.KI_App.activateModalHeaderControls === 'function') {
                window.KI_App.activateModalHeaderControls();
            }
            modal.style.visibility = 'visible';
            modal.classList.add('visible');
            modal.setAttribute('aria-hidden', 'false');
            
            setTimeout(() => {
                const btn = modal.querySelector('.modal-close');
                if (btn) btn.focus();
            }, 50);
        },

        renderModalChart(series, timeline_years, unit, containerId) {
            const container = document.getElementById(containerId);
            if (!container) return;
            state.chartJob = {
                series: series.slice(),
                timelineYears: timeline_years.slice(),
                unit,
                containerId
            };
            
            const dataMap = new Map();
            series.forEach(s => dataMap.set(s.year, s));
            
            const width = container.clientWidth || 600;
            const height = 260;
            const padT = 20, padB = 40, padL = 60, padR = 20;
            const w = width - padL - padR;
            const h = height - padT - padB;
            
            let minVal = 0;
            let maxVal = 0;
            timeline_years.forEach(year => {
                const pt = dataMap.get(year);
                if (pt && pt.value !== null) {
                    let v = unit === 'RUB' ? pt.value / 1000000 : pt.value;
                    if (v > maxVal) maxVal = v;
                    if (v < minVal) minVal = v;
                }
            });
            
            if (maxVal === 0 && minVal === 0) { maxVal = 1; }
            const range = maxVal - minVal;
            const scaleMin = minVal < 0 ? minVal - range * 0.1 : 0;
            const scaleMax = maxVal + range * 0.1;
            const scaleRange = scaleMax - scaleMin;
            
            const numPoints = timeline_years.length;
            const step = numPoints > 1 ? w / (numPoints - 1) : w;
            
            let svg = `<svg role="img" aria-label="Динамика по годам" viewBox="0 0 ${width} ${height}" style="width:100%; height:100%;">`;
            svg += `<text x="${padL}" y="12" font-size="10" fill="#6B7280">${esc(unit === 'RUB' ? 'млн ₽' : unit === 'percent' ? '%' : 'коэф.')}</text>`;
            
            [0, 0.5, 1].forEach(frac => {
                const v = scaleMin + scaleRange * frac;
                const y = padT + h - ((v - scaleMin) / scaleRange) * h;
                const fmt = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(v);
                svg += `<text x="${padL-10}" y="${y+4}" font-size="10" fill="#9CA3AF" text-anchor="end">${esc(fmt)}</text>`;
                svg += `<line x1="${padL}" y1="${y}" x2="${width-padR}" y2="${y}" stroke="#E5E7EB" stroke-dasharray="2,2"/>`;
            });
            
            let pathD = '';
            let isFirst = true;
            let circles = '';
            
            timeline_years.forEach((year, i) => {
                const cx = numPoints === 1 ? padL + w / 2 : padL + i * step;
                
                svg += `<text x="${cx}" y="${height - 15}" font-size="10" fill="#6B7280" text-anchor="middle">${year}</text>`;
                
                const pt = dataMap.get(year);
                if (pt && pt.upper_bound && pt.value !== null) {
                    const bound = this.formatValue(pt.value, unit, true);
                    circles += `<text x="${cx}" y="${padT + 16}" font-size="10" fill="#6B7280" text-anchor="middle">${esc(bound.val)}</text>`;
                    isFirst = true;
                    return;
                }
                if (pt && pt.value !== null) {
                    let v = unit === 'RUB' ? pt.value / 1000000 : pt.value;
                    const cy = padT + h - ((v - scaleMin) / scaleRange) * h;
                    
                    if (isFirst) {
                        pathD += `M${cx},${cy} `;
                        isFirst = false;
                    } else {
                        const prevPt = dataMap.get(timeline_years[i-1]);
                        if (prevPt && prevPt.value !== null) {
                            pathD += `L${cx},${cy} `;
                        } else {
                            pathD += `M${cx},${cy} `;
                        }
                    }
                    
                    circles += `<circle cx="${cx}" cy="${cy}" r="4" fill="#fff" stroke="#3B82F6" stroke-width="2"/>`;
                    if (pt.upper_bound) {
                         circles += `<text x="${cx}" y="${cy - 10}" font-size="10" fill="#6B7280" text-anchor="middle">&lt;</text>`;
                    }
                }
            });
            
            if (pathD) {
                svg += `<path d="${pathD}" fill="none" stroke="#3B82F6" stroke-width="2"/>`;
            }
            svg += circles;
            
            svg += '</svg>';
            container.innerHTML = svg;
        },

        rerenderVisibleCharts() {
            const job = state.chartJob;
            if (!job) return;
            const container = document.getElementById(job.containerId);
            if (!container || !this.isVisibleElement(container)) return;
            this.renderModalChart(job.series, job.timelineYears, job.unit, job.containerId);
        },

        clearChartJobs() {
            state.chartJob = null;
        },

        isVisibleElement(element) {
            let current = element;
            while (current) {
                if (current.hidden || (current.style && current.style.display === 'none')) return false;
                current = current.parentElement;
            }
            return true;
        }
    };

    window.KI_AnnualApp = AnnualApp;
    document.addEventListener('DOMContentLoaded', () => {
        if (window.KI_AnnualApp) {
            window.KI_AnnualApp.init();
        }
    });
})();