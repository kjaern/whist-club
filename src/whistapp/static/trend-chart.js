// Graf pr. år / pr. aften: one column of a statistics table, period by period.
// The data is embedded in the page by stats/trends.py; this only draws it.
(() => {
  const dialog = document.getElementById('stat-graph-dialog');
  if (!dialog) return;
  const title = document.getElementById('stat-graph-title'), chart = document.getElementById('stat-graph-chart');
  const legend = document.getElementById('stat-graph-legend'), tip = document.getElementById('stat-graph-tip');
  const tableBox = document.getElementById('stat-graph-table');
  const SLOTS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
  const NS = 'http://www.w3.org/2000/svg';
  const W = 640, H = 300, M = {top: 14, right: 16, bottom: 30, left: 52};
  let state = null;

  function fmt(v, f, axis) {
    if (v === null || v === undefined) return '—';
    if (f === 'pct') return (100 * v).toFixed(axis ? 0 : 1).replace('.', ',') + ' %';
    const text = (Math.round(v * 100) / 100).toFixed(2).replace(/\.?0+$/, '').replace('.', ',');
    return f === 'signed' && v > 0 && !axis ? '+' + text : text;
  }

  function niceTicks(lo, hi) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / 5, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= raw);
    const ticks = [];
    for (let t = Math.floor(lo / step) * step; t <= hi + step * 1e-9; t += step) ticks.push(+t.toFixed(10));
    if (ticks[ticks.length - 1] < hi) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  // An entity keeps its colour while shown; a newly shown one takes the first free slot.
  function show(name) {
    const used = new Set(Object.values(state.color));
    const slot = SLOTS.find(c => !used.has(c));
    if (!slot) return false;
    state.color[name] = slot;
    return true;
  }

  function draw() {
    const {data, col} = state, column = data.columns[col], f = column.fmt;
    const visible = column.series.filter(s => state.color[s.name]);
    chart.querySelector('svg')?.remove();
    const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${column.label} pr. ${data.per}`}, chart);
    const n = data.x.length, iw = W - M.left - M.right, ih = H - M.top - M.bottom;
    const xs = i => M.left + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
    const values = visible.flatMap(s => s.values.filter(v => v !== null));
    const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values, f === 'pct' ? 0.01 : 1));
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const ys = v => M.top + ih - ((v - lo) / (hi - lo)) * ih;
    for (const t of ticks) {
      el('line', {x1: M.left, x2: W - M.right, y1: ys(t), y2: ys(t), class: t === 0 ? 'graph-zero' : 'graph-grid'}, svg);
      el('text', {x: M.left - 8, y: ys(t) + 4, 'text-anchor': 'end', class: 'graph-axis'}, svg).textContent = fmt(t, f, true);
    }
    const every = Math.ceil(n / 12);
    data.x.forEach((label, i) => {
      if (i % every && i !== n - 1) return;
      el('text', {x: xs(i), y: H - 9, 'text-anchor': 'middle', class: 'graph-axis'}, svg).textContent = label;
    });
    const cross = el('line', {y1: M.top, y2: M.top + ih, class: 'graph-cross', visibility: 'hidden'}, svg);
    for (const s of visible) {
      const pts = s.values.map((v, i) => v === null ? null : [xs(i), ys(v)]).filter(Boolean);
      if (pts.length > 1) el('polyline', {points: pts.map(p => p.join(',')).join(' '), fill: 'none', stroke: state.color[s.name],
        'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', ...(s.total ? {'stroke-dasharray': '6 4'} : {})}, svg);
      for (const [x, y] of pts) el('circle', {cx: x, cy: y, r: 4, fill: state.color[s.name], class: 'graph-dot'}, svg);
    }
    if (!visible.length) el('text', {x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'graph-axis'}, svg).textContent = 'Vælg en linje nedenfor';

    // Hover or touch anywhere in the plot: the nearest period's values.
    const hit = el('rect', {x: M.left - 10, y: 0, width: iw + 20, height: H, fill: 'transparent'}, svg);
    const move = event => {
      const box = svg.getBoundingClientRect(), x = (event.clientX - box.left) * W / box.width;
      const i = Math.max(0, Math.min(n - 1, Math.round(n === 1 ? 0 : ((x - M.left) / iw) * (n - 1))));
      cross.setAttribute('x1', xs(i)); cross.setAttribute('x2', xs(i)); cross.setAttribute('visibility', 'visible');
      const rows = visible.map(s => [s, s.values[i]]).sort((a, b) => (b[1] ?? -Infinity) - (a[1] ?? -Infinity));
      tip.innerHTML = '';
      const head = document.createElement('strong'); head.textContent = data.titles[i]; tip.appendChild(head);
      for (const [s, v] of rows) {
        const line = document.createElement('div'), sw = document.createElement('span');
        sw.className = 'graph-swatch'; sw.style.background = state.color[s.name];
        line.append(sw, `${s.name}: ${fmt(v, f)}`); tip.appendChild(line);
      }
      tip.hidden = false;
      const left = (xs(i) / W) * box.width, wide = chart.clientWidth;
      tip.style.left = Math.min(Math.max(0, left - tip.offsetWidth / 2), wide - tip.offsetWidth) + 'px';
    };
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerdown', move);
    hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); });

    legend.textContent = '';
    for (const s of column.series) {
      const chip = document.createElement('button');
      chip.type = 'button'; chip.className = 'graph-chip';
      chip.setAttribute('aria-pressed', state.color[s.name] ? 'true' : 'false');
      const sw = document.createElement('span'); sw.className = 'graph-swatch';
      sw.style.background = state.color[s.name] || 'transparent';
      chip.append(sw, s.name);
      chip.addEventListener('click', () => {
        if (state.color[s.name]) delete state.color[s.name];
        else if (!show(s.name)) return;
        draw();
      });
      legend.appendChild(chip);
    }

    // The same numbers as a table, for reading exact values.
    const table = document.createElement('table'); table.className = 'night stat-table';
    const head = table.createTHead().insertRow();
    for (const label of ['', ...data.x]) { const th = document.createElement('th'); th.textContent = label; head.appendChild(th); }
    const body = table.createTBody();
    for (const s of visible) {
      const row = body.insertRow();
      row.insertCell().textContent = s.name;
      for (const v of s.values) row.insertCell().textContent = fmt(v, f);
    }
    tableBox.textContent = ''; tableBox.appendChild(table);
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('.stat-graph');
    if (!button) return;
    const data = JSON.parse(document.getElementById('trend-' + button.dataset.stat).textContent);
    const column = data.columns[button.dataset.col];
    state = {data, col: button.dataset.col, color: {}};
    // Members etc. are shown from the start; a totals row is one tap away (it dwarfs the rest).
    for (const s of column.series) if (!s.total) show(s.name);
    title.textContent = `${column.label} pr. ${data.per}`;
    tip.hidden = true;
    dialog.showModal();
    draw();
  });
  document.getElementById('close-stat-graph').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
})();
