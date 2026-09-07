/**
 * The dashboard's charts. Inline SVG, no library, no build step.
 *
 * The hub ships with nothing to install and runs off a laptop at a venue with
 * no internet, so a charting library was never an option - and for the three
 * shapes this dashboard actually needs it would have been a lot of weight for
 * very little. Those three shapes are:
 *
 *   line()    a value per match, one line per team or per source
 *   bars()    a magnitude per team, grouped where there are two of them
 *   scatter() one source against another, one dot per team
 *
 * Every one of them draws a gap where a value is null. That is the whole point
 * of the null-safety the analytics layer is careful about: "no scout was
 * watching" and "did nothing" are opposite facts and must never look the same
 * on a chart.
 *
 * Building and wiring are separate because desk.js re-renders whole panes with
 * innerHTML: `line(...)` returns markup, and `wire(root)` attaches the hover
 * layer once the markup is in the document.
 */

import { perFrame } from './timers.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// The categorical slots, in fixed order and never cycled. Colour follows the
// entity - a team keeps its colour when the picker drops another team, so
// nobody has to relearn the legend. Past six entities the caller folds the
// tail away rather than inventing a seventh hue.
export const SLOTS = 6;
export const slot = (i) => `var(--s${(i % SLOTS) + 1})`;

// One internal coordinate system, scaled to the pane by CSS. The dashboard has
// a 1240px floor, so the scale factor stays near 1 and the type stays legible.
const W = 760;

const CHARTS = new Map();
let seq = 0;

const nice = (v, places = 0) =>
  (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(places).replace(/\.0+$/, ''));

/** Axis ticks on round numbers, which is what makes an axis readable at all. */
function ticks(lo, hi, count = 4) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
  return out.length > 1 ? out : [lo, hi];
}

function frame(id, height, body, { legend = '', table = '', caption = '', width = W } = {}) {
  return `<div class="chart" data-chart="${id}">
    ${legend}
    <div class="plot">
      <svg viewBox="0 0 ${width} ${height}" role="img">${body}</svg>
      <div class="tip" hidden></div>
    </div>
    ${caption ? `<div class="hint">${caption}</div>` : ''}
    ${table}</div>`;
}

function legendOf(series) {
  // Always present for two or more series; a single series is named by the
  // caption above it and a one-swatch box would only repeat it.
  if (series.length < 2) return '';
  return `<div class="legend">${series.map((s) => `<span class="key">
    <i style="background:${s.color}"></i>${esc(s.label)}</span>`).join('')}</div>`;
}

/** Every value a tooltip can show, reachable without hovering anything. */
function tableOf(cols, rows, head) {
  if (!rows.length) return '';
  return `<details class="tblview"><summary>table view</summary>
    <table><thead><tr>${[head, ...cols].map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}
    </tbody></table></details>`;
}

// ─────────────────────────────────────────────────────────────────── line
/**
 * A value per match, one line per series.
 *
 * @param x       [{label, short}] one entry per match, in schedule order
 * @param series  [{label, color, values:[n|null], band?:[n|null]}]
 * @param unit    what the y axis counts, for the tooltip
 */
export function line({ x, series, unit = '', height = 210, caption = '', empty = 'Nothing to plot yet.' }) {
  const id = `c${++seq}`;
  const pad = { l: 42, r: 74, t: 12, b: 26 };
  const plotW = W - pad.l - pad.r, plotH = height - pad.t - pad.b;
  const all = series.flatMap((s) => [
    ...s.values, ...(s.band || []).flatMap((b, i) => (b == null || s.values[i] == null
      ? [] : [s.values[i] - b, s.values[i] + b]))]).filter((v) => v != null);
  if (!x.length || !all.length) return `<div class="chart empty-chart">${esc(empty)}</div>`;
  const hi = Math.max(...all), lo = Math.min(0, Math.min(...all));
  const ys = ticks(lo, hi);
  const top = Math.max(hi, ys[ys.length - 1]);
  const px = (i) => pad.l + (x.length === 1 ? plotW / 2 : (i / (x.length - 1)) * plotW);
  const py = (v) => pad.t + plotH - ((v - lo) / (top - lo || 1)) * plotH;

  const grid = ys.map((v) => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${py(v)}" y2="${py(v)}"
      class="grid"/><text x="${pad.l - 8}" y="${py(v) + 4}" class="ax" text-anchor="end">${nice(v)}</text>`).join('');

  // An x label every nth match: a 40-match schedule cannot show 40 of them.
  const every = Math.ceil(x.length / 12);
  const xlab = x.map((p, i) => (i % every ? '' :
    `<text x="${px(i)}" y="${height - 8}" class="ax" text-anchor="middle">${esc(p.short)}</text>`)).join('');

  const paths = series.map((s, si) => {
    // A null breaks the line by default: a straight segment over a match
    // nobody scouted is a claim we did not measure. `connect` is for the one
    // case where the gap is not a missing measurement - an event-wide x axis,
    // where a team is absent from most matches because it was not in them, and
    // joining its own consecutive matches is an ordinary time series.
    let d = '', open = false;
    s.values.forEach((v, i) => {
      if (v == null) { if (!s.connect) open = false; return; }
      d += `${open ? 'L' : 'M'}${px(i)} ${py(v)} `;
      open = true;
    });
    let band = '';
    if (s.band) {
      const up = [], down = [];
      s.values.forEach((v, i) => {
        const b = s.band[i];
        if (v == null || b == null) return;
        up.push(`${up.length ? 'L' : 'M'}${px(i)} ${py(v + b)}`);
        down.unshift(`L${px(i)} ${py(v - b)}`);
      });
      if (up.length > 1) band = `<path d="${up.join(' ')} ${down.join(' ')} Z"
        style="fill:${s.color}" class="band"/>`;
    }
    // The last real point carries the end dot and, for a readable number of
    // series, the direct label. More than four converging lines and the labels
    // would detach from them, so the legend carries identity instead.
    const lastI = s.values.reduce((a, v, i) => (v == null ? a : i), -1);
    const dot = lastI < 0 ? '' :
      `<circle cx="${px(lastI)}" cy="${py(s.values[lastI])}" r="4.5"
         style="fill:${s.color}" class="dot"/>`;
    const label = (lastI < 0 || series.length > 4) ? '' :
      `<text x="${px(lastI) + 10}" y="${py(s.values[lastI]) + 4}" class="endlab"
        >${esc(s.label)} ${nice(s.values[lastI])}</text>`;
    return `${band}<path d="${d.trim()}" style="stroke:${s.color}" class="ln"/>${dot}${label}`;
  }).join('');

  const hover = `<g class="hover" hidden><line class="cross" y1="${pad.t}" y2="${pad.t + plotH}"/></g>`;
  const body = `${grid}${xlab}${paths}${hover}`;

  CHARTS.set(id, { kind: 'line', x, series, unit, px, pad, plotW, plotH, W, height });
  return frame(id, height, body, {
    legend: legendOf(series),
    caption,
    table: tableOf(series.map((s) => s.label), x.map((p, i) =>
      [p.label, ...series.map((s) => (s.values[i] == null ? '—' : nice(s.values[i], 1)))]), 'match'),
  });
}

// ─────────────────────────────────────────────────────────────────── bars
/**
 * A magnitude per row, grouped where a row carries two of them.
 *
 * Horizontal, because the rows are team numbers and names and those read
 * across, not rotated forty-five degrees under a column.
 */
export function bars({ rows, series, unit = '', caption = '', empty = 'Nothing to plot yet.' }) {
  const id = `c${++seq}`;
  if (!rows.length || !rows.some((r) => r.values.some((v) => v != null))) {
    return `<div class="chart empty-chart">${esc(empty)}</div>`;
  }
  const pad = { l: 66, r: 58, t: 8, b: 22 };
  const barH = Math.min(18, Math.max(7, Math.round(150 / Math.max(1, rows.length))));
  const gap = 2;                                   // the surface gap, one width throughout
  const groupH = series.length * (barH + gap) + 12;
  const height = pad.t + pad.b + rows.length * groupH;
  const plotW = W - pad.l - pad.r;
  const hi = Math.max(1, ...rows.flatMap((r) => r.values.filter((v) => v != null)));
  const xs = ticks(0, hi);
  const top = Math.max(hi, xs[xs.length - 1]);
  const bx = (v) => (v / top) * plotW;

  const grid = xs.map((v) => `<line class="grid" x1="${pad.l + bx(v)}" x2="${pad.l + bx(v)}"
      y1="${pad.t}" y2="${height - pad.b}"/>
    <text class="ax" x="${pad.l + bx(v)}" y="${height - 6}" text-anchor="middle">${nice(v)}</text>`).join('');

  const marks = rows.map((r, ri) => {
    const y0 = pad.t + ri * groupH + 6;
    const label = `<text class="rowlab" x="${pad.l - 10}" y="${y0 + groupH / 2 - 8}"
      text-anchor="end">${esc(r.label)}</text>`;
    return label + r.values.map((v, si) => {
      if (v == null) return '';
      const y = y0 + si * (barH + gap);
      const w = Math.max(1.5, bx(v));
      const rr = Math.min(4, w);                   // rounded data-end, square at the baseline
      const d = `M${pad.l} ${y} H${pad.l + w - rr} A${rr} ${rr} 0 0 1 ${pad.l + w} ${y + rr}`
              + ` V${y + barH - rr} A${rr} ${rr} 0 0 1 ${pad.l + w - rr} ${y + barH} H${pad.l} Z`;
      return `<path d="${d}" style="fill:${series[si].color}" class="bar"
        data-row="${ri}" data-series="${si}"/>`
        + `<text class="val" x="${pad.l + w + 7}" y="${y + barH - 1}">${nice(v, 1)}</text>`;
    }).join('');
  }).join('');

  CHARTS.set(id, { kind: 'bars', rows, series, unit });
  return frame(id, height, `${grid}${marks}`, {
    legend: legendOf(series),
    caption,
    table: tableOf(series.map((s) => s.label),
                   rows.map((r) => [r.label, ...r.values.map((v) => (v == null ? '—' : nice(v, 1)))]),
                   'team'),
  });
}

// ──────────────────────────────────────────────────────────────── scatter
/**
 * One source against another, one dot per team.
 *
 * The line is y = x, not a fit: it says "these two sources agree here", and a
 * team far off it is the interesting one - either our scouts or theirs have
 * seen a robot the others have not.
 */
export function scatter({ points, xLabel, yLabel, height = 250, caption = '', diagonal = true,
                          width = W, empty = 'Nothing to plot yet.' }) {
  const id = `c${++seq}`;
  const pad = { l: 46, r: 16, t: 12, b: 34 };
  // Two of these sit side by side in one pane, and an SVG scaled down to half
  // a pane takes its type with it. A narrower coordinate system renders near
  // 1:1 there instead, so the axis labels stay readable.
  const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
  const pts = points.filter((p) => p.x != null && p.y != null);
  if (!pts.length) return `<div class="chart empty-chart">${esc(empty)}</div>`;
  // A y=x line only means anything when both axes count the same thing, so the
  // two axes share a scale exactly when that line is drawn.
  const xTop = Math.max(1, ...pts.map((p) => p.x));
  const yTop = Math.max(1, ...pts.map((p) => p.y));
  const xMax = diagonal ? Math.max(xTop, yTop) : xTop;
  const yMax = diagonal ? Math.max(xTop, yTop) : yTop;
  const xs = ticks(0, xMax), ys = ticks(0, yMax);
  const sx = (v) => pad.l + (v / xMax) * plotW;
  const sy = (v) => pad.t + plotH - (v / yMax) * plotH;

  const grid = ys.map((v) => `<line class="grid" x1="${pad.l}" x2="${width - pad.r}" y1="${sy(v)}" y2="${sy(v)}"/>
      <text class="ax" x="${pad.l - 8}" y="${sy(v) + 4}" text-anchor="end">${nice(v)}</text>`).join('')
    + xs.map((v) => `<text class="ax" x="${sx(v)}" y="${height - 16}" text-anchor="middle">${nice(v)}</text>`).join('');

  const diag = diagonal ? `<line class="agree" x1="${sx(0)}" y1="${sy(0)}"
    x2="${sx(xMax)}" y2="${sy(yMax)}"/>` : '';

  const dots = pts.map((p, i) => `<circle class="dot" cx="${sx(p.x)}" cy="${sy(p.y)}" r="5"
    style="fill:var(--s1)" data-i="${i}"/>`).join('');
  const axes = `<text class="ax" x="${pad.l + plotW / 2}" y="${height - 2}"
      text-anchor="middle">${esc(xLabel)}</text>
    <text class="ax" x="${-(pad.t + plotH / 2)}" y="12" transform="rotate(-90)"
      text-anchor="middle">${esc(yLabel)}</text>`;

  CHARTS.set(id, { kind: 'scatter', points: pts, xLabel, yLabel, width });
  return frame(id, height, `${grid}${diag}${dots}${axes}`, {
    caption, width,
    table: tableOf([xLabel, yLabel], pts.map((p) => [p.label, nice(p.x, 1), nice(p.y, 1)]), 'team'),
  });
}

// ─────────────────────────────────────────────────────────── hover layer
/**
 * Attach the hover readout to every chart under `root`.
 *
 * Values lead and the series name follows: the reader already knows which line
 * they are pointing at and wants the number. Everything the tooltip shows is
 * also in the table view, so hovering is never the only way to a value.
 */
export function wire(root) {
  // Charts are rebuilt whole on every refresh; drop the models whose markup
  // has already been replaced so the registry does not grow all afternoon.
  for (const id of [...CHARTS.keys()]) {
    if (!document.querySelector(`[data-chart="${id}"]`)) CHARTS.delete(id);
  }
  for (const host of (root || document).querySelectorAll('.chart[data-chart]')) {
    const model = CHARTS.get(host.dataset.chart);
    if (!model || host.dataset.wired) continue;
    host.dataset.wired = '1';
    const svg = host.querySelector('svg'), tip = host.querySelector('.tip');
    if (!svg || !tip) continue;
    const vb = svg.viewBox.baseVal;
    const at = (ev) => {
      const r = svg.getBoundingClientRect();
      return { x: ((ev.clientX - r.left) / r.width) * vb.width, rect: r };
    };
    const show = (html, ev) => {
      tip.innerHTML = html;
      tip.hidden = false;
      const r = host.getBoundingClientRect();
      const left = Math.min(Math.max(8, ev.clientX - r.left + 12), r.width - tip.offsetWidth - 8);
      tip.style.left = `${left}px`;
      tip.style.top = `${Math.max(4, ev.clientY - r.top - tip.offsetHeight - 12)}px`;
    };
    const hide = () => {
      tip.hidden = true;
      const g = svg.querySelector('.hover');
      if (g) g.setAttribute('hidden', '');
    };

    // All three of these read layout (getBoundingClientRect, offsetWidth) and
    // rewrite the tooltip, and the scatter one walks every dot on the chart.
    // Firing that per pointermove forces a synchronous layout per mouse move
    // for frames the browser was never going to paint separately.
    if (model.kind === 'line') {
      svg.addEventListener('pointermove', perFrame((ev) => {
        const { x } = at(ev);
        // The crosshair snaps to the nearest match, so the reader aims at a
        // match rather than at a 2px line.
        let best = 0, bd = Infinity;
        model.x.forEach((_, i) => {
          const d = Math.abs(model.px(i) - x);
          if (d < bd) { bd = d; best = i; }
        });
        const g = svg.querySelector('.hover');
        const ln = g.querySelector('.cross');
        ln.setAttribute('x1', model.px(best));
        ln.setAttribute('x2', model.px(best));
        g.removeAttribute('hidden');
        const rows = model.series.map((s) => `<div class="row"><i style="background:${s.color}"></i>
          <b>${s.values[best] == null ? '—' : nice(s.values[best], 1)}</b>
          <span>${esc(s.label)}</span></div>`).join('');
        show(`<div class="hd">${esc(model.x[best].label)}</div>${rows}
          ${model.unit ? `<div class="u">${esc(model.unit)}</div>` : ''}`, ev);
      }));
    } else if (model.kind === 'bars') {
      svg.addEventListener('pointermove', perFrame((ev) => {
        const m = ev.target.closest('.bar');
        if (!m) return hide();
        const r = model.rows[+m.dataset.row], si = +m.dataset.series;
        show(`<div class="hd">${esc(r.label)}</div><div class="row">
          <i style="background:${model.series[si].color}"></i>
          <b>${nice(r.values[si], 1)}</b><span>${esc(model.series[si].label)}</span></div>
          ${model.unit ? `<div class="u">${esc(model.unit)}</div>` : ''}`, ev);
      }));
    } else {
      svg.addEventListener('pointermove', perFrame((ev) => {
        // Nearest point rather than a direct hit: a 10px dot is a pinpoint.
        const r = svg.getBoundingClientRect();
        const mx = ((ev.clientX - r.left) / r.width) * vb.width;
        const my = ((ev.clientY - r.top) / r.height) * vb.height;
        let best = null, bd = 900;
        for (const c of svg.querySelectorAll('.dot')) {
          const d = (c.cx.baseVal.value - mx) ** 2 + (c.cy.baseVal.value - my) ** 2;
          if (d < bd) { bd = d; best = c; }
        }
        if (!best) return hide();
        const p = model.points[+best.dataset.i];
        show(`<div class="hd">${esc(p.label)}</div>
          <div class="row"><b>${nice(p.x, 1)}</b><span>${esc(model.xLabel)}</span></div>
          <div class="row"><b>${nice(p.y, 1)}</b><span>${esc(model.yLabel)}</span></div>`, ev);
      }));
    }
    svg.addEventListener('pointerleave', hide);
  }
}
