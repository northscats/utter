/* key_indicators.js */
(function() {
    // Also accept the lexical configuration from an already cached template.
    const config = window.KI_CONFIG || (typeof KI_CONFIG !== 'undefined' ? KI_CONFIG : null);
    
    const state = {
        period: 'year',
        date: null,
        dateFrom: null,
        quarterAnchor: null,
        data: null,
        lastFocused: null,
        reqId: 0,
        chartJobs: {}
    };
    
    const svgIcon = (path) => `<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><path d="${path}"/></svg>`;

    const cardDefinitions = [
        { id: 'cash', icon: svgIcon('M12 2v20M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6') },
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

    const esc = (str) => {
        if (str === null || str === undefined) return '';
        return String(str).replace(/[&<>'"]/g, m => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        })[m]);
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
            document.getElementById('ki-modal-subtitle').textContent =
                `Детализация по данным ОСВ · ${this.displayedPeriodLabel()}`;
        },
        scheduleChart(containerId, renderer, delay = 50) {
            state.chartJobs[containerId] = renderer;
            if (typeof setTimeout !== 'function') {
                renderer();
                return;
            }
            setTimeout(() => {
                if (state.chartJobs[containerId] === renderer) renderer();
            }, delay);
        },

        rerenderVisibleCharts() {
            Object.keys(state.chartJobs).forEach(containerId => {
                const container = document.getElementById(containerId);
                if (container && this.isVisibleElement(container)) {
                    state.chartJobs[containerId]();
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

            if (initialMode === 'annual') {
                state.mode = 'annual';
                document.querySelectorAll('.mode-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.mode === 'annual');
                });
                document.getElementById('ki-period-selector').style.display = 'none';
                document.getElementById('ki-date-select').style.display = 'none';
                document.getElementById('ki-date-from-select').style.display = 'none';
                document.getElementById('ki-quarter-buttons').style.display = 'none';
                document.querySelector('.tabs-bar').style.display = 'none';
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
        
        switchMode(mode) {
            if (state.mode === mode) return;
            state.mode = mode;

            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === mode);
            });

            const url = new URL(window.location);
            url.searchParams.set('mode', mode);
            window.history.replaceState({}, '', url);

            if (mode === 'annual') {
                state.reqId++;
                if (abortController) abortController.abort();
                document.getElementById('ki-period-selector').style.display = 'none';
                document.getElementById('ki-date-select').style.display = 'none';
                document.getElementById('ki-date-from-select').style.display = 'none';
                document.getElementById('ki-quarter-buttons').style.display = 'none';
                document.querySelector('.tabs-bar').style.display = 'none';
                document.getElementById('tab-analytics').classList.add('active');
                if (document.getElementById('tab-recommendations')) document.getElementById('tab-recommendations').classList.remove('active');
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

        bindEvents() {
            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.addEventListener('click', e => {
                    this.switchMode(e.target.dataset.mode);
                });
            });

            document.querySelectorAll('.period-btn').forEach(btn => {
                btn.addEventListener('click', e => {
                    this.onPeriodChange(e.target.dataset.period);
                });
            });

            document.getElementById('ki-quarter-buttons').addEventListener('click', e => {
                const button = e.target.closest('.quarter-btn');
                if (button && !button.disabled) this.onQuarterChange(button.dataset.value);
            });
            
            const modal = document.getElementById('ki-modal');
            modal.setAttribute('tabindex', '-1');
            modal.setAttribute('aria-labelledby', 'ki-modal-title');
            modal.setAttribute('aria-hidden', 'true');
            // Capture at document level: initial focus may still be on the
            // opener while the dialog's visibility transition is beginning.
            document.addEventListener('keydown', function(e) {
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
        },
        
        switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
            document.querySelector(`.tab-btn[data-tab="${tabName}"]`).classList.add('active');
            document.getElementById(`tab-${tabName}`).classList.add('active');
        },
        
        onPeriodChange(kind) {
            if (state.period === kind) return;
            state.period = kind;
            state.date = null;
            state.dateFrom = null;
            state.quarterAnchor = null;
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
            this.fetchData();
        },

        onDateFromChange(val) {
            if (state.dateFrom === val) return;
            state.dateFrom = val;
            if (state.date && val > state.date) state.date = val;
            this.fetchData();
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
        
        async fetchData() {
            this.showLoading();
            document.getElementById('ki-error').style.display = 'none';
            
            if (abortController) abortController.abort();
            abortController = new AbortController();
            const reqId = ++state.reqId;
            
            try {
                if (!config || !config.dataUrl) throw new Error('Не настроен адрес источника показателей.');
                let url = `${config.dataUrl}?period=${encodeURIComponent(state.period)}`;
                if (state.date) url += `&date=${encodeURIComponent(state.date)}`;
                if (['month', 'day', 'quarter'].includes(state.period)
                        && state.dateFrom) {
                    url += `&date_from=${encodeURIComponent(state.dateFrom)}`;
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
                
                if (!response.ok) {
                    throw new Error(data.error || data.message || `Ошибка сервера: ${response.status}`);
                }
                
                if (reqId !== state.reqId) return;
                
                state.data = data;
                if (data.period) {
                    state.period = data.period.kind;
                    state.date = data.period.date;
                    state.dateFrom = data.period.date_from || data.period.date;
                }
                
                this.render();
            } catch (e) {
                if (reqId !== state.reqId || e.name === 'AbortError') return;
                document.getElementById('ki-error-text').textContent = e.message;
                document.getElementById('ki-error').style.display = 'flex';
            } finally {
                if (reqId === state.reqId) this.hideLoading();
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
        },
        
        renderPeriodSelector() {
            const p = state.period;
            const dataP = state.data.period;
            
            document.querySelectorAll('.period-btn').forEach(btn => {
                if (btn.dataset.period === p) btn.classList.add('active');
                else btn.classList.remove('active');
                
                if (dataP && dataP.available_kinds) {
                    if (!dataP.available_kinds.includes(btn.dataset.period)) btn.style.display = 'none';
                    else btn.style.display = 'inline-block';
                }
            });
            
            const sel = document.getElementById('ki-date-select');
            const fromSel = document.getElementById('ki-date-from-select');
            const quarterButtons = document.getElementById('ki-quarter-buttons');
            sel.setAttribute('aria-label', p === 'quarter' ? 'Год' : 'Конечная дата');
            if (dataP && dataP.options && dataP.options.length > 0) {
                sel.innerHTML = dataP.options.map(opt =>
                    `<option value="${esc(opt.value)}" ${opt.value === dataP.date ? 'selected' : ''}>${p === 'month' || p === 'day' ? 'по ' : ''}${esc(opt.label)}</option>`
                ).join('');
                fromSel.innerHTML = dataP.options
                    .filter(opt => opt.value <= dataP.date)
                    .map(opt =>
                        `<option value="${esc(opt.value)}" ${opt.value === dataP.date_from ? 'selected' : ''}>с ${esc(opt.label)}</option>`
                    ).join('');
                if (p === 'quarter') {
                    const activeDate = new Date(`${dataP.date}T00:00:00`);
                    const activeYear = activeDate.getFullYear();
                    const activeQuarter = Math.floor(activeDate.getMonth() / 3) + 1;
                    const optionsByYear = new Map();
                    dataP.options.forEach(option => {
                        const year = option.value.slice(0, 4);
                        if (!optionsByYear.has(year)) optionsByYear.set(year, []);
                        optionsByYear.get(year).push(option);
                    });
                    sel.innerHTML = Array.from(optionsByYear.keys()).sort().map(year => {
                        const options = optionsByYear.get(year).slice()
                            .sort((a, b) => a.value.localeCompare(b.value));
                        const matchingQuarter = options.find(option =>
                            Math.floor((Number(option.value.slice(5, 7)) - 1) / 3) + 1 === activeQuarter);
                        const option = matchingQuarter || options[options.length - 1];
                        return `<option value="${esc(option.value)}" ${Number(year) === activeYear ? 'selected' : ''}>${esc(year)}</option>`;
                    }).join('');
                    const byQuarter = new Map(
                        dataP.options
                            .filter(opt => new Date(`${opt.value}T00:00:00`).getFullYear() === activeYear)
                            .map(opt => [Math.floor(new Date(`${opt.value}T00:00:00`).getMonth() / 3) + 1, opt])
                    );
                    quarterButtons.innerHTML = [1, 2, 3, 4].map(number => {
                        const option = byQuarter.get(number);
                        const active = option && option.value >= dataP.date_from
                            && option.value <= dataP.date;
                        return `<button type="button" class="quarter-btn${active ? ' active' : ''}"
                            ${option ? `data-value="${esc(option.value)}"` : 'disabled'}
                            aria-label="${number} квартал ${activeYear}"
                            aria-pressed="${active ? 'true' : 'false'}">${number}</button>`;
                    }).join('');
                    quarterButtons.style.display = 'flex';
                    quarterButtons.title = state.quarterAnchor
                        ? 'Нажмите конечный квартал диапазона'
                        : 'Нажмите начальный, затем конечный квартал диапазона';
                    sel.style.display = 'inline-block';
                    fromSel.style.display = 'none';
                } else if (p === 'month' || p === 'day') {
                    quarterButtons.style.display = 'none';
                    fromSel.style.display = 'inline-block';
                    sel.style.display = 'inline-block';
                } else {
                    quarterButtons.style.display = 'none';
                    fromSel.style.display = 'none';
                    sel.style.display = 'inline-block';
                }
            } else {
                sel.style.display = 'none';
                fromSel.style.display = 'none';
                quarterButtons.style.display = 'none';
            }
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
            const orderedIds = ['cash', 'revenue', 'collection', 'ar', 'ap', 'cogs', 'profit', 'credits', 'salary', 'capex'];
            
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
                if (!isNull && card.delta && (id !== 'cash' || (card.comparison && card.comparison.comparable))) {
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
                  ${id === 'cash' ? `<div class="kpi-delta neutral">${esc(this.cashCoverageText(card))}${card.date ? ` · на ${esc(card.date)}` : ''}</div>` : ''}
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
            
            if (state.data.methodology && state.data.methodology.length > 0) {
                let mHtml = '<div class="quality-box"><div class="quality-title">Методология расчета</div>';
                state.data.methodology.forEach(m => {
                    mHtml += `<div style="margin-bottom:12px;">
                        <div style="font-size:12px; font-weight:bold; margin-bottom:4px;">${esc(m.title)}</div>
                        <div style="font-size:12px; color:#4B5563; line-height:1.4;">${esc(m.text)}</div>
                    </div>`;
                });
                mHtml += '</div>';
                html += mHtml;
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
                    description: 'Доля начислений, фактически погашенных потребителями.',
                    detail: 'Простое отношение кредитового и дебетового оборота счёта 62 не является собираемостью: ОСВ не связывает конкретную оплату с начислением.',
                    required: 'Регистр начислений и распределённых оплат из 1С: предприятие, потребитель, категория потребителя, договор, лицевой счёт, услуга, расчётный период, документ и дата начисления, сумма начисления, документ и дата оплаты, сумма оплаты, распределение платежа по начислениям, погашенная сумма, аванс и его зачёт, корректировка, сторно и возврат.'
                },
                ar: {
                    description: 'Подтверждённая дебиторская задолженность покупателей по субсчёту 62.01.',
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

        cashMetricCard(label, value, sub, tooltip, needsData) {
            return `<div class="cash-analysis-card ${needsData ? 'needs-data' : ''}" tabindex="0">
                <div class="cash-analysis-label">${esc(label)}</div>
                <div class="cash-analysis-value ${needsData ? 'cash-value-missing' : 'cash-value-ok'}">${esc(value)}</div>
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
            const unavailableColumns = 'Для собираемости нужен регистр распределения оплат по начислениям; для отклонения от плана — утверждённый план сегмента; для АПГ — полные ОСВ за аналогичный период прошлого года с той же аналитикой.';
            return {
                title: `Выручка: ${segment.label}`,
                description: `Выручка по категории потребителей «${segment.label}».`,
                formula: 'Выручка сегмента = ОбК(90.01 сегмента) − ОбД(90.03 сегмента)',
                detail: 'Расчёт допустим только при однозначной и одинаковой аналитике сегмента на счетах 90.01 и 90.03.',
                required: `${missing ? `${segment.required} ` : ''}${unavailableColumns}`,
                norm: 'План сегмента не загружен',
                fact: missing ? 'Н/Д' : `${this.formatValue(fact, 'RUB').val} млн ₽ · ${share}`,
                deviation: missing ? 'Сегмент не подтверждён аналитикой ОСВ' : 'Отклонение от плана недоступно',
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
                title: 'Выручка брутто',
                description: 'Начисленная выручка до вычета НДС.',
                formula: 'Выручка брутто = ОбК(90.01*)',
                detail: 'Суммируются только конечные аналитические строки 90.01, без одновременного сложения родительских итогов.',
                fact: gross === null ? 'Н/Д' : `${grossFormatted.val} ${grossFormatted.u}`,
                required: gross === null ? 'Суточная ОСВ счёта 90 с аналитическими строками точного префикса 90.01 и кредитовым оборотом.' : null,
                source: 'кредитовый оборот 90.01'
            });
            const vatTooltip = this.metricTooltipData(card, {
                title: 'НДС с выручки',
                description: 'НДС, уменьшающий брутто-выручку до бухгалтерской выручки без НДС.',
                formula: 'НДС = ОбД(90.03*)',
                detail: 'Для распределения НДС по сегментам аналитика 90.03 должна совпадать с аналитикой 90.01.',
                fact: vat === null ? 'Н/Д' : `${vatFormatted.val} ${vatFormatted.u}`,
                required: vat === null ? 'Суточная ОСВ счёта 90 с аналитическими строками 90.03, дебетовым оборотом и тем же сегментным разрезом, что у 90.01.' : null,
                deviation: vat === null ? 'Нетто-выручка недоступна' : 'Учтён в расчёте нетто-выручки',
                deviationClass: vat === null ? 'missing' : 'good',
                source: 'дебетовый оборот 90.03'
            });
            const planTooltip = {
                title: 'Отклонение выручки от плана',
                description: 'Сравнение фактической выручки с утверждённым планом за тот же период и в той же базе НДС.',
                formula: 'Отклонение, % = (Факт / План − 1) × 100%',
                detail: 'План должен иметь период, версию утверждения и тот же сегментный состав, что фактическая выручка.',
                required: 'Утверждённый план выручки из 1С по месяцам и сегментам: сумма, база с НДС/без НДС, версия плана, дата утверждения и подразделение.',
                norm: 'Факт ≥ утверждённого плана',
                fact: 'Н/Д',
                deviation: 'План не загружен',
                deviationClass: 'missing',
                source: 'требуется регистр или документ финансового планирования 1С'
            };
            const collectionRequired = 'Регистр начислений и оплат: потребитель, договор, расчётный период, документ начисления, дата и сумма оплаты, распределение платежа, аванс, корректировка и сторно.';
            let rowsHtml = '';
            segmentRows.forEach(segment => {
                const share = segment.value !== null && total ? `${(segment.value / total * 100).toFixed(1)}%` : '—';
                const tooltip = this.cashTooltip(this.revenueSegmentTooltip(segment, segment.value, share));
                rowsHtml += `<tr class="cash-tooltip-host" tabindex="0">
                    <td><div class="obj-name-wrap"><span>${esc(segment.label)}</span><span class="obj-code">${esc(segment.code)}</span></div>${tooltip}</td>
                    <td>${segment.value === null ? 'Н/Д' : `${this.formatValue(segment.value, 'RUB').val}`}</td>
                    <td>${esc(share)}</td>
                    <td class="cash-value-missing">Нужны данные</td>
                    <td class="cash-value-missing">Нужны данные</td>
                    <td class="cash-value-missing">Нужны данные</td>
                </tr>`;
            });
            const tableTooltip = this.cashTooltip({
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
            });

            document.getElementById('ki-modal-title').textContent = 'Выручка по сегментам (сч. 90.01)';
            this.updateModalSubtitle();
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Фактическая выручка предприятия.</strong>
                    Выручка без НДС рассчитывается как ОбК(90.01*) − ОбД(90.03*). Если 90.03 отсутствует, факт показывается как брутто.</div>
                <div class="cash-summary-grid">
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Выручка общая</div>
                        <div class="cash-summary-value cash-value-ok">${esc(totalFormatted.val)}</div><div class="cash-summary-sub">${esc(totalFormatted.u)} · ${vat === null ? 'брутто' : 'без НДС'}</div>${this.cashTooltip(totalTooltip)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Выручка брутто</div>
                        <div class="cash-summary-value">${esc(grossFormatted.val)}</div><div class="cash-summary-sub">${esc(grossFormatted.u)} · ОбК 90.01</div>${this.cashTooltip(grossTooltip)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">НДС</div>
                        <div class="cash-summary-value ${vat === null ? 'cash-value-missing' : ''}">${vat === null ? 'Нужны данные' : esc(vatFormatted.val)}</div><div class="cash-summary-sub">${vat === null ? 'нет аналитики 90.03' : `${esc(vatFormatted.u)} · ОбД 90.03`}</div>${this.cashTooltip(vatTooltip)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Отклонение от плана</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div><div class="cash-summary-sub">план отсутствует в ОСВ</div>${this.cashTooltip(planTooltip)}</div>
                </div>
                <div class="modal-section-title">Детализация выручки по сегментам</div>
                ${(unclassified.length || conflicts.length || unknownAmounts.length || !segmentData.reconciled) ? `<div class="revenue-classification-note">
                    ${unclassified.length ? `Новые аналитики без категории: ${esc(unclassified.map(row => `${row.account}: ${row.analytic_label}`).join(', '))}.` : ''}
                    ${conflicts.length ? ` Конфликты: ${esc(conflicts.map(row => row.message + ' — ' + row.analytic_label).join('; '))}.` : ''}
                    ${unknownAmounts.length ? ` Нет суммы по аналитикам: ${esc(unknownAmounts.map(row => `${row.account}: ${row.analytic_label}`).join(', '))}. Значения не заменены нулём.` : ''}
                    ${segmentData.reconciliation_difference !== null && segmentData.reconciliation_difference !== undefined && Math.abs(segmentData.reconciliation_difference) > 0.01 ? ` Расхождение с общей выручкой: ${esc(this.formatValue(segmentData.reconciliation_difference, 'RUB').val)} млн ₽.` : ''}
                </div>` : ''}
                ${config.canEditRevenueMappings ? this.renderRevenueMappingEditor(segmentData.analytics || [], definitions) : ''}
                <div class="revenue-table-wrap"><table class="cash-table revenue-table">
                    <thead><tr><th>Сегмент</th><th>Выручка, млн ₽</th><th>Доля</th><th>Сбор</th><th>Δ к плану</th><th>Δ к АПГ</th></tr></thead>
                    <tbody>${rowsHtml}<tr class="cash-tooltip-host" tabindex="0"><td>ИТОГО${tableTooltip}</td><td>${esc(totalFormatted.val)}</td><td>100%</td>
                        <td class="cash-value-missing">Нужны данные</td><td class="cash-value-missing">Нужны данные</td><td class="cash-value-missing">Нужны данные</td></tr></tbody>
                </table></div>
                <div class="revenue-missing-grid">
                    ${this.cashMetricCard('Уровень сбора', 'Нужны данные', 'оплаты / начисления', {
                        title: 'Уровень сбора платежей', description: 'Доля начислений выбранного периода, погашенных оплатами.',
                        formula: 'Сбор = Оплаты, распределённые на начисления / Начисления × 100%',
                        detail: 'Обороты счёта 62 без распределения платежей не являются достоверной собираемостью.',
                        required: collectionRequired, norm: 'Утверждённый норматив не загружен', fact: 'Н/Д',
                        deviation: 'Расчёт невозможен', deviationClass: 'missing', source: 'требуется регистр взаиморасчётов 1С'
                    }, true)}
                    ${this.cashMetricCard('План выручки', 'Нужны данные', 'утверждённый план по сегментам', planTooltip, true)}
                    ${this.cashMetricCard('Сравнение с АПГ', 'Нужны данные', 'аналогичный период прошлого года', {
                        title: 'Изменение к аналогичному периоду', description: 'Сравнение факта с тем же календарным периодом предыдущего года.',
                        formula: 'Δ АПГ = Факт текущего периода − Факт аналогичного периода прошлого года',
                        detail: 'Периоды должны быть полностью покрыты и рассчитаны в одинаковой базе НДС.',
                        required: 'Полные ОСВ счёта 90 за текущий и аналогичный период прошлого года с одинаковой аналитикой 90.01 и 90.03.',
                        norm: 'Индивидуальный норматив не задан', fact: 'Н/Д',
                        deviation: 'АПГ не рассчитан', deviationClass: 'missing', source: 'требуются сопоставимые периоды ОСВ счёта 90'
                    }, true)}
                </div>
                <div class="modal-section-title">Динамика выручки внутри выбранного периода</div>
                <div class="cash-chart-legend"><span><i class="cash-chart-dot" style="background:#10B981"></i>Выручка общая</span></div>
                <div class="cash-line-chart revenue-line-chart" id="ki-revenue-line-chart"></div>`;
            this.renderRevenueLineChart(card);
            this.scheduleChart('ki-revenue-line-chart',
                () => this.renderRevenueLineChart(card), 0);
        },

        renderRevenueMappingEditor(analytics, definitions) {
            if (!analytics.length) return '';
            const options = definitions.map(item =>
                `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
            const rows = analytics.map(item => `
                <tr><td>${esc(item.account)}</td><td>${esc(item.analytic_label)}</td>
                <td><select class="revenue-mapping-select" data-account="${esc(item.account)}"
                    data-key="${esc(item.analytic_key)}"
                    data-label="${esc(item.analytic_label)}"><option value="">Не классифицировано</option>
                    ${options}</select></td></tr>`).join('');
            setTimeout(() => {
                document.querySelectorAll('.revenue-mapping-select').forEach(select => {
                    const found = analytics.find(item =>
                        item.account === select.dataset.account &&
                        item.analytic_key === select.dataset.key);
                    select.value = found && found.category ? found.category : '';
                });
            }, 0);
            return `<div class="modal-section-title">Сопоставление аналитик предприятия</div>
                <div class="revenue-mapping-help">Настройка действует для всех периодов. Сопоставьте 90.01 и 90.03 независимо.</div>
                <div class="revenue-table-wrap"><table class="cash-table revenue-mapping-table">
                <thead><tr><th>Счёт</th><th>Аналитика</th><th>Категория</th></tr></thead>
                <tbody>${rows}</tbody></table></div>
                <button type="button" class="revenue-mapping-save" onclick="window.KI_App.saveRevenueMappings()">Сохранить сопоставления</button>
                <span id="revenue-mapping-status" class="revenue-mapping-status"></span>`;
        },

        async saveRevenueMappings() {
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
                const response = await fetch(config.mappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                this.openModal('revenue');
            } catch (error) {
                status.textContent = error.message;
            }
        },

        renderRevenueLineChart(card) {
            const container = document.getElementById('ki-revenue-line-chart');
            if (!container) return;
            const points = (card.series || []).map((item, index) => ({
                index, label: item.label, value: item.value === null ? null : Number(item.value) / 1000000
            }));
            const valid = points.filter(item => item.value !== null && Number.isFinite(item.value));
            if (!valid.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const width = 820, height = 220, left = 50, right = 15, top = 16, bottom = 36;
            let min = Math.min(0, ...valid.map(item => item.value));
            let max = Math.max(0, ...valid.map(item => item.value));
            if (min === max) max = min + 1;
            const x = index => left + (points.length === 1 ? 0 : index * (width - left - right) / (points.length - 1));
            const y = value => top + (max - value) * (height - top - bottom) / (max - min);
            let path = '', drawing = false;
            points.forEach(item => {
                if (item.value === null || !Number.isFinite(item.value)) { drawing = false; return; }
                path += `${drawing ? ' L' : ' M'} ${x(item.index).toFixed(1)} ${y(item.value).toFixed(1)}`;
                drawing = true;
            });
            let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика выручки">`;
            if (valid.length === 1) svg += '<text x="70" y="14" font-size="10" fill="#6B7280">Одна точка данных: для линии нужны данные внутри периода</text>';
            [min, (min + max) / 2, max].forEach(value => {
                const py = y(value);
                svg += `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#E5E7EB" stroke-dasharray="3 3"/>
                    <text x="${left-6}" y="${py+4}" text-anchor="end" font-size="10" fill="#6B7280">${esc(value.toFixed(1))}</text>`;
            });
            svg += `<path d="${path}" fill="none" stroke="#10B981" stroke-width="3"/>`;
            points.forEach((item, index) => {
                if (item.value !== null && Number.isFinite(item.value)) {
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
                fact: 'Регистр начислений и распределённых оплат из 1С: предприятие, потребитель, категория потребителя, договор, лицевой счёт, услуга, расчётный период, документ и дата начисления, сумма начисления, документ и дата оплаты, сумма оплаты, распределение платежа по начислениям, погашенная сумма, аванс и его зачёт, корректировка, сторно и возврат.',
                plan: 'Утверждённый план собираемости из 1С или системы бюджетирования: предприятие, сегмент потребителей, месяц, плановый процент, версия плана, дата утверждения, ответственное лицо и период действия.',
                previousYear: 'Те же регистры начислений и распределённых оплат за полный аналогичный период прошлого года, рассчитанные по тем же сегментам, услугам и правилам учёта авансов, корректировок и сторно.',
                segments: 'Справочник соответствия аналитик счёта 62 категориям потребителей: контрагент, договор, лицевой счёт, вид договора, категория «Население / УК-ТСЖ / Бюджет / Прочие юрлица / Субсидии», дата начала и окончания действия категории.',
                subsidy: 'Регистр субсидий и компенсаций: бюджет, программа, получатель, расчётный период, документ начисления дохода, заявленная сумма, подтверждённая сумма, дата поступления, платёжный документ и распределение поступления по начислениям.'
            };
        },

        collectionTooltip(overrides = {}) {
            const required = this.collectionRequirements();
            return Object.assign({
                title: 'Фактический уровень собираемости',
                description: 'Доля начислений выбранного расчётного периода, погашенная распределёнными платежами.',
                formula: 'Собираемость = Оплаты, распределённые на начисления периода / Начисления периода после корректировок × 100%',
                detail: 'Дебетовый и кредитовый обороты счёта 62 не связывают платёж с начислением и поэтому сами по себе не являются собираемостью.',
                required: required.fact,
                norm: 'План или норматив предприятия не загружен',
                fact: 'Н/Д',
                deviation: 'Расчёт невозможен по одной ОСВ',
                deviationClass: 'missing',
                source: 'требуется регистр начислений и распределённых оплат из 1С'
            }, overrides);
        },

        collectionCell(content, tooltip, className = '') {
            return `<td class="cash-tooltip-host ${className}" tabindex="0">${content}${this.cashTooltip(tooltip)}</td>`;
        },

        renderCollectionModal(card) {
            const container = document.getElementById('ki-collection-modal-content');
            if (!container) return;
            const required = this.collectionRequirements();
            const arCard = state.data.cards.find(item => item.id === 'ar') || {};
            const revenueCard = state.data.cards.find(item => item.id === 'revenue') || {};
            const availableAr = (arCard.series || []).filter(item => item.value !== null);
            const currentAr = availableAr.length ? availableAr[availableAr.length - 1] : null;
            const previousAr = availableAr.length > 1 ? availableAr[availableAr.length - 2] : null;
            const arChange = currentAr && previousAr ? currentAr.value - previousAr.value : null;
            const revenueIsNet = revenueCard.value && !String(revenueCard.title || '').includes('брутто');
            const arRevenueRatio = arCard.value !== null && arCard.value !== undefined &&
                revenueIsNet ? arCard.value / revenueCard.value * 100 : null;
            const turnover = card.turnover_ratio;
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;

            const factTip = this.collectionTooltip();
            const turnoverTip = this.collectionTooltip({
                title: 'Соотношение оборотов счёта 62 — не собираемость',
                description: 'Справочное отношение кредитового оборота расчётов с покупателями к дебетовому обороту.',
                formula: 'Оборотный коэффициент = ОбК(62) / ОбД(62) × 100%',
                detail: 'ОбК может включать оплату старого долга, авансы, зачёты и сторно; ОбД — корректировки и иные операции. Значение не оценивается по нормативу собираемости.',
                required: null, norm: 'Не применяется', fact: pct(turnover),
                deviation: 'Справочный бухгалтерский индикатор', deviationClass: 'neutral',
                source: 'обороты загруженной ОСВ счёта 62'
            });
            const arChangeTip = this.collectionTooltip({
                title: 'Изменение дебиторской задолженности',
                description: 'Изменение конечного дебетового сальдо 62.01 между двумя доступными месячными снимками.',
                formula: 'Δ ДЗ = ДЗ на конец текущего месяца − ДЗ на конец предыдущего доступного месяца',
                detail: arChange === null ? 'Нет двух сопоставимых конечных сальдо 62.01.' : 'Рост ДЗ является косвенным сигналом ухудшения расчётов, но не заменяет собираемость.',
                required: arChange === null ? 'ОСВ счёта 62 с конечным сальдо 62.01 на две сопоставимые месячные даты.' : null,
                norm: 'Индивидуальный норматив не задан',
                fact: arChange === null ? 'Н/Д' : `${arChange >= 0 ? '+' : ''}${rub(arChange)}`,
                deviation: arChange === null ? 'Недостаточно снимков' : (arChange > 0 ? 'Задолженность выросла' : 'Задолженность снизилась'),
                deviationClass: arChange === null ? 'missing' : (arChange > 0 ? 'bad' : 'good'),
                source: 'конечные сальдо ОСВ 62.01'
            });
            const ratioTip = this.collectionTooltip({
                title: 'Дебиторская задолженность к выручке',
                description: 'Отношение конечной ДЗ покупателей к выручке за выбранный период.',
                formula: 'ДЗ / Выручка = СКд(62.01) / [ОбК(90.01) − ОбД(90.03)] × 100%',
                detail: 'Показатель характеризует объём средств в задолженности, но не показывает распределение оплат по начислениям.',
                required: arRevenueRatio === null ? 'ОСВ 62.01 с конечным дебетовым сальдо и ОСВ 90.01/90.03 за один сопоставимый период.' : null,
                norm: 'Индивидуальный норматив не задан', fact: pct(arRevenueRatio),
                deviation: arRevenueRatio === null ? 'Недостаточно данных' : 'Фактическое соотношение',
                deviationClass: arRevenueRatio === null ? 'missing' : 'neutral',
                source: 'ОСВ 62.01, 90.01 и 90.03'
            });

            const analytics = card.analytics || [];
            const analyticRows = analytics.length ? analytics.map(row => {
                const analyticBase = `Аналитика 1С: ${row.label}.`;
                const debitTip = this.collectionTooltip({
                    title: `Дебетовый оборот 62 — ${row.label}`,
                    description: 'Бухгалтерский дебетовый оборот выбранной аналитики счёта 62.',
                    formula: 'Σ ОбД(62) по аналитике и выбранному периоду',
                    detail: `${analyticBase} Оборот может включать начисления, корректировки и другие операции.`,
                    required: null, norm: 'Не применяется', fact: rub(row.debit),
                    deviation: 'Факт ОСВ, не подтверждённые начисления', deviationClass: 'neutral',
                    source: 'ОСВ счёта 62'
                });
                const creditTip = this.collectionTooltip({
                    title: `Кредитовый оборот 62 — ${row.label}`,
                    description: 'Бухгалтерский кредитовый оборот выбранной аналитики счёта 62.',
                    formula: 'Σ ОбК(62) по аналитике и выбранному периоду',
                    detail: `${analyticBase} Оборот может включать оплаты, зачёты, авансы, корректировки и сторно.`,
                    required: null, norm: 'Не применяется', fact: rub(row.credit),
                    deviation: 'Факт ОСВ, не подтверждённые оплаты', deviationClass: 'neutral',
                    source: 'ОСВ счёта 62'
                });
                const rowTurnoverTip = this.collectionTooltip({
                    title: `Соотношение оборотов — ${row.label}`,
                    description: 'Справочное соотношение оборотов выбранной аналитики, не фактическая собираемость.',
                    formula: 'ОбК(62, аналитика) / ОбД(62, аналитика) × 100%',
                    detail: analyticBase,
                    required: `${required.fact} ${required.segments}`,
                    norm: 'Норматив собираемости неприменим', fact: pct(row.turnover_ratio),
                    deviation: row.turnover_ratio === null ? 'Нет ненулевой базы ОбД' : 'Справочный индикатор',
                    deviationClass: row.turnover_ratio === null ? 'missing' : 'neutral',
                    source: 'аналитические обороты ОСВ счёта 62'
                });
                const segmentFactTip = this.collectionTooltip({
                    title: `Фактическая собираемость — ${row.label}`,
                    detail: `${analyticBase} Для расчёта требуется связать оплаты с начислениями этой аналитики.`,
                    required: `${required.fact} ${required.segments}`
                });
                const planTip = this.collectionTooltip({
                    title: `План собираемости — ${row.label}`,
                    description: 'Утверждённое целевое значение для сегмента и месяца.',
                    formula: 'План берётся из утверждённой версии бюджета, а не выводится из ОСВ.',
                    detail: analyticBase, required: `${required.plan} ${required.segments}`,
                    source: 'требуется план из 1С или системы бюджетирования'
                });
                const apgTip = this.collectionTooltip({
                    title: `Изменение к АПГ — ${row.label}`,
                    description: 'Разница собираемости с тем же календарным периодом прошлого года.',
                    formula: 'Δ АПГ = Собираемость текущего периода − Собираемость аналогичного периода прошлого года',
                    detail: analyticBase, required: `${required.previousYear} ${required.segments}`,
                    source: 'требуются сопоставимые регистры за два года'
                });
                const planDeltaTip = this.collectionTooltip({
                    title: `Отклонение от плана — ${row.label}`,
                    description: 'Разница фактической собираемости и утверждённого плана сегмента.',
                    formula: 'Δ к плану = Фактическая собираемость − План собираемости',
                    detail: analyticBase, required: `${required.fact} ${required.plan} ${required.segments}`,
                    source: 'требуются регистр оплат и утверждённый план'
                });
                const statusTip = this.collectionTooltip({
                    title: `Статус — ${row.label}`,
                    description: 'Оценка выполнения утверждённого плана собираемости.',
                    formula: 'Статус определяется по отклонению факта от утверждённого плана и согласованным порогам.',
                    detail: 'Статус не назначается по справочному отношению оборотов счёта 62.',
                    required: `${required.fact} ${required.plan} Справочник порогов статусов из 1С: сегмент, допустимое отклонение, уровень «Норма / Внимание / Критично», дата начала действия.`,
                    source: 'требуются факт, план и утверждённые пороги'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, this.collectionTooltip({
                        title: `Аналитика счёта 62 — ${row.label}`, description: 'Фактическая аналитика из загруженной ОСВ.',
                        formula: 'Группа определяется полным путём аналитики 1С.', detail: 'Автоматическое отнесение к категории потребителя не выполняется.',
                        required: required.segments, norm: 'Не применяется', fact: 'Есть в ОСВ',
                        deviation: 'Категория потребителя не подтверждена', deviationClass: 'missing', source: 'ОСВ счёта 62'
                    }))}
                    ${this.collectionCell(esc(rub(row.debit)), debitTip)}
                    ${this.collectionCell(esc(rub(row.credit)), creditTip)}
                    ${this.collectionCell(esc(pct(row.turnover_ratio)), rowTurnoverTip)}
                    ${this.collectionCell('Нужны данные', segmentFactTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', planTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', planDeltaTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', apgTip, 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', statusTip, 'cash-value-missing')}
                </tr>`;
            }).join('') : `<tr><td colspan="9" class="chart-empty">В ОСВ счёта 62 нет доступной аналитической детализации. ${esc(required.segments)}</td></tr>`;

            container.innerHTML = `
                <div class="modal-info-banner"><strong>Фактическая собираемость не выводится из оборотов ОСВ.</strong>
                    Отношение ОбК(62) / ОбД(62) показано отдельно как бухгалтерский индикатор и не сравнивается с нормативом 95%.</div>
                <div class="modal-summary collection-summary">
                    ${this.cashMetricCard('Фактическая собираемость', 'Нужны данные', 'распределённые оплаты / начисления', factTip, true)}
                    ${this.cashMetricCard('Соотношение оборотов 62', pct(turnover), 'справочно, не собираемость', turnoverTip, turnover === null)}
                    ${this.cashMetricCard('Изменение ДЗ', arChange === null ? 'Нужны данные' : `${arChange >= 0 ? '+' : ''}${rub(arChange)}`, 'к предыдущему доступному месяцу', arChangeTip, arChange === null)}
                    ${this.cashMetricCard('ДЗ / Выручка', pct(arRevenueRatio), 'бухгалтерский индикатор', ratioTip, arRevenueRatio === null)}
                </div>
                <div class="modal-section-title">Доступная динамика по ОСВ</div>
                <div class="collection-chart-grid">
                    <div><div class="collection-chart-title">Соотношение оборотов 62, % — не собираемость</div><div class="modal-chart-wrap" id="ki-collection-turnover-chart"></div></div>
                    <div><div class="collection-chart-title">Дебиторская задолженность 62.01, млн ₽</div><div class="modal-chart-wrap" id="ki-collection-ar-chart"></div></div>
                </div>
                <div class="modal-section-title">Детализация по фактическим аналитикам счёта 62</div>
                <div class="revenue-classification-note">Строки не называются сегментами потребителей до загрузки справочника соответствия. Субсидии нельзя автоматически определять по счёту 76.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table">
                    <thead><tr><th>Аналитика 1С</th><th>ОбД 62</th><th>ОбК 62</th><th>ОбК/ОбД</th><th>Факт сбора</th><th>План</th><th>Δ к плану</th><th>Δ к АПГ</th><th>Статус</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Какие данные нужны для полного окна</div>
                <div class="revenue-missing-grid collection-missing-grid">
                    ${this.cashMetricCard('Собираемость и Δ к плану', 'Нужны данные', 'начисления и распределённые оплаты', this.collectionTooltip({required: `${required.fact} ${required.plan}`}), true)}
                    ${this.cashMetricCard('Сравнение с АПГ', 'Нужны данные', 'полный сопоставимый период', this.collectionTooltip({title:'Сравнение собираемости с АПГ', required: required.previousYear}), true)}
                    ${this.cashMetricCard('Сегменты потребителей', 'Нужны данные', 'справочник категорий', this.collectionTooltip({title:'Собираемость по сегментам', required: required.segments}), true)}
                    ${this.cashMetricCard('Субсидии и компенсации', 'Нужны данные', 'отдельный регистр поступлений', this.collectionTooltip({title:'Собираемость субсидий и компенсаций', required: required.subsidy}), true)}
                </div>`;

            this.scheduleChart('ki-collection-turnover-chart', () => {
                this.renderBarChart(card.turnover_series || [], '%', 'ki-collection-turnover-chart', {
                    id: 'collection',
                    title: 'Соотношение оборотов счёта 62',
                    formula: 'ОбК(62) / ОбД(62) × 100%',
                    source: 'месячные обороты ОСВ счёта 62',
                    reason: 'Показатель не является фактической собираемостью'
                });
                this.renderBarChart(arCard.series || [], 'RUB', 'ki-collection-ar-chart', arCard);
            });
            this.scheduleChart('ki-collection-ar-chart', () =>
                this.renderBarChart(arCard.series || [], 'RUB', 'ki-collection-ar-chart', arCard), 0);
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
                title: 'Подтверждённая дебиторская задолженность покупателей',
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
                <div class="cash-info-banner"><strong>В расчёт включена только подтверждённая ДЗ покупателей 62.01.</strong>
                    Дебетовое сальдо счёта 76 добавляется только после утверждённого сопоставления аналитик.</div>
                <div class="collection-summary ar-summary">
                    ${this.cashMetricCard('Подтверждённая ДЗ', rub(current), '62.01 + разрешённые аналитики 76', totalTip, current === null || current === undefined)}
                    ${this.cashMetricCard('Изменение ДЗ', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'за выбранный период', changeTip, change === null)}
                    ${this.cashMetricCard('Оценочный DSO', dso === null ? 'Нужны данные' : `${dso.toFixed(0)} дней`, 'бухгалтерская оценка', dsoTip, dso === null)}
                    ${this.cashMetricCard('ДЗ / Выручка', pct(ratio), period.label || 'выбранный период', ratioTip, ratio === null)}
                </div>
                <div class="modal-section-title">Динамика подтверждённой ДЗ 62.01</div>
                <div class="modal-chart-wrap ar-line-chart" id="ki-ar-line-chart"></div>
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
            this.scheduleChart('ki-ar-line-chart', () => this.renderBarChart(
                card.series || [], 'RUB', 'ki-ar-line-chart', card), 50);
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
                const response = await fetch(config.debtorMappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                this.openModal('ar');
            } catch (error) {
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
                const dueTip = this.apTooltip({
                    title: `Срок и просрочка — ${item.label}`,
                    description: 'Состояние непогашенного обязательства относительно договорного или нормативного срока.',
                    formula: 'Просрочка = отчётная дата − установленный срок оплаты',
                    detail: 'Кредитовое сальдо ОСВ не содержит установленный срок погашения.',
                    required: item.account === '68' || item.account === '69' ? required.taxes :
                        item.account === '70' ? required.salary : required.terms,
                    source: 'требуется регистр обязательств и платежей'
                });
                return `<tr>
                    ${this.collectionCell(`<strong>${esc(item.label)}</strong><br><span class="ki-muted">сч. ${esc(item.account)}</span>`, tip)}
                    ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    ${this.collectionCell(esc(pct(share)), tip)}
                    ${this.collectionCell('Нужны данные', dueTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', dueTip, 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', this.apTooltip({title:`Статус — ${item.label}`, required:`${required.terms} ${required.status}`}), 'cash-value-missing')}
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
                    ${this.cashMetricCard('КЗ поставщикам', rub(current), 'сальдо 60.01', supplierTip, current === null || current === undefined)}
                    ${this.cashMetricCard('Изменение КЗ', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'за выбранный период', changeTip, change === null)}
                    ${this.cashMetricCard('ДЗ 62.01 / КЗ 60.01', arApRatio === null ? 'Нужны данные' : arApRatio.toFixed(2), 'узкий бухгалтерский охват', ratioTip, arApRatio === null)}
                    ${this.cashMetricCard('Оценочный DPO', dpo === null ? 'Нужны данные' : `${dpo.toFixed(0)} дней`, 'по обороту Кт 60.01', dpoTip, dpo === null)}
                </div>
                <div class="modal-section-title">Структура известных расчётных обязательств</div>
                <div class="revenue-classification-note">Известная сумма: <strong>${esc(rub(knownTotal))}</strong>. Это не полная КЗ, пока не сопоставлены разрешённые кредитовые аналитики счёта 76. Отсутствующие счета не принимаются за ноль.</div>
                <div class="ap-structure">${structure}</div>
                <div class="collection-table-wrap"><table class="collection-detail-table ap-detail-table">
                    <thead><tr><th>Вид обязательства</th><th>Сумма</th><th>Доля известной суммы</th><th>Срок оплаты</th><th>Просрочка</th><th>Статус</th></tr></thead>
                    <tbody>${componentRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика задолженности поставщикам 60.01</div>
                <div class="modal-chart-wrap ap-line-chart" id="ki-ap-line-chart"></div>
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
            this.scheduleChart('ki-ap-line-chart', () => this.renderBarChart(
                card.series || [], 'RUB', 'ki-ap-line-chart', card), 50);
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
                const response = await fetch(config.costMappingsUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-CSRFToken': config.csrfToken
                    },
                    body: JSON.stringify({mappings})
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || 'Не удалось сохранить');
                status.textContent = 'Сохранено';
                this.closeModal();
                await this.fetchData();
                this.openModal('cogs');
            } catch (error) {
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
            const change = card.delta && card.delta.value !== null &&
                card.delta.value !== undefined ? card.delta.value : null;
            const rub = value => value === null || value === undefined
                ? 'Н/Д' : `${this.formatValue(value, 'RUB').val} млн ₽`;
            const pct = value => value === null || value === undefined
                ? 'Н/Д' : `${Number(value).toFixed(1)}%`;
            const structure = card.cost_structure || {};

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
                detail: 'Выручка брутто при отсутствии 90.03 не используется скрыто.',
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
                <div class="collection-summary cogs-summary">
                    ${this.cashMetricCard('Себестоимость продаж', rub(current), 'дебет 90.02', totalTip, current === null || current === undefined)}
                    ${this.cashMetricCard('Изменение', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'к сопоставимому периоду', changeTip, change === null)}
                    ${this.cashMetricCard('Себестоимость / Выручка', pct(costRatio), 'по нетто-выручке', ratioTip, costRatio === null)}
                    ${this.cashMetricCard('Валовая прибыль', rub(cogsGrossProfit), 'нетто-выручка − 90.02', profitTip, cogsGrossProfit === null || cogsGrossProfit === undefined)}
                </div>
                <div class="modal-section-title">Признанные расходы счёта 90</div>
                <div class="collection-table-wrap"><table class="collection-detail-table cogs-recognized-table">
                    <thead><tr><th>Показатель</th><th>Сумма</th><th>Использование</th></tr></thead>
                    <tbody>${recognizedRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика себестоимости продаж 90.02</div>
                <div class="modal-chart-wrap cogs-line-chart" id="ki-cogs-line-chart"></div>
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
            this.scheduleChart('ki-cogs-line-chart', () => this.renderBarChart(
                card.series || [], 'RUB', 'ki-cogs-line-chart', card), 50);
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
            }).join('') : '<tr><td colspan="5" class="chart-empty">Нет месячной серии.</td></tr>';

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
                    ${this.cashMetricCard('Валовая прибыль', rub(card.gross_profit), `Рв = ${pct(margin(card.gross_profit))}`, grossTip, card.gross_profit === null || card.gross_profit === undefined)}
                    ${this.cashMetricCard('Прибыль от продаж', rub(card.profit_from_sales), `Рп = ${pct(margin(card.profit_from_sales))}`, salesTip, card.profit_from_sales === null || card.profit_from_sales === undefined)}
                    ${this.cashMetricCard('Сальдо счёта 91', rub(card.other_result), '91.01 − 91.02', otherTip, card.other_result === null || card.other_result === undefined)}
                    ${this.cashMetricCard('Результат до налога', rub(card.profit_before_tax), 'не чистая прибыль', pretaxTip, card.profit_before_tax === null || card.profit_before_tax === undefined)}
                </div>
                <div class="modal-section-title">Декомпозиция финансового результата</div>
                <div class="collection-table-wrap"><table class="collection-detail-table profit-waterfall-table">
                    <thead><tr><th>Уровень</th><th>Сумма</th><th>Комментарий</th></tr></thead>
                    <tbody>${decomposition}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика валовой прибыли</div>
                <div class="modal-chart-wrap profit-line-chart" id="ki-profit-line-chart"></div>
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
            this.scheduleChart('ki-profit-line-chart', () => this.renderBarChart(
                card.gross_series || [], 'RUB', 'ki-profit-line-chart',
                Object.assign({}, card, {title: 'Валовая прибыль'})), 50);
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
                        'Пропущенные месяцы не заменяются нулями. Нужны отдельные точные сальдо на конец каждого месяца.');
                    return `<tr>
                        ${this.collectionCell(esc(item.label), tip)}
                        ${this.collectionCell(esc(rub(item.value)), tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                        ${this.collectionCell(item.value === null || item.value === undefined ? 'Нет точного сальдо' : 'Подтверждено', tip, item.value === null || item.value === undefined ? 'cash-value-missing' : '')}
                    </tr>`;
                }).join('')
                : '<tr><td colspan="3" class="chart-empty">Нет месячной серии сальдо.</td></tr>';

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
                    ${this.cashMetricCard('Подтверждённый долг', rub(card.value), card.coverage && card.coverage.complete ? 'полный охват 66/67' : 'частичный охват', totalTip, card.value === null || card.value === undefined)}
                    ${this.cashMetricCard('Краткосрочный сч. 66', rub((byAccount['66'] || {}).value), `доля ${pct((byAccount['66'] || {}).share)}`, shortTip, (byAccount['66'] || {}).value === null || (byAccount['66'] || {}).value === undefined)}
                    ${this.cashMetricCard('Долгосрочный сч. 67', rub((byAccount['67'] || {}).value), `доля ${pct((byAccount['67'] || {}).share)}`, longTip, (byAccount['67'] || {}).value === null || (byAccount['67'] || {}).value === undefined)}
                    ${this.cashMetricCard('Изменение долга', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'к сопоставимой дате', changeTip, change === null)}
                </div>
                <div class="modal-section-title">Счета и бухгалтерские обороты</div>
                <div class="collection-table-wrap"><table class="collection-detail-table credits-account-table">
                    <thead><tr><th>Счёт</th><th>Сальдо</th><th>Доля</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th><th>Статус</th></tr></thead>
                    <tbody>${accountRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика подтверждённого долга</div>
                <div class="modal-chart-wrap credits-line-chart" id="ki-credits-line-chart"></div>
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
            this.scheduleChart('ki-credits-line-chart', () => this.renderBarChart(
                card.series || [], 'RUB', 'ki-credits-line-chart', card), 50);
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
                    ${this.cashMetricCard('Задолженность работникам', rub(card.value), 'кредитовое сальдо 70', debtTip, card.value == null)}
                    ${this.cashMetricCard('Дебетовое сальдо 70', rub(card.debit_balance), 'переплаты и другие расчёты', debitTip, card.debit_balance == null)}
                    ${this.cashMetricCard('Начисления работникам', rub(card.credit_turnover), 'ОбК 70 · не подтверждённый ФОТ', accrualTip, card.credit_turnover == null)}
                    ${this.cashMetricCard('Сальдо счёта 69', rub(ins.credit_balance), 'не статус просрочки', insuranceDebtTip, ins.credit_balance == null)}
                </div>
                <div class="modal-section-title">Сальдо и бухгалтерские обороты</div>
                <div class="collection-table-wrap"><table class="collection-detail-table salary-account-table">
                    <thead><tr><th>Счёт</th><th>Дебетовое сальдо</th><th>Кредитовое сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th></tr></thead>
                    <tbody>${accountRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика начислений и расчётных остатков</div>
                <p class="ki-muted">${granularity} динамика. Единственная мера графика — ОбК70 (кредитовый оборот), млн ₽. Остатки и ОбК69 приведены только в таблице. Оборот не равен денежным выплатам или подтверждённому ФОТ.</p>
                <div class="modal-chart-wrap salary-line-chart" id="ki-salary-line-chart"></div>
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
            this.scheduleChart('ki-salary-line-chart', () => this.renderBarChart(
                card.accrual_series || [], 'RUB', 'ki-salary-line-chart',
                {
                    id: 'salary-credit-turnover',
                    title: 'ОбК70 — кредитовый оборот',
                    unit: 'RUB',
                    value: card.credit_turnover,
                    formula: 'ОбК(70) за период точки',
                    source: 'ОСВ счёта 70: кредитовый оборот за период точки',
                    description: `${granularity} динамика кредитового оборота счёта 70; единственная мера графика.`,
                    notes: ['Бухгалтерские начисления не равны подтверждённому ФОТ или денежным выплатам. Пропуски не заменяются нулями.']
                }), 50);
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
            const dynamics = debitSeries.length
                ? debitSeries.map((item, index) => {
                    const dynTip = this.capexTooltip({
                        title: `Динамика счёта 08 — ${item.label}`,
                        description: 'Бухгалтерские обороты и точное сальдо счёта 08 за интервал динамики.',
                        formula: 'ОбД(08), ОбК(08), СКд(08)',
                        detail: 'Пропущенные месяцы не заменяются нулями. Плановая линия не строится без утверждённого календарного плана.',
                        required: `${req.balances} Для план-факта: ${req.program}`,
                        fact: rub(item.value),
                        deviation: item.value == null ? 'Нужны данные' : 'Подтверждённый оборот',
                        deviationClass: item.value == null ? 'missing' : 'neutral'
                    });
                    return `<tr>
                        ${this.collectionCell(esc(item.label), dynTip)}
                        ${this.collectionCell(esc(rub(item.value)), dynTip, item.value == null ? 'cash-value-missing' : '')}
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
                : '<tr><td colspan="5" class="chart-empty">Нет точной месячной серии счёта 08.</td></tr>';

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
                <div class="collection-summary capex-summary">
                    ${this.cashMetricCard('Формирование вложений', rub(card.debit_turnover), 'ОбД 08 за выбранный период', debitTip, card.debit_turnover == null)}
                    ${this.cashMetricCard('Незавершёнка', rub(card.closing_balance), 'конечное дебетовое сальдо 08', wipTip, card.closing_balance == null)}
                    ${this.cashMetricCard('Кредитовый оборот', rub(card.credit_turnover), 'не подтверждённый ввод', creditTip, card.credit_turnover == null)}
                    ${this.cashMetricCard('Изменение незавершёнки', balanceChange == null ? 'Нужны данные' : `${balanceChange >= 0 ? '+' : ''}${rub(balanceChange)}`, 'конец − начало периода', changeTip, balanceChange == null)}
                </div>
                <div class="modal-section-title">Сводные данные счёта 08</div>
                <div class="collection-table-wrap"><table class="collection-detail-table capex-account-table">
                    <thead><tr><th>Счёт</th><th>Начальное сальдо</th><th>Дебетовый оборот</th><th>Кредитовый оборот</th><th>Конечное сальдо</th></tr></thead>
                    <tbody>${accountRows}</tbody>
                </table></div>
                <div class="modal-section-title">Динамика бухгалтерских вложений</div>
                <div class="modal-chart-wrap capex-line-chart" id="ki-capex-line-chart"></div>
                <div class="collection-table-wrap"><table class="collection-detail-table capex-dynamics-table">
                    <thead><tr><th>Дата / период</th><th>ОбД 08</th><th>ОбК 08</th><th>СКд 08</th><th>План на дату</th></tr></thead>
                    <tbody>${dynamics}</tbody>
                </table></div>
                <div class="modal-section-title">Фактические аналитики счёта 08</div>
                <div class="revenue-classification-note">Аналитика становится объектом инвестпрограммы только после явного сопоставления со справочником объектов и утверждённой версией программы.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table capex-analytics-table">
                    <thead><tr><th>Аналитика 1С</th><th>Остаток</th><th>ОбД</th><th>ОбК</th><th>Доля оборота</th><th>Доля остатка</th><th>Объект</th><th>Мероприятие</th></tr></thead>
                    <tbody>${analyticRows}</tbody>
                </table></div>
                <div class="modal-section-title">Показатели, требующие дополнительных данных 1С</div>
                <div class="revenue-missing-grid capex-missing-grid">${unavailable}</div>`;
            this.scheduleChart('ki-capex-line-chart', () => this.renderBarChart(
                card.series || [], 'RUB', 'ki-capex-line-chart',
                Object.assign({}, card, {title: 'Формирование капитальных вложений'})), 50);
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

        renderCashModal(card) {
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
            (card.breakdown || []).forEach(item => {
                const match = String(item.label || '').match(/(\d{2})/);
                if (match) parts[match[1]] = item.value;
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
                const share = value !== null && total !== null && total !== 0 ? `${(value / total * 100).toFixed(1)}%` : '—';
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
                    fact: `${formatted.val} ${formatted.u} · ${dateText}${share !== '—' ? ` · ${share} от известного итога` : ''}`,
                    deviation: required ? 'Расчёт невозможен' : 'Оценивается после задания норматива',
                    source: required ? `требуется ОСВ по счёту ${account}` : `ОСВ по счёту ${account}`
                });
                rows += `<tr class="cash-tooltip-host" tabindex="0">
                    <td><div><strong>Счёт ${account}</strong> — ${esc(names[account])}${rowTooltip}</div></td>
                    <td>${esc(formatted.val)} ${esc(formatted.u)}</td>
                    <td>${esc(share)}</td>
                    <td>${esc(comments[account])}</td>
                    <td><span class="cash-data-state ${value === null ? 'missing' : 'ok'}">${value === null ? 'Нет данных' : 'Загружен'}</span></td>
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

            document.getElementById('ki-modal-title').textContent =
                'Ликвидность и денежные средства (сч. 50, 51, 52, 55)';
            this.updateModalSubtitle();
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Денежные средства по данным бухгалтерского учёта.</strong>
                    Итог рассчитан как сумма чистых конечных сальдо: СКд − СКк по счетам 50, 51, 52 и 55.
                    Средства счёта 55 могут иметь ограничения по использованию.</div>
                <div class="cash-summary-grid">
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Денежные средства</div>
                        <div class="cash-summary-value cash-value-ok">${esc(cashFormatted.val)}</div>
                        <div class="cash-summary-sub">${esc(cashFormatted.u)} · ${esc(dateText)}<br>${esc(coverageText)}</div>${this.cashTooltip(cashTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Изменение остатка</div>
                        <div class="cash-summary-value ${delta === null ? 'cash-value-missing' : 'cash-value-ok'}">${esc(deltaText)}</div>
                        <div class="cash-summary-sub">${esc(comparisonText)}</div>${this.cashTooltip(changeTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Текущая ликвидность</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет полного состава оборотных активов</div>${this.cashTooltip(currentLiquidityData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Cash Runway</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет очищенного платёжного потока</div>${this.cashTooltip(runwayData)}</div>
                </div>
                <div class="modal-section-title">Детализация по счетам денежных средств</div>
                <div style="overflow-x:auto"><table class="cash-table">
                    <thead><tr><th>Счёт / назначение</th><th>Сумма</th><th>Доля известного итога</th><th>Комментарий</th><th>Данные</th></tr></thead>
                    <tbody>${rows}<tr class="cash-tooltip-host" tabindex="0"><td><div>ИТОГО${this.cashTooltip(cashTipData)}</div></td><td>${esc(cashFormatted.val)} ${esc(cashFormatted.u)}<br>${esc(coverageText)}</td><td>${total ? '100% известного итога' : '—'}${card.coverage === 'partial' ? '<br>Частичный охват' : ''}</td><td>${esc(dateText)}; по загруженным счетам</td><td>${esc(coverageText)}</td></tr></tbody>
                </table></div>
                <div class="modal-section-title">Показатели ликвидности — наведите для методики и требований к данным</div>
                <div class="cash-metrics-grid">
                    ${this.cashMetricCard('Денежные средства', `${cashFormatted.val} ${cashFormatted.u}`, `${dateText} · ${coverageText}`, cashTipData, card.value == null)}
                    ${this.cashMetricCard('Изменение остатка', deltaText, comparisonText, changeTipData, delta === null)}
                    ${this.cashMetricCard('Коэффициент текущей ликвидности', 'Нужны данные', 'оборотные активы / краткосрочные обязательства', currentLiquidityData, true)}
                    ${this.cashMetricCard('Коэффициент абсолютной ликвидности', 'Нужны данные', 'деньги / краткосрочные обязательства', absoluteLiquidityData, true)}
                    ${this.cashMetricCard('Cash Runway', 'Нужны данные', 'остаток / среднедневные внешние платежи', runwayData, true)}
                    ${this.cashMetricCard('Обеспеченность топливом', 'Нужны данные', 'натуральный запас / среднедневной расход', fuelData, true)}
                </div>
                <div class="modal-section-title">Динамика денежных средств</div>
                <div class="cash-chart-legend"><span><i class="cash-chart-dot" style="background:#3B82F6"></i>Остаток</span>
                    <span><i class="cash-chart-dot" style="background:#10B981"></i>Изменение к предыдущей сопоставимой точке</span>
                    <span><i class="cash-chart-dot" style="background:#D97706"></i>Частичный охват счетов</span></div>
                <div class="cash-summary-sub">При смене состава счетов или пропуске данных линия прерывается, изменение не рассчитывается. Частичные остатки сравниваются только при одинаковом охвате. Даты и счета — в подсказках точек.</div>
                <div class="cash-line-chart" id="ki-cash-line-chart"></div>`;
            this.renderCashLineChart(card.series || []);
            this.scheduleChart('ki-cash-line-chart',
                () => this.renderCashLineChart(card.series || []), 0);
        },

        renderCashLineChart(series) {
            const container = document.getElementById('ki-cash-line-chart');
            if (!container) return;
            const points = series.map((item, index) => {
                const previous = series[index - 1];
                const comparison = item.comparison;
                // Fail closed if a stale/incomplete response omits comparison metadata.
                const comparable = !!(previous && comparison && comparison.comparable === true &&
                    Number.isFinite(item.value) && Number.isFinite(previous.value) &&
                    Number.isFinite(comparison.value) && item.date && previous.date &&
                    comparison.date === item.date && comparison.previous_date === previous.date &&
                    item.accounts && item.accounts.length && previous.accounts &&
                    [...item.accounts].sort().join(',') === [...previous.accounts].sort().join(','));
                return {
                    ...item, index, comparable,
                    value: Number.isFinite(item.value) ? item.value / 1000000 : null
                };
            });
            const valid = points.filter(item => item.value !== null && Number.isFinite(item.value));
            if (!valid.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const changes = points.map((item, index) => ({
                ...item, index,
                value: item.comparable ? item.comparison.value / 1000000 : null
            }));
            const values = valid.map(item => item.value).concat(
                changes.filter(item => item.value !== null).map(item => item.value));
            const width = 820, height = 220, left = 48, right = 15, top = 16, bottom = 36;
            let min = Math.min(0, ...values), max = Math.max(0, ...values);
            if (min === max) max = min + 1;
            const x = index => left + (points.length === 1 ? 0 : index * (width - left - right) / (points.length - 1));
            const y = value => top + (max - value) * (height - top - bottom) / (max - min);
            const pathFor = data => {
                let path = '', drawing = false;
                data.forEach(item => {
                    if (item.value === null || !Number.isFinite(item.value)) { drawing = false; return; }
                    path += `${drawing && item.comparable ? ' L' : ' M'} ${x(item.index).toFixed(1)} ${y(item.value).toFixed(1)}`;
                    drawing = true;
                });
                return path;
            };
            let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика денежных средств">`;
            if (valid.length === 1) svg += '<text x="70" y="14" font-size="10" fill="#6B7280">Одна точка данных: для линии нужны данные внутри периода</text>';
            [min, (min + max) / 2, max].forEach(value => {
                const py = y(value);
                svg += `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#E5E7EB" stroke-dasharray="3 3"/>
                    <text x="${left-6}" y="${py+4}" text-anchor="end" font-size="10" fill="#6B7280">${esc(value.toFixed(1))}</text>`;
            });
            svg += `<path d="${pathFor(points)}" fill="none" stroke="#3B82F6" stroke-width="3"/>
                <path d="${pathFor(changes)}" fill="none" stroke="#10B981" stroke-width="2" stroke-dasharray="5 4"/>`;
            points.forEach((item, index) => {
                if (item.value !== null && Number.isFinite(item.value)) {
                    svg += `<circle class="cash-chart-point" data-kind="balance" data-index="${index}" cx="${x(index)}" cy="${y(item.value)}" r="5" fill="${item.coverage === 'complete' ? '#3B82F6' : '#D97706'}" tabindex="0" aria-label="${esc(`Остаток на ${item.date || item.label}. ${this.cashCoverageText(item)}`)}"/>`;
                }
                if (changes[index].value !== null) {
                    svg += `<circle class="cash-chart-point" data-kind="change" data-index="${index}" cx="${x(index)}" cy="${y(changes[index].value)}" r="4" fill="#10B981" tabindex="0"/>`;
                }
                if (index % Math.max(1, Math.ceil(points.length / 8)) === 0 || index === points.length - 1)
                    svg += `<text x="${x(index)}" y="${height-10}" text-anchor="middle" font-size="9" fill="#6B7280">${esc(item.label)}</text>`;
            });
            container.innerHTML = svg + '</svg>';
            const pointTipData = point => {
                const index = Number(point.dataset.index);
                const isChange = point.dataset.kind === 'change';
                const item = isChange ? changes[index] : points[index];
                const comparisonText = this.cashComparisonText(points[index].comparison);
                const coverageText = this.cashCoverageText(points[index]);
                return isChange ? {
                    title: `Изменение остатка — ${item.date}`,
                    description: 'Разница бухгалтерских остатков на две точные даты, а не денежный поток NCF.',
                    formula: 'Изменение = Остаток на текущую дату − Остаток на предыдущую дату',
                    detail: `${comparisonText}. ${coverageText}.`,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.value >= 0 ? '+' : ''}${item.value.toFixed(2)} млн ₽`,
                    deviation: 'Фактическое изменение остатка',
                    source: 'точные конечные сальдо ОСВ по денежным счетам на указанные даты'
                } : {
                    title: `Остаток денежных средств — ${item.date || item.label}`,
                    description: 'Бухгалтерский остаток денежных средств на указанную дату.',
                    formula: 'Остаток = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                    detail: `${coverageText}. Отсутствующие счета не принимаются за ноль. ${comparisonText}.`,
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.value.toFixed(2)} млн ₽`,
                    deviation: changes[index].value === null ? comparisonText : `${changes[index].value >= 0 ? '+' : ''}${changes[index].value.toFixed(2)} млн ₽ · ${comparisonText}`,
                    source: 'конечные сальдо загруженных ОСВ по денежным счетам'
                };
            };
            container.querySelectorAll('.cash-chart-point').forEach(point => {
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
            const card = state.data.cards.find(c => c.id === cardId);
            if (!card) return;
            
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
            salaryContent.style.display = 'none';
            capexContent.style.display = 'none';
            if (cardId === 'cash') {
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
            if (card.breakdown && card.breakdown.length > 0) {
                bdTitle.style.display = 'block';
                let bdHtml = '<table class="collection-seg-table"><tbody>';
                card.breakdown.forEach(b => {
                    const f = this.formatValue(b.value, card.unit);
                    const tooltip = this.cashTooltip(this.metricTooltipData(card, {
                        title: `${card.title}: ${b.label}`,
                        fact: `${f.val} ${f.u}`,
                        detail: `${baseTooltip.detail} Детализация: ${b.label}.`,
                        deviation: 'Составная часть общего показателя'
                    }));
                    bdHtml += `<tr class="cash-tooltip-host" tabindex="0"><td>${esc(b.label)}${tooltip}</td><td>${esc(f.val)} ${f.u ? `<span style="color:#9CA3AF;font-size:10px;">${esc(f.u)}</span>` : ''}</td></tr>`;
                });
                bdHtml += '</tbody></table>';
                bdCont.innerHTML = bdHtml;
                bdCont.style.display = 'block';
            } else {
                bdTitle.style.display = 'none';
                bdCont.style.display = 'none';
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

            const chCont = document.getElementById('ki-modal-chart-container');
            const chTitle = document.getElementById('ki-modal-chart-title');
            if (card.series && card.series.length > 0) {
                chTitle.style.display = 'block';
                chCont.style.display = 'block';
                this.scheduleChart('ki-modal-chart-container',
                    () => this.renderBarChart(card.series, card.unit, 'ki-modal-chart-container', card), 50);
            } else {
                chTitle.style.display = 'none';
                chCont.style.display = 'none';
            }
            }
            
            if (window.KI_Layout) {
                const activeContainer = [
                    cashContent, revenueContent, collectionContent, arContent,
                    apContent, cogsContent, profitContent, creditsContent,
                    salaryContent, capexContent, genericContent
                ].find(el => el.style.display === 'block');
                if (activeContainer) {
                    window.KI_Layout.onModalOpen(cardId, activeContainer, 'osv');
                }
            }

            const overlay = document.getElementById('ki-modal');
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
            const overlay = document.getElementById('ki-modal');
            overlay.classList.remove('visible');
            overlay.style.visibility = 'hidden';
            overlay.setAttribute('aria-hidden', 'true');
            if (state.lastFocused) state.lastFocused.focus();
        },
        
        renderBarChart(series, unit, containerId, card) {
            const container = document.getElementById(containerId);
            if (!container) return;
            
            const valid = series.map(s => {
                let v = s.value;
                if (v !== null && unit === 'RUB') v = v / 1000000;
                return { ...s, scaledValue: v };
            }).filter(s => s.scaledValue != null && Number.isFinite(Number(s.scaledValue)));
            
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
            
            const step = w / series.length;
            
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
            
            let path = '', drawing = false;
            series.forEach((s, i) => {
                if (s.value == null || !Number.isFinite(Number(s.value))) {
                    drawing = false;
                    return;
                }
                const value = unit === 'RUB' ? s.value / 1000000 : Number(s.value);
                const x = padL + i * step + step / 2;
                const y = padT + h - ((value - minVal) / range) * h;
                path += `${drawing ? ' L' : ' M'} ${x} ${y}`;
                drawing = true;
            });
            svg += `<path d="${path}" fill="none" stroke="#3B82F6" stroke-width="2" stroke-linejoin="round"/>`;
            const labelStep = Math.max(1, Math.ceil(series.length / Math.max(2, Math.floor(w / 95))));
            series.forEach((s, i) => {
                const cx = padL + i * step + step / 2;
                let v = s.value;
                if (v !== null && unit === 'RUB') v = v / 1000000;
                
                if(v != null && Number.isFinite(Number(v))) {
                    const y = padT + h - ((v - minVal) / range) * h;
                    const fill = v >= 0 ? '#3B82F6' : '#EF4444';
                    svg += `<circle class="metric-chart-point" data-index="${i}" cx="${cx}" cy="${y}" r="4" fill="${fill}" tabindex="0" aria-label="${esc(s.label)}: ${esc(v)}"/>`;
                    
                    const fmtVal = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(v);
                    if (series.length <= 12) svg += `<text x="${cx}" y="${y - 8}" font-size="10" fill="#4B5563" text-anchor="middle" font-weight="bold">${esc(fmtVal)}</text>`;
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
    document.addEventListener('DOMContentLoaded', () => App.init());
})();
