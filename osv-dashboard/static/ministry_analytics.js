(function () {
  "use strict";

  const $ = selector => document.querySelector(selector);
  const numberFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
  const moneyFormat = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 2, maximumFractionDigits: 2
  });
  const colors = ["#315f9b", "#527aa8", "#7f9dbc", "#304f72", "#6a8c9e", "#758ab5", "#52786f", "#8a765a"];
  let currentPayload = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }
  function fmtMoney(value) {
    return value == null || !Number.isFinite(Number(value))
      ? "Нет подтверждённых значений"
      : `${moneyFormat.format(Number(value))} ₽`;
  }
  function fmt(value) {
    return numberFormat.format(Number(value || 0));
  }
  function selectedValues(select) {
    return Array.from(select.selectedOptions).map(option => option.value);
  }
  function buildParams() {
    const params = new URLSearchParams();
    const period = $("#period").value;
    if (period) params.set("month", period);
    selectedValues($("#resources")).forEach(value => params.append("resource", value));
    selectedValues($("#suppliers")).forEach(value => params.append("supplier", value));
    selectedValues($("#districts")).forEach(value => params.append("district", value));
    return params;
  }
  function setStatus(message, error = false) {
    const element = $("#status");
    element.textContent = message;
    element.classList.toggle("error", error);
    element.hidden = !message;
  }
  function updateSelect(select, options, selected = [], emptyLabel = "") {
    const values = Array.isArray(options) ? options : [];
    select.innerHTML = values.length
      ? values.map(item => {
        const value = typeof item === "object" ? (item.value ?? item.id ?? item.name) : item;
        const label = typeof item === "object" ? (item.label ?? item.name ?? item.value) : item;
        return `<option value="${escapeHtml(value)}"${selected.map(String).includes(String(value)) ? " selected" : ""}>${escapeHtml(label)}</option>`;
      }).join("")
      : `<option value="" disabled>${escapeHtml(emptyLabel || "Нет доступных значений")}</option>`;
  }
  function applyFilterChoices(filters) {
    const selected = filters.selected || {};
    updateSelect($("#period"), filters.periods, [selected.month], "Нет доступных периодов");
    updateSelect($("#resources"), filters.resources, selected.resources, "Нет доступных ресурсов");
    updateSelect($("#suppliers"), filters.suppliers, selected.suppliers, "Нет доступных поставщиков");
    updateSelect($("#districts"), filters.districts, selected.districts, "Нет доступных территорий");
  }
  function chartEmpty(text) {
    return `<div class="chart-empty">${escapeHtml(text)}</div>`;
  }
  function renderDistribution(items) {
    const chart = $("#distribution-chart");
    const table = $("#distribution-table");
    const rows = Array.isArray(items) ? items : [];
    const measured = rows.filter(item => item.rows > 0);
    const valid = measured.filter(item => item.debt != null && Number.isFinite(Number(item.debt)));
    if (!valid.length) {
      chart.innerHTML = chartEmpty(measured.length
        ? "В выбранных строках сумма долга отсутствует."
        : "За выбранный период нет строк по этим фильтрам.");
    } else {
      const width = 720, rowHeight = 42, top = 15, left = 240, right = 30;
      const height = Math.max(110, top + valid.length * rowHeight + 12);
      const values = valid.map(item => Number(item.debt));
      let min = Math.min(0, ...values), max = Math.max(0, ...values);
      if (min === max) { min -= 1; max += 1; }
      const x = value => left + (value - min) / (max - min) * (width - left - right);
      const zeroX = x(0);
      const ticks = Array.from({ length: 4 }, (_, index) => min + (max - min) * index / 3)
        .map(value => `<g><line class="grid" x1="${x(value)}" y1="8" x2="${x(value)}" y2="${height - 10}"></line><text x="${x(value)}" y="${height - 1}" text-anchor="middle">${escapeHtml(fmt(value))}</text></g>`).join("");
      const bars = valid.map((item, index) => {
        const y = top + index * rowHeight;
        const value = Number(item.debt);
        const barX = Math.min(zeroX, x(value));
        const barWidth = Math.max(1, Math.abs(x(value) - zeroX));
        return `<a href="${escapeHtml(item.sourceUrl)}" aria-label="Открыть исходные строки: ${escapeHtml(item.resource)}, ${escapeHtml(fmtMoney(item.debt))}"><title>${escapeHtml(item.resource)}: ${escapeHtml(fmtMoney(item.debt))}; строк: ${escapeHtml(fmt(item.rows))}; без долга: ${escapeHtml(fmt(item.missingDebt))}</title><text x="${left - 10}" y="${y + 17}" text-anchor="end">${escapeHtml(item.resource)}</text><rect class="bar" x="${barX}" y="${y}" width="${barWidth}" height="22" rx="3"></rect><text x="${Math.min(width - right, Math.max(left + 8, x(value) + (value < 0 ? -5 : 5)))}" y="${y + 16}" text-anchor="${value < 0 ? "end" : "start"}">${escapeHtml(fmt(value))}</text></a>`;
      }).join("");
      chart.innerHTML = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Распределение суммы задолженности по ресурсам">${ticks}<line class="grid" x1="${zeroX}" y1="8" x2="${zeroX}" y2="${height - 10}"></line>${bars}</svg>`;
    }
    table.innerHTML = measured.length
      ? `<table class="data-table"><thead><tr><th>Ресурс</th><th>Строки</th><th>Нет долга</th><th>Сумма загруженных значений</th></tr></thead><tbody>${measured.map(item => `<tr><td><a class="source-link" href="${escapeHtml(item.sourceUrl)}">${escapeHtml(item.resource)}</a>${item.groupRows ? `<br>Групповых строк: ${escapeHtml(fmt(item.groupRows))}` : ""}${item.duplicateRows ? `<br>Строки с повторяющимся ИНН: ${escapeHtml(fmt(item.duplicateRows))}` : ""}</td><td>${escapeHtml(fmt(item.rows))}</td><td>${escapeHtml(fmt(item.missingDebt))}</td><td>${escapeHtml(fmtMoney(item.debt))}</td></tr>`).join("")}</tbody></table>`
      : "";
  }
  function formatMonth(value) {
    const match = /^(\d{4})-(\d{2})$/.exec(String(value || ""));
    if (!match) return value || "";
    return new Intl.DateTimeFormat("ru-RU", {
      month: "short", year: "2-digit", timeZone: "UTC"
    }).format(new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1)));
  }
  function renderTimeline(timeline) {
    const chart = $("#timeline-chart");
    const legend = $("#timeline-legend");
    const table = $("#timeline-table");
    const series = Array.isArray(timeline.series) ? timeline.series : [];
    const periods = Array.isArray(timeline.periods) ? timeline.periods : [];
    const usable = series.filter(item => item.points?.some(point => point.debt != null));
    $("#comparison-note").textContent = timeline.comparisonNote || "";
    if (!usable.length || !periods.length) {
      chart.innerHTML = chartEmpty("Для временного графика недостаточно подтверждённых значений.");
      legend.innerHTML = "";
      table.innerHTML = "";
      return;
    }
    const values = usable.flatMap(item => item.points
      .filter(point => point.debt != null && Number.isFinite(Number(point.debt)))
      .map(point => Number(point.debt)));
    let min = Math.min(0, ...values), max = Math.max(0, ...values);
    if (min === max) { const pad = Math.abs(max || 1) * .1; min -= pad; max += pad; }
    const width = Math.max(620, 140 + Math.max(0, periods.length - 1) * 76);
    const height = 310, left = 72, right = 25, top = 24, bottom = 52;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const xAt = index => left + (periods.length === 1 ? plotWidth / 2 : index * plotWidth / (periods.length - 1));
    const yAt = value => top + ((max - value) / (max - min)) * plotHeight;
    const grid = Array.from({ length: 5 }, (_, index) => {
      const value = max - (max - min) * index / 4;
      const y = yAt(value);
      return `<g><line class="grid" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"></line><text x="${left - 8}" y="${y + 4}" text-anchor="end">${escapeHtml(fmt(value))}</text></g>`;
    }).join("");
    const xLabels = periods.map((period, index) =>
      `<text x="${xAt(index)}" y="${height - 18}" text-anchor="middle">${escapeHtml(formatMonth(period))}</text>`
    ).join("");
    const lines = usable.map((item, seriesIndex) => {
      const color = colors[seriesIndex % colors.length];
      const coordinates = item.points.map((point, index) => ({
        ...point, index, x: xAt(index),
        y: point.debt == null ? null : yAt(Number(point.debt)),
      }));
      let path = "", drawing = false;
      coordinates.forEach(point => {
        if (point.y == null || !Number.isFinite(point.y)) { drawing = false; return; }
        path += `${drawing ? " L" : " M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
        drawing = true;
      });
      const markers = coordinates.filter(point => point.y != null && Number.isFinite(point.y))
        .map(point => `<a href="${escapeHtml(point.sourceUrl)}" aria-label="Открыть строки ${escapeHtml(item.district)} за ${escapeHtml(point.month)}: ${escapeHtml(fmtMoney(point.debt))}"><title>${escapeHtml(item.district)} · ${escapeHtml(formatMonth(point.month))}: ${escapeHtml(fmtMoney(point.debt))}; долг заполнен в ${escapeHtml(fmt(point.debtCount))} из ${escapeHtml(fmt(point.rows))} строк; повторных ИНН: ${escapeHtml(fmt(point.duplicateRows))}; групповых строк: ${escapeHtml(fmt(point.groupRows))}</title><circle class="marker" cx="${point.x}" cy="${point.y}" r="4" stroke="${color}"></circle></a>`).join("");
      return `<path class="line" d="${path}" stroke="${color}"></path>${markers}`;
    }).join("");
    chart.innerHTML = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Задолженность по выбранным ОМСУ во времени"><text x="14" y="${top + plotHeight / 2}" transform="rotate(-90 14 ${top + plotHeight / 2})" text-anchor="middle">Сумма, ₽</text>${grid}${lines}${xLabels}</svg>`;
    legend.innerHTML = usable.map((item, index) =>
      `<li><span class="swatch" style="background:${colors[index % colors.length]}" aria-hidden="true"></span>${escapeHtml(item.district)}</li>`
    ).join("");
    table.innerHTML = `<table class="data-table"><thead><tr><th>ОМСУ</th>${periods.map(period => `<th>${escapeHtml(formatMonth(period))}</th>`).join("")}</tr></thead><tbody>${usable.map(item => `<tr><td>${escapeHtml(item.district)}</td>${item.points.map(point => `<td>${point.debt == null ? "—" : `<a class="source-link" href="${escapeHtml(point.sourceUrl)}">${escapeHtml(fmtMoney(point.debt))}</a>`}${point.rows ? `<br>строк: ${escapeHtml(fmt(point.rows))}; без долга: ${escapeHtml(fmt(point.missingDebt))}` : ""}${point.duplicateRows ? `<br>повторы ИНН: ${escapeHtml(fmt(point.duplicateRows))}` : ""}${point.groupRows ? `<br>групповые строки: ${escapeHtml(fmt(point.groupRows))}` : ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }
  function renderQuality(quality) {
    const values = [
      ["Принятые импорты", quality.imports],
      ["Строки отчётов", quality.rows],
      ["Строки с долгом", quality.debtRows],
      ["Без значения долга", quality.missingDebt],
      ["Групповые строки", quality.groupRows],
      ["Строки с повторным ИНН", quality.duplicateRows],
      ["Предупреждения импорта", quality.warningImports],
      ["Неоднозначные / пустые ОМСУ", quality.unknownTerritories],
    ];
    $("#quality-grid").innerHTML = values.map(([label, value]) =>
      `<div class="quality-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(fmt(value))}</strong></div>`
    ).join("");
    const warnings = Array.isArray(quality.warnings) ? quality.warnings : [];
    const unknowns = Array.isArray(quality.unknownTerritoryLabels)
      ? quality.unknownTerritoryLabels : [];
    const warningList = warnings.length
      ? warnings.map(item => `<li>${escapeHtml(item.message)}<small>${escapeHtml(item.supplier)} · ${escapeHtml(item.resource)} · ${escapeHtml(item.file)}</small></li>`).join("")
      : "<li>У выбранных импортов нет предупреждений.</li>";
    const unknownList = unknowns.length
      ? unknowns.map(item => `<li>${escapeHtml(item.label)}<small>Строк: ${escapeHtml(fmt(item.rows))}</small></li>`).join("")
      : "<li>Неопределённых или пустых подписей территории нет.</li>";
    const omittedWarnings = Number(quality.warningsOmitted || 0);
    const omittedLabels = Number(quality.unknownTerritoryLabelsOmitted || 0);
    $("#quality-details").innerHTML = `
      <section class="quality-detail" aria-labelledby="warnings-heading">
        <h3 id="warnings-heading">Предупреждения принятых импортов</h3>
        <ul class="quality-detail-list">${warningList}</ul>
        ${omittedWarnings ? `<p class="quality-note">Не показано предупреждений: ${escapeHtml(fmt(omittedWarnings))}.</p>` : ""}
      </section>
      <section class="quality-detail" aria-labelledby="territory-labels-heading">
        <h3 id="territory-labels-heading">Исходные подписи нераспределённых территорий</h3>
        <ul class="quality-detail-list">${unknownList}</ul>
        ${omittedLabels ? `<p class="quality-note">Не показано подписей: ${escapeHtml(fmt(omittedLabels))}.</p>` : ""}
      </section>`;
  }
  async function loadAnalytics() {
    setStatus("Обновляем аналитику…");
    try {
      const response = await fetch(`/ministry/api/analytics?${buildParams()}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `Ошибка запроса (${response.status})`);
      currentPayload = payload;
      applyFilterChoices(payload.filters || {});
      renderDistribution(payload.distribution);
      renderTimeline(payload.timeline || {});
      renderQuality(payload.quality || {});
      setStatus(payload.empty
        ? "За этот период нет строк по выбранным фильтрам."
        : `Период ${$("#period").selectedOptions[0]?.textContent || ""}. Суммы являются агрегатами загруженных отчётов.`);
    } catch (error) {
      setStatus(`Не удалось загрузить аналитику: ${error.message}`, true);
      $("#distribution-chart").innerHTML = chartEmpty("Данные недоступны.");
      $("#timeline-chart").innerHTML = chartEmpty("Данные недоступны.");
      $("#distribution-table").innerHTML = "";
      $("#timeline-table").innerHTML = "";
      $("#timeline-legend").innerHTML = "";
      $("#quality-grid").innerHTML = "";
      $("#quality-details").innerHTML = "";
    }
  }
  $("#filters").addEventListener("submit", event => {
    event.preventDefault();
    loadAnalytics();
  });
  $("#user-toggle").addEventListener("click", () => {
    const open = $("#user-menu").classList.toggle("open");
    $("#user-toggle").setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", event => {
    if (!event.target.closest("#user-menu")) {
      $("#user-menu").classList.remove("open");
      $("#user-toggle").setAttribute("aria-expanded", "false");
    }
  });
  loadAnalytics();
})();
