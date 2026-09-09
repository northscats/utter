/* key_indicators.js */
(function() {
    // Also accept the lexical configuration from an already cached template.
    const config = window.KI_CONFIG || (typeof KI_CONFIG !== 'undefined' ? KI_CONFIG : null);
    
    const state = {
        period: 'month',
        date: null,
        data: null,
        lastFocused: null,
        reqId: 0
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
                document.querySelector('.tabs-bar').style.display = 'none';
                document.getElementById('tab-analytics').classList.add('active');
                if (document.getElementById('tab-recommendations')) document.getElementById('tab-recommendations').classList.remove('active');
                document.querySelectorAll('.tab-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.tab === 'analytics');
                });
                document.getElementById('ki-operational-container').style.display = 'none';
                document.getElementById('ki-annual-container').style.display = 'block';
                document.getElementById('ki-error').style.display = 'none';
                this.closeModal();
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
                this.closeModal();
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
                    const focusable = modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
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
            this.fetchData();
        },
        
        onDateChange(val) {
            if (state.date === val) return;
            state.date = val;
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
            subtitle.textContent = config.companyName;
            
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
            if (dataP && dataP.options && dataP.options.length > 0) {
                sel.style.display = 'inline-block';
                sel.innerHTML = dataP.options.map(opt => 
                    `<option value="${esc(opt.value)}" ${opt.value === dataP.date ? 'selected' : ''}>${esc(opt.label)}</option>`
                ).join('');
            } else {
                sel.style.display = 'none';
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
                if (!isNull && card.delta) {
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
                const cashTooltip = id === 'cash' ? this.cashTooltip({
                    title: 'Денежные средства предприятия',
                    description: 'Денежные средства на расчётных и иных счетах по данным бухгалтерского учёта. Средства на счёте 55 могут иметь ограничения по использованию.',
                    formula: 'Денежные средства = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                    detail: 'Счёт 50 — касса; 51 — расчётные счета; 52 — валютные счета в рублёвой оценке; 55 — специальные счета.',
                    norm: 'Индивидуальный норматив предприятия не задан',
                    fact: `${formatted.val} ${formatted.u}`,
                    deviation: card.delta ? `${card.delta.value >= 0 ? '+' : ''}${this.formatValue(card.delta.value, 'RUB').val} млн ₽ к предыдущей сопоставимой дате` : 'Нет сопоставимой предыдущей даты',
                    source: 'Конечные сальдо загруженных ОСВ по счетам 50, 51, 52 и 55'
                }) : '';
                
                html += `
                <div class="kpi-card status-${statusClass} clickable ${id === 'cash' ? 'cash-tooltip-host' : ''}" data-metric="${esc(id)}" tabindex="0" onclick="window.KI_App.openModal('${esc(id)}')" role="button" aria-label="${esc(ariaLabel)}">
                  <div class="kpi-label">${esc(card.title)} <span class="status-dot ${statusClass}"></span></div>
                  <div class="kpi-value">${esc(formatted.val)}<span class="kpi-unit">${esc(formatted.u)}</span></div>
                  ${deltaHtml}
                   ${cashTooltip}
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
            const deviationClass = data.required ? 'missing' : 'good';
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

        initRichTooltip() {
            if (document.getElementById('ki-rich-tooltip-portal')) return;
            const portal = document.createElement('div');
            portal.id = 'ki-rich-tooltip-portal';
            portal.setAttribute('aria-hidden', 'true');
            document.body.appendChild(portal);
            this._richTooltipPortal = portal;
            this._richTooltipHost = null;

            const hostSelector = '.kpi-card.cash-tooltip-host, .cash-summary-card, .cash-analysis-card, .cash-table .cash-tooltip-host';
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
            const delta = card.delta && card.delta.value !== null ? card.delta.value : null;
            const deltaFormatted = this.formatValue(delta, 'RUB');
            const deltaText = delta === null ? 'Нужны данные' :
                `${delta > 0 ? '+' : ''}${deltaFormatted.val} ${deltaFormatted.u}`;
            const total = Number(card.value) || 0;
            let rows = '';
            accounts.forEach(account => {
                const value = Object.prototype.hasOwnProperty.call(parts, account) ? parts[account] : null;
                const formatted = this.formatValue(value, card.unit);
                const share = value !== null && total !== 0 ? `${(value / total * 100).toFixed(1)}%` : '—';
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
                    fact: `${formatted.val} ${formatted.u}${share !== '—' ? ` · ${share} от денежных средств` : ''}`,
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
                ? 'Нужны конечные сальдо счетов 50, 51, 52 и 55 минимум на две сопоставимые даты с одинаковым составом счетов. ' + fullNcfTip
                : 'Показано изменение бухгалтерского остатка между двумя сопоставимыми датами. Это не классифицированный отчёт о движении денежных средств. ' + fullNcfTip;
            const cashTipData = {
                title: 'Денежные средства предприятия',
                description: 'Денежные средства на расчётных и иных счетах по данным бухгалтерского учёта. Средства на счёте 55 могут иметь ограничения по использованию.',
                formula: 'Денежные средства = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                detail: 'Счёт 50 — касса; 51 — расчётные счета; 52 — валютные счета в рублёвой оценке; 55 — специальные счета.',
                norm: 'Индивидуальный норматив предприятия не задан',
                fact: `${cashFormatted.val} ${cashFormatted.u}`,
                deviation: delta === null ? 'Нет сопоставимой предыдущей даты' : `${delta > 0 ? '+' : ''}${deltaFormatted.val} ${deltaFormatted.u}`,
                source: 'конечные сальдо загруженных ОСВ по счетам 50, 51, 52 и 55'
            };
            const changeTipData = {
                title: 'NCF / изменение денежных средств',
                description: 'Изменение бухгалтерского остатка денежных средств между двумя сопоставимыми датами. Без статей ДДС это не полный отчёт о денежных потоках.',
                formula: 'Изменение ДС = Остаток на конец периода − Остаток на конец предыдущего периода',
                detail: 'Сравнение выполняется только при одинаковом составе счетов 50, 51, 52 и 55.',
                required: delta === null ? changeTip : fullNcfTip,
                norm: 'Положительное значение не является универсальной нормой',
                fact: deltaText,
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
            document.getElementById('ki-modal-subtitle').textContent =
                `Детализация по данным ОСВ · ${state.data.period && state.data.period.label ? state.data.period.label : 'выбранный период'}`;
            container.innerHTML = `
                <div class="cash-info-banner"><strong>Денежные средства по данным бухгалтерского учёта.</strong>
                    Итог рассчитан как сумма чистых конечных сальдо: СКд − СКк по счетам 50, 51, 52 и 55.
                    Средства счёта 55 могут иметь ограничения по использованию.</div>
                <div class="cash-summary-grid">
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Денежные средства</div>
                        <div class="cash-summary-value cash-value-ok">${esc(cashFormatted.val)}</div>
                        <div class="cash-summary-sub">${esc(cashFormatted.u)} · бухгалтерский остаток</div>${this.cashTooltip(cashTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Изменение за период</div>
                        <div class="cash-summary-value ${delta === null ? 'cash-value-missing' : 'cash-value-ok'}">${esc(deltaText)}</div>
                        <div class="cash-summary-sub">к предыдущей сопоставимой дате</div>${this.cashTooltip(changeTipData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Текущая ликвидность</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет полного состава оборотных активов</div>${this.cashTooltip(currentLiquidityData)}</div>
                    <div class="cash-summary-card cash-tooltip-host" tabindex="0"><div class="cash-summary-label">Cash Runway</div>
                        <div class="cash-summary-value cash-value-missing">Нужны данные</div>
                        <div class="cash-summary-sub">нет очищенного платёжного потока</div>${this.cashTooltip(runwayData)}</div>
                </div>
                <div class="modal-section-title">Детализация по счетам денежных средств</div>
                <div style="overflow-x:auto"><table class="cash-table">
                    <thead><tr><th>Счёт / назначение</th><th>Сумма</th><th>Доля</th><th>Комментарий</th><th>Данные</th></tr></thead>
                    <tbody>${rows}<tr class="cash-tooltip-host" tabindex="0"><td><div>ИТОГО${this.cashTooltip(cashTipData)}</div></td><td>${esc(cashFormatted.val)} ${esc(cashFormatted.u)}</td><td>${total ? '100%' : '—'}</td><td>По загруженным счетам</td><td>—</td></tr></tbody>
                </table></div>
                <div class="modal-section-title">Показатели ликвидности — наведите для методики и требований к данным</div>
                <div class="cash-metrics-grid">
                    ${this.cashMetricCard('Денежные средства', `${cashFormatted.val} ${cashFormatted.u}`, 'СКд − СКк по счетам 50, 51, 52, 55', cashTipData, false)}
                    ${this.cashMetricCard('NCF / изменение ДС', deltaText, delta === null ? 'нет двух сопоставимых дат' : 'изменение бухгалтерского остатка', changeTipData, delta === null)}
                    ${this.cashMetricCard('Коэффициент текущей ликвидности', 'Нужны данные', 'оборотные активы / краткосрочные обязательства', currentLiquidityData, true)}
                    ${this.cashMetricCard('Коэффициент абсолютной ликвидности', 'Нужны данные', 'деньги / краткосрочные обязательства', absoluteLiquidityData, true)}
                    ${this.cashMetricCard('Cash Runway', 'Нужны данные', 'остаток / среднедневные внешние платежи', runwayData, true)}
                    ${this.cashMetricCard('Обеспеченность топливом', 'Нужны данные', 'натуральный запас / среднедневной расход', fuelData, true)}
                </div>
                <div class="modal-section-title">Динамика денежных средств</div>
                <div class="cash-chart-legend"><span><i class="cash-chart-dot" style="background:#3B82F6"></i>Остаток</span>
                    <span><i class="cash-chart-dot" style="background:#10B981"></i>Изменение к предыдущему месяцу</span></div>
                <div class="cash-line-chart" id="ki-cash-line-chart"></div>`;
            this.renderCashLineChart(card.series || []);
        },

        renderCashLineChart(series) {
            const container = document.getElementById('ki-cash-line-chart');
            if (!container) return;
            const points = series.map((item, index) => ({
                index, label: item.label, value: item.value === null ? null : Number(item.value) / 1000000
            }));
            const valid = points.filter(item => item.value !== null && Number.isFinite(item.value));
            if (!valid.length) {
                container.innerHTML = '<div class="chart-empty">Нет сопоставимых данных для графика</div>';
                return;
            }
            const changes = points.map((item, index) => ({
                index, label: item.label,
                value: index > 0 && item.value !== null && points[index - 1].value !== null
                    ? item.value - points[index - 1].value : null
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
                    path += `${drawing ? ' L' : ' M'} ${x(item.index).toFixed(1)} ${y(item.value).toFixed(1)}`;
                    drawing = true;
                });
                return path;
            };
            let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Динамика денежных средств">`;
            [min, (min + max) / 2, max].forEach(value => {
                const py = y(value);
                svg += `<line x1="${left}" y1="${py}" x2="${width-right}" y2="${py}" stroke="#E5E7EB" stroke-dasharray="3 3"/>
                    <text x="${left-6}" y="${py+4}" text-anchor="end" font-size="10" fill="#6B7280">${esc(value.toFixed(1))}</text>`;
            });
            svg += `<path d="${pathFor(points)}" fill="none" stroke="#3B82F6" stroke-width="3"/>
                <path d="${pathFor(changes)}" fill="none" stroke="#10B981" stroke-width="2" stroke-dasharray="5 4"/>`;
            points.forEach((item, index) => {
                if (item.value !== null && Number.isFinite(item.value)) {
                    svg += `<circle class="cash-chart-point" data-kind="balance" data-index="${index}" cx="${x(index)}" cy="${y(item.value)}" r="5" fill="#3B82F6" tabindex="0"/>`;
                }
                if (changes[index].value !== null) {
                    svg += `<circle class="cash-chart-point" data-kind="change" data-index="${index}" cx="${x(index)}" cy="${y(changes[index].value)}" r="4" fill="#10B981" tabindex="0"/>`;
                }
                svg += `<text x="${x(index)}" y="${height-10}" text-anchor="middle" font-size="9" fill="#6B7280">${esc(String(item.label || '').slice(0, 8))}</text>`;
            });
            container.innerHTML = svg + '</svg>';
            const pointTipData = point => {
                const index = Number(point.dataset.index);
                const isChange = point.dataset.kind === 'change';
                const item = isChange ? changes[index] : points[index];
                const previous = index > 0 ? points[index - 1] : null;
                return isChange ? {
                    title: `Изменение денежных средств — ${item.label}`,
                    description: 'Разница между остатком денежных средств текущего и предыдущего сопоставимого месяца.',
                    formula: 'Изменение = Остаток текущего месяца − Остаток предыдущего месяца',
                    detail: previous ? `Сравниваются ${previous.label} и ${item.label}. Используется одинаковый состав доступных счетов 50, 51, 52 и 55.` : 'Предыдущий сопоставимый месяц отсутствует.',
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.value >= 0 ? '+' : ''}${item.value.toFixed(2)} млн ₽`,
                    deviation: 'Фактическое изменение остатка',
                    source: 'месячные конечные сальдо ОСВ по денежным счетам'
                } : {
                    title: `Остаток денежных средств — ${item.label}`,
                    description: 'Бухгалтерский остаток денежных средств на конец выбранного месяца.',
                    formula: 'Остаток = Σ(СКд − СКк) по счетам 50, 51, 52, 55',
                    detail: 'Отсутствующие в выбранном месяце счета не принимаются за ноль.',
                    norm: 'Индивидуальный норматив не задан',
                    fact: `${item.value.toFixed(2)} млн ₽`,
                    deviation: changes[index].value === null ? 'Нет предыдущего сопоставимого месяца' : `${changes[index].value >= 0 ? '+' : ''}${changes[index].value.toFixed(2)} млн ₽ к предыдущему месяцу`,
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
            document.getElementById('ki-modal-subtitle').textContent = `ID: ${card.id} · Статус: ${card.status_label || card.status}`;
            
            const def = cardDefinitions.find(d => d.id === cardId);
            document.getElementById('ki-modal-icon').innerHTML = def ? def.icon : '<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';

            const cashContent = document.getElementById('ki-cash-modal-content');
            const genericContent = document.getElementById('ki-generic-modal-content');
            if (cardId === 'cash') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'block';
                this.renderCashModal(card);
            } else {
                cashContent.style.display = 'none';
                genericContent.style.display = 'block';
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
                    bdHtml += `<tr><td>${esc(b.label)}</td><td>${esc(f.val)} ${f.u ? `<span style="color:#9CA3AF;font-size:10px;">${esc(f.u)}</span>` : ''}</td></tr>`;
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
                    dtHtml += `<div class="waterfall-bar">
                        <div class="waterfall-label">${esc(d.label)}</div>
                        <div class="waterfall-value">${esc(f.val)} ${esc(f.u)}</div>
                        ${d.reason ? `<div style="flex:1;text-align:right;font-size:10px; color:#EF4444;">${esc(d.reason)}</div>` : ''}
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
                setTimeout(() => this.renderBarChart(card.series, card.unit, 'ki-modal-chart-container'), 50);
            } else {
                chTitle.style.display = 'none';
                chCont.style.display = 'none';
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
        
        closeModal() {
            const overlay = document.getElementById('ki-modal');
            overlay.classList.remove('visible');
            overlay.style.visibility = 'hidden';
            overlay.setAttribute('aria-hidden', 'true');
            if (state.lastFocused) state.lastFocused.focus();
        },
        
        renderBarChart(series, unit, containerId) {
            const container = document.getElementById(containerId);
            
            const valid = series.map(s => {
                let v = s.value;
                if (v !== null && unit === 'RUB') v = v / 1000000;
                return { ...s, scaledValue: v };
            }).filter(s => s.scaledValue !== null);
            
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
            
            const barW = Math.max(10, (w / series.length) * 0.6);
            const step = w / series.length;
            
            let svg = `<svg viewBox="0 0 ${width} ${height}" style="width:100%; height:100%; display:block;">`;
            
            const zeroY = padT + h - ((0 - minVal) / range) * h;
            
            [minVal, minVal + range/2, maxVal].forEach(val => {
                const y = padT + h - ((val - minVal) / range) * h;
                const fmt = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(val);
                svg += `<text x="${padL-5}" y="${y+4}" font-size="10" fill="#9CA3AF" text-anchor="end">${esc(fmt)}</text>`;
                svg += `<line x1="${padL}" y1="${y}" x2="${width-padR}" y2="${y}" stroke="#E5E7EB" stroke-dasharray="2,2"/>`;
            });
            
            svg += `<line x1="${padL}" y1="${zeroY}" x2="${width-padR}" y2="${zeroY}" stroke="#9CA3AF" stroke-width="1"/>`;
            
            series.forEach((s, i) => {
                const cx = padL + i * step + step / 2;
                let v = s.value;
                if (v !== null && unit === 'RUB') v = v / 1000000;
                
                if(v !== null) {
                    const y = padT + h - ((v - minVal) / range) * h;
                    const barH = Math.abs(zeroY - y);
                    const yStart = v >= 0 ? y : zeroY;
                    const fill = v >= 0 ? '#3B82F6' : '#EF4444';
                    svg += `<rect x="${cx - barW/2}" y="${yStart}" width="${barW}" height="${Math.max(1, barH)}" fill="${fill}" rx="4"/>`;
                    
                    const fmtVal = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(v);
                    svg += `<text x="${cx}" y="${v >= 0 ? y - 6 : y + barH + 12}" font-size="10" fill="#4B5563" text-anchor="middle" font-weight="bold">${esc(fmtVal)}</text>`;
                }
                svg += `<text x="${cx}" y="${height - 5}" font-size="10" fill="#6B7280" text-anchor="middle">${esc(s.label)}</text>`;
            });
            
            svg += '</svg>';
            container.innerHTML = svg;
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