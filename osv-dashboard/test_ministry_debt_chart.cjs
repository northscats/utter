const assert = require("node:assert/strict");
const { buildSeries, renderSvg } = require("./static/ministry_debt_chart.js");

const providerA = { ownerId: "1", owner: "Поставщик А", resource: "Тепло" };
const twoPeriods = [
  { ...providerA, month: "2026-01", debt: 1250, reason: "" },
  { ...providerA, month: "2026-02", debt: 980, reason: "" },
  { ...providerA, month: "2026-03", debt: null, reason: "Значение отсутствует" },
  { ownerId: "2", owner: "Поставщик Б", resource: "Вода", month: "2026-01", debt: 700, reason: "" }
];

const series = buildSeries(twoPeriods);
assert.equal(series.length, 1, "single-period series must not be shown");
assert.equal(series[0].points.length, 2, "chart must contain two distinct monthly values");
assert.equal(series[0].excludedPeriods, 1, "missing debt should be excluded, not plotted as zero");
const svg = renderSvg(series[0]);
assert.match(svg, /<svg[^>]+role="img"/);
assert.match(svg, /class="debt-chart-line"/);
assert.equal((svg.match(/class="debt-chart-point"/g) || []).length, 2);

assert.deepEqual(
  buildSeries([{ ...providerA, month: "2026-01", debt: 1250, reason: "" }]),
  [],
  "one data point must not render a chart"
);
assert.deepEqual(
  buildSeries([
    { ...providerA, month: "2026-01", debt: 1250, reason: "" },
    { ...providerA, month: "2026-02", debt: 980, reason: "" },
    { ...providerA, month: "2026-02", debt: 1020, reason: "" }
  ]),
  [],
  "duplicate reports for a month must not be combined or plotted as a fabricated total"
);

console.log("Ministry debt chart tests passed.");
