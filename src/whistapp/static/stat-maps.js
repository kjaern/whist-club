// Kort: members as points, computed by stats/maps.py. Two kinds of map share this drawing:
// - fixed maps (Spillestil, Bordkort) bring their own coordinates on axes 1–3;
// - pick maps (Vælg selv, Makkere) bring each point's numbers, and the viewer picks which
//   three are the axes, drawn in real units.
// The map opens in 3D, turned a little, and turns by dragging; XY, XZ and YZ (1–2, 1–3, 2–3
// on a fixed map) show it flat from one side.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const SLOTS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
  const W = 640, H = 440, R = 170, CAMERA = 4;
  const VIEWS = {xy: [0, 1], xz: [0, 2], yz: [1, 2]};  // flat views: the axes across and up
  const TURNED = {yaw: 0.5, pitch: 0.3};

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function fmt(v, f) {
    if (v === null || v === undefined) return '—';
    if (f === 'pct') return (100 * v).toFixed(1).replace('.', ',') + ' %';
    if (f === 'int') return String(Math.round(v));
    return (Math.round(v * 100) / 100).toFixed(2).replace(/\.?0+$/, '').replace('.', ',');
  }

  for (const box of document.querySelectorAll('.stat-map')) {
    const map = JSON.parse(box.querySelector('script').textContent);
    const card = box.closest('.stat-map-card');
    const viewButtons = [...card.querySelectorAll('.stat-map-view')];
    const picks = [...card.querySelectorAll('.stat-map-pick')];
    const groupSelect = card.querySelector('.stat-map-group');
    const missing = card.querySelector('.stat-map-missing');
    const tip = el('div', {class: 'graph-tip', hidden: ''});
    box.appendChild(tip);
    const state = {view: '3d', ...TURNED, drag: null};
    const fields = Object.fromEntries((map.fields || []).map(f => [f.key, f]));
    const flat = () => VIEWS[state.view];
    // The axes on screen: all three in 3D, else the view's two.
    const shownAxes = () => flat() || [0, 1, 2];

    // A point [x, y, z] on screen: flat views just take two coordinates; 3D turns the point by
    // yaw (round the vertical axis) then pitch (round the horizontal one), with perspective.
    function project(c) {
      const pair = flat();
      if (pair) return {sx: W / 2 + c[pair[0]] * R, sy: H / 2 - c[pair[1]] * R, depth: 0, f: 1};
      const [x, y, z] = c;
      const cy = Math.cos(state.yaw), sy = Math.sin(state.yaw), cp = Math.cos(state.pitch), sp = Math.sin(state.pitch);
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const f = CAMERA / (CAMERA - z2);
      return {sx: W / 2 + x1 * R * f, sy: H / 2 - y2 * R * f, depth: z2, f};
    }

    // What to draw: points scaled into -1..1 on each axis, plus the axes themselves.
    function model() {
      if (!map.fields) {
        return {points: map.points.map(p => ({name: p.name, detail: p.detail.split(' · '), c: [p.x, p.y, p.z]})),
          edges: map.edges || [], fixed: true};
      }
      const group = groupSelect ? map.groups.find(g => String(g.id) === groupSelect.value) : null;
      const source = group ? group.points : map.points;
      const keys = picks.map(s => s.value);
      const known = (p, k) => p.values[k] !== null && p.values[k] !== undefined;
      // Only the axes on screen need a value: a point without a third number still shows flat.
      const shown = source.filter(p => shownAxes().every(i => known(p, keys[i])));
      const axes = keys.map(k => {
        const values = shown.filter(p => known(p, k)).map(p => p.values[k]);
        let lo = values.length ? Math.min(...values) : 0, hi = values.length ? Math.max(...values) : 0;
        if (!(hi > lo)) { const pad = fields[k].fmt === 'pct' ? 0.05 : 1; lo -= pad; hi += pad; }
        const pad = (hi - lo) * 0.1;
        return {key: k, field: fields[k], lo: lo - pad, hi: hi + pad};
      });
      const scale = (a, v) => (v === null || v === undefined ? 0 : -1 + 2 * (v - a.lo) / (a.hi - a.lo));
      const points = shown.map(p => ({name: p.name, c: axes.map(a => scale(a, p.values[a.key])),
        detail: map.fields.map(f => `${f.label}: ${fmt(p.values[f.key], f.fmt)}`)}));
      const means = [0, 1, 2].map(i => points.reduce((s, p) => s + p.c[i], 0) / (points.length || 1));
      return {points, edges: [], axes, means, left: source.length - shown.length};
    }

    function drawFixedAxes(svg) {
      for (const i of shownAxes()) {
        const end = [0, 0, 0];
        end[i] = 1.15;
        const from = project(end.map(v => -v)), to = project(end);
        el('line', {x1: from.sx, y1: from.sy, x2: to.sx, y2: to.sy, class: 'graph-grid'}, svg);
        el('text', {x: to.sx + 4, y: to.sy - 4, class: 'graph-axis'}, svg).textContent = `Akse ${i + 1}`;
      }
    }

    // Real axes from the corner (-1, -1, -1), with the lowest and highest value at the ends.
    // Labels are placed by how the axis lies on screen: across, up, or into the depth (3D z).
    function drawPickedAxes(svg, m) {
      const axes = shownAxes();
      axes.forEach((i, n) => {
        const a = m.axes[i], start = [-1, -1, -1], end = [-1, -1, -1];
        end[i] = 1;
        const style = flat() ? ['across', 'up'][n] : ['across', 'up', 'depth'][i];
        const from = project(start), to = project(end);
        el('line', {x1: from.sx, y1: from.sy, x2: to.sx, y2: to.sy, class: 'graph-zero'}, svg);
        const lo = el('text', {x: from.sx, y: from.sy, class: 'graph-axis'}, svg);
        const hi = el('text', {x: to.sx, y: to.sy, class: 'graph-axis'}, svg);
        const name = el('text', {x: (from.sx + to.sx) / 2, y: (from.sy + to.sy) / 2, class: 'stat-map-axis-name'}, svg);
        name.textContent = `${'XYZ'[i]}: ${a.field.label}`;
        if (style === 'across') {
          lo.setAttribute('dy', 18); hi.setAttribute('dy', 18); hi.setAttribute('text-anchor', 'end');
          name.setAttribute('dy', 34); name.setAttribute('text-anchor', 'middle');
        } else if (style === 'up') {
          lo.setAttribute('dx', -6); hi.setAttribute('dx', -6); lo.setAttribute('text-anchor', 'end'); hi.setAttribute('text-anchor', 'end');
          hi.setAttribute('dy', 4);
          name.setAttribute('transform', `rotate(-90 ${name.getAttribute('x')} ${name.getAttribute('y')})`);
          name.setAttribute('dy', -52); name.setAttribute('text-anchor', 'middle');
        } else {
          hi.setAttribute('dx', 6); name.setAttribute('dx', 8);
        }
        if (style !== 'depth') lo.textContent = fmt(a.lo, a.field.fmt);  // the corner already has two labels
        hi.textContent = fmt(a.hi, a.field.fmt);
      });
      const pair = flat();
      if (pair && m.points.length > 1) {  // the averages split a flat map into four corners
        const [h, v] = pair, line = (i, value) => [-1, 1].map(t => {
          const c = [0, 0, 0];
          c[i] = value; c[i === h ? v : h] = t;
          return project(c);
        });
        for (const [a, b] of [line(h, m.means[h]), line(v, m.means[v])]) {
          el('line', {x1: a.sx, y1: a.sy, x2: b.sx, y2: b.sy, class: 'graph-cross'}, svg);
        }
      }
    }

    function draw() {
      tip.hidden = true;
      box.querySelector('svg')?.remove();
      const m = model();
      const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': map.title}, null);
      box.insertBefore(svg, tip);
      if (m.fixed) drawFixedAxes(svg); else drawPickedAxes(svg, m);
      if (missing) missing.textContent = m.left ? ` ${m.left} vises ikke, fordi de mangler et af tallene.` : '';
      const pts = m.points.map((p, i) => ({...p, i, ...project(p.c)}));
      for (const e of m.edges) {
        const a = pts[e.a], b = pts[e.b];
        el('line', {x1: a.sx, y1: a.sy, x2: b.sx, y2: b.sy, class: 'stat-map-edge', 'stroke-opacity': (0.15 + 0.75 * e.w).toFixed(2),
          'stroke-width': (1 + 4 * e.w).toFixed(1)}, svg);
      }
      for (const p of [...pts].sort((a, b) => a.depth - b.depth)) {
        const g = el('g', {class: 'stat-map-point', tabindex: 0}, svg);
        el('circle', {cx: p.sx, cy: p.sy, r: (9 * p.f).toFixed(1), fill: SLOTS[p.i % SLOTS.length], class: 'graph-dot'}, g);
        el('text', {x: p.sx + 12 * p.f, y: p.sy + 4, class: 'stat-map-label'}, g).textContent = p.name;
        const show = () => {
          tip.textContent = '';
          const head = document.createElement('strong'); head.textContent = p.name; tip.appendChild(head);
          for (const part of p.detail) { const line = document.createElement('span'); line.textContent = part; tip.appendChild(line); }
          tip.hidden = false;
          const scale = box.clientWidth / W;
          tip.style.left = Math.min(Math.max(0, p.sx * scale + 12), Math.max(0, box.clientWidth - tip.offsetWidth)) + 'px';
          tip.style.top = Math.max(0, p.sy * scale + 12) + 'px';
        };
        g.addEventListener('pointerenter', show);
        g.addEventListener('focus', show);
        g.addEventListener('click', show);
        g.addEventListener('pointerleave', () => { tip.hidden = true; });
        g.addEventListener('blur', () => { tip.hidden = true; });
      }
    }

    box.addEventListener('pointerdown', event => {
      if (flat() || event.target.closest('.stat-map-point')) return;
      state.drag = {x: event.clientX, y: event.clientY, yaw: state.yaw, pitch: state.pitch};
      try { box.setPointerCapture(event.pointerId); } catch { /* a synthetic pointer */ }
    });
    box.addEventListener('pointermove', event => {
      if (!state.drag) return;
      state.yaw = state.drag.yaw + (event.clientX - state.drag.x) * 0.01;
      state.pitch = Math.max(-1.5, Math.min(1.5, state.drag.pitch + (event.clientY - state.drag.y) * 0.01));
      draw();
    });
    for (const end of ['pointerup', 'pointercancel']) box.addEventListener(end, () => { state.drag = null; });

    function setView(view) {
      Object.assign(state, {view}, TURNED);  // 3D always starts from the same slight turn
      for (const b of viewButtons) b.setAttribute('aria-pressed', String(b.dataset.view === view));
      box.classList.toggle('stat-map-turning', view === '3d');
      draw();
    }

    for (const select of [...picks, groupSelect].filter(Boolean)) select.addEventListener('change', draw);
    for (const b of viewButtons) b.addEventListener('click', () => setView(b.dataset.view));
    setView('3d');
  }
})();
