/* GROKHOOD Desk: share cards for X. Drawn on <canvas>, no libraries.
   Three cards (trade, night, audit), each in 1200x675 (post) and 1080x1350 (portrait).
   Only data the app already reads from /api (shapes in docs/ARCHITECTURE.md). Missing data is left out,
   never filled in. Shared as a PNG File via navigator.share, else downloaded.
   Debug (demo only): ?demo=1&card=trade|night|audit[&layout=portrait][&sym=LFROG] draws the card full page. */
(function () {
  'use strict';

  const LIME = '#cdff00', GREEN = '#00d35a', RED = '#ff453a', CYAN = '#64d2ff', INK = '#0b0b0b', WHITE = '#ffffff';
  const FAM = '-apple-system, "SF Pro Display", BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif';
  const SIZES = { land: [1200, 675], port: [1080, 1350] };
  const EXIT = ['sold', 'stopped', 'timeout'];
  const PALETTE = ['#ffd60a', '#64d2ff', '#ff9f0a', '#30d158', '#bf5af2', '#ff6b9a', '#5e5ce6', '#66d4cf', '#ff453a', '#ac8e68'];
  const CHAIN = { solana: 'Solana', robinhood: 'Robinhood Chain' };

  let A = null; // { api, S, DEMO, toast }

  /* ================= small helpers ================= */
  const num = (v) => (v == null || v === '' || !isFinite(+v) ? null : +v);
  const toMs = (ts) => (window.Charts ? window.Charts.toMs(ts) : (typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts) || 0));
  const fmtPrice = (v) => (window.Charts ? window.Charts.fmtPrice(v) : String(v));
  const enc = encodeURIComponent;
  function usd(v, sign) {
    v = num(v); if (v == null) return null;
    const s = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (sign) return (v > 0 ? '+$' : v < 0 ? '-$' : '$') + s;
    return (v < 0 ? '-$' : '$') + s;
  }
  function pct(v, d) { v = num(v); if (v == null) return null; return (v > 0 ? '+' : v < 0 ? '-' : '') + Math.abs(v).toFixed(d == null ? 1 : d) + '%'; }
  const money = (s) => { const m = String(s || '').replace(/,/g, '').match(/([+-]?)\$?(\d+(?:\.\d+)?)/); return m ? (m[1] === '-' ? -1 : 1) * +m[2] : null; };
  const clock = (t) => new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const day = (t) => new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  function hold(ms) {
    if (!(ms > 0)) return null;
    const m = Math.round(ms / 60000);
    if (m < 60) return Math.max(1, m) + 'm';
    if (m < 1440) return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
    return Math.floor(m / 1440) + 'd ' + Math.floor((m % 1440) / 60) + 'h';
  }
  function hashStr(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const sym = (c) => String((c && (c.symbol || c.name)) || '').replace(/^\$/, '');

  function loadImg(src, cors) {
    return new Promise((res) => {
      if (!src) { res(null); return; }
      const i = new Image();
      if (cors) i.crossOrigin = 'anonymous';
      const t = setTimeout(() => res(null), 3000);
      i.onload = () => { clearTimeout(t); res(i); };
      i.onerror = () => { clearTimeout(t); res(null); };
      i.src = src;
    });
  }

  /* ================= mascot ================= */
  // the logo mascot (brand/logo.svg), recoloured per card: body, outline ink, hat, eyes, spark
  function mascotSVG(o) {
    const body = o.body || INK, ink = o.ink || INK, hat = o.hat || LIME, spark = o.spark || ink, fuse = o.fuse || ink;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">${o.bg ? `<rect width="512" height="512" rx="114" fill="${o.bg}"/>` : ''}
<g transform="translate(256 256) scale(0.93) translate(-278 -282)">
${o.ring === false ? '' : `<circle cx="208" cy="348" r="148" fill="none" stroke="${o.ring || ink}" stroke-width="11" opacity="0.3"/>`}
<path d="M352 238 L384 206 L404 226 L452 152" fill="none" stroke="${fuse}" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M458 84 Q463 124 500 132 Q463 140 458 180 Q453 140 416 132 Q453 124 458 84 Z" fill="${spark}"/>
<rect x="326" y="222" width="46" height="38" rx="9" transform="rotate(-42 349 241)" fill="${body}"/>
<circle cx="236" cy="326" r="148" fill="${body}"/>
<g stroke="${o.eye || '#fff'}" stroke-width="40" stroke-linecap="round"><line x1="232" y1="284" x2="254" y2="316"/><line x1="314" y1="252" x2="336" y2="284"/></g>
<g transform="translate(30 -2) rotate(-19 210 230)" stroke="${ink}" stroke-width="9" stroke-linejoin="round" stroke-linecap="round">
<path d="M84 238 L50 136 C120 150 220 140 290 160 C330 172 350 200 352 228 Z" fill="${hat}"/>
<path d="M40 240 C150 262 280 246 372 220 C300 206 160 222 40 240 Z" fill="${hat}"/>
<g transform="rotate(24 128 210)"><path d="M30 66 C16 88 60 158 132 214 C116 140 74 72 30 66 Z" fill="#fff"/><path d="M36 78 L122 198" fill="none" stroke-width="6"/></g>
</g></g></svg>`;
  }
  const mascotCache = {};
  function mascot(o) {
    const k = JSON.stringify(o);
    if (!mascotCache[k]) mascotCache[k] = loadImg('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(mascotSVG(o)));
    return mascotCache[k];
  }

  /* ================= canvas primitives ================= */
  const F = (w, s) => `${w} ${s}px ${FAM}`;
  function rr(c, x, y, w, h, r) {
    r = Math.min(r, h / 2, w / 2);
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }
  // text with auto shrink to fit max width; returns drawn width
  function txt(c, s, x, y, o) {
    let size = o.size;
    c.font = F(o.w || 700, size);
    if (o.max) while (size > (o.min || 10) && c.measureText(s).width > o.max) { size -= 2; c.font = F(o.w || 700, size); }
    if (o.max && c.measureText(s).width > o.max) { while (s.length > 1 && c.measureText(s + '...').width > o.max) s = s.slice(0, -1); s = s.trimEnd() + '...'; }
    c.fillStyle = o.color || WHITE; c.textAlign = o.align || 'left'; c.textBaseline = o.base || 'alphabetic';
    if (o.track) { try { c.letterSpacing = o.track + 'px'; } catch (e) { /* older engines */ } }
    c.fillText(s, x, y);
    const w = c.measureText(s).width;
    if (o.track) { try { c.letterSpacing = '0px'; } catch (e) { /* noop */ } }
    return w;
  }
  function measure(c, s, w, size) { c.font = F(w, size); return c.measureText(s).width; }
  // pill: y is the top edge; align left|right; returns width
  function pill(c, s, x, y, o) {
    const size = o.size || 24, h = o.h || Math.round(size * 1.6), padX = o.padX == null ? Math.round(size * 0.65) : o.padX;
    const icon = o.icon ? h * 0.5 : 0, gap = o.icon ? size * 0.35 : 0;
    const tw = measure(c, s, o.w || 800, size), w = tw + padX * 2 + icon + gap;
    const x0 = o.align === 'right' ? x - w : x;
    rr(c, x0, y, w, h, o.r == null ? h / 2 : o.r);
    if (o.bg) { c.fillStyle = o.bg; c.fill(); }
    if (o.stroke) { c.lineWidth = o.lw || 3; c.strokeStyle = o.stroke; c.stroke(); }
    if (o.icon) o.icon(c, x0 + padX + icon / 2, y + h / 2, icon, o.fg);
    txt(c, s, x0 + padX + icon + gap, y + h / 2 + size * 0.36, { size, w: o.w || 800, color: o.fg });
    return w;
  }
  const ICON = {
    check(c, x, y, s, col) { c.save(); c.strokeStyle = col; c.lineWidth = s * 0.2; c.lineCap = 'round'; c.lineJoin = 'round'; c.beginPath(); c.moveTo(x - s * 0.36, y + s * 0.02); c.lineTo(x - s * 0.1, y + s * 0.3); c.lineTo(x + s * 0.4, y - s * 0.3); c.stroke(); c.restore(); },
    x(c, x, y, s, col) { c.save(); c.strokeStyle = col; c.lineWidth = s * 0.2; c.lineCap = 'round'; c.beginPath(); const d = s * 0.3; c.moveTo(x - d, y - d); c.lineTo(x + d, y + d); c.moveTo(x + d, y - d); c.lineTo(x - d, y + d); c.stroke(); c.restore(); },
    dash(c, x, y, s, col) { c.save(); c.strokeStyle = col; c.lineWidth = s * 0.2; c.lineCap = 'round'; c.beginPath(); c.moveTo(x - s * 0.3, y); c.lineTo(x + s * 0.3, y); c.stroke(); c.restore(); },
    up(c, x, y, s, col) { c.save(); c.fillStyle = col; c.beginPath(); c.moveTo(x, y - s * 0.4); c.lineTo(x + s * 0.42, y + s * 0.32); c.lineTo(x - s * 0.42, y + s * 0.32); c.closePath(); c.fill(); c.restore(); },
    down(c, x, y, s, col) { c.save(); c.fillStyle = col; c.beginPath(); c.moveTo(x, y + s * 0.4); c.lineTo(x + s * 0.42, y - s * 0.32); c.lineTo(x - s * 0.42, y - s * 0.32); c.closePath(); c.fill(); c.restore(); },
  };
  function mark(c, x, y, r, pass, bgCol) {
    c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2);
    c.fillStyle = pass === true ? GREEN : pass === false ? RED : '#636366'; c.fill();
    (pass === true ? ICON.check : pass === false ? ICON.x : ICON.dash)(c, x, y, r * 1.5, bgCol || INK);
  }

  // themed background. bright: lime or red full bleed with a halftone wash; dark: black with a lime glow
  function background(c, W, H, theme) {
    if (theme === 'dark') {
      c.fillStyle = '#050505'; c.fillRect(0, 0, W, H);
      let g = c.createRadialGradient(W * 0.85, H * 0.95, 0, W * 0.85, H * 0.95, Math.max(W, H) * 0.8);
      g.addColorStop(0, 'rgba(205,255,0,0.22)'); g.addColorStop(0.5, 'rgba(205,255,0,0.05)'); g.addColorStop(1, 'rgba(205,255,0,0)');
      c.fillStyle = g; c.fillRect(0, 0, W, H);
      g = c.createRadialGradient(0, 0, 0, 0, 0, W * 0.6);
      g.addColorStop(0, 'rgba(0,211,90,0.10)'); g.addColorStop(1, 'rgba(0,211,90,0)');
      c.fillStyle = g; c.fillRect(0, 0, W, H);
      dots(c, W, H, 'rgba(255,255,255,0.045)', 22);
      return;
    }
    const base = theme === 'red' ? RED : LIME;
    c.fillStyle = base; c.fillRect(0, 0, W, H);
    const g = c.createRadialGradient(W * 0.12, H * 0.1, 0, W * 0.12, H * 0.1, Math.max(W, H) * 0.9);
    g.addColorStop(0, 'rgba(255,255,255,0.32)'); g.addColorStop(0.55, 'rgba(255,255,255,0)'); g.addColorStop(1, 'rgba(0,0,0,0.10)');
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    dots(c, W, H, 'rgba(0,0,0,0.07)', 20);
  }
  function dots(c, W, H, col, step) {
    c.fillStyle = col;
    for (let y = step / 2; y < H; y += step) for (let x = ((y / step) % 2 ? step / 2 : 0) + step / 2; x < W; x += step) { c.beginPath(); c.arc(x, y, 1.6, 0, Math.PI * 2); c.fill(); }
  }

  function inkOf(theme) { return theme === 'dark' ? WHITE : INK; }
  function soft(theme, a) { return theme === 'dark' ? `rgba(255,255,255,${a})` : `rgba(0,0,0,${a})`; }

  // small brand mark: mascot + "GROKHOOD Desk". align right: x is the right edge
  async function wordmark(c, x, y, theme, size, align) {
    size = size || 30;
    const m = await mascot(theme === 'dark' ? { bg: LIME } : theme === 'red' ? { bg: INK, body: LIME, ink: INK, hat: WHITE, fuse: LIME, spark: LIME, ring: false } : { ring: false });
    const ms = size * 1.7;
    const w1 = measure(c, 'GROKHOOD', 900, size), w2 = measure(c, 'Desk', 700, size);
    const total = ms + size * 0.4 + w1 + size * 0.28 + w2;
    let x0 = align === 'right' ? x - total : x;
    if (m) c.drawImage(m, x0, y - ms * 0.78, ms, ms);
    x0 += ms + size * 0.4;
    txt(c, 'GROKHOOD', x0, y, { size, w: 900, color: inkOf(theme), track: -0.5 });
    txt(c, 'Desk', x0 + w1 + size * 0.28, y, { size, w: 700, color: theme === 'dark' ? LIME : theme === 'red' ? WHITE : 'rgba(0,0,0,0.55)' });
  }
  function modeTag(c, x, y, theme, mode, size, align) {
    if (!mode) return 0;
    const dark = theme === 'dark';
    return pill(c, mode, x, y, { size: size || 22, w: 800, bg: dark ? (mode === 'DEMO' ? 'rgba(235,235,245,0.16)' : 'rgba(255,159,10,0.2)') : INK,
      fg: dark ? (mode === 'DEMO' ? 'rgba(235,235,245,0.75)' : '#ff9f0a') : (theme === 'red' ? RED : LIME), align });
  }

  async function avatar(c, coin, x, y, d, theme) {
    const name = sym(coin) || '?';
    const col = PALETTE[hashStr(coin.mint || name) % PALETTE.length];
    c.save();
    c.beginPath(); c.arc(x + d / 2, y + d / 2, d / 2 + 4, 0, Math.PI * 2); c.fillStyle = theme === 'dark' ? '#000' : INK; c.fill();
    c.beginPath(); c.arc(x + d / 2, y + d / 2, d / 2, 0, Math.PI * 2); c.clip();
    const img = coin.image ? await loadImg(coin.image, true) : null;
    if (img) c.drawImage(img, x, y, d, d);
    else { c.fillStyle = col; c.fillRect(x, y, d, d); txt(c, name.charAt(0).toUpperCase(), x + d / 2, y + d / 2 + d * 0.17, { size: Math.round(d * 0.48), w: 900, color: INK, align: 'center' }); }
    c.restore();
  }

  /* ================= price line (trade card) ================= */
  function priceLine(c, x, y, w, h, candles, t0, t1, col, live) {
    let pts = (candles || []).map((k) => [toMs(k[0]), +k[4]]).filter((p) => p[0] && isFinite(p[1]));
    if (pts.length < 2) return false;
    if (t0 && t1) {
      const span = Math.max(t1 - t0, 5 * 60000);
      const a = t0 - span * 0.45, b = live ? t1 : t1 + span * 0.3;
      const cut = pts.filter((p) => p[0] >= a && p[0] <= b);
      if (cut.length >= 2) pts = cut;
    }
    const tA = pts[0][0], tB = pts[pts.length - 1][0];
    let lo = Infinity, hi = -Infinity; for (const p of pts) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
    if (hi === lo) { hi *= 1.01; lo *= 0.99; }
    const pad = (hi - lo) * 0.14; lo -= pad; hi += pad;
    const X = (t) => x + ((t - tA) / Math.max(1, tB - tA)) * w;
    const Y = (v) => y + h - ((v - lo) / (hi - lo)) * h;
    const at = (t) => { let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; return best; };
    // held window
    if (t0 && t1 && t0 >= tA - 60000) {
      const xa = Math.max(x, X(t0)), xb = Math.min(x + w, X(Math.min(t1, tB)));
      const g = c.createLinearGradient(0, y, 0, y + h); g.addColorStop(0, col + '40'); g.addColorStop(1, col + '05');
      c.fillStyle = g; c.fillRect(xa, y - 8, Math.max(0, xb - xa), h + 16);
    }
    // area
    c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))));
    c.lineTo(X(tB), y + h); c.lineTo(X(tA), y + h); c.closePath();
    const g2 = c.createLinearGradient(0, y, 0, y + h); g2.addColorStop(0, col + '55'); g2.addColorStop(1, col + '00');
    c.fillStyle = g2; c.fill();
    // line with glow
    c.save(); c.shadowColor = col; c.shadowBlur = 18;
    c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))));
    c.strokeStyle = col; c.lineWidth = 5; c.lineJoin = 'round'; c.lineCap = 'round'; c.stroke(); c.restore();
    const dot = (t, label, fill) => {
      if (!t || t < tA - 60000 || t > tB + 60000) return;
      const p = at(t), px = X(p[0]), py = Y(p[1]);
      c.save(); c.setLineDash([6, 8]); c.strokeStyle = 'rgba(255,255,255,0.35)'; c.lineWidth = 2; c.beginPath(); c.moveTo(px, y - 6); c.lineTo(px, y + h); c.stroke(); c.restore();
      c.beginPath(); c.arc(px, py, 13, 0, Math.PI * 2); c.fillStyle = INK; c.fill();
      c.beginPath(); c.arc(px, py, 9, 0, Math.PI * 2); c.fillStyle = fill; c.fill();
      const tw = measure(c, label, 900, 18) + 20;
      const lx = Math.min(x + w - tw, Math.max(x, px - tw / 2)), ly = py - 50 < y - 10 ? py + 22 : py - 48;
      rr(c, lx, ly, tw, 30, 15); c.fillStyle = fill; c.fill();
      txt(c, label, lx + tw / 2, ly + 21.5, { size: 18, w: 900, color: INK, align: 'center' });
    };
    dot(t0, 'IN', WHITE);
    dot(t1, live ? 'NOW' : 'OUT', col);
    return true;
  }

  /* ================= TRADE card ================= */
  async function drawTrade(c, W, H, d) {
    const port = H > W, win = d.pnl == null ? true : d.pnl >= 0;
    const theme = win ? 'lime' : 'red', ink = INK, P = port ? 64 : 56;
    background(c, W, H, theme);
    // header: avatar + name, brand top right
    const av = port ? 92 : 76;
    await avatar(c, d.coin, P, P, av, theme);
    const nx = P + av + 22;
    txt(c, d.coin.name || sym(d.coin), nx, P + av * 0.48, { size: port ? 44 : 38, w: 900, color: ink, max: port ? 520 : 400, track: -0.5 });
    const sub = ['$' + sym(d.coin), CHAIN[d.coin.chain] || d.coin.chain].filter(Boolean).join(' \u00b7 ');
    const sw = txt(c, sub, nx, P + av * 0.48 + (port ? 42 : 36), { size: port ? 26 : 23, w: 700, color: soft(theme, 0.6) });
    if (d.mode) modeTag(c, nx + sw + 14, P + av * 0.48 + (port ? 14 : 12), theme, d.mode, port ? 20 : 18);
    await wordmark(c, W - P, P + (port ? 46 : 40), theme, port ? 30 : 26, 'right');

    // hero number
    const big = d.pnlText || 'n/a';
    const heroY = port ? 450 : 292;
    txt(c, big, P - 6, heroY, { size: port ? 250 : 172, w: 900, color: ink, max: port ? W - P * 2 : 640, track: -6 });
    // pct pill + reason chip
    const py = heroY + (port ? 36 : 28), ph = port ? 96 : 82;
    let px = P;
    if (d.pctText) px += pill(c, d.pctText, px, py, { size: port ? 60 : 52, w: 900, h: ph, bg: ink, fg: win ? LIME : RED, icon: win ? ICON.up : ICON.down }) + 16;
    if (d.reason) pill(c, d.reason, px, py, { size: port ? 34 : 29, w: 800, h: ph, stroke: ink, lw: 5, fg: ink });

    // chart panel
    const cp = port ? { x: P, y: 640, w: W - P * 2, h: 380 } : { x: 712, y: 150, w: W - 712 - P, h: 300 };
    c.save(); c.shadowColor = 'rgba(0,0,0,0.25)'; c.shadowBlur = 30; c.shadowOffsetY = 12;
    rr(c, cp.x, cp.y, cp.w, cp.h, 34); c.fillStyle = INK; c.fill(); c.restore();
    const cs = d.candles.length > 1 ? toMs(d.candles[d.candles.length - 1][0]) - toMs(d.candles[0][0]) : 0;
    txt(c, cs > 0 && !(d.inAt && d.inAt >= toMs(d.candles[0][0])) ? `PRICE \u00b7 LAST ${cs >= 3600000 ? Math.round(cs / 3600000) + 'H' : Math.max(1, Math.round(cs / 60000)) + 'M'}` : 'PRICE', cp.x + 28, cp.y + 44, { size: 18, w: 800, color: 'rgba(255,255,255,0.45)', track: 2 });
    if (d.priceFrom && d.priceTo) {
      txt(c, `${d.priceFrom}  \u2192  ${d.priceTo}`, cp.x + cp.w - 28, cp.y + 44, { size: 22, w: 800, color: WHITE, align: 'right', max: cp.w - 140 });
    }
    const okLine = priceLine(c, cp.x + 30, cp.y + 80, cp.w - 60, cp.h - 112, d.candles, d.inAt, d.outAt, win ? LIME : RED, d.open);
    if (!okLine) txt(c, 'No price data', cp.x + cp.w / 2, cp.y + cp.h / 2 + 10, { size: 24, w: 700, color: 'rgba(255,255,255,0.4)', align: 'center' });

    // stats row
    const stats = [];
    if (d.open) { if (d.inAt) stats.push(['ENTERED', clock(d.inAt)]); stats.push(['STATUS', 'Still open']); }
    else { if (d.inAt) stats.push(['IN', clock(d.inAt)]); if (d.outAt) stats.push(['OUT', clock(d.outAt)]); }
    if (d.held) stats.push(['HELD', d.held]);
    const sy = port ? 1108 : 490, colW = port ? 270 : 205;
    stats.forEach((s, i) => {
      txt(c, s[0], P + i * colW, sy, { size: port ? 22 : 19, w: 800, color: soft(theme, 0.55), track: 2 });
      txt(c, s[1], P + i * colW, sy + (port ? 52 : 46), { size: port ? 46 : 40, w: 900, color: ink, max: colW - 16, track: -1 });
    });
    // agents
    const ay = port ? 1196 : 572;
    let ax = P;
    for (const a of d.agents) {
      ax += pill(c, a.text, ax, ay, { size: port ? 26 : 22, w: 800, bg: 'rgba(0,0,0,0.88)', fg: WHITE, icon: (cc, x, y, s) => { cc.beginPath(); cc.arc(x, y, s * 0.42, 0, Math.PI * 2); cc.fillStyle = a.color; cc.fill(); }, padX: 18 }) + 12;
    }
    // mascot peeking from the bottom right
    const m = await mascot(win ? { ring: false } : { ring: false, hat: WHITE });
    const ms = port ? 340 : 290;
    if (m) {
      c.save(); c.translate(W - ms * 0.86, H - ms * 0.62); c.rotate(win ? -0.12 : 0.18);
      c.drawImage(m, -ms * 0.08, -ms * 0.14, ms, ms); c.restore();
    }
  }

  /* ================= NIGHT card ================= */
  function balanceLine(c, x, y, w, h, points, marks, start) {
    const pts = (points || []).map((p) => [toMs(p[0]), +p[1]]).filter((p) => p[0] && isFinite(p[1]));
    if (pts.length < 2) return false;
    const tA = pts[0][0], tB = pts[pts.length - 1][0];
    let lo = Infinity, hi = -Infinity; for (const p of pts) { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }
    if (start != null) { lo = Math.min(lo, start); hi = Math.max(hi, start); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
    const X = (t) => x + ((t - tA) / Math.max(1, tB - tA)) * w;
    const Y = (v) => y + h - ((v - lo) / (hi - lo)) * h;
    if (start != null) {
      c.save(); c.setLineDash([3, 10]); c.lineCap = 'round'; c.strokeStyle = 'rgba(255,255,255,0.3)'; c.lineWidth = 3;
      c.beginPath(); c.moveTo(x, Y(start)); c.lineTo(x + w, Y(start)); c.stroke(); c.restore();
    }
    c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))));
    c.lineTo(X(tB), y + h + 400); c.lineTo(X(tA), y + h + 400); c.closePath();
    const g = c.createLinearGradient(0, y, 0, y + h); g.addColorStop(0, 'rgba(205,255,0,0.42)'); g.addColorStop(1, 'rgba(205,255,0,0)');
    c.fillStyle = g; c.fill();
    c.save(); c.shadowColor = LIME; c.shadowBlur = 26;
    c.beginPath(); pts.forEach((p, i) => (i ? c.lineTo(X(p[0]), Y(p[1])) : c.moveTo(X(p[0]), Y(p[1]))));
    c.strokeStyle = LIME; c.lineWidth = 6; c.lineJoin = 'round'; c.lineCap = 'round'; c.stroke(); c.restore();
    for (const m of marks || []) {
      if (m.kind !== 'win' && m.kind !== 'loss') continue;
      const t = toMs(m.ts); if (t < tA || t > tB) continue;
      let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      const px = X(best[0]), py = Y(best[1]);
      c.beginPath(); c.arc(px, py, 13, 0, Math.PI * 2); c.fillStyle = '#050505'; c.fill();
      c.beginPath(); c.arc(px, py, 9, 0, Math.PI * 2); c.fillStyle = m.kind === 'win' ? GREEN : RED; c.fill();
    }
    const last = pts[pts.length - 1];
    c.save(); c.shadowColor = LIME; c.shadowBlur = 30; c.beginPath(); c.arc(X(last[0]), Y(last[1]), 12, 0, Math.PI * 2); c.fillStyle = LIME; c.fill(); c.restore();
    return [X(last[0]), Y(last[1])];
  }
  async function tradeTile(c, x, y, w, h, label, t, theme) {
    rr(c, x, y, w, h, 28); c.fillStyle = 'rgba(28,28,30,0.86)'; c.fill();
    c.lineWidth = 2; c.strokeStyle = 'rgba(255,255,255,0.08)'; c.stroke();
    const col = t.pnl >= 0 ? LIME : RED;
    txt(c, label, x + 26, y + 36, { size: 18, w: 800, color: 'rgba(235,235,245,0.55)', track: 2 });
    const av = 50;
    await avatar(c, t.coin, x + 26, y + 50, av, 'dark');
    txt(c, '$' + sym(t.coin), x + 26 + av + 16, y + 50 + av * 0.68, { size: 30, w: 900, color: WHITE, max: w - av - 70 - 150 });
    txt(c, t.value, x + w - 26, y + 50 + av * 0.68, { size: 36, w: 900, color: col, align: 'right' });
    if (t.value2) txt(c, t.value2 + (t.how ? ' \u00b7 ' + t.how : ''), x + 26, y + h - 20, { size: 21, w: 700, color: 'rgba(235,235,245,0.6)', max: w - 52 });
  }
  async function drawNight(c, W, H, d) {
    const port = H > W, P = port ? 64 : 56, theme = 'dark';
    background(c, W, H, theme);
    const up = d.todayPct == null ? true : d.todayPct >= 0;
    // chart band behind
    const band = port ? { y: 640, h: 330 } : { y: 532, h: 112 };
    const endPt = balanceLine(c, 0, band.y, W - (port ? 150 : 130), band.h, d.points, d.marks, d.start);

    const tw0 = txt(c, d.dateLabel ? 'TONIGHT \u00b7 ' + d.dateLabel.toUpperCase() : 'TONIGHT', P, P + 22, { size: port ? 24 : 21, w: 900, color: LIME, track: 3 });
    if (d.mode) modeTag(c, P + tw0 + 22, P - 6, theme, d.mode, port ? 20 : 18);
    await wordmark(c, W - P, P + 26, theme, port ? 30 : 26, 'right');
    const balY = port ? 290 : 210;
    txt(c, d.balanceText || 'n/a', P - 6, balY, { size: port ? 190 : 150, w: 900, color: WHITE, max: port ? W - P * 2 : 660, track: -6 });
    let px = P; const py = balY + (port ? 34 : 26), ph = port ? 88 : 72;
    if (d.todayPct != null) px += pill(c, `${pct(d.todayPct, 2)} today`, px, py, { size: port ? 48 : 36, w: 900, h: ph, bg: up ? LIME : RED, fg: INK, icon: up ? ICON.up : ICON.down }) + 14;
    if (d.multiplier != null) px += pill(c, `${d.multiplier.toFixed(2)}\u00d7 all time`, px, py, { size: port ? 40 : 32, w: 900, h: ph, stroke: LIME, lw: 4, fg: LIME }) + 14;

    // wins / losses
    const wy = port ? 540 : 420;
    let wx = P;
    const wl = (n, label, col) => {
      const w1 = txt(c, String(n), wx, wy, { size: port ? 80 : 64, w: 900, color: col, track: -2 });
      const w2 = txt(c, label, wx + w1 + 12, wy, { size: port ? 30 : 26, w: 800, color: 'rgba(235,235,245,0.7)' });
      wx += w1 + 12 + w2 + (port ? 48 : 40);
    };
    if (d.wins != null) { wl(d.wins, d.wins === 1 ? 'win' : 'wins', GREEN); wl(d.losses, d.losses === 1 ? 'loss' : 'losses', RED); }

    // best / worst tiles
    const tiles = [];
    if (d.best) tiles.push(['BEST TRADE', d.best]);
    if (d.worst) tiles.push(['WORST TRADE', d.worst]);
    if (port) {
      const tw = (W - P * 2 - 24) / 2, ty = 1080;
      for (let i = 0; i < tiles.length; i++) await tradeTile(c, P + i * (tw + 24), ty, tiles.length === 1 ? W - P * 2 : tw, 170, tiles[i][0], tiles[i][1], theme);
    } else {
      const tx = 760, tw = W - tx - P;
      for (let i = 0; i < tiles.length; i++) await tradeTile(c, tx, 96 + i * 152, tw, 140, tiles[i][0], tiles[i][1], theme);
    }
    if (!tiles.length) txt(c, 'No closed trades yet tonight', port ? P : 760, port ? 1080 : 200, { size: 28, w: 700, color: 'rgba(235,235,245,0.55)' });
    // mascot sticker
    // the mascot rides the end of tonight's line
    const m = await mascot({ ring: false, body: LIME, ink: INK, hat: WHITE, fuse: LIME, spark: LIME });
    if (m && endPt) {
      const ms = port ? 230 : 160;
      c.save(); c.translate(endPt[0] + ms * 0.02, endPt[1] - ms * 0.42); c.rotate(-0.1);
      c.shadowColor = 'rgba(205,255,0,0.55)'; c.shadowBlur = 36; c.drawImage(m, -ms * 0.45, -ms * 0.6, ms, ms); c.restore();
    }
  }

  /* ================= AUDIT card ================= */
  function stack(c, cx, top, rx, gap, layers, beamN, labels) {
    const RY = rx * 0.2, TH = Math.max(10, gap * 0.3);
    const n = layers.length;
    for (let i = n - 1; i >= 0; i--) {
      const l = layers[i], cy = top + i * gap;
      const col = l.pass === true ? GREEN : l.pass === false ? RED : '#636366';
      const side = l.pass === true ? '#0f3d24' : l.pass === false ? '#5a1a16' : '#2c2c2e';
      c.beginPath(); c.moveTo(cx - rx, cy); c.lineTo(cx - rx, cy + TH); c.ellipse(cx, cy + TH, rx, RY, 0, Math.PI, 0, true); c.lineTo(cx + rx, cy); c.closePath();
      c.fillStyle = side; c.fill();
      c.beginPath(); c.ellipse(cx, cy, rx, RY, 0, 0, Math.PI * 2);
      const g = c.createLinearGradient(0, cy - RY, 0, cy + RY); g.addColorStop(0, '#3a3a3c'); g.addColorStop(1, '#232325');
      c.fillStyle = g; c.fill(); c.lineWidth = 3; c.strokeStyle = col; c.stroke();
      c.beginPath(); c.ellipse(cx, cy, rx * 0.6, RY * 0.6, 0, 0, Math.PI * 2); c.lineWidth = 1.5; c.strokeStyle = 'rgba(235,235,245,0.1)'; c.stroke();
      txt(c, String(l.n), cx, cy + RY * 0.42, { size: Math.round(RY * 1.1), w: 900, color: col, align: 'center' });
    }
    if (beamN != null) {
      const i = layers.findIndex((l) => l.n === beamN); if (i < 0) return;
      const cy = top + i * gap;
      const bw = labels ? labels : rx * 2.6;
      c.save();
      const g = c.createLinearGradient(0, cy - gap * 0.55, 0, cy + gap * 0.55);
      g.addColorStop(0, 'rgba(100,210,255,0)'); g.addColorStop(0.5, 'rgba(100,210,255,0.22)'); g.addColorStop(1, 'rgba(100,210,255,0)');
      c.fillStyle = g; c.fillRect(cx - rx * 1.3, cy - gap * 0.55, bw, gap * 1.1);
      c.shadowColor = CYAN; c.shadowBlur = 22; c.fillStyle = CYAN; c.fillRect(cx - rx * 1.3, cy + gap * 0.46, bw, 3);
      c.fillRect(cx - rx * 1.3, cy - gap * 0.5, bw, 2);
      c.restore();
    }
  }
  async function drawAudit(c, W, H, d) {
    const port = H > W, P = port ? 64 : 56;
    const fail = d.failed;
    const theme = fail ? 'red' : 'lime', ink = INK;
    background(c, W, H, theme);
    const av = port ? 84 : 64;
    await avatar(c, d.coin, P, P, av, theme);
    const nx = P + av + 20;
    txt(c, d.coin.name || sym(d.coin) || 'Coin', nx, P + av * 0.5, { size: port ? 40 : 32, w: 900, color: ink, max: port ? 520 : 380 });
    const sub = ['$' + sym(d.coin), CHAIN[d.coin.chain] || d.coin.chain].filter(Boolean).join(' \u00b7 ');
    const sw = txt(c, sub, nx, P + av * 0.5 + (port ? 38 : 32), { size: port ? 24 : 21, w: 700, color: soft(theme, 0.6) });
    if (d.mode) modeTag(c, nx + sw + 12, P + av * 0.5 + (port ? 12 : 10), theme, d.mode, port ? 19 : 17);

    // verdict
    const vx = P - 4, vy = port ? 330 : 270;
    const head = fail ? 'REJECTED' : 'CLEARED';
    txt(c, head, vx, vy, { size: port ? 170 : 112, w: 900, color: ink, max: port ? W - P * 2 : 520, track: -4 });
    const reason = fail ? (d.reason || 'failed a check') : `${d.ok}/${d.total} parts`;
    const rLines = wrap(c, reason, port ? W - P * 2 : 500, 900, port ? 72 : 52);
    rLines.slice(0, 2).forEach((s, i) => {
      const y = vy + (port ? 96 : 72) + i * (port ? 80 : 58);
      if (fail) pill(c, s, vx + 4, y - (port ? 66 : 48), { size: port ? 56 : 40, w: 900, bg: ink, fg: WHITE, r: 18, padX: 22 });
      else pill(c, s, vx + 4, y - (port ? 66 : 48), { size: port ? 56 : 40, w: 900, bg: ink, fg: LIME, r: 18, padX: 22, icon: ICON.check });
    });
    const line2Y = vy + (port ? 96 : 72) + (Math.min(2, rLines.length) - 1) * (port ? 80 : 58) + (port ? 70 : 54);
    const note = fail ? (d.failN ? `Auditor stopped at part ${d.failN} of ${d.total}. The desk won't buy.` : "The desk won't buy.") : 'Every part checked. Goes to the Analyst.';
    const nLines = wrap(c, note, port ? W - P * 2 : 500, 700, port ? 30 : 25);
    nLines.slice(0, 2).forEach((s, i) => txt(c, s, vx + 4, line2Y + i * (port ? 40 : 33), { size: port ? 30 : 25, w: 700, color: soft(theme, 0.72) }));

    // stack panel
    const sp = port ? { x: P, y: 640, w: W - P * 2, h: 600 } : { x: 572, y: 36, w: W - 572 - 36, h: H - 72 };
    c.save(); c.shadowColor = 'rgba(0,0,0,0.3)'; c.shadowBlur = 36; c.shadowOffsetY = 14;
    rr(c, sp.x, sp.y, sp.w, sp.h, 36); c.fillStyle = INK; c.fill(); c.restore();
    const n = Math.max(1, d.layers.length);
    const gap = Math.min(port ? 64 : 64, (sp.h - (port ? 110 : 84)) / Math.max(1, n - 1 + 0.6));
    const top = sp.y + (sp.h - (n - 1) * gap) / 2 - gap * 0.08;
    const rx = port ? 120 : 92, cx = sp.x + (port ? 40 : 30) + rx;
    const lx = cx + rx + (port ? 44 : 34), rxEdge = sp.x + sp.w - (port ? 36 : 28);
    c.save(); rr(c, sp.x, sp.y, sp.w, sp.h, 36); c.clip();
    stack(c, cx, top, rx, gap, d.layers, fail ? d.failN : d.layers[d.layers.length - 1].n, sp.x + sp.w - (cx - rx * 1.3));
    c.restore();
    d.layers.forEach((l, i) => {
      const y = top + i * gap;
      const col = l.pass === true ? WHITE : l.pass === false ? RED : 'rgba(235,235,245,0.38)';
      const mr = port ? 15 : 13;
      mark(c, lx + mr, y, mr, l.pass, INK);
      const val = String(l.value == null || l.value === '' ? 'n/a' : l.value);
      const vs = l.pass === null ? (port ? 20 : 16) : (port ? 26 : 21);
      const valW = Math.min(port ? 300 : 190, measure(c, val, 800, vs));
      txt(c, l.name || 'Part ' + l.n, lx + mr * 2 + 14, y + (port ? 9 : 7.5), { size: port ? 26 : 21, w: 700, color: l.pass === null ? 'rgba(235,235,245,0.45)' : 'rgba(235,235,245,0.92)', max: rxEdge - (lx + mr * 2 + 14) - valW - 14, min: 15 });
      txt(c, val, rxEdge, y + (port ? 9 : 7.5), { size: vs, w: 800, color: col, align: 'right', max: port ? 300 : 190, min: 12 });
    });

    // brand
    await wordmark(c, P, H - (port ? 56 : 44), theme, port ? 30 : 25);
    const m = await mascot(fail ? { ring: false, hat: WHITE } : { ring: false });
    if (m && !port) { const ms = 150; c.save(); c.translate(572 - ms - 4, H - ms - 10); c.rotate(fail ? 0.2 : -0.1); c.drawImage(m, 0, 0, ms, ms); c.restore(); }
    if (m && port) { const ms = 200; c.save(); c.translate(W - P - ms + 30, 360); c.rotate(fail ? 0.2 : -0.1); c.drawImage(m, 0, 0, ms, ms); c.restore(); }
  }
  function wrap(c, s, max, w, size) {
    c.font = F(w, size);
    const words = String(s).split(/\s+/); const out = []; let cur = '';
    for (const wd of words) { const t = cur ? cur + ' ' + wd : wd; if (c.measureText(t).width > max && cur) { out.push(cur); cur = wd; } else cur = t; }
    if (cur) out.push(cur);
    return out;
  }

  /* ================= data (only what /api already returns) ================= */
  function modeLabel(st) { if (A && A.DEMO) return 'DEMO'; return st && st.mode === 'PAPER' ? 'PAPER' : null; }
  async function getState() { return (A.S && A.S.state) || A.api('/api/state').catch(() => null); }
  async function activityRows(untilMs, pages) {
    let rows = (A.S && A.S.activity && A.S.activity.rows) ? A.S.activity.rows.slice() : null;
    let next = rows ? A.S.activity.next : undefined;
    if (!rows) { const d = await A.api('/api/activity'); rows = (d.rows || []).slice(); next = d.next; }
    let k = 0;
    while (next != null && k < (pages || 4) && (untilMs == null || toMs(rows[rows.length - 1].ts) > untilMs)) {
      const d = await A.api('/api/activity?before=' + enc(next)); rows = rows.concat(d.rows || []); next = d.next; k++;
    }
    return rows;
  }
  const reasonOf = (r) => {
    const s = String(r.subtitle || '').split('\u00b7').pop().trim();
    if (r.kind === 'timeout') return cap(s || 'dead money');
    if (r.kind === 'stopped') return 'Stopped out';
    return cap(s) || null;
  };

  async function tradeData(arg) {
    const mint = arg.mint;
    const [coin, audit, dec, st, rows] = await Promise.all([
      A.api('/api/coin/' + enc(mint)),
      A.api('/api/audit/' + enc(mint)).catch(() => null),
      A.api('/api/agents/analyst/decisions').catch(() => null),
      getState(), activityRows(null, 4).catch(() => []),
    ]);
    const mine = rows.filter((r) => r.mint === mint);
    let exit = arg.rowId != null ? mine.find((r) => String(r.id) === String(arg.rowId) && EXIT.includes(r.kind)) : null;
    const pos = coin.position;
    const open = !exit && !!pos;
    if (!exit && !open) exit = mine.find((r) => EXIT.includes(r.kind)) || null;
    if (!exit && !open) throw new Error('No trade on this coin yet');
    const outAt = exit ? toMs(exit.ts) : null;
    const buy = mine.find((r) => r.kind === 'bought' && (outAt == null || toMs(r.ts) <= outAt));
    const inAt = buy ? toMs(buy.ts) : null;
    const d = { coin, open, candles: coin.candles || [], inAt, mode: modeLabel(st), agents: [] };
    if (open) {
      d.pnl = num(pos.pnlUsd); d.pnlText = usd(pos.pnlUsd, true); d.pctText = pct(pos.pnlPct, 1);
      d.reason = pos.trailActive ? 'Trail armed' : 'Open';
      d.priceFrom = num(pos.entryPrice) != null ? fmtPrice(+pos.entryPrice) : null; d.priceTo = num(coin.price) != null ? fmtPrice(+coin.price) : null;
      const lastC = d.candles.length ? toMs(d.candles[d.candles.length - 1][0]) : Date.now();
      d.outAt = lastC; d.held = inAt ? hold(Date.now() - inAt) : null;
    } else {
      d.pnl = money(exit.value); d.pnlText = exit.value || null; d.pctText = exit.value2 || null;
      d.reason = reasonOf(exit); d.outAt = outAt; d.held = inAt ? hold(outAt - inAt) : null;
    }
    if (audit && audit.layers && audit.layers.length) d.agents.push({ text: `Auditor ${audit.layers.filter((l) => l.pass === true).length}/${audit.layers.length}`, color: '#0a84ff' });
    const v = dec && (dec.rows || []).find((r) => r.mint === mint && /score\s+\d+/.test(r.reason || ''));
    if (v) d.agents.push({ text: 'Analyst ' + v.reason.match(/score\s+(\d+)/)[1], color: '#ffd60a' });
    if (coin.chain === 'robinhood' && num(coin.safetyScore) != null) d.agents.push({ text: 'Safety ' + coin.safetyScore, color: LIME });
    if (!open) d.agents.push({ text: 'Risk \u00b7 ' + (exit.kind === 'timeout' ? 'timed out' : exit.kind === 'stopped' ? 'stopped' : 'trailed out'), color: '#ff6b9a' });
    return d;
  }

  async function nightData() {
    const [st, ch] = await Promise.all([A.api('/api/state'), A.api('/api/chart?range=LIVE')]);
    const pts = (ch && ch.points) || [];
    const from = pts.length ? toMs(pts[0][0]) : null;
    const rows = await activityRows(from, 6).catch(() => []);
    const exits = rows.filter((r) => EXIT.includes(r.kind) && (from == null || toMs(r.ts) >= from) && money(r.value) != null);
    const coins = {};
    const tr = exits.map((r) => ({ pnl: money(r.value), value: r.value, value2: r.value2, how: reasonOf(r), coin: { mint: r.mint, chain: r.chain, image: r.image, symbol: String(r.title || '').replace(/^(Sold|Stopped)\s+/, '') } }));
    tr.forEach((t) => { coins[t.coin.mint] = t.coin; });
    let best = null, worst = null;
    for (const t of tr) { if (!best || t.pnl > best.pnl) best = t; if (!worst || t.pnl < worst.pnl) worst = t; }
    if (worst === best) worst = null;
    const last = pts.length ? toMs(pts[pts.length - 1][0]) : null;
    return {
      balanceText: usd(st.balance), todayPct: num(st.todayPct), multiplier: num(st.multiplier), mode: modeLabel(st),
      points: pts, marks: ch.marks || [], start: num(st.nightStart) != null ? +st.nightStart : num(ch.start),
      wins: tr.length ? tr.filter((t) => t.pnl >= 0).length : (ch.marks || []).filter((m) => m.kind === 'win').length,
      losses: tr.length ? tr.filter((t) => t.pnl < 0).length : (ch.marks || []).filter((m) => m.kind === 'loss').length,
      best, worst, dateLabel: last ? day(last) : null,
    };
  }

  async function auditData(arg) {
    const [a, st] = await Promise.all([A.api('/api/audit/' + enc(arg.mint)), getState()]);
    if (!a || !a.layers || !a.layers.length) throw new Error('No audit for this coin yet');
    const layers = a.layers.slice().sort((x, y) => x.n - y.n);
    const failedL = layers.find((l) => l.pass === false);
    const failed = a.passed === false || !!failedL;
    const coin = { mint: a.mint || arg.mint, name: a.name, symbol: a.symbol, chain: a.chain, image: (A.S && A.S.coin && A.S.coin[arg.mint] && A.S.coin[arg.mint].image) || null };
    return { coin, layers, failed, failN: failedL ? failedL.n : null, reason: a.reason || (failedL ? `${String(failedL.name).toLowerCase()} ${failedL.value}` : null),
      ok: layers.filter((l) => l.pass === true).length, total: layers.length, mode: modeLabel(st) };
  }

  /* ================= render + share ================= */
  const DRAW = { trade: drawTrade, night: drawNight, audit: drawAudit };
  const DATA = { trade: tradeData, night: () => nightData(), audit: auditData };
  async function render(kind, data, layout) {
    const [W, H] = SIZES[layout];
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const c = cv.getContext('2d');
    await DRAW[kind](c, W, H, data);
    return cv;
  }
  const toBlob = (cv) => new Promise((res) => cv.toBlob((b) => res(b), 'image/png'));
  function fileName(kind, data, layout) {
    const s = (data.coin && sym(data.coin)) || 'night';
    return `grokhood-desk-${kind}-${s.toLowerCase().replace(/[^a-z0-9]+/g, '')}${layout === 'port' ? '-portrait' : ''}.png`;
  }
  async function shareFile(file) {
    try {
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); return 'shared'; }
    } catch (e) { if (e && e.name === 'AbortError') return 'cancelled'; }
    const url = URL.createObjectURL(file);
    const a = document.createElement('a'); a.href = url; a.download = file.name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return 'downloaded';
  }

  let styled = false;
  function style() {
    if (styled) return; styled = true;
    const s = document.createElement('style');
    s.textContent = `.shs-seg{display:flex;margin:4px var(--g) 12px;background:rgba(118,118,128,.24);border-radius:9px;padding:2px}
.shs-seg button{flex:1;height:30px;border-radius:7px;font-size:13px;font-weight:600;color:#fff}
.shs-seg button.on{background:#636366}
.shs-img{margin:0 var(--g) 14px;border-radius:14px;overflow:hidden;background:#1c1c1e;display:grid;place-items:center;min-height:180px}
.shs-img img{display:block;width:100%;height:auto;max-height:46vh;object-fit:contain}
.sheet .shs-btn{display:flex;width:calc(100% - 2 * var(--g))}
.sheet .shs-btn.go{background:#cdff00;color:#000}
.shs-img .shs-msg{color:var(--text2);font-size:15px;padding:30px 20px;text-align:center}
#activity-longpress .row,.list .row{-webkit-touch-callout:none}
.iconbtn[data-share] svg{stroke:#fff}
.card-debug #app,.card-debug #sheet-root,.card-debug #toast{display:none!important}
.card-debug body{margin:0;background:#000;overflow:hidden}
.card-debug canvas.card-full{display:block}`;
    document.head.appendChild(s);
  }

  // open the share sheet: build both layouts first, so the Share tap calls navigator.share inside the user gesture
  async function open(kind, arg) {
    if (!A) return;
    style();
    const root = document.getElementById('sheet-root');
    root.innerHTML = `<div class="sheet-bg"></div><div class="sheet"><div class="grabber"></div>
      <h2>${{ trade: 'Share trade', night: 'Share tonight', audit: 'Share audit' }[kind]}</h2>
      <div class="shs-seg"><button data-l="land" class="on">Post 16:9</button><button data-l="port">Portrait 4:5</button></div>
      <div class="shs-img"><div class="shs-msg">Drawing the card...</div></div>
      <button class="cta shs-btn go" data-go disabled style="opacity:.4">Share</button><button class="cta ghost shs-btn" data-no>Cancel</button></div>`;
    const close = () => { root.innerHTML = ''; urls.forEach((u) => URL.revokeObjectURL(u)); };
    const urls = [];
    root.querySelector('.sheet-bg').onclick = close;
    root.querySelector('[data-no]').onclick = close;
    const files = {}; let layout = 'land';
    const imgBox = root.querySelector('.shs-img'), go = root.querySelector('[data-go]');
    const show = () => {
      root.querySelectorAll('[data-l]').forEach((b) => b.classList.toggle('on', b.dataset.l === layout));
      if (files[layout]) { const u = URL.createObjectURL(files[layout]); urls.push(u); imgBox.innerHTML = `<img alt="Share card preview" src="${u}">`; }
    };
    root.querySelectorAll('[data-l]').forEach((b) => { b.onclick = () => { layout = b.dataset.l; show(); }; });
    go.onclick = async () => {
      if (!files[layout]) return;
      const r = await shareFile(files[layout]);
      if (r === 'downloaded' && A.toast) A.toast('Card saved');
      if (r !== 'cancelled') close();
    };
    try {
      const data = await DATA[kind](arg || {});
      for (const l of ['land', 'port']) {
        const blob = await toBlob(await render(kind, data, l));
        files[l] = new File([blob], fileName(kind, data, l), { type: 'image/png' });
      }
      if (!root.contains(go)) return;
      show(); go.disabled = false; go.style.opacity = 1;
    } catch (e) {
      imgBox.innerHTML = `<div class="shs-msg">${String((e && e.message) || 'Could not draw the card').replace(/[<>&]/g, '')}</div>`;
    }
  }

  // long-press an exit row in Activity (or the Home list) to share that trade
  function bindLongPress() {
    let t = null, fired = false, sx = 0, sy = 0;
    const rowOf = (e) => e.target.closest && e.target.closest('.list .row[data-id]');
    const cancel = () => { clearTimeout(t); t = null; };
    document.addEventListener('touchstart', (e) => {
      const r = rowOf(e); if (!r) return;
      fired = false; sx = e.touches[0].clientX; sy = e.touches[0].clientY;
      cancel(); t = setTimeout(() => {
        const rows = (A.S && A.S.activity && A.S.activity.rows) || [];
        const row = rows.find((x) => String(x.id) === r.dataset.id);
        if (!row || !row.mint || !EXIT.includes(row.kind)) return;
        fired = true; if (navigator.vibrate) navigator.vibrate(12);
        open('trade', { mint: row.mint, rowId: row.id });
      }, 480);
    }, { passive: true });
    document.addEventListener('touchmove', (e) => { if (t && (Math.abs(e.touches[0].clientX - sx) > 8 || Math.abs(e.touches[0].clientY - sy) > 8)) cancel(); }, { passive: true });
    document.addEventListener('touchend', cancel, { passive: true });
    document.addEventListener('contextmenu', (e) => { if (rowOf(e)) e.preventDefault(); });
    document.addEventListener('click', (e) => { if (fired && rowOf(e)) { e.preventDefault(); e.stopPropagation(); fired = false; } }, true);
  }

  // demo only: ?demo=1&card=trade|night|audit[&layout=portrait][&sym=X] draws the card full page for screenshots
  async function debugCard() {
    const qs = new URLSearchParams(location.search);
    const kind = qs.get('card');
    if (!A.DEMO || !DRAW[kind]) return;
    style();
    document.documentElement.classList.add('card-debug');
    const layout = qs.get('layout') === 'portrait' ? 'port' : 'land';
    const s = qs.get('sym') || (kind === 'audit' ? 'DUCK' : kind === 'trade' ? 'LFROG' : null);
    const coin = s && A.coins ? A.coins(s) : null;
    try {
      const data = await DATA[kind](coin ? { mint: coin.mint } : {});
      const cv = await render(kind, data, layout);
      cv.className = 'card-full'; cv.style.width = cv.width + 'px'; cv.style.height = cv.height + 'px';
      document.body.appendChild(cv);
    } catch (e) { document.body.insertAdjacentHTML('beforeend', `<pre style="color:#fff">${String(e && e.stack || e)}</pre>`); }
  }

  function init(o) {
    A = o;
    style();
    bindLongPress();
    debugCard();
  }

  window.Share = { init, open, render, data: DATA };
})();
