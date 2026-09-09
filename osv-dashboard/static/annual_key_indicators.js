/* annual_key_indicators.js */
(function() {
    const config = window.KI_CONFIG || (typeof KI_CONFIG !== 'undefined' ? KI_CONFIG : null);
    
    const state = {
        active: false,
        year: null,
        data: null,
        reqId: 0
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
        
        init() {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('year')) {
                state.year = parseInt(urlParams.get('year'), 10);
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
            this.fetchData();
        },

        deactivate() {
            state.active = false;
            state.reqId++;
            if (abortController) abortController.abort();
        },

        onYearChange(yearStr) {
            const year = parseInt(yearStr, 10);
            if (state.year === year) return;
            state.year = year;
            
            const url = new URL(window.location);
            url.searchParams.set('year', year);
            window.history.replaceState({}, '', url);

            this.fetchData();
        },

        retry() {
            this.fetchData();
        },

        async fetchData() {
            if (!state.active) return;
            
            if (window.KI_App) window.KI_App.showLoading();
            document.getElementById('ki-error').style.display = 'none';
            
            if (abortController) abortController.abort();
            abortController = new AbortController();
            const reqId = ++state.reqId;
            
            try {
                if (!config || !config.annualDataUrl) throw new Error('Не настроен адрес источника годовых показателей.');
                let url = config.annualDataUrl;
                if (state.year) url += `?year=${encodeURIComponent(state.year)}`;
                
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
                state.year = data.year;
                
                this.render();
            } catch (e) {
                if (reqId !== state.reqId || e.name === 'AbortError') return;
                document.getElementById('ki-error-text').textContent = e.message;
                document.getElementById('ki-error').style.display = 'flex';
            } finally {
                if (reqId === state.reqId && window.KI_App) window.KI_App.hideLoading();
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

        render() {
            if (!state.data || !state.active) return;
            
            const subtitle = document.getElementById('ki-topbar-subtitle');
            subtitle.textContent = config.companyName;
            
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
                    buttons.innerHTML = state.data.years.map(y =>
                        `<button type="button" class="annual-year-btn${y === state.data.year ? ' active' : ''}" ` +
                        `data-year="${y}" aria-pressed="${y === state.data.year ? 'true' : 'false'}" ` +
                        `onclick="window.KI_AnnualApp.onYearChange(${y})">${y}</button>`
                    ).join('');
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

        renderCards() {
            const grid = document.getElementById('ki-annual-cards-grid');
            if (!state.data.cards) return;
            
            let html = '';
            
            // Expected cards: revenue, cogs, net_profit, profitability, cash, ar, ap, equity, assets, current_liquidity
            const order = ['revenue', 'cogs', 'net_profit', 'profitability', 'cash', 'ar', 'ap', 'equity', 'assets', 'current_liquidity'];
            
            order.forEach(id => {
                const card = state.data.cards.find(c => c.id === id);
                if (!card) return;
                
                let isNull = (card.value === null || card.value === undefined);
                let formatted = this.formatValue(card.value, card.unit, card.upper_bound);
                
                let deltaHtml = '';
                let hasDelta = card.delta !== null && card.delta !== undefined;
                let comparisonNote = card.comparison_note ? ` · ${card.comparison_note}` : '';

                if (!isNull && hasDelta) {
                    let dClass = 'neutral';
                    if (card.delta > 0) dClass = 'up';
                    if (card.delta < 0) dClass = 'down';
                    
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
                } else if (!isNull && state.data.previous_year) {
                    deltaHtml = `<div class="kpi-delta neutral">Нет данных за ${state.data.previous_year}</div>`;
                }
                
                const ariaLabel = `${card.title}. Значение: ${formatted.val} ${formatted.u}.`;
                const statusClass = 'grey';
                
                html += `
                <div class="kpi-card status-${statusClass} clickable" data-metric="${esc(id)}" tabindex="0" onclick="window.KI_AnnualApp.openModal('${esc(id)}')" role="button" aria-label="${esc(ariaLabel)}">
                  <div class="kpi-label">${esc(card.title)} <span class="status-dot ${statusClass}" style="opacity:0.3"></span></div>
                  <div class="kpi-value">${esc(formatted.val)}<span class="kpi-unit">${esc(formatted.u)}</span></div>
                  <div style="font-size:12px;color:#6B7280;margin-bottom:8px">${esc(card.period_label)} ${esc(state.data.year)}</div>
                  ${deltaHtml}
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
                state.data.sources.forEach(src => {
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
            const card = state.data.cards.find(c => c.id === cardId);
            if (!card) return;
            
            if (window.KI_App) {
                window.KI_App.state.lastFocused = document.activeElement;
            }
            
            document.getElementById('ki-modal-title').textContent = card.title;
            document.getElementById('ki-modal-subtitle').textContent = `${card.period_label} ${state.data.year} · Источник: годовая аналитика 1С`;
            
            const defIcon = '<svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" fill="none" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>';
            document.getElementById('ki-modal-icon').innerHTML = defIcon;

            const rb = document.getElementById('ki-modal-reason-banner');
            if (card.missing_reason) {
                rb.style.display = 'block';
                rb.innerHTML = `<strong>Внимание:</strong> ${esc(card.missing_reason)}`;
            } else {
                rb.style.display = 'none';
            }
            
            let provText = 'Нет данных';
            if (card.provenance) {
                provText = `Файл: ${card.provenance.filename}, Лист: ${card.provenance.sheet}, Ячейка: ${card.provenance.cell}`;
            }
            document.getElementById('ki-modal-formula').textContent = card.formula || 'Показатель из годовой отчетности 1С';
            document.getElementById('ki-modal-source').textContent = provText;
            
            document.getElementById('ki-modal-breakdown-title').style.display = 'none';
            document.getElementById('ki-modal-breakdown-container').style.display = 'none';
            
            const chCont = document.getElementById('ki-modal-chart-container');
            const chTitle = document.getElementById('ki-modal-chart-title');
            if (card.series && card.series.length > 0 && state.data.timeline_years) {
                chTitle.style.display = 'block';
                this.renderModalChart(card.series, state.data.timeline_years, card.unit, 'ki-modal-chart-container');
                chCont.style.display = 'block';
            } else {
                chTitle.style.display = 'none';
                chCont.style.display = 'none';
            }
            
            document.getElementById('ki-modal-details-title').style.display = 'none';
            document.getElementById('ki-modal-details-container').style.display = 'none';
            
            const modal = document.getElementById('ki-modal');
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
                const cx = padL + i * step;
                
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
        }
    };

    window.KI_AnnualApp = AnnualApp;
    document.addEventListener('DOMContentLoaded', () => {
        if (window.KI_AnnualApp) {
            window.KI_AnnualApp.init();
        }
    });
})();