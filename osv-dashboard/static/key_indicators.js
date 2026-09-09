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
                const metricTooltip = this.cashTooltip(this.metricTooltipData(card));
                
                html += `
                <div class="kpi-card status-${statusClass} clickable cash-tooltip-host" data-metric="${esc(id)}" tabindex="0" onclick="window.KI_App.openModal('${esc(id)}')" role="button" aria-label="${esc(ariaLabel)}">
                  <div class="kpi-label">${esc(card.title)} <span class="status-dot ${statusClass}"></span></div>
                  <div class="kpi-value">${esc(formatted.val)}<span class="kpi-unit">${esc(formatted.u)}</span></div>
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
                description: definition.description || 'Фактическое значение показателя по данным бухгалтерского учёта.',
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
            document.getElementById('ki-modal-subtitle').textContent =
                `Детализация фактической выручки · ${state.data.period && state.data.period.label ? state.data.period.label : 'выбранный период'}`;
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
                <div class="modal-section-title">Динамика выручки за 12 месяцев</div>
                <div class="cash-chart-legend"><span><i class="cash-chart-dot" style="background:#10B981"></i>Выручка общая</span></div>
                <div class="cash-line-chart revenue-line-chart" id="ki-revenue-line-chart"></div>`;
            this.renderRevenueLineChart(card);
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
            const points = (card.series || []).slice(-12).map((item, index) => ({
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
                svg += `<text x="${x(index)}" y="${height-10}" text-anchor="middle" font-size="9" fill="#6B7280">${esc(String(item.label || '').slice(0, 8))}</text>`;
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
                        deviation: change === null ? 'Нет предыдущего сопоставимого месяца' : `${change >= 0 ? '+' : ''}${change.toFixed(2)} млн ₽ к предыдущему месяцу`,
                        detail: 'Точка отражает фактическую общую выручку за месяц. Пропущенные периоды не заменяются нулём.'
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

            setTimeout(() => {
                this.renderBarChart(card.turnover_series || [], '%', 'ki-collection-turnover-chart', {
                    id: 'collection',
                    title: 'Соотношение оборотов счёта 62',
                    formula: 'ОбК(62) / ОбД(62) × 100%',
                    source: 'месячные обороты ОСВ счёта 62',
                    reason: 'Показатель не является фактической собираемостью'
                });
                this.renderBarChart(arCard.series || [], 'RUB', 'ki-collection-ar-chart', arCard);
            }, 50);
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
                deviation: 'Фактическое подтверждённое сальдо 62.01',
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
            const rows = analytics.length ? analytics.map(row => {
                const share = current ? row.value / current * 100 : null;
                const analyticTip = this.arTooltip({
                    title: `ДЗ по аналитике — ${row.label}`,
                    description: 'Дебетовое сальдо 62.01 по фактическому пути аналитики из ОСВ.',
                    formula: 'max(СКд − СКк, 0) по аналитике 62.01',
                    detail: 'Строка не называется сегментом потребителей до загрузки справочника категорий.',
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
                    ${this.collectionCell(`<strong>${esc(row.label)}</strong>`, analyticTip)}
                    ${this.collectionCell(esc(rub(row.value)), analyticTip)}
                    ${this.collectionCell(esc(pct(share)), shareTip)}
                    ${this.collectionCell('Нужны данные', overdueTip, 'cash-value-missing')}
                    ${this.collectionCell('Нужны данные', overdueTip, 'cash-value-missing')}
                    ${this.collectionCell('Недостаточно данных', statusTip, 'cash-value-missing')}
                </tr>`;
            }).join('') : `<tr><td colspan="6" class="chart-empty">В ОСВ 62.01 нет аналитических сальдо на выбранную дату. ${esc(required.segments)}</td></tr>`;

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
                    ${this.cashMetricCard('Подтверждённая ДЗ', rub(current), 'сальдо 62.01', totalTip, current === null || current === undefined)}
                    ${this.cashMetricCard('Изменение ДЗ', change === null ? 'Нужны данные' : `${change >= 0 ? '+' : ''}${rub(change)}`, 'за выбранный период', changeTip, change === null)}
                    ${this.cashMetricCard('Оценочный DSO', dso === null ? 'Нужны данные' : `${dso.toFixed(0)} дней`, 'бухгалтерская оценка', dsoTip, dso === null)}
                    ${this.cashMetricCard('ДЗ / Выручка', pct(ratio), period.label || 'выбранный период', ratioTip, ratio === null)}
                </div>
                <div class="modal-section-title">Динамика подтверждённой ДЗ 62.01</div>
                <div class="modal-chart-wrap ar-line-chart" id="ki-ar-line-chart"></div>
                <div class="modal-section-title">Детализация по фактическим аналитикам 62.01</div>
                <div class="revenue-classification-note">Аналитики не относятся к населению, УК/ТСЖ, бюджету или другим сегментам автоматически. Для этого нужен справочник категорий должников из 1С.</div>
                <div class="collection-table-wrap"><table class="collection-detail-table ar-detail-table">
                    <thead><tr><th>Аналитика 1С</th><th>ДЗ</th><th>Доля</th><th>Просрочка</th><th>Возраст долга</th><th>Статус</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>
                <div class="modal-section-title">Недоступные показатели макета</div>
                <div class="revenue-missing-grid ar-missing-grid">
                    ${this.cashMetricCard('Просрочка >180 дней', 'Нужны данные', 'документы и сроки оплаты', overdueTip, true)}
                    ${this.cashMetricCard('Старение задолженности', 'Нужны данные', 'непогашенные остатки по документам', agingTip, true)}
                    ${this.cashMetricCard('ДЗ по сегментам', 'Нужны данные', 'категории должников', this.arTooltip({title:'ДЗ по сегментам потребителей', required: required.segments}), true)}
                    ${this.cashMetricCard('ДЗ по субсидиям', 'Нужны данные', 'отдельный регистр компенсаций', subsidyTip, true)}
                </div>`;
            setTimeout(() => this.renderBarChart(
                card.series || [], 'RUB', 'ki-ar-line-chart', card), 50);
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
            setTimeout(() => this.renderBarChart(
                card.series || [], 'RUB', 'ki-ap-line-chart', card), 50);
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
            const revenueContent = document.getElementById('ki-revenue-modal-content');
            const collectionContent = document.getElementById('ki-collection-modal-content');
            const arContent = document.getElementById('ki-ar-modal-content');
            const apContent = document.getElementById('ki-ap-modal-content');
            const genericContent = document.getElementById('ki-generic-modal-content');
            if (cardId === 'cash') {
                genericContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                cashContent.style.display = 'block';
                this.renderCashModal(card);
            } else if (cardId === 'revenue') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                revenueContent.style.display = 'block';
                this.renderRevenueModal(card);
            } else if (cardId === 'collection') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
                collectionContent.style.display = 'block';
                this.renderCollectionModal(card);
            } else if (cardId === 'ar') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                apContent.style.display = 'none';
                arContent.style.display = 'block';
                this.renderArModal(card);
            } else if (cardId === 'ap') {
                genericContent.style.display = 'none';
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'block';
                this.renderApModal(card);
            } else {
                cashContent.style.display = 'none';
                revenueContent.style.display = 'none';
                collectionContent.style.display = 'none';
                arContent.style.display = 'none';
                apContent.style.display = 'none';
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
                setTimeout(() => this.renderBarChart(card.series, card.unit, 'ki-modal-chart-container', card), 50);
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
        
        renderBarChart(series, unit, containerId, card) {
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
                    svg += `<rect class="metric-chart-point" data-index="${i}" x="${cx - barW/2}" y="${yStart}" width="${barW}" height="${Math.max(1, barH)}" fill="${fill}" rx="4" tabindex="0"/>`;
                    
                    const fmtVal = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 0, maximumFractionDigits: 1}).format(v);
                    svg += `<text x="${cx}" y="${v >= 0 ? y - 6 : y + barH + 12}" font-size="10" fill="#4B5563" text-anchor="middle" font-weight="bold">${esc(fmtVal)}</text>`;
                }
                svg += `<text x="${cx}" y="${height - 5}" font-size="10" fill="#6B7280" text-anchor="middle">${esc(s.label)}</text>`;
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