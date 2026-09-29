(function () {
  'use strict';
  var node = document.getElementById('la-data');
  if (!node) return;
  var data;
  try { data = JSON.parse(node.textContent); } catch (e) { return; }
  var NS = 'http://www.w3.org/2000/svg';
  var nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
  function fmt(v) { return v === null || v === undefined ? '—' : nf.format(v); }
  function sfmt(v) { return v === null || v === undefined ? '—' : (v > 0 ? '+' : '') + nf.format(v); }
  function short(v) {
    var a = Math.abs(v);
    if (a >= 1e6) return nf.format(Math.round(v / 1e5) / 10) + ' млн';
    if (a >= 1e3) return nf.format(Math.round(v / 100) / 10) + ' тыс.';
    return nf.format(v);
  }
  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function title(n, t) { n.appendChild(el('title', {}, t)); return n; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function ticks(min, max, count) {
    var span = max - min || Math.abs(max) || 1;
    var step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = span / count / step;
    step *= err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    var out = [], s = Math.floor(min / step) * step;
    for (var v = s; v <= max + step * 0.5; v += step) out.push(Math.round(v / step) * step);
    return out;
  }
  function td(tr, text, cls) { var c = document.createElement('td'); c.textContent = text; if (cls) c.className = cls; tr.appendChild(c); }

  var versions = data.versions || [];
  var metrics = data.metrics || [];

  function drawTrend(m) {
    var box = document.getElementById('la-trend');
    clear(box);
    var pts = (m && m.points) || [];
    document.getElementById('la-latest').textContent = m ? fmt(m.latest) : '—';
    document.getElementById('la-unit').textContent = m ? (m.unit || '') : '';
    var d = document.getElementById('la-delta');
    d.className = 'la-delta';
    if (m && m.delta !== null && m.delta !== undefined) {
      d.textContent = sfmt(m.delta) + ' к предыдущей';
      if (m.delta > 0) d.className += ' up'; else if (m.delta < 0) d.className += ' down';
    } else d.textContent = versions.length < 2 ? 'одна версия, сравнения нет' : 'изменение недоступно';

    var rows = document.getElementById('la-trend-rows'); clear(rows);
    versions.forEach(function (v, i) {
      var tr = document.createElement('tr');
      td(tr, v.label || ('#' + v.id)); td(tr, v.date || '—'); td(tr, fmt(pts[i]), 'num');
      rows.appendChild(tr);
    });

    var vals = pts.filter(function (v) { return v !== null && v !== undefined; });
    box.setAttribute('aria-label', (m ? m.label : 'Показатель') + ': динамика по ' + versions.length + ' версиям');
    if (!m || !vals.length) {
      var p = document.createElement('p'); p.className = 'la-empty';
      p.textContent = 'Недостаточно данных для полного итога: в принятых версиях есть пустые или нечисловые значения. Они не считаются нулями.';
      box.appendChild(p); return;
    }
    var W = 760, H = 250, L = 64, R = 16, T = 14, B = 36;
    var mn = Math.min.apply(null, vals.concat([0])), mx = Math.max.apply(null, vals.concat([0]));
    var tk = ticks(mn, mx, 4); mn = tk[0]; mx = tk[tk.length - 1]; if (mn === mx) mx = mn + 1;
    var n = versions.length;
    function x(i) { return n === 1 ? (L + W - R) / 2 : L + i * (W - L - R) / (n - 1); }
    function y(v) { return T + (mx - v) / (mx - mn) * (H - T - B); }
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'presentation' });
    svg.appendChild(el('title', {}, m.label + ', ' + (m.unit || '')));
    tk.forEach(function (t) {
      svg.appendChild(el('line', { x1: L, x2: W - R, y1: y(t), y2: y(t), 'class': t === 0 ? 'zero' : 'grid' }));
      svg.appendChild(el('text', { x: L - 8, y: y(t) + 4, 'text-anchor': 'end' }, short(t)));
    });
    var every = Math.max(1, Math.ceil(n / 8));
    versions.forEach(function (v, i) {
      if (i % every === 0 || i === n - 1) svg.appendChild(el('text', { x: x(i), y: H - 12, 'text-anchor': 'middle' }, v.date || v.label || ''));
    });
    // segments split by nulls
    var segs = [], cur = [];
    pts.forEach(function (v, i) {
      if (v === null || v === undefined) { if (cur.length) segs.push(cur); cur = []; }
      else cur.push([x(i), y(v)]);
    });
    if (cur.length) segs.push(cur);
    var zy = y(Math.max(mn, Math.min(0, mx)));
    segs.forEach(function (s) {
      var dstr = s.map(function (p, k) { return (k ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
      if (s.length > 1) {
        svg.appendChild(el('path', { d: dstr + ' L' + s[s.length - 1][0].toFixed(1) + ' ' + zy + ' L' + s[0][0].toFixed(1) + ' ' + zy + ' Z', 'class': 'area' }));
        svg.appendChild(el('path', { d: dstr, 'class': 'line' }));
      }
    });
    pts.forEach(function (v, i) {
      var lbl = (versions[i].label || '#' + versions[i].id) + (versions[i].date ? ' (' + versions[i].date + ')' : '');
      if (v === null || v === undefined) {
        svg.appendChild(title(el('circle', { cx: x(i), cy: zy, r: 4, 'class': 'miss' }), lbl + ': нет данных'));
      } else {
        svg.appendChild(title(el('circle', { cx: x(i), cy: y(v), r: 4, 'class': 'pt' }), lbl + ': ' + fmt(v) + ' ' + (m.unit || '')));
      }
    });
    box.appendChild(svg);
  }

  drawTrend(metrics.find(function (m) { return m.key === 'objects_count'; }));

  var rank = document.getElementById('la-rank');
  var ch = data.changes || {};
  if (rank && ch.ranking && ch.ranking.length) {
    var list = ch.ranking.slice();
    var RH = 26, W2 = 520, LW = 150, RP = 70, top = 6;
    var H2 = top * 2 + list.length * RH;
    var ds = list.map(function (r) { return r.delta || 0; });
    var lo = Math.min(0, Math.min.apply(null, ds)), hi = Math.max(0, Math.max.apply(null, ds));
    if (lo === hi) hi = 1;
    var LP = lo < 0 ? RP : 0;
    function sx(v) { return LW + LP + (v - lo) / (hi - lo) * (W2 - LW - LP - RP); }
    var svg = el('svg', { viewBox: '0 0 ' + W2 + ' ' + H2, role: 'presentation' });
    svg.appendChild(el('title', {}, 'Изменение по УИН, тыс. руб.'));
    svg.appendChild(el('line', { x1: sx(0), x2: sx(0), y1: 0, y2: H2, 'class': 'zero' }));
    list.forEach(function (r, i) {
      var v = r.delta || 0, yy = top + i * RH;
      var name = (i + 1) + '. ' + String(r.uin || '—');
      svg.appendChild(title(el('text', { x: LW - 8, y: yy + RH / 2 + 4, 'text-anchor': 'end', 'class': 'lbl' }, name.length > 20 ? name.slice(0, 19) + '…' : name), name + (r.title ? ' · ' + r.title : '')));
      var x0 = sx(Math.min(0, v)), w = Math.max(1, Math.abs(sx(v) - sx(0)));
      svg.appendChild(title(el('rect', { x: x0, y: yy + 5, width: w, height: RH - 10, rx: 3, 'class': v < 0 ? 'neg' : 'pos' }), name + ' · ' + r.field_label + ': ' + sfmt(r.delta) + ' ' + r.unit));
      svg.appendChild(el('text', { x: v < 0 ? x0 - 6 : x0 + w + 6, y: yy + RH / 2 + 4, 'text-anchor': v < 0 ? 'end' : 'start' }, (v > 0 ? '+' : '') + short(v)));
    });
    rank.setAttribute('aria-label', 'Крупнейшие изменения финансовых полей: ' + list.length + ', тыс. руб.');
    rank.appendChild(svg);
  }
})();
