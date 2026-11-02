/* Canvas charts for GROKHOOD Desk: live balance line, coin candles, analyst score ring.
   devicePixelRatio aware. No libraries. */
(function () {
  'use strict';
  const C = {
    green: '#00d35a', red: '#ff453a', orange: '#ff9f0a', blue: '#0a84ff', cyan: '#64d2ff',
    yellow: '#ffd60a', text2: 'rgba(235,235,245,0.6)', text3: 'rgba(235,235,245,0.3)', card2: '#2c2c2e',
  };
  const FONT = '-apple-system, "SF Pro Text", BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif';
  const reduced = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const lerp = (a, b, t) => a + (b - a) * t;

  function fit(canvas) {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /* ---------------- live balance line ---------------- */
  class LineChart {
    constructor(canvas, opts = {}) {
      this.c = canvas;
      this.opts = opts;
      this.points = [];
      this.start = null;
      this.marks = [];
      this.markBorn = new Map();
      this.slide = null; // {t0, fromLast:[ts,v]}
      this.scrubX = null;
      this.alive = true;
      this._loop = this._loop.bind(this);
      this._bindScrub();
      this.ro = new ResizeObserver(() => this.draw());
      this.ro.observe(canvas);
      requestAnimationFrame(this._loop);
    }
    destroy() { this.alive = false; this.ro.disconnect(); }
    setData(d) {
      this.points = (d.points || []).filter((p) => p && p[1] != null).map((p) => [toMs(p[0]), +p[1]]);
      this.start = d.start == null ? null : +d.start;
      this.setMarks(d.marks || [], true);
      this.slide = null;
      this.draw();
    }
    setMarks(marks, initial) {
      this.marks = marks.map((m) => ({ ...m, t: toMs(m.ts) }));
      const now = performance.now();
      for (const m of this.marks) {
        const k = m.t + '|' + m.label;
        if (!this.markBorn.has(k)) this.markBorn.set(k, initial ? now - 1000 : now);
      }
    }
    push(p) {
      const pt = [toMs(p[0]), +p[1]];
      const last = this.points[this.points.length - 1];
      if (last && pt[0] <= last[0]) { last[1] = pt[1]; this.draw(); return; }
      this.slide = reduced() || !last ? null : { t0: performance.now(), from: last.slice(), first: this.points[0] && this.points[0].slice() };
      this.points.push(pt);
      if (this.opts.maxPoints && this.points.length > this.opts.maxPoints) this.points.shift();
    }
    _bindScrub() {
      const c = this.c;
      const at = (e) => {
        const r = c.getBoundingClientRect();
        const t = e.touches ? e.touches[0] : e;
        return t.clientX - r.left;
      };
      const move = (e) => { this.scrubX = at(e); this.draw(); };
      const end = () => { this.scrubX = null; if (this.opts.onScrub) this.opts.onScrub(null); this.draw(); };
      c.addEventListener('touchstart', move, { passive: true });
      c.addEventListener('touchmove', move, { passive: true });
      c.addEventListener('touchend', end);
      c.addEventListener('mousemove', (e) => { if (e.buttons) move(e); });
      c.addEventListener('mouseup', end);
      c.addEventListener('mouseleave', () => { if (this.scrubX != null) end(); });
    }
    _loop() {
      if (!this.alive) return;
      if (this.c.isConnected) this.draw();
      requestAnimationFrame(this._loop);
    }
    draw() {
      const { ctx, w, h } = fit(this.c);
      ctx.clearRect(0, 0, w, h);
      const pts = this.points;
      if (pts.length < 2) return;
      const now = performance.now();

      // slide-in of the newest point
      let series = pts;
      if (this.slide) {
        const k = Math.min(1, (now - this.slide.t0) / 250);
        const e = easeOut(k);
        const last = pts[pts.length - 1];
        const from = this.slide.from;
        series = pts.slice(0, -1).concat([[lerp(from[0], last[0], e), lerp(from[1], last[1], e)]]);
        if (k >= 1) this.slide = null;
      }
      const x0 = series[0][0], x1 = series[series.length - 1][0];
      const padL = 0, padR = 14, top = 74, bot = 16;
      let lo = Infinity, hi = -Infinity;
      for (const p of series) { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; }
      if (this.start != null) { lo = Math.min(lo, this.start); hi = Math.max(hi, this.start); }
      if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
      const span = hi - lo; hi += span * 0.06; lo -= span * 0.06;
      const X = (t) => padL + (x1 === x0 ? 0 : ((t - x0) / (x1 - x0)) * (w - padL - padR));
      const Y = (v) => top + (1 - (v - lo) / (hi - lo)) * (h - top - bot);

      // dashed start line
      if (this.start != null) {
        ctx.save();
        ctx.setLineDash([2, 5]);
        ctx.strokeStyle = C.text3; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(0, Math.round(Y(this.start)) + 0.5); ctx.lineTo(w, Math.round(Y(this.start)) + 0.5); ctx.stroke();
        ctx.restore();
      }

      // scrub split
      let scrubIdx = null;
      if (this.scrubX != null) {
        let best = 0, bd = Infinity;
        series.forEach((p, i) => { const d = Math.abs(X(p[0]) - this.scrubX); if (d < bd) { bd = d; best = i; } });
        scrubIdx = best;
        if (this.opts.onScrub) this.opts.onScrub(series[best][1], series[best][0]);
      }

      // gradient fill
      const grad = ctx.createLinearGradient(0, top, 0, h);
      grad.addColorStop(0, 'rgba(0,211,90,0.26)');
      grad.addColorStop(1, 'rgba(0,211,90,0)');
      ctx.beginPath();
      series.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
      ctx.lineTo(X(series[series.length - 1][0]), h); ctx.lineTo(X(series[0][0]), h); ctx.closePath();
      ctx.fillStyle = grad; ctx.fill();

      // line
      ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.lineWidth = 2.25;
      const drawLine = (from, to, color) => {
        ctx.beginPath();
        for (let i = from; i <= to; i++) { const p = series[i]; i === from ? ctx.moveTo(X(p[0]), Y(p[1])) : ctx.lineTo(X(p[0]), Y(p[1])); }
        ctx.strokeStyle = color; ctx.stroke();
      };
      if (scrubIdx != null) {
        drawLine(0, scrubIdx, C.green);
        drawLine(scrubIdx, series.length - 1, 'rgba(0,211,90,0.32)');
        const sx = X(series[scrubIdx][0]);
        ctx.strokeStyle = C.text3; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(sx + 0.5, top - 20); ctx.lineTo(sx + 0.5, h); ctx.stroke();
        ctx.fillStyle = C.green;
        ctx.beginPath(); ctx.arc(sx, Y(series[scrubIdx][1]), 5, 0, Math.PI * 2); ctx.fill();
      } else {
        drawLine(0, series.length - 1, C.green);
      }

      // marks
      this._drawMarks(ctx, series, X, Y, x0, x1, w, top, now);

      // live dot
      if (scrubIdx == null) {
        const lp = series[series.length - 1];
        const lx = X(lp[0]), ly = Y(lp[1]);
        if (!reduced()) {
          const ph = (now % 1600) / 1600;
          ctx.fillStyle = `rgba(0,211,90,${0.35 * (1 - ph)})`;
          ctx.beginPath(); ctx.arc(lx, ly, 5 + ph * 10, 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = C.green;
        ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, Math.PI * 2); ctx.fill();
      }
    }
    _valueAt(series, t) {
      if (t <= series[0][0]) return series[0][1];
      for (let i = 1; i < series.length; i++) {
        if (series[i][0] >= t) {
          const a = series[i - 1], b = series[i];
          const k = b[0] === a[0] ? 1 : (t - a[0]) / (b[0] - a[0]);
          return lerp(a[1], b[1], k);
        }
      }
      return series[series.length - 1][1];
    }
    _drawMarks(ctx, series, X, Y, x0, x1, w, top, now) {
      const ms = this.marks.filter((m) => m.t >= x0 && m.t <= x1).sort((a, b) => a.t - b.t);
      if (!ms.length) return;
      ctx.font = `600 12px ${FONT}`;
      const H = 22, gap = 4, rowH = H + gap;
      const placed = []; // label rects already on the canvas
      const items = [];
      const hits = (x0, y0, w0) => placed.some((p) => !(x0 + w0 + 4 < p.x0 || x0 > p.x1 + 4 || y0 + H + gap <= p.y0 || y0 >= p.y1 + gap));
      const prio = ms.filter((m) => m.kind !== 'buy').concat(ms.filter((m) => m.kind === 'buy'));
      for (const m of prio) {
        const x = X(m.t), y = Y(this._valueAt(series, m.t));
        const tw = ctx.measureText(m.label).width + 16;
        const lx = Math.max(4, Math.min(w - tw - 4, x - tw / 2));
        // later mark moves up a row while it would overlap an earlier one
        let ly = Math.min(y - 14 - H, top - 4);
        while (ly >= 2 && hits(lx, ly, tw)) ly -= rowH;
        if (ly < 2) { items.push({ m, x, y, dotOnly: true }); continue; }
        placed.push({ x0: lx, x1: lx + tw, y0: ly, y1: ly + H });
        items.push({ m, x, y, lx, ly, tw });
      }
      const alphaOf = (it) => { const born = this.markBorn.get(it.m.t + '|' + it.m.label) || 0; return reduced() ? 1 : Math.min(1, (now - born) / 200); };
      const colOf = (it) => (it.m.kind === 'buy' ? C.blue : it.m.kind === 'win' ? C.green : C.red);
      // pass 1: stems, pass 2: dots, pass 3: pills (so no stem ever crosses a label)
      ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
      for (const it of items) {
        if (it.dotOnly) continue;
        ctx.globalAlpha = alphaOf(it) * 0.45; ctx.strokeStyle = colOf(it);
        ctx.beginPath(); ctx.moveTo(it.x + 0.5, it.ly + H); ctx.lineTo(it.x + 0.5, it.y - 5); ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const it of items) {
        ctx.globalAlpha = alphaOf(it);
        ctx.fillStyle = '#000'; ctx.beginPath(); ctx.arc(it.x, it.y, 5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = colOf(it); ctx.beginPath(); ctx.arc(it.x, it.y, 3.5, 0, Math.PI * 2); ctx.fill();
      }
      ctx.textBaseline = 'middle';
      for (const it of items) {
        if (it.dotOnly) continue;
        const col = colOf(it);
        ctx.globalAlpha = alphaOf(it);
        roundRect(ctx, it.lx, it.ly, it.tw, H, H / 2);
        ctx.fillStyle = '#000'; ctx.fill();
        ctx.fillStyle = hexA(col, 0.2); ctx.fill();
        ctx.fillStyle = it.m.kind === 'buy' ? '#4da3ff' : col;
        ctx.fillText(it.m.label, it.lx + 8, it.ly + H / 2 + 0.5);
      }
      ctx.globalAlpha = 1;
    }
  }

  /* ---------------- coin candles ---------------- */
  class CandleChart {
    constructor(canvas) {
      this.c = canvas; this.candles = []; this.exitLine = null; this.entry = null;
      this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(canvas);
    }
    destroy() { this.ro.disconnect(); }
    setData(candles, exitLine, entry) {
      this.candles = (candles || []).filter((c) => c && c[4] != null);
      this.exitLine = exitLine == null ? null : +exitLine;
      this.entry = entry == null ? null : +entry;
      this.draw();
    }
    draw() {
      const { ctx, w, h } = fit(this.c);
      ctx.clearRect(0, 0, w, h);
      let cs = this.candles;
      if (cs.length < 1) return;
      const maxBars = Math.floor((w - 100) / 7);
      if (cs.length > maxBars) {
        const k = Math.ceil(cs.length / maxBars), out = [];
        for (let i = 0; i < cs.length; i += k) {
          const g = cs.slice(i, i + k);
          out.push([g[0][0], g[0][1], Math.max(...g.map((x) => x[2])), Math.min(...g.map((x) => x[3])), g[g.length - 1][4], g.reduce((s, x) => s + (+x[5] || 0), 0)]);
        }
        cs = out;
      }
      ctx.font = `500 11px ${FONT}`;
      let lw = 0;
      { let a = Infinity, b = -Infinity; for (const c of cs) { a = Math.min(a, c[3]); b = Math.max(b, c[2]); } for (const v of [a, b, this.exitLine, this.entry]) if (v != null) lw = Math.max(lw, ctx.measureText(fmtPrice(v)).width); }
      const padL = 12, padR = Math.ceil(lw) + 22, top = 16, volH = 52, gap = 12;
      const priceB = h - volH - gap;
      let lo = Infinity, hi = -Infinity, vmax = 0;
      for (const c of cs) { lo = Math.min(lo, c[3]); hi = Math.max(hi, c[2]); vmax = Math.max(vmax, +c[5] || 0); }
      for (const extra of [this.exitLine, this.entry]) if (extra != null) { lo = Math.min(lo, extra); hi = Math.max(hi, extra); }
      if (hi - lo <= 0) { hi = hi * 1.01 + 1e-12; lo = lo * 0.99; }
      const sp = hi - lo; hi += sp * 0.08; lo -= sp * 0.08;
      const Y = (v) => top + (1 - (v - lo) / (hi - lo)) * (priceB - top);
      const step = (w - padL - padR) / cs.length;
      const bw = Math.max(2, Math.min(9, step * 0.62));

      // gridlines + labels
      ctx.font = `500 11px ${FONT}`; ctx.textBaseline = 'middle'; ctx.fillStyle = C.text3;
      for (let i = 0; i <= 3; i++) {
        const v = lo + ((hi - lo) * i) / 3;
        const y = Math.round(Y(v)) + 0.5;
        ctx.strokeStyle = 'rgba(84,84,88,0.35)'; ctx.lineWidth = 0.5;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 6, y); ctx.stroke();
        ctx.fillText(fmtPrice(v), w - padR + 10, y);
      }
      cs.forEach((c, i) => {
        const x = padL + step * i + step / 2;
        const up = c[4] >= c[1];
        const col = up ? C.green : C.red;
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y(c[2])); ctx.lineTo(Math.round(x) + 0.5, Y(c[3])); ctx.stroke();
        const yo = Y(c[1]), yc = Y(c[4]);
        const bh = Math.max(1, Math.abs(yc - yo));
        roundRect(ctx, x - bw / 2, Math.min(yo, yc), bw, bh, Math.min(1.5, bw / 3)); ctx.fill();
        if (vmax > 0) {
          const vh = Math.max(1, ((+c[5] || 0) / vmax) * volH);
          ctx.fillStyle = up ? 'rgba(0,211,90,0.35)' : 'rgba(255,69,58,0.35)';
          roundRect(ctx, x - bw / 2, h - vh, bw, vh, Math.min(1.5, bw / 3)); ctx.fill();
        }
      });
      const hline = (v, color, label, dash) => {
        const y = Math.round(Y(v)) + 0.5;
        ctx.save(); ctx.setLineDash(dash); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 4, y); ctx.stroke(); ctx.restore();
        ctx.font = `600 11px ${FONT}`;
        const tw = ctx.measureText(label).width + 12;
        const lx = padL + 2, ly = y - 22;
        roundRect(ctx, lx, ly, tw, 18, 9); ctx.fillStyle = '#000'; ctx.fill(); ctx.fillStyle = hexA(color, 0.2); ctx.fill();
        ctx.fillStyle = color; ctx.fillText(label, lx + 6, ly + 9.5);
        const pl = fmtPrice(v); const pw = ctx.measureText(pl).width + 10;
        roundRect(ctx, w - padR + 6, y - 9, pw, 18, 4); ctx.fillStyle = color; ctx.fill();
        ctx.fillStyle = '#000'; ctx.fillText(pl, w - padR + 11, y + 0.5);
      };
      if (this.entry != null) hline(this.entry, '#4da3ff', 'Entry', [1, 3]);
      if (this.exitLine != null) hline(this.exitLine, C.orange, 'Risk trailing stop', [5, 4]);
    }
  }

  /* ---------------- score ring ---------------- */
  class ScoreRing {
    constructor(canvas) { this.c = canvas; this.v = 0; this.target = null; this.t0 = 0; this.from = 0; }
    set(score) {
      if (score == null) { this.target = null; this.draw(0); return; }
      this.from = this.v; this.target = Math.max(0, Math.min(100, +score)); this.t0 = performance.now();
      const tick = () => {
        const k = reduced() ? 1 : Math.min(1, (performance.now() - this.t0) / 700);
        this.v = lerp(this.from, this.target, easeOut(k));
        this.draw(this.v);
        if (k < 1 && this.c.isConnected) requestAnimationFrame(tick);
      };
      tick();
    }
    draw(v) {
      const { ctx, w, h } = fit(this.c);
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 8, lw = 11;
      ctx.lineCap = 'round'; ctx.lineWidth = lw;
      ctx.strokeStyle = C.card2;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      if (this.target != null) {
        const col = v >= 70 ? C.green : v >= 50 ? C.orange : C.red;
        ctx.strokeStyle = col;
        ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * v) / 100); ctx.stroke();
      }
      ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.font = `700 40px -apple-system, "SF Pro Display", BlinkMacSystemFont, sans-serif`;
      ctx.fillText(this.target == null ? '--' : String(Math.round(v)), cx, cy + 10);
      ctx.font = `500 12px ${FONT}`; ctx.fillStyle = C.text2;
      ctx.fillText('of 100', cx, cy + 30);
      ctx.textAlign = 'left';
    }
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function toMs(ts) {
    if (typeof ts === 'number') return ts < 1e12 ? ts * 1000 : ts;
    const t = Date.parse(ts); return isNaN(t) ? 0 : t;
  }
  const SUB = '₀₁₂₃₄₅₆₇₈₉';
  function fmtPrice(v) {
    if (v == null || !isFinite(v)) return 'n/a';
    const a = Math.abs(v);
    if (a >= 1000) return '$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 });
    if (a >= 1) return '$' + v.toFixed(2);
    if (a >= 0.01) return '$' + v.toFixed(4);
    if (a === 0) return '$0';
    const s = a.toFixed(20).slice(2);
    const zeros = s.match(/^0*/)[0].length;
    const sig = s.slice(zeros, zeros + 4).replace(/0+$/, '') || '0';
    if (zeros < 4) return '$0.' + '0'.repeat(zeros) + sig;
    return '$0.0' + String(zeros).split('').map((d) => SUB[+d]).join('') + sig;
  }

  window.Charts = { LineChart, CandleChart, ScoreRing, fmtPrice, toMs };
})();
