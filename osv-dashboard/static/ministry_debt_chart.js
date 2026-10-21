(function (root) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function buildSeries(history) {
    const groups = new Map();
    (Array.isArray(history) ? history : []).forEach(item => {
      if (!item || !/^\d{4}-(0[1-9]|1[0-2])$/.test(String(item.month || ""))) return;
      const ownerId = String(item.ownerId == null ? "" : item.ownerId);
      const owner = String(item.owner || "Поставщик не указан");
      const resource = String(item.resource || "Ресурс не указан");
      const key = JSON.stringify([ownerId, owner, resource]);
      if (!groups.has(key)) {
        groups.set(key, {
          key, ownerId, owner, resource, months: new Map()
        });
      }
      const group = groups.get(key);
      const monthRows = group.months.get(item.month) || [];
      monthRows.push(item);
      group.months.set(item.month, monthRows);
    });

    return Array.from(groups.values()).map(group => {
      const points = [];
      let excludedPeriods = 0;
      Array.from(group.months.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .forEach(([month, rows]) => {
          if (rows.length !== 1) {
            excludedPeriods += 1;
            return;
          }
          const item = rows[0];
          const debt = Number(item.debt);
          if (item.debt == null || item.reason || !Number.isFinite(debt)) {
            excludedPeriods += 1;
            return;
          }
          points.push({ month, debt, version: item.version });
        });

      return {
        key: group.key,
        owner: group.owner,
        ownerId: group.ownerId,
        resource: group.resource,
        label: `${group.resource} · ${group.owner}`,
        points,
        excludedPeriods
      };
    })
      .filter(series => series.points.length >= 2)
      .sort((left, right) =>
        right.points.length - left.points.length
        || left.label.localeCompare(right.label, "ru"));
  }

  function formatMonth(value) {
    const [year, month] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("ru-RU", {
      month: "short", year: "2-digit", timeZone: "UTC"
    }).format(new Date(Date.UTC(year, month - 1, 1)));
  }

  function renderSvg(series, availableWidth) {
    const points = Array.isArray(series && series.points) ? series.points : [];
    if (points.length < 2) return "";

    const requestedWidth = Number(availableWidth);
    const width = Number.isFinite(requestedWidth) && requestedWidth > 0
      ? Math.max(280, Math.floor(requestedWidth))
      : 700;
    const height = 300;
    const left = 82, right = 24, top = 26, bottom = 58;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const values = points.map(point => Number(point.debt));
    let minValue = Math.min(0, ...values);
    let maxValue = Math.max(0, ...values);
    if (minValue === maxValue) {
      const padding = Math.abs(maxValue || 1) * 0.1;
      minValue -= padding;
      maxValue += padding;
    }
    const valueRange = maxValue - minValue;
    const coordinates = points.map((point, index) => ({
      ...point,
      x: left + (points.length === 1 ? plotWidth / 2 : index * plotWidth / (points.length - 1)),
      y: top + ((maxValue - Number(point.debt)) / valueRange) * plotHeight
    }));
    const ticks = Array.from({ length: 5 }, (_, index) => maxValue - valueRange * index / 4);
    const grid = ticks.map(value => {
      const y = top + ((maxValue - value) / valueRange) * plotHeight;
      const label = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value);
      return `<g class="debt-chart-grid"><line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"></line><text x="${left - 10}" y="${y + 4}" text-anchor="end">${escapeHtml(label)}</text></g>`;
    }).join("");
    const path = coordinates.map((point, index) =>
      `${index ? "L" : "M"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    ).join(" ");
    const maxLabels = Math.max(2, Math.floor(plotWidth / 58));
    const labelStep = Math.max(1, Math.ceil(points.length / maxLabels));
    const xLabels = coordinates.map((point, index) => {
      if (index % labelStep !== 0 && index !== coordinates.length - 1) return "";
      return `<text class="debt-chart-x-label" x="${point.x}" y="${height - 20}" text-anchor="middle">${escapeHtml(formatMonth(point.month))}</text>`;
    }).join("");
    const markers = coordinates.map(point =>
      `<circle class="debt-chart-point" cx="${point.x}" cy="${point.y}" r="4"><title>${escapeHtml(formatMonth(point.month))}: ${escapeHtml(new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(point.debt))} ₽</title></circle>`
    ).join("");

    return `<svg class="debt-chart-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="График задолженности по периодам: ${escapeHtml(series.label)}">
      <text class="debt-chart-axis-title" x="18" y="${top + plotHeight / 2}" transform="rotate(-90 18 ${top + plotHeight / 2})" text-anchor="middle">Долг, ₽</text>
      ${grid}<path class="debt-chart-line" d="${path}"></path>${markers}${xLabels}
    </svg>`;
  }

  const api = { buildSeries, renderSvg };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MinistryDebtChart = api;
})(typeof window !== "undefined" ? window : globalThis);
