(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const csrfToken = $('meta[name="csrf-token"]')?.content || "";
  const state = {
    page: 1, perPage: 50, sort: "debt", direction: "desc",
    lastFocus: null, currentRow: null, previewToken: null, filters: null,
    mapData: null, mapGeometry: null, mapLoadPromise: null,
    month: "", selectedDistricts: [], pendingDistricts: [], organizationQuery: "",
    minDebt: "", maxDebt: "",
    leftMode: "overview"
  };
  const els = {
    month: $("#period-filter"), organizationQuery: $("#organization-filter-query"),
    districtSearch: $("#omcu-search"), districtOptions: $("#omcu-options"),
    debtMin: $("#debt-min"), debtMax: $("#debt-max"),
    rows: $("#rows-body"), rowsStatus: $("#rows-status"),
    imports: $("#imports-list"), importsStatus: $("#imports-status")
  };
  const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
  const moneyFormat = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }
  function display(value, fallback = "—") {
    return value === null || value === undefined || value === "" ? fallback : String(value);
  }
  function fmtNumber(value) {
    if (value === null || value === undefined || value === "") return "—";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numberFormat.format(numeric) : String(value);
  }
  function fmtMoney(value) {
    if (value === null || value === undefined || value === "") return "—";
    const numeric = Number(value);
    return Number.isFinite(numeric) ? moneyFormat.format(numeric) + " ₽" : String(value);
  }
  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }
  function setMessage(element, message, type) {
    element.textContent = message;
    element.className = "live-message" + (type ? " " + type : "");
  }
  function makeOption(value, label, selected) {
    return `<option value="${escapeHtml(value)}"${String(value) === String(selected) ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }
  function getParams() {
    const params = new URLSearchParams({
      month: state.month,
      resource: "all",
      q: state.organizationQuery,
      page: String(state.page),
      per_page: String(state.perPage),
      sort: state.sort,
      direction: state.direction
    });
    state.selectedDistricts.forEach(name => params.append("district", name));
    if (state.minDebt !== "") params.set("debt_min", state.minDebt);
    if (state.maxDebt !== "") params.set("debt_max", state.maxDebt);
    return params;
  }
  async function requestJson(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    const response = await fetch(url, { credentials: "same-origin", ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!String(response.headers.get("content-type") || "").includes("application/json")) {
      throw new Error("Ответ сервера не является данными кабинета. Возможно, сессия завершилась — войдите снова.");
    }
    if (!response.ok) throw new Error(payload.message || payload.error || `Ошибка запроса (${response.status})`);
    return payload;
  }
  function renderSummary(summary) {
    const values = [
      [summary.imports, "Загрузок", "за выбранный период"],
      [summary.sourceRows, "Строк в источниках", "как передано поставщиками"],
      [summary.dataRows, "Строк данных", "учтено в выборке"],
      [summary.matchedDebts, "Совпавших долгов", "значение из источника"],
      [fmtMoney(summary.debtTotal), "Сумма долга", "без пересчёта"],
      [summary.missingInn, "ИНН отсутствует", "в строках данных"],
      [summary.duplicateInn, "Повторяющиеся ИНН", "требуют проверки"],
      [summary.groupRows, "Групповых строк", "помечено в источнике"],
      [summary.coverage == null ? "—" : (typeof summary.coverage === "number" ? `${numberFormat.format(summary.coverage)}%` : summary.coverage), "Покрытие", "показатель API"],
      [summary.warningCount, "Предупреждения", "по загруженным файлам"]
    ];
    const cards = $$("#summary-grid .metric");
    values.forEach((item, index) => {
      const card = cards[index];
      if (!card) return;
      $(".metric-value", card).textContent = item[0] === "—" ? "—" : fmtNumber(item[0]);
      $(".metric-label", card).textContent = item[1];
      $(".metric-note", card).textContent = item[2];
      $(".metric-value", card).classList.toggle("small", index === 4);
    });
  }
  function renderMonthly(monthly) {
    const chart = $("#monthly-chart");
    if (!Array.isArray(monthly) || !monthly.length) {
      chart.innerHTML = '<div class="chart-empty">За выбранный период нет помесячных значений из источников.</div>';
      return;
    }
    const max = Math.max(...monthly.map(item => Number(item.debt)).filter(Number.isFinite), 0);
    if (max <= 0) {
      chart.innerHTML = '<div class="chart-empty">Суммы для диаграммы не переданы.</div>';
      return;
    }
    chart.innerHTML = monthly.map(item => {
      const number = Number(item.debt);
      const height = Number.isFinite(number) ? Math.max(3, number / max * 81) : 3;
      const label = item.label || item.month || "—";
      return `<div class="chart-item" title="${escapeHtml(label)}: ${escapeHtml(fmtMoney(item.debt))}">
        <span class="chart-value">${escapeHtml(fmtCompact(item.debt))}</span>
        <div class="chart-bar" style="height:${height}%"></div>
        <span class="chart-label">${escapeHtml(label)}</span>
      </div>`;
    }).join("");
  }
  function fmtCompact(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "—";
    if (Math.abs(numeric) >= 1000000) return numberFormat.format(numeric / 1000000) + " млн";
    if (Math.abs(numeric) >= 1000) return numberFormat.format(numeric / 1000) + " тыс.";
    return numberFormat.format(numeric);
  }
  function renderMap(map) {
    const content = $("#map-content");
    const toggle = $("#map-mode-toggle");
    state.mapData = map || null;
    if (!map || map.available !== true || !map.geometryUrl) {
      const message = map?.note || "Проверенная геометрия территориальных границ не передана.";
      content.innerHTML = `<div class="map-unavailable"><div class="map-message"><div class="map-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 6.5 8.5 4l7 2.5L21 4v13.5L15.5 20l-7-2.5L3 20zM8.5 4v13.5M15.5 6.5V20"/></svg></div><strong>Геометрия недоступна</strong><p>${escapeHtml(message)}</p></div></div>`;
      toggle.disabled = true;
      if (state.leftMode === "map") setLeftMode(false);
      return;
    }
    toggle.disabled = false;
    if (state.mapGeometry && !$("#municipality-map")) renderMapGeometry(state.mapGeometry);
    syncMapSelection();
  }
  function setLeftMode(showMap) {
    if (showMap && !state.mapData?.available) return;
    state.leftMode = showMap ? "map" : "overview";
    $("#overview-view").hidden = showMap;
    $("#map-panel").hidden = !showMap;
    const toggle = $("#map-mode-toggle");
    toggle.setAttribute("aria-expanded", String(showMap));
    toggle.setAttribute("aria-pressed", String(showMap));
    toggle.setAttribute("aria-label", showMap ? "Показать показатели" : "Показать карту");
    toggle.title = showMap ? "Показать показатели" : "Показать карту";
    $("#map-mode-icon").hidden = showMap;
    $("#overview-mode-icon").hidden = !showMap;
    if (showMap) loadMapGeometry();
  }
  function municipalityPolygons(geometry) {
    if (geometry?.type === "Polygon") return [geometry.coordinates || []];
    if (geometry?.type === "MultiPolygon") return geometry.coordinates || [];
    return [];
  }
  function collectPositions(value, positions) {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) {
      positions.push([Number(value[0]), Number(value[1])]);
      return;
    }
    value.forEach(item => collectPositions(item, positions));
  }
  function mapPathData(feature, project) {
    return municipalityPolygons(feature.geometry).map(polygon => polygon.map(ring => {
      if (!Array.isArray(ring) || ring.length < 4) return "";
      return ring.map((position, index) => {
        const [x, y] = project(position);
        return `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
      }).join(" ") + " Z";
    }).filter(Boolean).join(" ")).filter(Boolean).join(" ");
  }
  function renderMapGeometry(collection) {
    const content = $("#map-content");
    const features = Array.isArray(collection?.features) ? collection.features : [];
    const positions = [];
    features.forEach(feature => collectPositions(feature.geometry?.coordinates, positions));
    if (!features.length || !positions.length) {
      content.innerHTML = '<div class="map-error"><strong>Карта недоступна</strong>Файл не содержит полигонов для отображения.</div>';
      return;
    }

    const width = 760, height = 700, padding = 18;
    let minLatitude = Infinity, maxLatitude = -Infinity;
    positions.forEach(([, latitude]) => {
      minLatitude = Math.min(minLatitude, latitude);
      maxLatitude = Math.max(maxLatitude, latitude);
    });
    const latitudeFactor = Math.cos(((minLatitude + maxLatitude) / 2) * Math.PI / 180);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    positions.forEach(([longitude, latitude]) => {
      const x = longitude * latitudeFactor;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, latitude);
      maxY = Math.max(maxY, latitude);
    });
    const scale = Math.min(
      (width - padding * 2) / Math.max(maxX - minX, 0.000001),
      (height - padding * 2) / Math.max(maxY - minY, 0.000001)
    );
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    const project = ([longitude, latitude]) => [
      width / 2 + (longitude * latitudeFactor - centerX) * scale,
      height / 2 - (latitude - centerY) * scale
    ];
    const paths = features.map(feature => {
      const name = feature.properties?.territory;
      const d = mapPathData(feature, project);
      if (!name || !d) return "";
      const safeName = escapeHtml(name);
      return `<path class="map-area" data-territory="${safeName}" d="${d}" fill-rule="evenodd" clip-rule="evenodd" tabindex="0" role="button" aria-label="Выбрать ${safeName}"><title>${safeName}</title></path>`;
    }).join("");
    if (!paths) {
      content.innerHTML = '<div class="map-error"><strong>Карта недоступна</strong>Геометрию не удалось отобразить.</div>';
      return;
    }
    content.innerHTML = `<svg id="municipality-map" class="map-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Карта муниципальных образований Московской области">${paths}</svg>`;
    $$(".map-area", content).forEach(path => {
      const select = () => selectMapTerritory(path.dataset.territory);
      path.addEventListener("click", select);
      path.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
    });
    state.mapGeometry = collection;
    syncMapSelection();
  }
  function loadMapGeometry() {
    if (state.mapGeometry) {
      syncMapSelection();
      return Promise.resolve(true);
    }
    if (state.mapLoadPromise) return state.mapLoadPromise;
    if (!state.mapData?.geometryUrl) return Promise.resolve(false);
    const content = $("#map-content");
    content.innerHTML = '<div class="map-loading">Загружаем векторные границы…</div>';
    state.mapLoadPromise = fetch(state.mapData.geometryUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/geo+json, application/json" }
    }).then(async response => {
      if (!response.ok) throw new Error(`Ошибка загрузки карты (${response.status})`);
      const geometry = await response.json();
      if (!Array.isArray(geometry.features) || geometry.features.length !== 56) {
        throw new Error("В файле карты ожидались 56 территориальных полигонов.");
      }
      renderMapGeometry(geometry);
      return true;
    }).catch(error => {
      content.innerHTML = `<div class="map-error"><strong>Не удалось загрузить карту</strong>${escapeHtml(error.message)}<br><button class="button" type="button" id="retry-map" style="margin-top:10px">Повторить</button></div>`;
      $("#retry-map").addEventListener("click", loadMapGeometry);
      return false;
    }).finally(() => { state.mapLoadPromise = null; });
    return state.mapLoadPromise;
  }
  function syncMapSelection() {
    const selected = state.selectedDistricts || [];
    const label = $("#map-selected");
    if (label) {
      label.textContent = !selected.length
        ? "Все территории"
        : selected.length === 1
          ? `Выбрано: ${selected[0]}`
          : `Выбрано территорий: ${numberFormat.format(selected.length)}`;
    }
    $$(".map-area").forEach(path => {
      const isSelected = selected.includes(path.dataset.territory);
      path.classList.toggle("is-selected", isSelected);
      path.setAttribute("aria-pressed", String(isSelected));
    });
  }
  function selectMapTerritory(name) {
    const optionExists = (state.filters?.districts || [])
      .some(option => option.name === name);
    if (!optionExists) {
      $("#map-selected").textContent = `Территория «${name}» отсутствует в списке фильтров.`;
      return;
    }
    state.selectedDistricts = state.selectedDistricts.includes(name)
      ? state.selectedDistricts.filter(value => value !== name)
      : [...state.selectedDistricts, name];
    state.pendingDistricts = [...state.selectedDistricts];
    state.page = 1;
    syncMapSelection();
    renderDistrictOptions();
    updateHeaderFilterIndicators();
    loadDashboard();
  }
  function renderRows(rows, pagination) {
    const list = Array.isArray(rows) ? rows : [];
    els.rows.innerHTML = "";
    if (!list.length) {
      els.rows.innerHTML = '<tr><td colspan="5"><div class="empty-state"><strong>Строки не найдены</strong>Измените условия поиска или проверьте наличие отчётов за выбранный месяц.</div></td></tr>';
    } else {
      els.rows.innerHTML = list.map((row, index) => {
        const statusClass = /ошиб|предуп|не сопостав/i.test(row.status || "") ? "warn" : "";
        const rowKey = escapeHtml(row.id);
        return `<tr tabindex="0" role="button" aria-label="Открыть строку: ${escapeHtml(display(row.name, "Без названия"))}, ИНН ${escapeHtml(display(row.inn))}" data-row-index="${index}" data-row-id="${rowKey}">
          <td><div class="company-cell">${escapeHtml(display(row.name, "Без названия"))}${row.isGroupRow ? ' <span class="tag">групповая строка</span>' : ""}</div><div class="subcell">${escapeHtml(display(row.owner, display(row.file)))}</div>${row.status ? `<div class="subcell"><span class="badge ${statusClass}">${escapeHtml(row.status)}</span></div>` : ""}</td>
          <td><div class="mono">${escapeHtml(display(row.inn))}</div><div class="subcell">${escapeHtml(display(row.kpp))}</div></td>
          <td>${escapeHtml(display(row.territory))}${row.detail ? `<div class="subcell">${escapeHtml(row.detail)}</div>` : ""}</td>
          <td><div>${escapeHtml(display(row.month))} ${escapeHtml(display(row.year, ""))}</div></td>
          <td><div class="amount">${escapeHtml(fmtMoney(row.debt))}</div><div class="subcell">${escapeHtml(display(row.periods, "Периоды не указаны"))}</div></td>
        </tr>`;
      }).join("");
      $$("#rows-body tr[data-row-index]").forEach(tr => {
        const row = list[Number(tr.dataset.rowIndex)];
        tr.addEventListener("click", () => openDetails(row));
        tr.addEventListener("keydown", event => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetails(row); }
        });
      });
    }
    const page = pagination || {};
    const current = Number(page.page || state.page);
    const perPage = Number(page.perPage || state.perPage);
    const total = Number(page.total || 0);
    const pages = Number(page.totalPages || 0);
    const from = total ? (current - 1) * perPage + 1 : 0;
    const to = Math.min(current * perPage, total);
    $("#pagination-label").textContent = total ? `Показаны ${numberFormat.format(from)}–${numberFormat.format(to)} из ${numberFormat.format(total)}` : "Нет строк";
    $("#page-number").textContent = pages ? `${current} / ${pages}` : "—";
    $("#page-prev").disabled = current <= 1;
    $("#page-next").disabled = !pages || current >= pages;
    $("#rows-caption").textContent = `${numberFormat.format(total)} строк · переход к исходной строке`;
  }
  function renderImports(imports) {
    const data = Array.isArray(imports) ? imports : [];
    els.imports.innerHTML = "";
    if (!data.length) {
      els.imports.innerHTML = '<div class="empty-state"><strong>Загрузок пока нет</strong>После импорта исходные файлы появятся в журнале.</div>';
      return;
    }
    els.imports.innerHTML = data.slice(0, 8).map(item => `<div class="import-item">
      <div><a class="import-file" href="/ministry/import/${encodeURIComponent(item.id)}/file" title="${escapeHtml(display(item.file))}">${escapeHtml(display(item.file, "Файл без названия"))}</a>
        <div class="import-meta">Владелец данных: ${escapeHtml(display(item.owner, "не указан"))} · загрузил: ${escapeHtml(display(item.uploader, "не указан"))}</div>
        <div class="import-meta">${escapeHtml(display(item.month))} ${escapeHtml(display(item.year, ""))} · ${escapeHtml(display(item.resource))} · версия ${escapeHtml(display(item.version))}</div>
        <div class="import-meta">${escapeHtml(formatDate(item.createdAt))}${Number(item.warningCount) ? ` · предупреждений: ${escapeHtml(item.warningCount)}` : ""}</div></div>
      <div class="import-count">${escapeHtml(fmtNumber(item.rowCount))} строк</div>
    </div>`).join("");
  }
  function setOptions(select, values, selected, allLabel) {
    const options = [];
    if (allLabel) options.push(makeOption("all", allLabel, selected || "all"));
    (values || []).forEach(item => {
      const value = typeof item === "object" ? (item.value ?? item.id ?? item.name) : item;
      const label = typeof item === "object" ? (item.label ?? item.name ?? item.value) : item;
      options.push(makeOption(value, label, selected));
    });
    select.innerHTML = options.join("");
  }
  function applyFilters(filters) {
    state.filters = filters || {};
    const selected = filters?.selected || {};
    setOptions(els.month, filters?.months || [], selected.month || state.month || "");
    if (!els.month.options.length) els.month.innerHTML = makeOption("", "Нет доступных периодов", "");
    state.month = selected.month || els.month.value || "";
    state.selectedDistricts = Array.isArray(selected.districts)
      ? selected.districts
      : (selected.district && selected.district !== "all" ? [selected.district] : []);
    state.organizationQuery = selected.q ?? state.organizationQuery;
    state.minDebt = selected.debtMin ?? "";
    state.maxDebt = selected.debtMax ?? "";
    els.organizationQuery.value = state.organizationQuery;
    els.debtMin.value = state.minDebt;
    els.debtMax.value = state.maxDebt;
    state.pendingDistricts = [...state.selectedDistricts];
    renderDistrictOptions();
    updateHeaderFilterIndicators();
    syncMapSelection();
  }
  function renderDistrictOptions() {
    if (!els.districtOptions) return;
    const search = (els.districtSearch.value || "").trim().toLocaleLowerCase("ru");
    const selected = new Set(state.pendingDistricts);
    const districts = (state.filters?.districts || []).filter(item => {
      if (!item || item.name === "unassigned") return false;
      return `${item.label || ""} ${item.name || ""}`.toLocaleLowerCase("ru").includes(search);
    });
    els.districtOptions.innerHTML = districts.length
      ? districts.map((item, index) => {
        const name = String(item.name || "");
        const label = String(item.label || name);
        const id = `omcu-option-${index}`;
        return `<label class="omcu-option" for="${id}"><input id="${id}" type="checkbox" data-omcu-option value="${escapeHtml(name)}"${selected.has(name) ? " checked" : ""}><span>${escapeHtml(label)}</span></label>`;
      }).join("")
      : '<div class="empty-state">Совпадений не найдено.</div>';
    updateOmcuSelectionLabel();
  }
  function updateOmcuSelectionLabel() {
    const label = $("#omcu-selection-label");
    if (!label) return;
    const count = state.pendingDistricts.length;
    label.textContent = count ? `${numberFormat.format(count)} из 56 выбрано` : "Все ОМСУ";
  }
  function updateHeaderFilterIndicators() {
    const count = $("#omcu-filter-count");
    if (count) {
      count.textContent = state.selectedDistricts.length ? String(state.selectedDistricts.length) : "";
      count.hidden = !state.selectedDistricts.length;
    }
    $("#debt-filter-toggle")?.classList.toggle(
      "has-filter", state.minDebt !== "" || state.maxDebt !== "");
    $("#org-filter-toggle")?.classList.toggle(
      "has-filter", Boolean(state.organizationQuery));
  }
  function updateDebtSortIndicator() {
    const indicator = $("#debt-sort-indicator");
    if (indicator) {
      indicator.textContent = state.direction === "asc" ? "↑" : "↓";
      indicator.hidden = state.sort !== "debt";
    }
  }
  function closeHeaderFilterPopovers() {
    $$(".header-filter-popover").forEach(popover => {
      popover.hidden = true;
      const button = $(`[aria-controls="${popover.id}"]`);
      button?.setAttribute("aria-expanded", "false");
    });
  }
  function toggleHeaderFilter(toggleId, popoverId) {
    const button = $(`#${toggleId}`);
    const popover = $(`#${popoverId}`);
    if (!button || !popover) return;
    const shouldOpen = popover.hidden;
    closeHeaderFilterPopovers();
    if (!shouldOpen) return;
    popover.hidden = false;
    button.setAttribute("aria-expanded", "true");
    if (popoverId === "omcu-filter-popover") {
      state.pendingDistricts = [...state.selectedDistricts];
      renderDistrictOptions();
      els.districtSearch.focus();
    } else if (popoverId === "org-filter-popover") {
      els.organizationQuery.focus();
    } else if (popoverId === "debt-range-popover") {
      els.debtMin.value = state.minDebt;
      els.debtMax.value = state.maxDebt;
      $("#debt-range-error").hidden = true;
      els.debtMin.focus();
    }
  }
  async function loadDashboard() {
    els.rowsStatus.className = "status-line";
    els.rowsStatus.textContent = "Загружаем строки отчётов…";
    els.importsStatus.className = "status-line";
    els.importsStatus.textContent = "Загрузка журнала…";
    try {
      const data = await requestJson(`/ministry/api/data?${getParams().toString()}`);
      if (data.filters) applyFilters(data.filters);
      renderSummary(data.summary || {});
      renderMonthly(data.monthly || []);
      renderRows(data.rows || [], data.pagination);
      renderImports(data.imports || []);
      renderMap(data.map);
      els.rowsStatus.textContent = data.rows?.length ? `Источник: ${numberFormat.format(data.rows.length)} строк на этой странице. Показаны реквизиты и происхождение из отчёта.` : "По заданным фильтрам нет строк.";
      els.importsStatus.textContent = data.imports?.length ? "Сведения о загрузках получены из журнала." : "Загрузки не зарегистрированы.";
      $("#chart-period").textContent = display(els.month.selectedOptions[0]?.textContent, "");
    } catch (error) {
      els.rowsStatus.className = "status-line error";
      els.rowsStatus.innerHTML = `Не удалось загрузить сведения: ${escapeHtml(error.message)} <button class="button" type="button" id="retry-data" style="min-height:28px;margin-left:8px">Повторить</button>`;
      $("#retry-data").addEventListener("click", loadDashboard);
      els.rows.innerHTML = "";
      $("#pagination-label").textContent = "Данные недоступны";
      $("#page-number").textContent = "—";
      $("#page-prev").disabled = true;
      $("#page-next").disabled = true;
      els.importsStatus.className = "status-line error";
      els.importsStatus.textContent = "Журнал загрузок недоступен.";
      els.imports.innerHTML = "";
    }
  }
  function detailPair(label, value) {
    return `<div class="detail-pair"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(display(value))}</dd></div>`;
  }
  function openDetails(row) {
    if (!row) return;
    state.currentRow = row;
    state.lastFocus = document.activeElement;
    const backdrop = $("#detail-backdrop");
    const drawer = $(".detail-drawer", backdrop);
    backdrop.hidden = false;
    document.body.style.overflow = "hidden";
    $("#detail-title").textContent = display(row.name, "Детали строки");
    $("#detail-subtitle").textContent = `${display(row.inn, "ИНН не указан")} · ${display(row.resource)} · ${display(row.month)} ${display(row.year, "")}`;
    const content = $("#detail-content");
    content.innerHTML = `<section class="detail-card"><h3>Реквизиты и исходные значения</h3><dl class="detail-grid">
      ${detailPair("ИНН", row.inn)}${detailPair("КПП", row.kpp)}${detailPair("Поставщик", row.owner)}
      ${detailPair("Территория", row.territory)}${detailPair("Уточнение", row.detail)}${detailPair("Ресурс", row.resource)}
      ${detailPair("Отчётный период", `${display(row.month)} ${display(row.year, "")}`)}${detailPair("Статус строки", row.status)}
      ${detailPair("Долг из отчёта", fmtMoney(row.debt))}${detailPair("Периоды", row.periods)}
      ${detailPair("Текущие начисления", fmtMoney(row.currentPeriodAccrual))}${detailPair("Счёт / лист", `${display(row.sheet)} / ${display(row.sourceRow)}`)}
      ${detailPair("Исходный файл", row.file)}${detailPair("Версия файла", row.version)}
       ${detailPair("Загрузил", row.uploader)}${detailPair("Идентификатор загрузки", row.id)}
       </dl><p><a class="button" href="/ministry/import/${encodeURIComponent(row.importId)}/file">Скачать исходный XLSX</a></p></section>
       <div class="provenance"><strong>Происхождение.</strong> Значения приведены из строки ${escapeHtml(display(row.sourceRow))} листа «${escapeHtml(display(row.sheet))}» файла «${escapeHtml(display(row.file))}». Автоматические итоги не добавляются.</div>
       ${Array.isArray(row.sourceValues) && row.sourceValues.length ? `<section class="detail-card"><h3>Исходные значения строки по столбцам</h3><ol>${row.sourceValues.map((value, index) => `<li><span class="mono">${index + 1}.</span> ${escapeHtml(display(value, "(пусто)"))}</li>`).join("")}</ol></section>` : ""}
      <section class="detail-card"><h3>Проверка реквизитов по открытым данным</h3>
        <div class="dadata-row"><div class="field"><label for="lookup-inn">ИНН для поиска</label><input id="lookup-inn" value="${escapeHtml(row.inn || "")}" inputmode="numeric" aria-label="ИНН для поиска по справочнику"></div><button type="button" class="button" id="lookup-company"${row.inn ? "" : " disabled"}>Проверить ИНН</button></div>
        <div id="lookup-result" class="live-message" role="status">Справочные данные не применяются автоматически к отчётной строке.</div>
      </section>
      <section class="detail-card"><h3>Доступная история по ИНН</h3><div id="debtor-history" class="timeline"><div class="empty-state">Загружаем историю…</div></div></section>`;
    const innInput = $("#lookup-inn");
    $("#lookup-company").addEventListener("click", () => lookupInn(innInput.value));
    innInput.addEventListener("input", () => { $("#lookup-company").disabled = !innInput.value.trim(); });
    drawer.focus();
    loadDebtorHistory(row);
  }
  async function loadDebtorHistory(row) {
    const target = $("#debtor-history");
    if (!row.inn) {
      target.innerHTML = '<div class="empty-state">Для просмотра истории нужен ИНН в исходной строке.</div>';
      return;
    }
    try {
      const data = await requestJson(`/ministry/api/debtors/${encodeURIComponent(row.inn)}`);
      const company = data.company;
      const history = Array.isArray(data.history) ? data.history : [];
      const rows = Array.isArray(data.rows) ? data.rows : [];
      const companyMarkup = company ? `<section class="detail-card"><h3>Справочные сведения о должнике</h3><dl class="detail-grid">
        ${detailPair("Наименование", company.name)}${detailPair("ИНН", company.inn)}
        ${detailPair("КПП", company.kpp)}${detailPair("Адрес", company.address)}
        ${detailPair("Источник справочника", company.source || company.provenance)}
        ${detailPair("Снимок DaData", company.dadataSource ? `${company.dadataSource} · ${formatDate(company.dadataReceivedAt)}` : "Не запрашивался")}</dl></section>` : "";
      const timeline = [...history, ...rows].map(item => `<div class="timeline-row"><span class="timeline-date">${escapeHtml(display(item.month, display(item.date)))}</span><span>${escapeHtml(display(item.name, display(item.resource, "Строка отчёта")))}<span class="subcell">${escapeHtml(display(item.file, display(item.owner)))}</span></span><span class="amount">${escapeHtml(fmtMoney(item.debt))}</span></div>`).join("");
      target.innerHTML = companyMarkup + (timeline ? `<div class="timeline">${timeline}</div>` : '<div class="empty-state">История по этому ИНН не найдена среди доступных отчётов.</div>') + (data.hasMore ? '<p class="source-note">Для удобства просмотра показаны первые 500 исходных строк.</p>' : "");
    } catch (error) {
      target.innerHTML = `<div class="empty-state"><strong>История недоступна</strong>${escapeHtml(error.message)}</div>`;
    }
  }
  async function lookupInn(inn) {
    const result = $("#lookup-result");
    if (!inn.trim()) return;
    result.className = "live-message";
    result.textContent = "Выполняется поиск. Изменения не будут внесены в отчёт.";
    try {
      const data = await requestJson("/ministry/api/dadata/lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ inn: inn.trim() })
      });
      const snapshot = data.snapshot || {};
      const company = snapshot.normalized || data.normalized || data.company || {};
      const provenance = [
        snapshot.source || data.provenance || "DaData",
        snapshot.actualityDate ? `актуальность ${snapshot.actualityDate}` : "",
        snapshot.receivedAt ? `получено ${formatDate(snapshot.receivedAt)}` : "",
        data.message || ""
      ].filter(Boolean).join(" · ");
      result.className = "live-message";
      result.innerHTML = `<span><strong>${escapeHtml(display(company.name, "Совпадение не найдено"))}</strong><br>ИНН: ${escapeHtml(display(company.inn, inn))}${company.kpp ? ` · КПП: ${escapeHtml(company.kpp)}` : ""}<br>Источник / совпадение: ${escapeHtml(typeof provenance === "string" ? provenance : JSON.stringify(provenance))}<br><em>Справочные данные показаны для проверки и не применены к отчёту.</em></span>`;
    } catch (error) {
      result.className = "live-message error";
      result.textContent = `Не удалось выполнить поиск: ${error.message}`;
    }
  }
  function closeDetail() {
    $("#detail-backdrop").hidden = true;
    document.body.style.overflow = "";
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }
  function openImport() {
    state.lastFocus = document.activeElement;
    $("#import-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    $("#report-file").focus();
  }
  function closeImport() {
    $("#import-backdrop").hidden = true;
    document.body.style.overflow = "";
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }
  function renderPreview(data) {
    state.previewToken = data.previewToken;
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const preview = $("#preview-result");
    const warningList = Array.isArray(data.warnings) && data.warnings.length
      ? `<ul class="warning-list">${data.warnings.map(warning => `<li>${escapeHtml(typeof warning === "string" ? warning : (warning.message || JSON.stringify(warning)))}</li>`).join("")}</ul>`
      : '<div class="source-note" style="padding:10px 12px">Предупреждений от проверки нет.</div>';
    const tableRows = rows.slice(0, 12).map(row => `<tr><td>${escapeHtml(display(row.name))}</td><td class="mono">${escapeHtml(display(row.inn))}</td><td>${escapeHtml(display(row.territory))}</td><td class="amount">${escapeHtml(fmtMoney(row.debt))}</td><td>${escapeHtml(display(row.sourceRow))}</td></tr>`).join("");
    preview.innerHTML = `<section class="preview-box" aria-label="Предварительный просмотр импорта">
      <div class="preview-summary">
        <div class="preview-stat"><span>Поставщик</span><strong>${escapeHtml(display(data.owner))}</strong></div>
        <div class="preview-stat"><span>Период</span><strong>${escapeHtml(display(data.period))}</strong></div>
        <div class="preview-stat"><span>Строк данных</span><strong>${escapeHtml(fmtNumber(data.dataRows))}</strong></div>
        <div class="preview-stat"><span>Предупреждения</span><strong>${escapeHtml(fmtNumber(data.warningCount))}</strong></div>
      </div>
      <div class="status-line">Файл: ${escapeHtml(display(data.file))} · ресурс: ${escapeHtml(display(data.resource))} · строк источника: ${escapeHtml(fmtNumber(data.rowCount))}</div>
      <div class="status-line">Владелец данных: ${escapeHtml(display(data.owner))} · в заголовке файла: ${escapeHtml(display(data.sourceSupplier, "не указано"))}${data.sourceSupplierInn ? ` · ИНН ${escapeHtml(data.sourceSupplierInn)}` : ""}</div>
      ${warningList}
      <div class="preview-table"><table aria-label="Первые строки файла"><thead><tr><th>Организация</th><th>ИНН</th><th>Территория</th><th>Долг</th><th>Строка</th></tr></thead><tbody>${tableRows || '<tr><td colspan="5">Проверка не вернула строки для отображения.</td></tr>'}</tbody></table></div>
      <div class="preview-actions"><button class="button" type="button" id="cancel-preview">Отмена</button><button class="button primary" type="button" id="confirm-import"${data.previewToken ? "" : " disabled"}>Подтвердить импорт</button></div>
    </section>`;
    $("#cancel-preview").addEventListener("click", () => { state.previewToken = null; preview.innerHTML = ""; });
    $("#confirm-import").addEventListener("click", applyImport);
  }
  async function submitPreview(event) {
    event.preventDefault();
    const file = $("#report-file").files[0];
    const providerId = $("#provider-id").value.trim();
    if (!file || !providerId) return;
    const button = $("#preview-button");
    button.disabled = true;
    button.textContent = "Проверка…";
    $("#preview-result").innerHTML = "";
    setMessage($("#preview-message"), "Файл передан для предварительной проверки.", "");
    const formData = new FormData();
    formData.append("file", file);
    formData.append("provider_id", providerId);
    formData.append("csrf_token", csrfToken);
    try {
      const data = await requestJson("/ministry/import/preview", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: formData
      });
      if (!data.previewToken) throw new Error("Сервер не вернул токен предварительного просмотра.");
      renderPreview(data);
      setMessage($("#preview-message"), "Проверьте предупреждения и строки. Импорт ещё не выполнен.", "");
    } catch (error) {
      setMessage($("#preview-message"), `Не удалось проверить файл: ${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Проверить файл";
    }
  }
  async function applyImport(event) {
    const button = event.currentTarget;
    if (!state.previewToken) return;
    if (!window.confirm("Подтвердить импорт проверенного отчёта? Загруженные данные будут добавлены в кабинет.")) return;
    button.disabled = true;
    button.textContent = "Импорт…";
    const form = new URLSearchParams({ preview_token: state.previewToken, csrf_token: csrfToken });
    try {
      const data = await requestJson("/ministry/import/apply", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "X-CSRFToken": csrfToken },
        body: form.toString()
      });
      if (data.ok === false) throw new Error(data.message || "Импорт отклонён.");
      setMessage($("#preview-message"), data.message || "Импорт выполнен.", "success");
      state.previewToken = null;
      $("#preview-result").innerHTML = "";
      $("#preview-form").reset();
      await loadDashboard();
      window.setTimeout(closeImport, 900);
    } catch (error) {
      setMessage($("#preview-message"), `Импорт не выполнен: ${error.message}`, "error");
      button.disabled = false;
      button.textContent = "Подтвердить импорт";
    }
  }
  function setupMapToggle() {
    $("#map-mode-toggle").addEventListener("click", () => {
      setLeftMode(state.leftMode !== "map");
    });
  }
  function trapDialogKeys(event, element) {
    if (event.key === "Escape") {
      if (!$("#detail-backdrop").hidden) closeDetail();
      else if (!$("#import-backdrop").hidden) closeImport();
    }
    if (event.key !== "Tab") return;
    const dialog = element;
    const focusable = $$('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]', dialog)
      .filter(node => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function init() {
    $("#user-toggle").addEventListener("click", () => {
      const menu = $("#user-menu");
      const open = menu.classList.toggle("open");
      $("#user-toggle").setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", event => {
      const menu = $("#user-menu");
      if (!menu.contains(event.target)) {
        menu.classList.remove("open");
        $("#user-toggle").setAttribute("aria-expanded", "false");
      }
      if (!event.target.closest(".header-filter-anchor")) {
        closeHeaderFilterPopovers();
      }
    });
    document.addEventListener("keydown", event => {
      if (!$("#detail-backdrop").hidden) trapDialogKeys(event, $(".detail-drawer"));
      else if (!$("#import-backdrop").hidden) trapDialogKeys(event, $(".import-dialog"));
      else if (event.key === "Escape" && $("#user-menu").classList.contains("open")) {
        $("#user-menu").classList.remove("open");
        $("#user-toggle").setAttribute("aria-expanded", "false");
        $("#user-toggle").focus();
      } else if (event.key === "Escape" && $$(".header-filter-popover:not([hidden])").length) {
        closeHeaderFilterPopovers();
      }
    });
    $("#close-detail").addEventListener("click", closeDetail);
    $("#detail-backdrop").addEventListener("click", event => { if (event.target === event.currentTarget) closeDetail(); });
    $("#open-import").addEventListener("click", openImport);
    $("#close-import").addEventListener("click", closeImport);
    $("#import-backdrop").addEventListener("click", event => { if (event.target === event.currentTarget) closeImport(); });
    $("#preview-form").addEventListener("submit", submitPreview);
    [
      ["org-filter-toggle", "org-filter-popover"],
      ["omcu-filter-toggle", "omcu-filter-popover"],
      ["period-filter-toggle", "period-filter-popover"],
      ["debt-filter-toggle", "debt-range-popover"]
    ].forEach(([toggleId, popoverId]) => {
      $(`#${toggleId}`).addEventListener("click", () => toggleHeaderFilter(toggleId, popoverId));
    });
    const applyOrganizationFilter = () => {
      state.organizationQuery = els.organizationQuery.value.trim();
      state.page = 1;
      updateHeaderFilterIndicators();
      closeHeaderFilterPopovers();
      loadDashboard();
    };
    $("#org-filter-apply").addEventListener("click", applyOrganizationFilter);
    $("#org-filter-clear").addEventListener("click", () => {
      els.organizationQuery.value = "";
      applyOrganizationFilter();
    });
    els.organizationQuery.addEventListener("keydown", event => {
      if (event.key === "Enter") { event.preventDefault(); applyOrganizationFilter(); }
    });
    els.districtSearch.addEventListener("input", renderDistrictOptions);
    els.districtOptions.addEventListener("change", event => {
      if (!event.target.matches("[data-omcu-option]")) return;
      state.pendingDistricts = $$("[data-omcu-option]:checked", els.districtOptions)
        .map(input => input.value);
      updateOmcuSelectionLabel();
    });
    $("#omcu-select-all").addEventListener("click", () => {
      state.pendingDistricts = (state.filters?.districts || [])
        .filter(item => item.name !== "unassigned").map(item => item.name);
      renderDistrictOptions();
    });
    $("#omcu-clear").addEventListener("click", () => {
      state.pendingDistricts = [];
      renderDistrictOptions();
    });
    $("#omcu-apply").addEventListener("click", () => {
      state.selectedDistricts = [...state.pendingDistricts];
      state.page = 1;
      updateHeaderFilterIndicators();
      syncMapSelection();
      closeHeaderFilterPopovers();
      loadDashboard();
    });
    $("#period-filter-apply").addEventListener("click", () => {
      state.month = els.month.value || "";
      state.page = 1;
      closeHeaderFilterPopovers();
      loadDashboard();
    });
    const applyDebtRange = () => {
      const min = els.debtMin.value.trim();
      const max = els.debtMax.value.trim();
      const error = $("#debt-range-error");
      const minNumber = min === "" ? null : Number(min);
      const maxNumber = max === "" ? null : Number(max);
      if ((minNumber !== null && !Number.isFinite(minNumber))
        || (maxNumber !== null && !Number.isFinite(maxNumber))) {
        error.textContent = "Введите корректные числовые границы.";
        error.hidden = false;
        return;
      }
      if (minNumber !== null && maxNumber !== null && minNumber > maxNumber) {
        error.textContent = "Минимум не может быть больше максимума.";
        error.hidden = false;
        return;
      }
      state.minDebt = min;
      state.maxDebt = max;
      state.page = 1;
      updateHeaderFilterIndicators();
      closeHeaderFilterPopovers();
      loadDashboard();
    };
    $("#debt-filter-apply").addEventListener("click", applyDebtRange);
    $("#debt-filter-clear").addEventListener("click", () => {
      els.debtMin.value = "";
      els.debtMax.value = "";
      applyDebtRange();
    });
    $("#reset-filters").addEventListener("click", () => {
      state.month = state.filters?.months?.[0]?.value || els.month.options[0]?.value || "";
      els.month.value = state.month;
      state.selectedDistricts = [];
      state.pendingDistricts = [];
      state.organizationQuery = "";
      state.minDebt = "";
      state.maxDebt = "";
      els.organizationQuery.value = "";
      els.debtMin.value = "";
      els.debtMax.value = "";
      state.page = 1;
      updateHeaderFilterIndicators();
      syncMapSelection();
      closeHeaderFilterPopovers();
      loadDashboard();
    });
    $$("[data-sort]").forEach(button => button.addEventListener("click", () => {
      const sort = button.dataset.sort;
      state.direction = state.sort === sort && state.direction === "asc" ? "desc" : "asc";
      state.sort = sort;
      updateDebtSortIndicator();
      state.page = 1;
      loadDashboard();
    }));
    $("#page-prev").addEventListener("click", () => { if (state.page > 1) { state.page--; loadDashboard(); } });
    $("#page-next").addEventListener("click", () => { state.page++; loadDashboard(); });
    setupMapToggle();
    loadDashboard();
  }
  init();
})();
