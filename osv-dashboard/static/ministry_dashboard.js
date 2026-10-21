(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const csrfToken = $('meta[name="csrf-token"]')?.content || "";
  const state = {
    page: 1, perPage: 50, sort: "debt", direction: "desc",
    lastFocus: null, currentRow: null, detailRequestId: 0, previewToken: null, filters: null,
    mapData: null, mapGeometry: null, mapLoadPromise: null,
    month: "", selectedDistricts: [], pendingDistricts: [],
    selectedResources: [], pendingResources: [], organizationQuery: "",
    minDebt: "", maxDebt: "",
    leftMode: "overview", importHistory: [], canDeleteImports: false,
    visibleColumns: null
  };
  const els = {
    month: $("#period-filter"), organizationQuery: $("#organization-filter-query"),
    districtSearch: $("#omcu-search"), districtOptions: $("#omcu-options"),
    resourceOptions: $("#resource-options"),
    debtMin: $("#debt-min"), debtMax: $("#debt-max"),
    rows: $("#rows-body"), rowsStatus: $("#rows-status"),
    tableHeader: $("#table-header-scroll"), tableScroll: $("#table-scroll")
  };
  const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
  const moneyFormat = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const TABLE_OPTIONAL_COLUMNS = [
    { key: "resource", label: "Вид ресурса", description: "Например, тепло или электроэнергия." },
    { key: "detail", label: "Детализация потребителя", description: "Текст из одноимённого поля исходного отчёта." },
    { key: "periods", label: "Периоды задолженности", description: "Срок просрочки, если он указан." },
    { key: "currentPeriodAccrual", label: "Начислено за период", description: "Сумма начисления за отчётный период." },
    { key: "yearToDateAccrual", label: "Начислено с начала года", description: "Сумма из исходного отчёта." },
    { key: "status", label: "Статус строки", description: "Статус, переданный в отчёте." },
    { key: "owner", label: "Поставщик данных", description: "Организация, загрузившая отчёт." },
    { key: "file", label: "Исходный файл", description: "Имя книги с этой строкой." },
    { key: "sourceRow", label: "Строка источника", description: "Номер строки и переход к карточке источника." }
  ];
  const DEFAULT_VISIBLE_COLUMNS = ["resource", "detail"];
  const TABLE_COLUMN_STORAGE_KEY = "ministry-data-table-columns-v1";

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
  function loadVisibleColumns() {
    try {
      const stored = JSON.parse(window.localStorage.getItem(TABLE_COLUMN_STORAGE_KEY) || "null");
      if (Array.isArray(stored)) {
        const known = new Set(TABLE_OPTIONAL_COLUMNS.map(column => column.key));
        return new Set(stored.filter(key => known.has(key)));
      }
    } catch (_) {
      // Use the cabinet defaults if browser storage is unavailable or malformed.
    }
    return new Set(DEFAULT_VISIBLE_COLUMNS);
  }
  function applyTableColumnVisibility() {
    const selected = state.visibleColumns || new Set(DEFAULT_VISIBLE_COLUMNS);
    const optional = new Set(TABLE_OPTIONAL_COLUMNS.map(column => column.key));
    $$("[data-column]").forEach(element => {
      const key = element.dataset.column;
      element.hidden = optional.has(key) && !selected.has(key);
    });
    const count = $("#visible-column-count");
    if (count) count.textContent = String(selected.size);
  }
  function saveVisibleColumns() {
    try {
      window.localStorage.setItem(
        TABLE_COLUMN_STORAGE_KEY,
        JSON.stringify([...state.visibleColumns])
      );
      const status = $("#data-tool-save-status");
      if (status) status.textContent = "Настройки сохранены в этом браузере.";
    } catch (_) {
      const status = $("#data-tool-save-status");
      if (status) status.textContent = "Не удалось сохранить настройки в браузере.";
    }
  }
  function renderColumnSettings() {
    const content = $("#data-tools-content");
    const visible = state.visibleColumns || new Set(DEFAULT_VISIBLE_COLUMNS);
    content.innerHTML = `<p class="data-tool-note">Основные столбцы организации, ИНН/КПП, ОМСУ, периода и долга остаются на месте. Дополнительные поля можно включать по необходимости; изменения применяются сразу.</p>
      <div class="column-settings-list">${TABLE_OPTIONAL_COLUMNS.map(column => `<label class="column-setting"><input type="checkbox" data-column-toggle="${escapeHtml(column.key)}"${visible.has(column.key) ? " checked" : ""}><span>${escapeHtml(column.label)}<small>${escapeHtml(column.description)}</small></span></label>`).join("")}</div>
      <p class="data-tool-note">В текущей структуре отчёта сохраняется «Детализация потребителя»; отдельный числовой объём потребления не выделен.</p>
      <div class="data-tool-footer"><span class="data-tool-save-status" id="data-tool-save-status">Настройки сохранены в этом браузере.</span><button class="button" type="button" id="reset-column-settings">По умолчанию</button></div>`;
    $$("[data-column-toggle]", content).forEach(input => {
      input.addEventListener("change", () => {
        const key = input.dataset.columnToggle;
        if (input.checked) state.visibleColumns.add(key);
        else state.visibleColumns.delete(key);
        applyTableColumnVisibility();
        saveVisibleColumns();
      });
    });
    $("#reset-column-settings").addEventListener("click", () => {
      state.visibleColumns = new Set(DEFAULT_VISIBLE_COLUMNS);
      applyTableColumnVisibility();
      saveVisibleColumns();
      renderColumnSettings();
    });
  }
  function renderImportHistory() {
    const list = $("#import-history-list");
    if (!list) return;
    const search = ($("#import-history-search").value || "").trim().toLocaleLowerCase("ru");
    const imports = [...state.importHistory].sort((left, right) => {
      const leftDate = Date.parse(left.createdAt || "") || 0;
      const rightDate = Date.parse(right.createdAt || "") || 0;
      return rightDate - leftDate || Number(right.id || 0) - Number(left.id || 0);
    });
    const filtered = imports.filter(item => [
      item.file, item.owner, item.sourceSupplier, item.resource,
      item.year, item.month, item.version
    ].join(" ").toLocaleLowerCase("ru").includes(search));
    const count = $("#import-history-result-count");
    if (count) count.textContent = `Показано ${fmtNumber(filtered.length)} из ${fmtNumber(imports.length)}`;
    list.innerHTML = filtered.length
      ? filtered.map(item => {
        const period = item.year && item.month
          ? `${String(item.month).padStart(2, "0")}.${item.year}`
          : "Период не указан";
        const warnings = Number(item.warningCount || 0);
        return `<li class="import-history-item">
          <time class="import-history-date">${escapeHtml(formatDate(item.createdAt))}</time>
          <div><span class="import-history-file">${escapeHtml(display(item.file, "Файл без имени"))}</span>
            <div class="import-history-meta">${escapeHtml(display(item.owner))} · ${escapeHtml(period)} · ${escapeHtml(display(item.resource))}<br>Версия ${escapeHtml(display(item.version))} · строк в источнике: ${escapeHtml(fmtNumber(item.rowCount))} · строк данных: ${escapeHtml(fmtNumber(item.dataRowCount))}${item.uploader ? ` · загрузил: ${escapeHtml(item.uploader)}` : ""}</div>
          </div>
          <div class="import-history-badges">${item.isLatest ? '<span class="import-history-badge">Актуальная версия</span>' : ""}${warnings ? `<span class="import-history-badge">Предупреждений: ${escapeHtml(fmtNumber(warnings))}</span>` : ""}<a class="button" href="/ministry/import/${encodeURIComponent(item.id)}/file" download>Скачать</a>${state.canDeleteImports ? `<button class="button import-history-delete" type="button" data-delete-import="${Number(item.id)}" aria-label="Удалить загрузку ${escapeHtml(display(item.file, "без имени"))}" title="Удалить загрузку">Удалить загрузку</button>` : ""}</div>
        </li>`;
      }).join("")
      : '<li class="empty-state">Загрузки по этому запросу не найдены.</li>';
  }
  async function deleteImportHistoryItem(importIdValue, button) {
    const importId = Number(importIdValue);
    const item = state.importHistory.find(entry => Number(entry.id) === importId);
    if (!Number.isInteger(importId) || importId <= 0 || !item) return;
    const currentVersionImpact = item.isLatest
      ? "Эта версия сейчас актуальна. Если есть предыдущая версия, она станет актуальной; если её нет, данные этого отчёта исчезнут из кабинета."
      : "Другие версии отчёта останутся без изменений.";
    const confirmed = window.confirm(
      `Удалить загрузку «${item.file || "Файл без имени"}»?\n\n`
      + `Будут безвозвратно удалены ${fmtNumber(item.dataRowCount)} строк отчёта и исходный XLSX-файл.\n`
      + `${currentVersionImpact}\n\nДействие нельзя отменить.`,
    );
    if (!confirmed) return;

    const status = $("#import-history-action-status");
    button.disabled = true;
    status.hidden = false;
    status.className = "import-history-status";
    status.textContent = "Удаляем загрузку…";
    try {
      const result = await requestJson(
        `/ministry/import/${importId}/delete`,
        { method: "POST", headers: { "X-CSRFToken": csrfToken } },
      );
      state.importHistory = state.importHistory.filter(
        entry => Number(entry.id) !== importId,
      );
      $("#import-history-count").textContent = fmtNumber(state.importHistory.length);
      renderImportHistory();
      status.className = `import-history-status${result.fileCleanupPending ? " warning" : " success"}`;
      status.textContent = result.message || "Загрузка удалена.";
      await loadDashboard();
      renderImportHistory();
    } catch (error) {
      status.className = "import-history-status error";
      status.textContent = `Не удалось удалить загрузку: ${error.message}`;
      button.disabled = false;
    }
  }
  function openDataTools(mode) {
    state.lastFocus = document.activeElement;
    const historyMode = mode === "history";
    $("#data-tools-title").textContent = historyMode ? "Хронология загрузок" : "Настройка столбцов таблицы";
    $("#data-tools-subtitle").textContent = historyMode
      ? "Последние 100 принятых файлов — от новых к старым."
      : "Выберите дополнительные поля, которые нужно видеть в таблице.";
    const content = $("#data-tools-content");
    if (historyMode) {
      content.innerHTML = `<div class="import-history-toolbar"><input class="import-history-search" id="import-history-search" type="search" placeholder="Поиск по файлу, поставщику или ресурсу" aria-label="Поиск в истории загрузок"><span class="import-history-count" id="import-history-result-count"></span></div><div class="import-history-status" id="import-history-action-status" role="status" hidden></div><ol class="import-history-list" id="import-history-list"></ol>`;
      renderImportHistory();
      $("#import-history-search").addEventListener("input", renderImportHistory);
      $("#import-history-list").addEventListener("click", event => {
        const button = event.target.closest("[data-delete-import]");
        if (button) void deleteImportHistoryItem(button.dataset.deleteImport, button);
      });
    } else {
      renderColumnSettings();
    }
    $("#data-tools-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    const firstControl = historyMode ? $("#import-history-search") : $("[data-column-toggle]");
    (firstControl || $(".data-tools-dialog")).focus();
  }
  function closeDataTools() {
    $("#data-tools-backdrop").hidden = true;
    document.body.style.overflow = "";
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
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
    if (state.selectedResources.length) {
      params.delete("resource");
      state.selectedResources.forEach(name => params.append("resource", name));
    }
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
    if (list.length) {
      els.rows.innerHTML = list.map((row, index) => {
        const statusClass = /ошиб|предуп|не сопостав/i.test(row.status || "") ? "warn" : "";
        const rowKey = escapeHtml(row.id);
        return `<tr tabindex="0" role="button" aria-label="Открыть строку: ${escapeHtml(display(row.name, "Без названия"))}, ИНН ${escapeHtml(display(row.inn))}" data-row-index="${index}" data-row-id="${rowKey}">
          <td data-column="organization"><div class="company-cell">${escapeHtml(display(row.name, "Без названия"))}${row.isGroupRow ? ' <span class="tag">групповая строка</span>' : ""}</div><div class="subcell">${escapeHtml(display(row.owner, display(row.file)))}</div></td>
          <td data-column="inn"><div class="mono">${escapeHtml(display(row.inn))}</div><div class="subcell">${escapeHtml(display(row.kpp))}</div></td>
          <td data-column="district">${escapeHtml(display(row.district))}</td>
          <td data-column="period"><div>${escapeHtml(display(row.month))} ${escapeHtml(display(row.year, ""))}</div></td>
          <td data-column="debt"><div class="amount">${escapeHtml(fmtMoney(row.debt))}</div></td>
          <td data-column="resource">${escapeHtml(display(row.resource))}</td>
          <td data-column="detail">${escapeHtml(display(row.detail))}</td>
          <td data-column="periods">${row.periods == null ? "—" : escapeHtml(fmtNumber(row.periods))}</td>
          <td data-column="currentPeriodAccrual">${escapeHtml(fmtMoney(row.currentPeriodAccrual))}</td>
          <td data-column="yearToDateAccrual">${escapeHtml(fmtMoney(row.yearToDateAccrual))}</td>
          <td data-column="status">${row.status ? `<span class="badge ${statusClass}">${escapeHtml(row.status)}</span>` : "—"}</td>
          <td data-column="owner">${escapeHtml(display(row.owner))}</td>
          <td data-column="file">${escapeHtml(display(row.file))}</td>
          <td data-column="sourceRow">${escapeHtml(display(row.sourceRow))}</td>
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
    applyTableColumnVisibility();
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
    state.selectedResources = Array.isArray(selected.resources)
      ? selected.resources
      : (selected.resource && selected.resource !== "all"
        ? [selected.resource] : []);
    state.pendingResources = [...state.selectedResources];
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
    renderResourceOptions();
    updateHeaderFilterIndicators();
    syncMapSelection();
  }
  function renderResourceOptions() {
    if (!els.resourceOptions) return;
    const resources = (state.filters?.resources || [])
      .map(value => String(value || "").trim())
      .filter(Boolean);
    const selected = new Set(state.pendingResources);
    els.resourceOptions.innerHTML = resources.length
      ? resources.map((name, index) => {
        const id = `resource-option-${index}`;
        return `<label class="resource-option" for="${id}"><input id="${id}" type="checkbox" data-resource-option value="${escapeHtml(name)}"${selected.has(name) ? " checked" : ""}><span>${escapeHtml(name)}</span></label>`;
      }).join("")
      : '<div class="empty-state">Нет доступных видов ресурса.</div>';
    updateResourceSelectionLabel();
  }
  function updateResourceSelectionLabel() {
    const label = $("#resource-selection-label");
    if (!label) return;
    const total = (state.filters?.resources || []).length;
    const count = state.pendingResources.length;
    label.textContent = !total || !count || count === total
      ? "Все ресурсы"
      : `${numberFormat.format(count)} из ${numberFormat.format(total)} выбрано`;
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
        const label = String(item.label || name)
          .replace(/^Городской округ\s+/i, "г.о. ")
          .replace(/^Муниципальный округ\s+/i, "м.о. ");
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
    const resourceCount = $("#resource-filter-count");
    if (resourceCount) {
      resourceCount.textContent = state.selectedResources.length
        ? String(state.selectedResources.length) : "";
      resourceCount.hidden = !state.selectedResources.length;
    }
    $("#resource-filter-toggle")?.classList.toggle(
      "has-filter", state.selectedResources.length > 0);
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
  function positionHeaderFilterPopover(button, popover) {
    const anchor = button.getBoundingClientRect();
    const margin = 12;
    const gap = 8;
    const width = popover.getBoundingClientRect().width;
    const desiredLeft = popover.classList.contains("align-right")
      ? anchor.right - width
      : anchor.left;
    const maxLeft = Math.max(margin, window.innerWidth - width - margin);
    const left = Math.min(Math.max(margin, desiredLeft), maxLeft);
    const height = popover.getBoundingClientRect().height;
    const belowTop = anchor.bottom + gap;
    const spaceBelow = window.innerHeight - belowTop - margin;
    const spaceAbove = anchor.top - gap - margin;
    const desiredTop = height <= spaceBelow || spaceBelow >= spaceAbove
      ? belowTop
      : anchor.top - height - gap;
    const maxTop = Math.max(margin, window.innerHeight - height - margin);
    const top = Math.min(Math.max(margin, desiredTop), maxTop);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.right = "auto";
  }
  function repositionOpenHeaderFilterPopover() {
    const popover = $(".header-filter-popover:not([hidden])");
    if (!popover) return;
    const button = $(`[aria-controls="${popover.id}"]`);
    if (button) positionHeaderFilterPopover(button, popover);
  }
  function toggleHeaderFilter(toggleId, popoverId) {
    const button = $(`#${toggleId}`);
    const popover = $(`#${popoverId}`);
    if (!button || !popover) return;
    const shouldOpen = popover.hidden;
    closeHeaderFilterPopovers();
    if (!shouldOpen) return;
    if (popover.parentElement !== document.body) document.body.append(popover);
    popover.classList.add("is-portaled");
    popover.hidden = false;
    button.setAttribute("aria-expanded", "true");
    if (popoverId === "omcu-filter-popover") {
      state.pendingDistricts = [...state.selectedDistricts];
      renderDistrictOptions();
      els.districtSearch.focus();
    } else if (popoverId === "resource-filter-popover") {
      state.pendingResources = [...state.selectedResources];
      renderResourceOptions();
      $("#resource-options input")?.focus();
    } else if (popoverId === "org-filter-popover") {
      els.organizationQuery.focus();
    } else if (popoverId === "debt-range-popover") {
      els.debtMin.value = state.minDebt;
      els.debtMax.value = state.maxDebt;
      $("#debt-range-error").hidden = true;
      els.debtMin.focus();
    }
    positionHeaderFilterPopover(button, popover);
  }
  async function loadDashboard() {
    els.rowsStatus.className = "table-state-overlay";
    els.rowsStatus.hidden = false;
    els.rowsStatus.innerHTML = '<span class="table-loading-spinner" aria-hidden="true"></span><span>Загружаем строки отчётов…</span>';
    $("#pagination-label").textContent = "Загружаем строки отчётов…";
    $("#page-number").textContent = "—";
    $("#page-prev").disabled = true;
    $("#page-next").disabled = true;
    try {
      const data = await requestJson(`/ministry/api/data?${getParams().toString()}`);
      if (data.filters) applyFilters(data.filters);
      state.canDeleteImports = Boolean(data.permissions?.canDeleteImports);
      state.importHistory = Array.isArray(data.imports) ? data.imports : [];
      $("#import-history-count").textContent = fmtNumber(state.importHistory.length);
      if ($("#import-history-list")) renderImportHistory();
      const rows = Array.isArray(data.rows) ? data.rows : [];
      renderRows(rows, data.pagination);
      renderMap(data.map);
      if (rows.length) {
        els.rowsStatus.hidden = true;
      } else {
        els.rowsStatus.className = "table-state-overlay empty";
        els.rowsStatus.hidden = false;
        els.rowsStatus.innerHTML = '<div class="table-empty-state"><strong>По выбранным фильтрам строк не найдено.</strong><span>Измените фильтры и попробуйте снова.</span></div>';
      }
    } catch (error) {
      els.rowsStatus.className = "table-state-overlay error";
      els.rowsStatus.hidden = false;
      els.rowsStatus.innerHTML = `<div class="table-state-error">Не удалось загрузить сведения: ${escapeHtml(error.message)} <button class="button" type="button" id="retry-data" style="min-height:28px;margin-left:8px">Повторить</button></div>`;
      $("#retry-data").addEventListener("click", loadDashboard);
      els.rows.innerHTML = "";
      $("#pagination-label").textContent = "Данные недоступны";
      $("#page-number").textContent = "—";
      $("#page-prev").disabled = true;
      $("#page-next").disabled = true;
    }
  }
  function detailPair(label, value) {
    return `<div class="detail-pair"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(display(value))}</dd></div>`;
  }
  function setDetailFullscreen(expanded) {
    const backdrop = $("#detail-backdrop");
    const modal = $(".detail-modal", backdrop);
    const button = $("#toggle-detail-fullscreen");
    if (!backdrop || !modal || !button) return;
    const isExpanded = Boolean(expanded);
    modal.classList.toggle("is-fullscreen", isExpanded);
    backdrop.classList.toggle("detail-fullscreen", isExpanded);
    button.setAttribute("aria-pressed", String(isExpanded));
    button.setAttribute("aria-label", isExpanded
      ? "Восстановить размер модального окна"
      : "Развернуть модальное окно на весь экран");
    button.title = isExpanded ? "Восстановить размер" : "Развернуть на весь экран";
    button.innerHTML = isExpanded
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8V3h5M3 3l7 7m11 6v5h-5m5 0-7-7M16 3h5v5m0-5-7 7M3 16v5h5m-5 0 7-7"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3H3v5M3 3l6 6M16 3h5v5m0-5-6 6M3 16v5h5m-5 0 6-6m12 1v5h-5m5 0-6-6"/></svg>';
  }
  function openDetails(row) {
    if (!row) return;
    state.currentRow = row;
    const requestId = ++state.detailRequestId;
    state.lastFocus = document.activeElement;
    const backdrop = $("#detail-backdrop");
    setDetailFullscreen(false);
    backdrop.hidden = false;
    document.body.style.overflow = "hidden";
    $("#detail-title").textContent = display(row.name, "Детали предприятия");
    $("#detail-subtitle").textContent = `${display(row.inn, "ИНН не указан")} · ${display(row.resource)} · ${display(row.month)} ${display(row.year, "")}`;
    const content = $("#detail-content");
    content.innerHTML = `<div class="detail-workspace">
      <div class="detail-column">
        <section class="detail-card"><h3>Атрибуты предприятия и строки отчёта</h3><dl class="detail-grid">
          ${detailPair("ИНН", row.inn)}${detailPair("КПП", row.kpp)}
          ${detailPair("Поставщик данных", row.owner)}${detailPair("Территория", row.territory)}
          ${detailPair("ОМСУ", row.district)}${detailPair("Уточнение", row.detail)}
          ${detailPair("Ресурс", row.resource)}${detailPair("Отчётный период", `${display(row.month)} ${display(row.year, "")}`)}
          ${detailPair("Статус строки", row.status)}${detailPair("Долг из отчёта", fmtMoney(row.debt))}
          ${detailPair("Периоды задолженности", row.periods)}${detailPair("Начислено за текущий период", fmtMoney(row.currentPeriodAccrual))}
          ${detailPair("Начислено с начала года", fmtMoney(row.yearToDateAccrual))}${detailPair("Счёт / лист", `${display(row.sheet)} / ${display(row.sourceRow)}`)}
          ${detailPair("Тип листа", row.sheetKind)}${detailPair("Тип строки", row.isGroupRow ? "Групповая строка" : "Строка предприятия")}
          ${detailPair("Версия файла", row.version)}${detailPair("Исходный файл", row.file)}
          ${detailPair("Загрузил", row.uploader)}${detailPair("Идентификатор строки", row.id)}
          ${detailPair("Идентификатор загрузки", row.importId)}
        </dl><p><a class="button" href="/ministry/import/${encodeURIComponent(row.importId)}/file">Скачать исходный XLSX</a></p>
          <div class="provenance"><strong>Источник.</strong> Строка ${escapeHtml(display(row.sourceRow))} листа «${escapeHtml(display(row.sheet))}» файла «${escapeHtml(display(row.file))}».</div>
        </section>
      </div>
      <div class="detail-column">
        <section class="detail-card debt-chart-card">
          <div class="debt-chart-heading"><div><h3>График задолженности</h3><p>Значения по отчётным месяцам; повторные строки не суммируются.</p></div>
            <label class="debt-chart-series" for="debt-chart-series">Поставщик и ресурс<select id="debt-chart-series" disabled><option>Загружаем историю…</option></select></label>
          </div>
          <div class="debt-chart-viewport" id="debt-chart-plot" role="status" aria-live="polite"><div class="debt-chart-empty">Загружаем историю задолженности…</div></div>
          <p class="debt-chart-note" id="debt-chart-note">Значения не пересчитываются и не складываются между источниками.</p>
        </section>
        <section class="detail-card"><h3>Сведения из общего справочника</h3><div id="directory-profile"><div class="empty-state">Загружаем сведения…</div></div></section>
      </div>
    </div>`;
    $(".detail-modal", backdrop).focus();
    loadDebtChartData(row, requestId);
  }
  function renderDebtChart(history, row, hasMore = false) {
    const plot = $("#debt-chart-plot");
    const note = $("#debt-chart-note");
    const select = $("#debt-chart-series");
    const chart = window.MinistryDebtChart;
    if (!chart) {
      select.disabled = true;
      plot.innerHTML = '<div class="debt-chart-empty">Не удалось подключить построение графика.</div>';
      note.textContent = "Обновите страницу и попробуйте открыть карточку снова.";
      return;
    }
    const seriesList = chart.buildSeries(history);
    if (!seriesList.length) {
      select.disabled = true;
      select.innerHTML = '<option>Нет данных для построения</option>';
      plot.innerHTML = '<div class="debt-chart-empty">Для графика нужны минимум два подтверждённых значения за разные отчётные месяцы по одному поставщику и ресурсу. Неоднозначные и повторные строки не объединяются.</div>';
      note.textContent = hasMore
        ? "Проверены первые 500 строк доступной истории; данных для двух отчётных месяцев в одной серии пока недостаточно."
        : "В доступной истории пока нет серии из двух подтверждённых месячных значений.";
      return;
    }
    select.innerHTML = seriesList.map((series, index) =>
      `<option value="${index}">${escapeHtml(series.label)} · периодов: ${series.points.length}</option>`
    ).join("");
    const selectedIndex = seriesList.findIndex(series =>
      series.ownerId === String(row.ownerId == null ? "" : row.ownerId)
      && series.resource === String(row.resource || "Ресурс не указан")
    );
    select.value = String(selectedIndex >= 0 ? selectedIndex : 0);
    select.disabled = seriesList.length < 2;
    const renderSelectedSeries = () => {
      const series = seriesList[Number(select.value)] || seriesList[0];
      plot.innerHTML = chart.renderSvg(series);
      const notes = [`Отчётных месяцев: ${series.points.length}`, series.excludedPeriods
        ? `неоднозначных периодов исключено: ${series.excludedPeriods}` : ""].filter(Boolean);
      if (hasMore) notes.push("история ограничена первыми 500 строками");
      note.textContent = notes.join(" · ");
    };
    select.addEventListener("change", renderSelectedSeries);
    renderSelectedSeries();
  }
  async function loadDebtChartData(row, requestId) {
    if (!row.inn) {
      $("#directory-profile").innerHTML = '<p class="directory-profile-empty">Карточка справочника не связана с этой строкой.</p>';
      renderDebtChart([], row);
      return;
    }
    try {
      const data = await requestJson(`/ministry/api/debtors/${encodeURIComponent(row.inn)}`);
      if (requestId !== state.detailRequestId || state.currentRow !== row) return;
      const company = data.company;
      const history = Array.isArray(data.history) ? data.history : [];
      const companyMarkup = company ? `<dl class="detail-grid">
        ${detailPair("Наименование", company.fullName || company.name)}${detailPair("ИНН", company.inn)}
        ${detailPair("КПП", company.kpp)}${detailPair("Адрес", company.address)}
        ${detailPair("Источник справочника", company.source || company.provenance)}
        ${detailPair("Снимок DaData", company.dadataSource ? `${company.dadataSource} · ${formatDate(company.dadataReceivedAt)}` : "Не запрашивался")}</dl>` : "";
      $("#directory-profile").innerHTML = companyMarkup || '<p class="directory-profile-empty">Сведения о предприятии не найдены в общем справочнике.</p>';
      renderDebtChart(history, row, Boolean(data.hasMore));
    } catch (error) {
      if (requestId !== state.detailRequestId || state.currentRow !== row) return;
      $("#directory-profile").innerHTML = '<p class="directory-profile-empty">Не удалось загрузить сведения из справочника.</p>';
      renderDebtChart([], row);
    }
  }
  function closeDetail() {
    state.detailRequestId += 1;
    state.currentRow = null;
    setDetailFullscreen(false);
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
      else if (!$("#data-tools-backdrop").hidden) closeDataTools();
    }
    if (event.key !== "Tab") return;
    const dialog = element;
    const focusable = $$('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href],summary,[tabindex="0"]', dialog)
      .filter(node => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function init() {
    const sourceFilters = new URLSearchParams(window.location.search);
    state.month = sourceFilters.get("month") || "";
    state.organizationQuery = sourceFilters.get("q") || "";
    state.selectedDistricts = sourceFilters.getAll("district").filter(Boolean);
    state.pendingDistricts = [...state.selectedDistricts];
    state.selectedResources = sourceFilters.getAll("resource")
      .filter(value => value && value !== "all");
    state.pendingResources = [...state.selectedResources];
    state.visibleColumns = loadVisibleColumns();
    applyTableColumnVisibility();
    const syncTableHeader = () => {
      const scrollbarWidth = Math.max(
        0,
        els.tableScroll.offsetWidth - els.tableScroll.clientWidth
      );
      els.tableHeader.style.paddingRight = `${scrollbarWidth}px`;
      els.tableHeader.scrollLeft = els.tableScroll.scrollLeft;
    };
    els.tableScroll.addEventListener("scroll", syncTableHeader, { passive: true });
    window.addEventListener("resize", syncTableHeader);
    syncTableHeader();
    document.addEventListener("click", event => {
      if (!event.target.closest(".header-filter-anchor, .header-filter-popover")) {
        closeHeaderFilterPopovers();
      }
    });
    document.addEventListener("scroll", event => {
      const popover = $(".header-filter-popover:not([hidden])");
      if (!popover || popover.contains(event.target)) return;
      repositionOpenHeaderFilterPopover();
    }, true);
    window.addEventListener("resize", repositionOpenHeaderFilterPopover);
    document.addEventListener("keydown", event => {
      if (!$("#detail-backdrop").hidden) trapDialogKeys(event, $(".detail-modal"));
      else if (!$("#import-backdrop").hidden) trapDialogKeys(event, $(".import-dialog"));
      else if (!$("#data-tools-backdrop").hidden) trapDialogKeys(event, $(".data-tools-dialog"));
      else if (event.key === "Escape" && $$(".header-filter-popover:not([hidden])").length) {
        closeHeaderFilterPopovers();
      }
    });
    $("#toggle-detail-fullscreen").addEventListener("click", () => {
      setDetailFullscreen(!$(".detail-modal").classList.contains("is-fullscreen"));
    });
    $("#close-detail").addEventListener("click", closeDetail);
    $("#detail-backdrop").addEventListener("click", event => { if (event.target === event.currentTarget) closeDetail(); });
    $("#open-import").addEventListener("click", openImport);
    $("#open-import-history").addEventListener("click", () => openDataTools("history"));
    $("#open-column-settings").addEventListener("click", () => openDataTools("columns"));
    $("#close-data-tools").addEventListener("click", closeDataTools);
    $("#data-tools-backdrop").addEventListener("click", event => {
      if (event.target === event.currentTarget) closeDataTools();
    });
    $("#close-import").addEventListener("click", closeImport);
    $("#import-backdrop").addEventListener("click", event => { if (event.target === event.currentTarget) closeImport(); });
    $("#preview-form").addEventListener("submit", submitPreview);
    [
      ["org-filter-toggle", "org-filter-popover"],
      ["omcu-filter-toggle", "omcu-filter-popover"],
      ["resource-filter-toggle", "resource-filter-popover"],
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
    els.resourceOptions.addEventListener("change", event => {
      if (!event.target.matches("[data-resource-option]")) return;
      state.pendingResources = $$(
        "[data-resource-option]:checked", els.resourceOptions,
      ).map(input => input.value);
      updateResourceSelectionLabel();
    });
    $("#resource-select-all").addEventListener("click", () => {
      state.pendingResources = (state.filters?.resources || []).map(String);
      renderResourceOptions();
    });
    $("#resource-clear").addEventListener("click", () => {
      state.pendingResources = [];
      renderResourceOptions();
    });
    $("#resource-apply").addEventListener("click", () => {
      state.selectedResources = [...state.pendingResources];
      state.page = 1;
      updateHeaderFilterIndicators();
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
