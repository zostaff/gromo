/* GROMO: iPhone web app (analysis powered by GROKHOOD). Plain JS, no build.
   Real mode reads only /api/* (shapes in docs/ARCHITECTURE.md). ?demo=1 uses the local fixture
   generator at the bottom of this file, shows a grey DEMO pill, and never talks to the backend. */
(function () {
  'use strict';
  // Auditor layer count: 9 by default, follows what the API returns (8 when layer 9 is off).
  var NL = 9;

  /* ================= boot: token, mode ================= */
  const qs = new URLSearchParams(location.search);
  const DEMO = qs.get('demo') === '1';
  const DEMO_STATE = DEMO ? qs.get('state') : null; // loading | empty | error (design only)
  const STORY_LIVE = DEMO && qs.get('story') === 'live'; // the same night, played live from $120 by the recorder
  const STORY = DEMO && (qs.get('story') === '1' || STORY_LIVE); // demo video: one scripted PAPER night from a $120 stack
  const SHOW = DEMO && (qs.get('show') === '1' || STORY);
  const STORY_STACK = STORY_LIVE && +qs.get('stack') > 0 ? +qs.get('stack') : 120; // live story: starting stack (default $120)
  const STORY_T0 = STORY_LIVE && /^\d{1,2}:\d{2}$/.test(qs.get('t0') || '') ? qs.get('t0').split(':').map(Number) : [23, 45]; // live story: night start, local time // demo video: the recorder drives a fast scripted session through window.__show
  const TOKEN_KEY = 'desk.token';
  (function takeToken() {
    const m = location.hash.match(/(?:^#|&)t=([^&]+)/);
    if (m) {
      try { localStorage.setItem(TOKEN_KEY, decodeURIComponent(m[1])); } catch (e) { /* private mode */ }
      history.replaceState(null, '', location.pathname + location.search + '#/home');
    }
  })();
  const token = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } };
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const standalone = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  const { fmtPrice, toMs } = window.Charts;

  /* ================= helpers ================= */
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; }
  const num = (v) => (v == null || v === '' || !isFinite(+v) ? null : +v);
  function usd(v, opts = {}) {
    v = num(v); if (v == null) return 'n/a';
    const s = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (opts.sign) return (v > 0 ? '+$' : v < 0 ? '-$' : '$') + s;
    return (v < 0 ? '-$' : '$') + s;
  }
  function compact(v) {
    v = num(v); if (v == null) return 'n/a';
    const a = Math.abs(v);
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return '$' + (v / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return '$' + (v / 1e3).toFixed(1) + 'K';
    return '$' + v.toFixed(0);
  }
  function pct(v, d = 2) { v = num(v); if (v == null) return 'n/a'; return (v > 0 ? '+' : v < 0 ? '-' : '') + Math.abs(v).toFixed(d) + '%'; }
  const intf = (v) => (num(v) == null ? 'n/a' : Math.round(+v).toLocaleString('en-US'));
  const time = (ts) => { const t = toMs(ts); return t ? new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : ''; };
  function ago(ts) {
    const s = Math.max(0, (Date.now() - toMs(ts)) / 1000);
    if (s < 60) return 'now'; if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h'; return Math.floor(s / 86400) + 'd';
  }
  function ageText(h) {
    h = num(h); if (h == null) return null;
    if (h < 1) return Math.max(1, Math.round(h * 60)) + 'm old';
    if (h < 48) return Math.round(h) + 'h old';
    return Math.round(h / 24) + 'd old';
  }
  const CHAIN = { solana: 'Solana', robinhood: 'Robinhood Chain' };
  const fullShield = (s) => { const m = /^(\d+)\/(\d+)/.exec(String(s || '')); return !!m && m[1] === m[2]; };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function toast(text) {
    const t = $('#toast'); t.textContent = text; t.classList.add('on');
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('on'), 1800);
  }

  /* ================= icons ================= */
  const I = {
    search: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.6-4.6"/></svg>',
    bell: '<svg viewBox="0 0 24 24"><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M15 4.5 7.5 12 15 19.5"/></svg>',
    share: '<svg viewBox="0 0 24 24"><path d="M12 3.5v11M8 7.2l4-3.9 4 3.9"/><path d="M8.5 10.5H6.8A1.3 1.3 0 0 0 5.5 11.8v7.4a1.3 1.3 0 0 0 1.3 1.3h10.4a1.3 1.3 0 0 0 1.3-1.3v-7.4a1.3 1.3 0 0 0-1.3-1.3h-1.7"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 5h5v5M19 5l-8 8M17 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h4"/></svg>',
    check: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m3.5 8.4 3 3 6-6.6"/></svg>',
    x: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m4.5 4.5 7 7M11.5 4.5l-7 7"/></svg>',
    replay: '<svg viewBox="0 0 24 24"><path d="M4.6 13.4A7.6 7.6 0 1 0 6.8 6.6"/><path d="M5.4 3.6v3.9h3.9"/><path d="M12 8.4v4l2.8 1.8"/></svg>',
    shield: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.2 13.5 3.4v4.2c0 3.2-2.2 5.6-5.5 7.2C4.7 13.2 2.5 10.8 2.5 7.6V3.4z" opacity=".25"/><path d="m5.3 8 1.9 1.9 3.6-3.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const AG = {
    scanner: { label: 'Scanner', color: 'cyan', hex: '#64d2ff', glyph: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.2"/><circle cx="10" cy="10" r="3.4"/><path d="M10 10 15.2 4.8"/></svg>' },
    auditor: { label: 'Auditor', color: 'blue', hex: '#0a84ff', glyph: '<svg viewBox="0 0 20 20"><ellipse cx="10" cy="5.2" rx="6.4" ry="2.4"/><path d="M3.6 5.2v3.2c0 1.3 2.9 2.4 6.4 2.4s6.4-1.1 6.4-2.4V5.2M3.6 11.4c0 1.3 2.9 2.4 6.4 2.4s6.4-1.1 6.4-2.4M3.6 14.4c0 1.3 2.9 2.4 6.4 2.4s6.4-1.1 6.4-2.4"/></svg>' },
    analyst: { label: 'Analyst', color: 'yellow', hex: '#ffd60a', glyph: '<svg viewBox="0 0 20 20"><path d="M3 14.5a7 7 0 1 1 14 0"/><path d="m10 14.5 3.6-4.4"/><circle cx="10" cy="14.5" r="1.2"/></svg>' },
    executor: { label: 'Executor', color: 'green', hex: '#00d35a', glyph: '<svg viewBox="0 0 20 20"><path d="M11.2 2.5 4.5 11h4.8l-1 6.5 6.8-8.6h-4.9z"/></svg>' },
    risk: { label: 'Risk', color: 'pink', hex: '#ff6b9a', glyph: '<svg viewBox="0 0 20 20"><path d="M10 2.5 16 5v4.6c0 3.8-2.6 6.4-6 7.9-3.4-1.5-6-4.1-6-7.9V5z"/><path d="M7.4 10.6h5.2"/></svg>' },
  };
  const ORDER = ['scanner', 'auditor', 'analyst', 'executor', 'risk'];
  const glyph = (n) => `<span class="glyph" style="background:${AG[n].hex}22;color:${AG[n].hex}">${AG[n].glyph}</span>`;

  const CHAIN_BADGE = {
    solana: '<span class="chain" style="background:#000"><svg viewBox="0 0 10 10"><path d="M2.2 2.4h6.4L7.7 3.5H1.3zM2.2 6.5h6.4L7.7 7.6H1.3z" fill="#14f195"/><path d="M1.3 4.45h6.4l.9 1.1H2.2z" fill="#9945ff"/></svg></span>',
    robinhood: '<span class="chain" style="background:#00c805"><svg viewBox="0 0 10 10"><path d="M2.4 7.8C3 4.6 5 2.6 8 2.2 7.4 5.2 5.4 7 2.4 7.8z" fill="#000"/></svg></span>',
  };
  const PALETTE = ['#ffd60a', '#64d2ff', '#ff9f0a', '#30d158', '#bf5af2', '#ff6b9a', '#5e5ce6', '#66d4cf', '#ff453a', '#ac8e68'];
  function hashStr(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function coinAvatar(c, big) {
    const name = c.symbol || c.name || '?';
    const letter = esc(String(name).replace(/^\$/, '').charAt(0).toUpperCase() || '?');
    const col = PALETTE[hashStr(c.mint || name) % PALETTE.length];
    const img = c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'mono',textContent:'${letter}',style:'background:${col}'}))">`
      : `<span class="mono" style="background:${col}">${letter}</span>`;
    return `<span class="coin-av${big ? ' big' : ''}">${img}${CHAIN_BADGE[c.chain] || ''}</span>`;
  }

  /* ================= network ================= */
  const net = {
    failing: false, lastOk: null, retryAt: 0, delay: 3, retryFn: null, authFail: false,
    ok() { this.failing = false; this.authFail = false; this.delay = 3; this.lastOk = Date.now(); paintBanners(); },
    fail(fn, err) {
      this.failing = true; this.authFail = !!(err && err.status === 401);
      this.retryFn = fn; this.retryAt = Date.now() + this.delay * 1000;
      this.delay = Math.min(30, Math.round(this.delay * 1.7));
      paintBanners();
    },
  };
  function bannerText() {
    if (!net.failing) return '';
    if (net.authFail) return 'Not signed in · open your GROMO link from Telegram';
    const n = Math.max(0, Math.ceil((net.retryAt - Date.now()) / 1000));
    const last = net.lastOk ? time(net.lastOk) : 'never';
    return `GROMO offline · retrying in ${n}s · last update ${last}`;
  }
  function paintBanners() { const t = bannerText(); $$('.offline').forEach((b) => { b.textContent = t; }); }
  setInterval(() => {
    if (net.failing && !net.authFail) {
      paintBanners();
      if (Date.now() >= net.retryAt && net.retryFn) { const f = net.retryFn; net.retryFn = null; f(); }
    }
  }, 1000);

  async function api(path, opts = {}) {
    if (DEMO) return Demo.api(path, opts);
    const init = { method: opts.method || 'GET', headers: { Authorization: 'Bearer ' + token() } };
    if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    let res;
    try { res = await fetch(path, init); } catch (e) { const er = new Error('offline'); er.status = 0; throw er; }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const msg = (data && (data.error || (typeof data.detail === 'string' ? data.detail : null))) || 'Error ' + res.status;
      const er = new Error(msg); er.status = res.status; er.data = data; throw er;
    }
    return data;
  }

  /* ================= store ================= */
  const S = {
    state: null, chart: {}, range: 'LIVE', activity: null, account: null, settings: null,
    coin: {}, audit: {}, decisions: {}, unreadSeen: false, layerLive: {},
    copy: null, replay: {}, replayH: 24, nightPost: null, nightPreview: null,
  };
  let stateTimer = null;
  function refreshStateSoon() {
    if (stateTimer) return;
    stateTimer = setTimeout(async () => {
      stateTimer = null;
      try { S.state = await api('/api/state'); net.ok(); current && current.onState && current.onState(); } catch (e) { /* banner handled by page loads */ }
    }, 900);
  }

  /* ================= SSE ================= */
  const EVENTS = ['scan', 'audit_layer', 'audit', 'verdict', 'signal', 'fill', 'exit', 'balance', 'activity', 'price', 'agent'];
  function startStream() {
    const handle = (type, data) => {
      if (type === 'audit_layer' && data && data.mint) {
        (S.layerLive[data.mint] = S.layerLive[data.mint] || {})[data.n] = data;
      }
      if (type === 'balance' && data) {
        if (data.balance == null && data.value != null) data.balance = data.value;
        if (S.state && data.balance != null) {
          S.state.balance = data.balance;
          const ns = num(S.state.nightStart);
          if (data.todayPct != null) S.state.todayPct = data.todayPct; else if (ns) S.state.todayPct = (data.balance / ns - 1) * 100;
          if (data.multiplier != null) S.state.multiplier = data.multiplier; else if (ns) S.state.multiplier = data.balance / ns;
          if (data.mode) S.state.mode = data.mode;
        }
      }
      if (type === 'activity' && data) {
        S.unreadSeen = false;
        if (S.state) S.state.unread = (S.state.unread || 0) + 1;
        if (S.activity && S.activity.rows && !S.activity.rows.some((r) => r.id != null && r.id === data.id)) S.activity.rows.unshift(data);
      }
      if (current && current.onEvent) current.onEvent(type, data);
      if (['scan', 'audit', 'verdict', 'signal', 'fill', 'exit', 'agent', 'activity'].includes(type)) refreshStateSoon();
    };
    if (DEMO) { Demo.stream(handle); return; }
    if (!token()) return;
    let es, backoff = 2000;
    const open = () => {
      es = new EventSource('/api/stream?t=' + encodeURIComponent(token()));
      es.onopen = () => { backoff = 2000; };
      EVENTS.forEach((t) => es.addEventListener(t, (ev) => { let d = null; try { d = JSON.parse(ev.data); } catch (e) { return; } handle(t, d); }));
      es.onmessage = (ev) => { try { const d = JSON.parse(ev.data); if (d && d.type) handle(d.type, d.data || d); } catch (e) { /* ignore */ } };
      es.onerror = () => { es.close(); setTimeout(open, backoff); backoff = Math.min(30000, backoff * 2); };
    };
    open();
  }

  /* ================= odometer ================= */
  class Odometer {
    constructor(node) { this.node = node; this.str = null; }
    set(str, animate = true) {
      if (str === this.str) return;
      const old = this.str; this.str = str;
      const same = old && old.length === str.length && [...str].every((c, i) => /\d/.test(c) === /\d/.test(old[i]) && (/\d/.test(c) || c === old[i]));
      if (!same) {
        this.node.innerHTML = [...str].map((c) => /\d/.test(c)
          ? `<span class="odo-d"><span class="odo-s" style="transform:translateY(-${+c}em)">${'0123456789'.split('').map((d) => `<span>${d}</span>`).join('')}</span></span>`
          : `<span class="odo-c">${esc(c)}</span>`).join('');
        return;
      }
      const slots = Array.from(this.node.children);
      const roll = animate && !reduced();
      let k = 0;
      for (let i = str.length - 1; i >= 0; i--) {
        if (!/\d/.test(str[i])) continue;
        const s = slots[i].firstElementChild;
        if (str[i] !== old[i]) {
          s.style.transition = roll ? '' : 'none';
          s.style.transitionDelay = roll ? (k * 30) + 'ms' : '0ms';
          s.style.transform = `translateY(-${+str[i]}em)`;
          k++;
        }
      }
    }
  }

  /* ================= router ================= */
  const TABS = ['home', 'agents', 'copy', 'activity', 'account'];
  let current = null;
  const stack = [];
  function parse(hash) {
    const p = (hash || '').replace(/^#\/?/, '').split('/');
    const name = p[0] || 'home';
    let arg = p[1] ? decodeURIComponent(p[1]) : null;
    if (DEMO && arg && arg[0] === '$' && Demo.bySym[arg.slice(1)]) arg = Demo.bySym[arg.slice(1)].mint; // demo shortcut: #/coin/$HCAT
    if ((name === 'coin' || name === 'audit') && arg) return { name, arg, depth: 1 };
    if (name === 'replay') return { name, arg, depth: 1 };
    return { name: TABS.includes(name) ? name : 'home', depth: 0 };
  }
  const PAGES = {};
  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }
  function back() {
    if (stack.length > 1) history.back(); else location.hash = '#/home';
  }
  function route() {
    const h = location.hash || '#/home';
    const r = parse(h);
    let anim = 'fade';
    if (stack.length > 1 && stack[stack.length - 2] === h) { stack.pop(); anim = 'pop'; }
    else if (r.depth > 0) { if (stack[stack.length - 1] !== h) stack.push(h); anim = stack.length > 1 ? 'push' : 'fade'; }
    else { if (current && current.depth > 0) anim = 'pop'; stack.length = 0; stack.push(h); }
    const prev = current;
    const page = PAGES[r.name](r.arg);
    page.depth = r.depth;
    page.el.classList.add('page');
    if (r.depth > 0) page.el.classList.add('pushed');
    $('#pages').appendChild(page.el);
    page.el.addEventListener('scroll', () => page.el.classList.toggle('scrolled', page.el.scrollTop > 4), { passive: true });
    current = page;
    $$('#tabbar a').forEach((a) => a.classList.toggle('on', a.dataset.tab === (r.depth ? null : r.name)));
    $('#tabbar').classList.toggle('hidden', r.depth > 0);
    paintBanners();
    page.load && page.load();
    if (prev) {
      if (anim === 'fade' || reduced()) {
        if (anim === 'fade' && !reduced()) page.el.classList.add('enter-fade');
        prev.destroy && prev.destroy(); prev.el.remove();
      } else {
        page.el.classList.add(anim === 'push' ? 'enter-push' : 'enter-pop');
        prev.el.classList.add(anim === 'push' ? 'leave-push' : 'leave-pop');
        if (anim === 'pop') prev.el.style.zIndex = 3;
        setTimeout(() => { prev.destroy && prev.destroy(); prev.el.remove(); page.el.classList.remove('enter-push', 'enter-pop'); }, 430);
      }
    }
  }
  window.addEventListener('hashchange', route);

  /* ================= shared pieces ================= */
  const skel = (w, h, extra = '') => `<div class="sk" style="width:${w};height:${h}px;${extra}"></div>`;
  function modePill() {
    if (DEMO) return '<span class="pill grey">DEMO</span>';
    const m = S.state && S.state.mode;
    if (m === 'PAPER') return '<span class="pill orange">PAPER</span>';
    if (m === 'LIVE') return '<span class="pill green">LIVE</span>';
    return '';
  }
  function navBack(title) {
    return `<header class="nav"><button class="back" data-back>${I.back}<span>Back</span></button><div class="nav-center">${esc(title || '')}</div><div class="grow"></div></header><div class="offline"></div>`;
  }
  function bindBack(root) { $$('[data-back]', root).forEach((b) => b.addEventListener('click', back)); }
  // Share card button in a pushed page's nav bar (cards are drawn by share.js)
  function navShare(root, kind, arg) {
    const h = $('header.nav', root); if (!h || $('[data-share]', h) || !window.Share) return;
    const b = el(`<button class="iconbtn" data-share aria-label="Share" style="margin-right:2px">${I.share}</button>`);
    b.onclick = () => window.Share.open(kind, arg); h.appendChild(b);
  }

  function actRow(r, fresh) {
    const kind = r.kind;
    const v = String(r.value == null ? '' : r.value);
    const vcls = kind === 'rejected' ? 'muted' : v.startsWith('+') ? 'up' : v.startsWith('-') ? 'down' : '';
    const v2 = String(r.value2 == null ? '' : r.value2);
    const v2cls = v2.startsWith('+') ? 'up' : v2.startsWith('-') ? 'down' : 'muted';
    const sub = r.subtitle || '';
    const subT = /\d:\d\d/.test(sub) || !r.ts ? sub : `${sub} · ${time(r.ts)}`;
    const href = r.mint ? (kind === 'rejected' ? '#/audit/' : '#/coin/') + encodeURIComponent(r.mint) : null;
    return `<a class="row${fresh ? ' new' : ''}" ${href ? `href="${href}"` : ''} data-id="${esc(r.id)}">
      ${coinAvatar({ mint: r.mint, chain: r.chain, image: r.image, name: (r.title || '').replace(/^(Bought|Sold|Stopped|Rejected)\s+/, '') })}
      <div class="rt"><b>${esc(r.title)}</b><span>${esc(subT)}</span></div>
      <div class="rv"><b class="${vcls}">${esc(v)}</b>${v2 ? `<span class="${v2cls}">${esc(v2)}</span>` : ''}</div></a>`;
  }
  const actSkel = (n) => Array.from({ length: n }, () => `<div class="row">${skel('44px', 44, 'border-radius:22px')}<div class="rt">${skel('55%', 16)}${skel('35%', 13, 'margin-top:8px')}</div>${skel('64px', 16)}</div>`).join('');

  /* ================= agent cards (home carousel) ================= */
  function agentStatus(a) {
    const s = a && a.status;
    const cls = s === 'paused' ? 'paused' : s === 'down' ? 'down' : 'live';
    const label = s === 'paused' ? 'Paused' : s === 'down' ? 'Down' : 'Running';
    return `<span class="astat"><span class="sdot ${cls}"></span>${label}</span>`;
  }
  function makeCard(name) {
    const node = el(`<article class="acard" data-agent="${name}">
      <div class="ahead">${glyph(name)}<span class="aname">${AG[name].label}</span><span data-s></span></div>
      <div data-b class="abox" style="display:flex;flex-direction:column;flex:1;min-height:0">${skel('45%', 40, 'margin-top:16px')}${skel('60%', 16, 'margin-top:12px')}<div style="margin-top:auto">${skel('100%', 30)}${skel('100%', 30, 'margin-top:8px')}${skel('70%', 16, 'margin-top:14px')}</div></div></article>`);
    const body = $('[data-b]', node);
    const st = { seen: new Set(), ring: null, inited: false };
    function update(a) {
      $('[data-s]', node).innerHTML = agentStatus(a);
      const c = (a && a.card) || {};
      CARD[name](body, c, a, st);
    }
    return { node, update };
  }
  const CARD = {
    scanner(body, c, a, st) {
      const feed = (c.feed || []).slice(0, 5);
      const rows = feed.map((f) => {
        const k = f.mint + f.verdict;
        const fresh = st.inited && !st.seen.has(k); st.seen.add(k);
        const right = f.verdict === 'kill' ? esc(f.reason || 'killed') : '→ auditor';
        return `<div class="fr ${f.verdict === 'kill' ? 'kill' : 'pass'}" style="${fresh ? '' : 'animation:none'}"><span class="fn">${esc(f.name || f.symbol)}</span><span class="fs">${esc(f.symbol || '')}</span><span class="fv">${right}</span></div>`;
      }).join('');
      body.innerHTML = `<div class="abig" style="color:${AG.scanner.hex}">${intf(c.pools)}<small>pools</small></div>
        <div class="muted" style="font-size:15px;margin-top:2px">${esc(a.subtitle || 'Watching graduations')}</div>
        <div class="abody">${feed.length ? `<div class="feed">${rows}</div>` : '<div class="empty" style="padding:22px 0 8px"><div class="dash"></div>Listening for graduations</div>'}</div>`;
      st.inited = true;
    },
    auditor(body, c, a) {
      const cur = c.current;
      const layers = new Map(((cur && cur.layers) || []).map((l) => [+l.n, l.pass]));
      if (cur && S.layerLive[cur.mint]) for (const [n, l] of Object.entries(S.layerLive[cur.mint])) layers.set(+n, l.pass);
      // in progress = no failed part yet and some part still unread (null); audit-off runs leave all null with last "audit off"
      const vals = [...layers.values()];
      const anyFail = vals.some((p) => p === false);
      const auditOff = c.last && /audit off/i.test(c.last) && vals.every((p) => p == null);
      const reading = !!cur && !anyFail && !auditOff && (layers.size < NL || vals.some((p) => p == null));
      let firstOpen = 0; for (let n = 1; n <= NL; n++) if (!layers.has(n) || layers.get(n) == null) { firstOpen = n; break; }
      const strips = Array.from({ length: NL }, (_, i) => {
        const n = i + 1; const p = layers.has(n) ? layers.get(n) : undefined;
        const cls = p === true ? 'ok' : p === false ? 'bad' : reading && n === firstOpen ? 'reading' : !reading && p === null ? 'na' : '';
        return `<div class="strip ${cls}">${p === true ? '✓' : p === false ? '✕' : n}</div>`;
      }).join('');
      let line;
      if (!cur) line = '<span class="muted">No coin taken apart yet</span>';
      else if (reading && firstOpen) line = `<span style="color:var(--cyan)">taking apart ${esc(cur.name)}...</span>`;
      else if (c.last && fullShield(c.last)) line = `<span class="up">${esc(c.last)}</span>`;
      else if (c.last) line = `<span class="down">${esc(c.last)}</span>`;
      else line = '';
      body.innerHTML = `<div class="abig" style="color:#4da3ff">${intf(c.takenApart)}<small>taken apart</small></div>
        <div class="muted" style="font-size:15px;margin-top:2px">${cur ? 'Now: ' + esc(cur.name) : esc(a.subtitle || '')}</div>
        <div class="abody"><div class="strips">${strips}</div><div class="aline">${line}</div>
        <div class="afoot"><span>${intf(c.rejected)} rejected</span><span>${NL} parts per coin</span></div></div>`;
      if (cur) body.querySelector('.strips').onclick = () => go('#/audit/' + encodeURIComponent(cur.mint));
    },
    analyst(body, c, a, st) {
      const sc = c.scoring;
      if (!st.ring) {
        body.innerHTML = `<div class="abig" style="color:var(--yellow)">Scoring</div>
          <div class="ringbox abody"><canvas width="132" height="132"></canvas><div class="ringmeta"><div class="cn"></div><div class="cs"></div><div class="cp" style="margin-top:10px"></div></div></div>
          <div class="afoot" style="margin-top:14px"><span data-scored></span><span data-mode></span></div>`;
        st.ring = new window.Charts.ScoreRing($('canvas', body));
        st.last = undefined;
      }
      const key = sc ? sc.mint + ':' + sc.score : null;
      if (key !== st.last) { st.ring.set(sc ? sc.score : null); st.last = key; }
      $('.cn', body).textContent = sc ? sc.name : 'Waiting';
      $('.cs', body).textContent = sc ? (sc.passed ? 'Passes to Executor' : 'Below the bar') : 'No coin to score yet';
      $('.cp', body).innerHTML = sc ? (sc.passed ? '<span class="pill green">pass</span>' : '<span class="pill red">reject</span>') : '';
      $('[data-scored]', body).textContent = `${intf(c.scored)} scored`;
      $('[data-mode]', body).textContent = c.mode === 'grok' ? 'Judge: Grok' : c.mode === 'rules' ? 'Judge: rules' : '';
    },
    executor(body, c) {
      const stt = c.state || 'idle';
      const p = clamp(num(c.progress) || 0, 0, 100);
      const line = stt === 'executing' ? `<span>Executing... ${Math.round(p)}%</span><span></span>`
        : stt === 'filled' ? '<span class="up">✓ Filled</span><span></span>'
        : '<span class="muted">Waiting for a signal</span><span></span>';
      body.innerHTML = `<div class="abig" style="color:var(--green)">${intf(c.trades)}<small>trades</small></div>
        <div class="ticket">${c.name ? coinAvatar({ mint: c.mint || c.name, name: c.name, chain: c.chain }) : '<span class="glyph" style="background:#2c2c2e;color:var(--text3)">' + AG.executor.glyph + '</span>'}
          <div class="rt"><b>${c.name ? esc(c.name) : 'No ticket yet'}</b><span>${num(c.stackPct) != null ? +c.stackPct + '% of stack' : 'n/a'}</span></div>
          <div class="tv">${c.lastSize != null ? usd(c.lastSize) : ''}</div></div>
        <div class="abody"><div class="xline">${line}</div><div class="bar"><i style="width:${stt === 'filled' ? 100 : stt === 'executing' ? p : 0}%"></i></div>
        <div class="afoot"><span>Opens on FOMO</span><span>You tap Buy</span></div></div>`;
    },
    risk(body, c) {
      const open = num(c.open) || 0;
      body.innerHTML = `<div class="abig" style="color:var(--pink)">${c.guarding ? 'Guarding' : 'Idle'}</div>
        <div class="muted" style="font-size:15px;margin-top:2px">${open} open position${open === 1 ? '' : 's'}</div>
        <div class="rail"><i class="rs"></i><i class="rt2"></i><b class="re"></b></div>
        <div class="raill"><span class="pill red mono">${num(c.stopPct) != null ? 'stop -' + +c.stopPct + '%' : 'stop n/a'}</span><span class="faint">entry</span><span class="pill green mono">${num(c.trailPct) != null ? 'trail ' + +c.trailPct + '%' : 'trail n/a'}</span></div>
        <div class="abody">
          <div class="afoot" style="margin-top:0;align-items:baseline"><span>Last exit</span><b style="font-size:17px;color:#fff">${c.last ? esc(c.last) : '<span class="muted" style="font-weight:500">No exits yet</span>'}</b></div>
        </div>`;
    },
  };

  /* ================= replay pieces (home card, agents link, replay page) ================= */
  const REFUSED = ['scanner', 'auditor', 'analyst'];
  function replayHeadline(s, cls) {
    s = s || {};
    if (num(s.refused) === 0) return 'GROMO has not said no to a coin in this window yet';
    return `GROMO said no to <b>${intf(s.refused)}</b> coin${num(s.refused) === 1 ? '' : 's'}, <b class="${cls || 'lime'}">${intf(s.dodged)}</b> of them would have lost money`;
  }
  function replayCard(d) {
    const s = d.summary || {};
    const st = (d.byStage || []).filter((x) => REFUSED.includes(x.stage) && num(x.n) > 0);
    const bar = st.length ? `<div class="rc-bar">${st.map((x) => `<i style="flex:${+x.n};background:${AG[x.stage].hex}"></i>`).join('')}</div>
      <div class="rc-leg">${st.map((x) => `<span style="--c:${AG[x.stage].hex}">${AG[x.stage].label} ${intf(x.n)}</span>`).join('')}</div>` : '';
    return `<a class="rcard" href="#/replay"><div class="rc-h"><span class="rc-t">${I.replay}Replay · last 24h</span><span class="chev">›</span></div>
      <div class="rc-line">${replayHeadline(s)}</div>${bar}</a>`;
  }

  /* ================= HOME ================= */
  PAGES.home = function () {
    const page = { el: el(`<section>
      <header class="nav">
        <a href="#/account" class="avatar" aria-label="Account"><img src="icons/favicon.svg" alt=""></a>
        <a href="#/agents" class="nav-title brand">GROMO <span class="chev">›</span></a>
        <div class="grow"></div>
        <button class="iconbtn" data-search aria-label="Search">${I.search}</button>
        <button class="iconbtn" data-bell aria-label="Notifications">${I.bell}<span class="dot" hidden></span></button>
      </header>
      <div class="offline"></div>
      <div class="hero" style="position:relative">
        <button class="iconbtn" data-share aria-label="Share tonight" style="position:absolute;bottom:-12px;right:calc(var(--g) + var(--sar) + 6px)">${I.share}</button>
        <div class="balance" data-bal>${skel('240px', 60)}</div>
        <div class="sub-row" data-sub>${skel('180px', 18)}</div>
      </div>
      <div class="chart-wrap"><canvas></canvas><div class="chart-empty" hidden></div></div>
      <div class="ranges">${['LIVE', '1H', '4H', '1D', '1W', 'ALL'].map((r) => `<button data-r="${r}">${r === 'LIVE' ? '<i class="ldot"></i>' : ''}${r}</button>`).join('')}</div>
      <div class="chart-sep"></div>
      <div data-replay></div>
      <div class="section-h"><h2 data-agh>Agents</h2></div>
      <div class="carousel"></div>
      <div class="dots">${ORDER.map((_, i) => `<i class="${i ? '' : 'on'}"></i>`).join('')}</div>
      <div class="section-h"><h2>Activity</h2><a href="#/activity">See all</a></div>
      <div class="list" data-act>${actSkel(5)}</div>
    </section>`) };
    const root = page.el;
    const balEl = $('[data-bal]', root);
    const odo = new Odometer(balEl);
    let scrubbing = false;
    const chart = new window.Charts.LineChart($('canvas', root), {
      maxPoints: 2000,
      onScrub: (v) => {
        if (v == null) { scrubbing = false; paintHero(false); return; }
        scrubbing = true; odo.set(usd(v), false);
      },
    });
    const cards = {};
    const car = $('.carousel', root);
    ORDER.forEach((n) => { cards[n] = makeCard(n); car.appendChild(cards[n].node); });
    car.addEventListener('scroll', () => {
      const i = Math.round(car.scrollLeft / (car.firstElementChild.offsetWidth + 10));
      $$('.dots i', root).forEach((d, k) => d.classList.toggle('on', k === i));
    }, { passive: true });
    $('[data-search]', root).onclick = openSearch;
    $('[data-share]', root).onclick = () => window.Share && window.Share.open('night');
    $('[data-bell]', root).onclick = () => { S.unreadSeen = true; if (S.state) S.state.unread = 0; api('/api/read', { method: 'POST' }).catch(() => {}); go('#/activity'); };

    function paintHero(animate = true) {
      const s = S.state; if (!s) return;
      if (balEl.querySelector('.sk')) balEl.innerHTML = '';
      if (!scrubbing) odo.set(usd(s.balance), animate);
      const tp = num(s.todayPct);
      $('[data-sub]', root).innerHTML = `${tp != null ? `<span class="${tp >= 0 ? 'up' : 'down'}">${tp >= 0 ? '▲' : '▼'} ${pct(tp)} today</span>` : '<span class="muted">n/a today</span>'}
        ${num(s.multiplier) != null ? `<span class="pill green">${(+s.multiplier).toFixed(2)}×</span>` : ''}${modePill()}`;
      $('[data-bell] .dot', root).hidden = !(s.unread > 0 && !S.unreadSeen);
      const agents = s.agents || [];
      const running = agents.filter((a) => a.status === 'running').length;
      $('[data-agh]', root).innerHTML = `Agents <span class="muted">· ${running} running</span>`;
      ORDER.forEach((n) => cards[n].update(agents.find((a) => a.name === n) || { name: n, status: 'down', card: {} }));
    }
    function paintChart() {
      const d = S.chart[S.range];
      $$('.ranges button', root).forEach((b) => b.classList.toggle('on', b.dataset.r === S.range));
      const empty = $('.chart-empty', root);
      if (!d) { empty.hidden = false; empty.innerHTML = skel('calc(100% - 32px)', 200); chart.setData({ points: [] }); return; }
      if (!d.points || d.points.length < 2) {
        empty.hidden = false; empty.innerHTML = '<div class="dash"></div><span>Waiting for the first trade</span>';
        chart.setData({ points: [] }); return;
      }
      empty.hidden = true;
      chart.setData(d);
    }
    function paintAct() {
      const box = $('[data-act]', root);
      if (!S.activity) return;
      const rows = (S.activity.rows || []).slice(0, 5);
      box.innerHTML = rows.length ? rows.map((r) => actRow(r)).join('') : '<div class="empty"><div class="dash"></div>Nothing yet tonight</div>';
    }
    function paintReplay() {
      const box = $('[data-replay]', root);
      const d = S.replay[24];
      if (d === undefined) { box.innerHTML = `<div class="rcard">${skel('40%', 14)}${skel('92%', 22, 'margin-top:12px')}${skel('70%', 22, 'margin-top:6px')}${skel('100%', 8, 'margin-top:16px')}</div>`; return; }
      if (!d) { box.innerHTML = ''; return; }
      box.innerHTML = replayCard(d);
    }
    async function loadReplay() {
      try { S.replay[24] = await api('/api/replay?h=24'); } catch (e) { if (S.replay[24] === undefined) S.replay[24] = null; }
      paintReplay();
    }
    async function loadChart(r) {
      try { S.chart[r] = await api('/api/chart?range=' + r); net.ok(); if (S.range === r) paintChart(); }
      catch (e) { net.fail(() => loadChart(r), e); }
    }
    $$('.ranges button', root).forEach((b) => b.addEventListener('click', () => { S.range = b.dataset.r; paintChart(); loadChart(S.range); }));

    page.load = async function load() {
      paintHero(false); paintChart(); paintAct(); paintReplay(); loadReplay();
      try {
        const [st, ch, ac] = await Promise.all([api('/api/state'), api('/api/chart?range=' + S.range), api('/api/activity')]);
        S.state = st; S.chart[S.range] = ch; S.activity = ac;
        net.ok(); paintHero(); paintChart(); paintAct();
      } catch (e) { net.fail(load, e); }
    };
    page.onState = () => paintHero();
    page.onEvent = (type, d) => {
      if (type === 'balance' && d) {
        paintHero();
        if (S.range === 'LIVE' && d.balance != null) {
          const pt = [d.ts || Date.now(), d.balance];
          const c = S.chart.LIVE; if (c && c.points) { c.points.push(pt); }
          chart.push(pt);
        }
      }
      if ((type === 'fill' || type === 'exit') && S.chart.LIVE) { loadChart('LIVE'); }
      if (type === 'scan' && S.state) {
        const sc = (S.state.agents || []).find((a) => a.name === 'scanner');
        if (sc && sc.card) { sc.card.feed = [{ name: d.name, symbol: d.symbol, mint: d.mint, chain: d.chain, verdict: d.verdict === 'pass' ? 'pass' : 'kill', reason: d.reason, liq: d.liquidityUsd }].concat(sc.card.feed || []).slice(0, 6); paintHero(); }
      }
      if (type === 'audit_layer' || type === 'verdict' || type === 'signal' || type === 'fill') {
        if (S.state) {
          const ag = (n) => (S.state.agents || []).find((a) => a.name === n);
          if (type === 'audit_layer') { const au = ag('auditor'); if (au && au.card && (!au.card.current || au.card.current.mint !== d.mint)) { au.card.current = { mint: d.mint, name: d.name || (au.card.current && au.card.current.name) || '', layers: [] }; } }
          if (type === 'verdict') { const an = ag('analyst'); if (an && an.card) an.card.scoring = { mint: d.mint, name: d.name || '', score: d.score, passed: d.passed }; }
          if (type === 'signal' || type === 'fill') { const ex = ag('executor'); if (ex && ex.card) { Object.assign(ex.card, d.card || {}); if (d.progress != null) ex.card.progress = d.progress; else if (type === 'signal') ex.card.progress = 0; if (d.state) ex.card.state = d.state; else ex.card.state = type === 'fill' ? 'filled' : 'executing'; if (d.name) ex.card.name = d.name; if (d.sizeUsd != null) ex.card.lastSize = d.sizeUsd; if (d.mint) ex.card.mint = d.mint; if (d.chain) ex.card.chain = d.chain; } }
          paintHero();
        }
      }
      if (type === 'activity') {
        const box = $('[data-act]', root);
        if (S.activity) { box.innerHTML = (S.activity.rows || []).slice(0, 5).map((r, i) => actRow(r, i === 0)).join(''); }
        paintHero();
      }
    };
    page.destroy = () => chart.destroy();
    return page;
  };

  /* ================= SEARCH ================= */
  function openSearch() {
    const root = $('#sheet-root');
    root.innerHTML = `<div class="sheet-bg"></div><div class="sheet full"><div class="grabber"></div>
      <div class="searchbar"><label>${I.search}<input type="search" placeholder="Name, symbol or mint" autocomplete="off" autocapitalize="off" spellcheck="false"></label><button data-x>Cancel</button></div>
      <div class="list" data-res><div class="empty">Search coins GROMO has seen</div></div></div>`;
    const close = () => { root.innerHTML = ''; };
    $('.sheet-bg', root).onclick = close; $('[data-x]', root).onclick = close;
    const inp = $('input', root); setTimeout(() => inp.focus(), 50);
    let t = null, seq = 0;
    inp.addEventListener('input', () => {
      clearTimeout(t);
      const q = inp.value.trim();
      t = setTimeout(async () => {
        const my = ++seq; const box = $('[data-res]', root);
        if (!q) { box.innerHTML = '<div class="empty">Search coins GROMO has seen</div>'; return; }
        box.innerHTML = actSkel(3);
        try {
          const d = await api('/api/search?q=' + encodeURIComponent(q));
          if (my !== seq) return;
          const rows = d.rows || [];
          box.innerHTML = rows.length ? rows.map((r) => `<a class="row" href="#/coin/${encodeURIComponent(r.mint)}">${coinAvatar(r)}<div class="rt"><b>${esc(r.name)}</b><span>${esc(r.symbol || '')} · ${esc(CHAIN[r.chain] || r.chain || '')}</span></div><div class="rv"><span class="faint" style="font-family:var(--mono);font-size:12px">${esc(String(r.mint).slice(0, 4))}...${esc(String(r.mint).slice(-4))}</span></div></a>`).join('')
            : `<div class="empty">No coin matches "${esc(q)}"</div>`;
          $$('a', box).forEach((a) => a.addEventListener('click', close));
        } catch (e) { box.innerHTML = `<div class="empty"><span class="down">${esc(e.message)}</span></div>`; }
      }, 250);
    });
  }

  /* ================= confirm sheet ================= */
  function sheet({ title, text, ok, okClass = 'red', cancel = 'Cancel', input }) {
    return new Promise((resolve) => {
      const root = $('#sheet-root');
      root.innerHTML = `<div class="sheet-bg"></div><div class="sheet"><div class="grabber"></div><h2>${esc(title)}</h2><p>${esc(text)}</p>
        ${input ? `<input class="bigfield" placeholder="${esc(input)}" autocomplete="off" autocapitalize="characters" spellcheck="false">` : ''}
        <button class="cta ${okClass}" data-ok ${input ? 'disabled style="opacity:.4"' : ''}>${esc(ok)}</button><button class="cta ghost" data-no>${esc(cancel)}</button></div>`;
      const done = (v) => { root.innerHTML = ''; resolve(v); };
      $('.sheet-bg', root).onclick = () => done(false);
      $('[data-no]', root).onclick = () => done(false);
      const okb = $('[data-ok]', root);
      if (input) {
        const f = $('.bigfield', root); setTimeout(() => f.focus(), 60);
        f.addEventListener('input', () => { const good = f.value.trim().toUpperCase() === input; okb.disabled = !good; okb.style.opacity = good ? 1 : 0.4; });
      }
      okb.onclick = () => done(true);
    });
  }

  /* ================= AGENTS ================= */
  PAGES.agents = function () {
    const page = { el: el(`<section><header class="nav"><div class="nav-center">Agents</div><div class="grow"></div></header>
      <h1 class="large-title">Agents</h1><div class="offline"></div>
      <div class="muted" style="padding:0 16px 16px;font-size:15px">Scanner → Auditor → Analyst → Executor → Risk</div>
      <div class="group" style="margin-bottom:14px"><a class="cell link" href="#/replay" style="min-height:60px"><span class="glyph" style="background:rgba(205,255,0,0.14);color:var(--lime)">${I.replay}</span><div class="cl">Replay<small data-rpl>What the refused coins did next</small></div><span class="chev">›</span></a></div>
      <div data-list>${ORDER.map(() => `<div class="bigcard">${skel('50%', 24)}${skel('100%', 160, 'margin:16px 0 12px')}</div>`).join('')}</div></section>`) };
    const root = page.el;
    const openMore = {};
    function paint() {
      const s = S.state; if (!s) return;
      const list = $('[data-list]', root);
      list.innerHTML = ORDER.map((n) => {
        const a = (s.agents || []).find((x) => x.name === n) || { name: n, status: 'down', card: {} };
        const riskLocked = n === 'risk' && (num(a.card && a.card.open) || 0) > 0;
        const dec = S.decisions[n];
        const rows = dec ? (dec.rows || []) : null;
        const shown = rows ? (openMore[n] ? rows : rows.slice(0, 5)) : null;
        return `<div class="bigcard" data-a="${n}">
          <div class="ahead">${glyph(n)}<div style="flex:1;min-width:0"><div class="aname">${AG[n].label}</div>${agentStatus(a)}</div>
            <label class="switch" aria-label="Run ${AG[n].label}"><input type="checkbox" ${a.status !== 'paused' ? 'checked' : ''} ${riskLocked || a.status === 'down' ? 'disabled' : ''}><i></i></label></div>
          <div class="stats"><span class="pill ${AG[n].color}">${intf(a.count)} today</span>${a.subtitle ? `<span class="pill grey">${esc(a.subtitle)}</span>` : ''}</div>
          ${riskLocked ? '<div class="muted" style="font-size:13px;margin:-2px 0 8px">Locked while positions are open</div>' : ''}
          <div data-dec>${rows == null ? Array.from({ length: 3 }, () => `<div class="dec">${skel('100%', 16)}</div>`).join('')
            : rows.length ? shown.map((r) => decRow(r)).join('') + (rows.length > 5 ? `<button class="more" data-more>${openMore[n] ? 'Show less' : `Show all ${rows.length}`}</button>` : '')
            : `<div class="empty" style="padding:18px 0">${n === 'scanner' ? 'Listening for graduations' : n === 'auditor' ? 'No coin taken apart yet' : 'Nothing yet tonight'}</div>`}</div></div>`;
      }).join('');
      $$('.bigcard', list).forEach((card) => {
        const n = card.dataset.a;
        const inp = $('.switch input', card);
        inp.addEventListener('change', async () => {
          const want = inp.checked;
          try { await api(`/api/agents/${n}/${want ? 'resume' : 'pause'}`, { method: 'POST' }); const a = S.state.agents.find((x) => x.name === n); if (a) a.status = want ? 'running' : 'paused'; toast(`${AG[n].label} ${want ? 'running' : 'paused'}`); paint(); refreshStateSoon(); }
          catch (e) { inp.checked = !want; toast(e.message); }
        });
        const m = $('[data-more]', card); if (m) m.onclick = () => { openMore[n] = !openMore[n]; paint(); };
      });
    }
    function decRow(r) {
      const v = String(r.verdict || '');
      const good = /pass|buy|bought|fill|clear|sold|ok/i.test(v);
      const bad = /kill|reject|fail|stop|skip/i.test(v);
      const href = r.mint ? (bad ? '#/audit/' : '#/coin/') + encodeURIComponent(r.mint) : '';
      return `<a class="dec" ${href ? `href="${href}"` : ''}><span class="dt">${esc(time(r.ts))}</span><span class="dn">${esc(r.name || '')}</span><span class="dr">${esc(r.reason || '')}</span><span class="pill ${good ? 'green' : bad ? 'red' : 'grey'}">${esc(v)}</span></a>`;
    }
    function paintRpl() {
      const d = S.replay[24]; const sm = d && d.summary; const t = $('[data-rpl]', root);
      if (t && sm && num(sm.refused) != null) t.textContent = `24h: ${intf(sm.refused)} refused, ${intf(sm.dodged)} would have lost money`;
    }
    page.load = async function load() {
      paint(); paintRpl();
      api('/api/replay?h=24').then((d) => { S.replay[24] = d; paintRpl(); }).catch(() => {});
      try {
        S.state = await api('/api/state'); net.ok(); paint();
        await Promise.all(ORDER.map(async (n) => { try { S.decisions[n] = await api(`/api/agents/${n}/decisions`); } catch (e) { S.decisions[n] = S.decisions[n] || { rows: [] }; } }));
        paint();
      } catch (e) { net.fail(load, e); }
    };
    page.onState = paint;
    return page;
  };

  /* ================= COPY ================= */
  // GROMO's opinion on a coin someone copied: the selling point of the tab, so it is always shown
  function deskChip(d, big) {
    const v = d && d.verdict;
    const cls = v === 'cleared' ? 'ok' : v === 'rejected' ? 'bad' : v === 'watching' ? 'wait' : 'unseen';
    const label = v === 'cleared' ? (d.label || 'Cleared') : v === 'rejected' ? (d.label || 'Rejected') : v === 'watching' ? (d.label || 'Watching') : 'GROMO has not seen it';
    const ic = !big ? '' : v === 'cleared' ? I.check : v === 'rejected' ? I.x : '';
    return `<span class="dchip ${cls}${big ? ' big' : ''}"><img class="dm" src="icons/favicon.svg" alt="">${ic ? `<i>${ic}</i>` : ''}<span>${esc(label)}</span></span>`;
  }
  const handleTxt = (h) => '@' + String(h || '?').replace(/^@/, '');
  function handleAv(h, big) {
    const n = String(h || '?').replace(/^@/, '');
    return `<i class="hav${big ? ' big' : ''}" style="background:${PALETTE[hashStr(n) % PALETTE.length]}">${esc(n.charAt(0).toUpperCase() || '?')}</i>`;
  }
  const copyBtn = (url) => (url ? `<a class="copybtn" href="${esc(url)}">Copy on FOMO</a>` : '');
  const agoText = (ts) => { if (!ts) return 'n/a'; const a = ago(ts); return a === 'now' ? 'just now' : a + ' ago'; };
  const coinHref = (address, desk) => {
    const v = desk && desk.verdict;
    if (!address || !v || v === 'unseen') return '';
    return (v === 'rejected' ? '#/audit/' : '#/coin/') + encodeURIComponent(address);
  };

  PAGES.copy = function () {
    const page = { el: el(`<section><header class="nav"><div class="nav-center">Copy</div><div class="grow"></div></header>
      <h1 class="large-title">Copy</h1><div class="offline"></div>
      <div data-c><div class="cstate">${skel('12px', 12, 'border-radius:6px')}<div class="cl">${skel('55%', 18)}${skel('40%', 13, 'margin-top:8px')}</div></div>
      <div class="section-h"><h2>Hot now</h2></div>${Array.from({ length: 2 }, () => `<div class="hot">${skel('60%', 20)}${skel('40%', 14, 'margin-top:12px')}${skel('100%', 30, 'margin-top:14px')}</div>`).join('')}</div></section>`) };
    const root = page.el, box = $('[data-c]', root);
    let all = false;
    function stateCard(d) {
      const n = (d.traders || []).length;
      if (d.state === 'no_token') return `<div class="cstate off"><span class="glyph" style="background:rgba(255,159,10,0.16);color:var(--orange)">${AG.executor.glyph}</span>
        <div class="cl"><b>Copy bot is not connected</b><small>Add <code>FOMO_TOKEN</code> of a separate empty FOMO account to the copy bot</small></div></div>`;
      if (d.state === 'no_traders') return `<div class="cstate off"><span class="glyph" style="background:rgba(205,255,0,0.14);color:var(--lime)">${AG.scanner.glyph}</span>
        <div class="cl"><b>No traders followed yet</b><small>Import the top traders in the copy bot: <code>/top</code></small></div></div>`;
      if (d.state === 'stale') return `<div class="cstate warn"><span class="sdot"></span><div class="cl"><b>Copy bot is quiet</b><small>Last poll ${agoText(d.lastPoll)}. Check that the copy bot is running</small></div></div>`;
      if (d.state === 'running') return `<div class="cstate"><span class="sdot live"></span><div class="cl"><b>Copying ${intf(n)} trader${n === 1 ? '' : 's'}</b><small>Last poll ${agoText(d.lastPoll)}</small></div>${d.verified ? '<span class="pill green">FOMO verified</span>' : '<span class="pill orange">Not verified</span>'}</div>`;
      return `<div class="cstate warn"><span class="sdot"></span><div class="cl"><b>Copy bot state unknown</b><small>Open the copy bot to check it</small></div></div>`;
    }
    function hotCard(h) {
      const hs = h.handles || [];
      const href = coinHref(h.address, h.desk);
      const coin = { mint: h.address, chain: h.chain, symbol: h.symbol, name: h.name, image: h.image };
      const who = hs.slice(0, 3).map(handleTxt).join(', ') + (hs.length > 3 ? ` +${hs.length - 3}` : '');
      return `<div class="hot">
        <${href ? `a href="${href}"` : 'div'} class="hot-top">${coinAvatar(coin)}<div class="rt"><b>${esc(h.name || h.symbol || 'n/a')}</b><span>${esc(h.symbol ? '$' + String(h.symbol).replace(/^\$/, '') : '')} · ${compact(h.usd)} bought · ${h.lastTs ? ago(h.lastTs) : 'n/a'}</span></div>
          <div class="hot-n"><b>${intf(h.buyers)}</b><span>buyer${num(h.buyers) === 1 ? '' : 's'}</span></div></${href ? 'a' : 'div'}>
        <div class="hot-who"><span class="stackav">${hs.slice(0, 4).map((x) => handleAv(x)).join('')}</span><span class="hw">${esc(who)}</span>${copyBtn(h.coinUrl)}</div>
        <div class="hot-foot"><span class="hot-k">GROMO</span>${deskChip(h.desk, true)}</div></div>`;
    }
    function tradeRow(t) {
      const buy = t.side === 'buy';
      const coin = { mint: t.address, chain: t.chain, symbol: t.symbol, name: t.name, image: t.image };
      const href = coinHref(t.address, t.desk);
      const sy = t.symbol ? '$' + String(t.symbol).replace(/^\$/, '') : (t.name || 'n/a');
      return `<div class="trow"><${href ? `a href="${href}"` : 'div'} class="tr1">${coinAvatar(coin)}
        <div class="rt"><b><span class="side ${buy ? 'buy' : 'sell'}">${buy ? 'BUY' : 'SELL'}</span>${esc(sy)}</b><span>${esc(handleTxt(t.handle))} · ${t.ts ? ago(t.ts) : 'n/a'}</span></div>
        <div class="rv"><b>${usd(t.usd)}</b><span>mcap ${compact(t.mcap)}</span></div></${href ? 'a' : 'div'}>
        <div class="tr2">${deskChip(t.desk)}${t.coinUrl ? `<a class="copybtn ghost" href="${esc(t.coinUrl)}">Copy on FOMO</a>` : ''}</div></div>`;
    }
    function traderCell(t) {
      const inner = `${handleAv(t.handle, true)}<div class="cl">${esc(handleTxt(t.handle))}<small>${esc([t.name, t.lastTs ? 'last trade ' + ago(t.lastTs) : 'no trades yet'].filter(Boolean).join(' · '))}</small></div>
        <span class="cv">${intf(t.trades24h)} in 24h</span>`;
      return t.profileUrl ? `<a class="cell link" href="${esc(t.profileUrl)}" target="_blank" rel="noopener">${inner}<span class="chev">›</span></a>` : `<div class="cell">${inner}</div>`;
    }
    function paint() {
      const d = S.copy; if (!d) return;
      const hot = d.hot || [], trades = d.trades || [], traders = d.traders || [];
      const live = d.state === 'running' || d.state === 'stale';
      const shown = all ? trades : trades.slice(0, 12);
      box.innerHTML = stateCard(d)
        + (live || hot.length ? `<div class="section-h"><h2>Hot now <span class="muted">· 2+ traders</span></h2></div>
          ${hot.length ? hot.map(hotCard).join('') : '<div class="empty" style="padding:18px var(--g)">No coin bought by 2 followed traders in 24h</div>'}` : '')
        + (live || trades.length ? `<div class="section-h"><h2>Trades</h2></div><div class="section-sub">What followed traders did, with GROMO's verdict</div>
          <div class="list">${trades.length ? shown.map(tradeRow).join('') + (trades.length > shown.length || all ? `<button class="more" data-more>${all ? 'Show less' : `Show all ${trades.length}`}</button>` : '') : '<div class="empty" style="padding:18px 0">No trades from followed traders yet</div>'}</div>` : '')
        + (traders.length ? `<div class="group-h">Traders · ${intf(traders.length)} followed</div><div class="group">${traders.map(traderCell).join('')}</div>` : '')
        + '<div class="group-f" style="text-align:center;padding:24px 16px 0">The copy bot buys from its own FOMO account.<br>GROMO only shows what it thinks of each coin.</div>';
      const m = $('[data-more]', box); if (m) m.onclick = () => { all = !all; paint(); };
    }
    async function refresh() { try { S.copy = await api('/api/copy'); net.ok(); const y = root.scrollTop; paint(); root.scrollTop = y; } catch (e) { /* keep last */ } }
    page.load = async function load() {
      paint();
      try { S.copy = await api('/api/copy'); net.ok(); paint(); } catch (e) { net.fail(load, e); }
    };
    const timer = setInterval(refresh, 30000);
    page.destroy = () => clearInterval(timer);
    return page;
  };

  /* ================= REPLAY ================= */
  const RANGES_H = [[24, '24H'], [72, '3D'], [168, '7D']];
  const OUTCOME = { dodged: ['green', 'Dodged'], missed: ['orange', 'Missed'], win: ['green', 'Win'], loss: ['red', 'Loss'] };
  const EXIT_K = { stop: 'stop', trail: 'trailing stop', timeout: 'timeout', horizon: 'held to the end' };
  const stageChip = (s) => (AG[s] ? `<span class="pill" style="background:${AG[s].hex}26;color:${AG[s].hex}">${AG[s].label}</span>` : '');
  PAGES.replay = function () {
    const shareArg = { h: S.replayH };
    const page = { el: el(`<section>${navBack('Replay')}<div class="rp-head"><h1>Replay</h1><p>What GROMO said no to, played forward</p></div>
      <div class="rp-chips">${RANGES_H.map(([h, l]) => `<button data-h="${h}">${l}</button>`).join('')}</div><div class="offline"></div><div data-c></div></section>`) };
    const root = page.el; bindBack(root);
    const box = $('[data-c]', root);
    navShare(root, 'replay', shareArg);
    function skelView() {
      box.innerHTML = `<div class="rp-hero">${skel('30%', 12)}${skel('40%', 70, 'margin-top:10px')}${skel('80%', 18, 'margin-top:14px')}${skel('60%', 18, 'margin-top:6px')}</div>
        <div class="tiles">${Array.from({ length: 6 }, () => `<div class="tile">${skel('70%', 12)}${skel('60%', 24, 'margin-top:6px')}</div>`).join('')}</div>
        <div class="section-h"><h2>By stage</h2></div><div class="funnel">${Array.from({ length: 4 }, () => `<div class="frow">${skel('100%', 14)}</div>`).join('')}</div>`;
    }
    function tile(label, value, cls, small) { return `<div class="tile"><span>${label}</span><b class="${cls || ''}">${value}</b>${small ? `<small>${small}</small>` : ''}</div>`; }
    const signCls = (v) => (num(v) == null ? 'faint' : v > 0 ? 'up' : v < 0 ? 'down' : '');
    function paint() {
      $$('.rp-chips button', root).forEach((b) => b.classList.toggle('on', +b.dataset.h === S.replayH));
      const d = S.replay[S.replayH];
      if (d === undefined) { skelView(); return; }
      if (!d) { box.innerHTML = '<div class="empty"><div class="dash"></div><b>Replay is not available</b>GROMO could not build it right now</div>'; return; }
      const s = d.summary || {};
      const label = (RANGES_H.find((r) => r[0] === S.replayH) || [0, (d.windowH || S.replayH) + 'H'])[1];
      const stages = d.byStage || [];
      const max = Math.max(1, ...stages.map((x) => num(x.n) || 0));
      const rows = d.rows || [];
      const funnel = stages.length ? stages.map((x) => {
        const a = AG[x.stage] || { label: x.stage, hex: '#8e8e93' };
        const n = num(x.n) || 0, dg = num(x.dodged) || 0;
        const ex = x.stage === 'executor';
        return `<div class="frow">${AG[x.stage] ? glyph(x.stage) : ''}<span class="fl">${esc(a.label)}<small class="${signCls(x.avgPct)}">avg ${pct(x.avgPct, 1)}</small></span>
          <div class="fb"><i style="width:${(n / max) * 100}%;background:${a.hex}40"><b style="width:${ex ? 100 : n ? (dg / n) * 100 : 0}%;background:${a.hex}"></b></i></div>
          <div class="fv"><b>${intf(x.n)}</b><span>${ex ? 'Buy buttons' : intf(x.dodged) + ' dodged'}</span></div></div>`;
      }).join('') : '<div class="empty" style="padding:18px 0">No decisions in this window</div>';
      const rowHtml = (r) => {
        const o = OUTCOME[r.outcome] || ['grey', r.outcome || 'n/a'];
        return `<a class="row rp-row" ${r.mint ? `href="#/coin/${encodeURIComponent(r.mint)}"` : ''}>${coinAvatar(r)}
          <div class="rt"><b><em>${esc(r.symbol ? '$' + String(r.symbol).replace(/^\$/, '') : r.name || 'n/a')}</em><span class="pill ${o[0]}">${esc(o[1])}</span></b><span>${stageChip(r.stage)}<i>${esc(r.reason || '')}</i></span></div>
          <div class="rv"><b class="${signCls(r.simPct)}">${pct(r.simPct, 1)}</b><span>${esc(EXIT_K[r.simExit] || r.simExit || 'n/a')} · end ${pct(r.endPct, 0)}</span></div></a>`;
      };
      box.innerHTML = `
        <div class="rp-hero"><img class="rp-m" src="icons/glyph.svg" alt="">
          <div class="rp-k">LAST ${esc(label)} · ${intf(d.evaluated)} REPLAYED</div>
          <div class="rp-big"><b>${intf(s.dodged)}</b><span>coins<br>dodged</span></div>
          <p class="rp-line">${replayHeadline(s)}</p></div>
        <div class="tiles">
          ${tile('Dodged', intf(s.dodged), 'up', 'would have lost')}
          ${tile('Missed', intf(s.missed), '', 'would have won')}
          ${tile('Dumped', intf(s.dumped), 'down', 'fell 50% or more')}
          ${tile('Runners', intf(s.runners), '', 'went 2x or more')}
          ${tile('Avg refused', pct(s.avgRefusedPct, 1), signCls(s.avgRefusedPct), 'if bought anyway')}
          ${tile('Signals avg', pct(s.signalAvgPct, 1), signCls(s.signalAvgPct), intf(s.signals) + ' Buy buttons')}
        </div>
        <div class="section-h"><h2>By stage</h2></div>
        <div class="funnel">${funnel}</div>
        <div class="section-h"><h2>Coins</h2></div><div class="section-sub">Biggest simulated moves first</div>
        <div class="list">${rows.length ? rows.map(rowHtml).join('') : '<div class="empty" style="padding:22px 0"><div class="dash"></div>Nothing replayed yet</div>'}</div>
        <div class="group-f rp-note">${intf(d.pending)} pending · ${intf(d.noData)} without price data. Each coin is replayed for ${num(d.horizonH) != null ? intf(d.horizonH) + 'h' : 'n/a'} from the decision with GROMO's stop, trailing stop, timeout and FOMO fees, as if bought at the decision price.</div>`;
    }
    async function load(h) {
      paint();
      try { const d = await api('/api/replay?h=' + h); S.replay[h] = d; net.ok(); if (S.replayH === h) paint(); }
      catch (e) { if (S.replay[h] === undefined) { net.fail(() => load(h), e); } }
    }
    $$('.rp-chips button', root).forEach((b) => b.addEventListener('click', () => { S.replayH = +b.dataset.h; shareArg.h = S.replayH; paint(); load(S.replayH); }));
    page.load = () => load(S.replayH);
    return page;
  };

  /* ================= ACTIVITY ================= */
  PAGES.activity = function () {
    const page = { el: el(`<section><header class="nav"><div class="nav-center">Activity</div><div class="grow"></div></header>
      <h1 class="large-title">Activity</h1><div class="offline"></div>
      <div class="list" data-list>${actSkel(9)}</div><div data-sent style="height:40px"></div></section>`) };
    const root = page.el;
    let loading = false;
    S.unreadSeen = true;
    if (S.state && S.state.unread) { S.state.unread = 0; api('/api/read', { method: 'POST' }).catch(() => {}); }
    function paint() {
      if (!S.activity) return;
      const rows = S.activity.rows || [];
      $('[data-list]', root).innerHTML = rows.length ? rows.map((r) => actRow(r)).join('') + (S.activity.next == null ? '<div class="faint" style="text-align:center;padding:18px 0;font-size:13px">That is everything</div>' : actSkel(2))
        : '<div class="empty"><div class="dash"></div><b>Nothing yet tonight</b>Trades and rejects land here</div>';
    }
    async function more() {
      if (loading || !S.activity || S.activity.next == null) return;
      loading = true;
      try { const d = await api('/api/activity?before=' + encodeURIComponent(S.activity.next)); S.activity.rows = S.activity.rows.concat(d.rows || []); S.activity.next = d.next; net.ok(); paint(); }
      catch (e) { net.fail(more, e); }
      loading = false;
    }
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) more(); }, { root, rootMargin: '400px' });
    io.observe($('[data-sent]', root));
    page.load = async function load() {
      paint();
      try { S.activity = await api('/api/activity'); net.ok(); paint(); } catch (e) { net.fail(load, e); }
    };
    page.onEvent = (t) => { if (t === 'activity' && S.activity) { const rows = S.activity.rows; $('[data-list]', root).innerHTML = rows.map((r, i) => actRow(r, i === 0)).join(''); } };
    page.destroy = () => io.disconnect();
    return page;
  };

  /* ================= COIN ================= */
  PAGES.coin = function (mint) {
    const page = { el: el(`<section>${navBack('')}<div data-c></div></section>`) };
    const root = page.el; bindBack(root);
    let candles = null, timer = null;
    const box = $('[data-c]', root);
    function paint() {
      const c = S.coin[mint];
      if (!c) {
        box.innerHTML = `<div class="coin-head">${skel('56px', 56, 'border-radius:28px')}<div style="flex:1">${skel('60%', 28)}${skel('40%', 16, 'margin-top:8px')}</div></div>
          <div class="price">${skel('55%', 44)}</div><div class="candles" style="padding:16px">${skel('100%', 260)}</div>`;
        return;
      }
      $('.nav-center', root).textContent = c.name || '';
      navShare(root, 'trade', { mint });
      const pos = c.position;
      const sh = c.shield;
      const shCls = !sh ? 'na' : fullShield(sh) ? 'ok' : 'bad';
      const age = ageText(c.ageH);
      const sep = num(c.sinceEntryPct);
      const meta = [esc(c.symbol), esc(CHAIN[c.chain] || c.chain), age].filter(Boolean).join(' · ');
      const timeLeft = pos && pos.timeoutAt ? Math.max(0, toMs(pos.timeoutAt) - Date.now()) : null;
      const tl = timeLeft == null ? 'n/a' : timeLeft <= 0 ? 'now' : `${Math.floor(timeLeft / 3600000)}h ${String(Math.floor((timeLeft % 3600000) / 60000)).padStart(2, '0')}m`;
      const stopPct = pos && num(pos.stopPrice) != null && num(pos.entryPrice) ? ((pos.stopPrice / pos.entryPrice - 1) * 100) : null;
      box.innerHTML = `
        <div class="coin-head">${coinAvatar(c, true)}<div style="min-width:0;flex:1"><h1>${esc(c.name)}</h1>
          <div class="meta"><span>${meta}</span><a class="shield ${shCls}" href="#/audit/${encodeURIComponent(mint)}">${I.shield}${esc(sh || 'n/a')}</a></div></div></div>
        <div class="price">${fmtPrice(num(c.price))}</div>
        <div class="price-sub">${sep != null ? `<span class="${sep >= 0 ? 'up' : 'down'}">${sep >= 0 ? '▲' : '▼'} ${Math.abs(sep).toFixed(1)}% since entry</span>` : '<span class="muted">No position</span>'}</div>
        <div class="candles"><canvas></canvas>${(c.candles || []).length ? '' : '<div class="chart-empty"><div class="dash"></div><span>No candles yet</span></div>'}</div>
        <div class="section-h" style="padding-top:18px"><h2>Stats</h2></div>
        <div class="stats-grid">
          <div><span>Market cap</span><b>${compact(c.marketCap)}</b></div><div><span>Liquidity</span><b>${compact(c.liquidity)}</b></div>
          <div><span>24h volume</span><b>${compact(c.volume24h)}</b></div><div><span>Holders</span><b>${intf(c.holders)}</b></div>
          ${c.safetyScore != null ? `<div><span>Safety Score</span><b>${intf(c.safetyScore)}</b></div><div><span>Mint</span><b style="font-family:var(--mono);font-size:13px">${esc(mint.slice(0, 4))}...${esc(mint.slice(-4))}</b></div>` : ''}
        </div>
        ${pos ? `
        <div class="section-h"><h2>Your position</h2></div><div class="section-sub">${usd(pos.sizeUsd)} in · Executor</div>
        <div class="card">
          <h3>Profit and loss</h3>
          <div class="cbig ${num(pos.pnlUsd) >= 0 ? 'up' : 'down'}">${usd(pos.pnlUsd, { sign: true })} <span style="font-size:20px">${pct(pos.pnlPct, 1)}</span></div>
          <div class="kv" style="margin-top:8px"><span>Entry</span><b>${fmtPrice(num(pos.entryPrice))}</b></div>
          <div class="kv"><span>Mode</span><b>${pos.mode === 'LIVE' ? '<span class="pill green">LIVE</span>' : pos.mode === 'PAPER' ? '<span class="pill orange">PAPER</span>' : 'n/a'}</b></div>
        </div>
        <div class="card">
          <h3>Exit plan <span class="faint">· Risk</span></h3>
          <div class="kv"><span>Stop</span><b>${fmtPrice(num(pos.stopPrice))}${stopPct != null ? ` <span class="pill red mono">${stopPct.toFixed(0)}%</span>` : ''}</b></div>
          <div class="kv"><span>Trail</span><b>${pos.trailActive && pos.trailPrice != null ? fmtPrice(num(pos.trailPrice)) + ' <span class="pill green mono">active</span>' : '<span class="muted">not armed yet</span>'}</b></div>
          <div class="kv"><span>Time left</span><b>${tl}</b></div>
        </div>` : ''}
        <div class="group" style="margin-top:22px"><a class="cell link" href="#/audit/${encodeURIComponent(mint)}">${glyph('auditor')}<div class="cl">Auditor report<small>${esc(sh ? sh + ' parts' : 'Not taken apart')}</small></div><span class="chev">›</span></a></div>`;
      const cv = $('canvas', box);
      if (candles) candles.destroy();
      candles = new window.Charts.CandleChart(cv);
      candles.setData(c.candles || [], c.exitLine, pos ? pos.entryPrice : null);
      paintCta(c);
    }
    function paintCta(c) {
      let bar = $('.cta-bar', root);
      if (!bar) { bar = el('<div class="cta-bar"></div>'); root.appendChild(bar); }
      if (!c.coinUrl) { bar.innerHTML = ''; return; }
      bar.innerHTML = `<a class="cta${c.position ? ' red' : ''}" href="${esc(c.coinUrl)}">${c.position ? 'Sell on FOMO' : 'Buy on FOMO'}</a>
        ${c.chain === 'robinhood' && c.grokhoodUrl ? `<a class="cta ghost" href="${esc(c.grokhoodUrl)}" target="_blank" rel="noopener">Open on GROKHOOD</a>` : ''}`;
    }
    page.load = async function load() {
      paint();
      try { S.coin[mint] = await api('/api/coin/' + encodeURIComponent(mint)); net.ok(); paint(); }
      catch (e) { if (e.status === 404) { box.innerHTML = '<div class="empty"><div class="dash"></div><b>Coin not found</b>GROMO has no record of this mint</div>'; return; } net.fail(load, e); }
    };
    let pending = false;
    page.onEvent = (t, d) => {
      if ((t === 'price' || t === 'exit' || t === 'fill') && d && d.mint === mint && !pending) {
        pending = true; setTimeout(async () => { pending = false; try { S.coin[mint] = await api('/api/coin/' + encodeURIComponent(mint)); const y = root.scrollTop; paint(); root.scrollTop = y; } catch (e) { /* keep last */ } }, SHOW ? 60 : 1500);
      }
    };
    page.destroy = () => { if (candles) candles.destroy(); clearInterval(timer); };
    return page;
  };

  /* ================= AUDIT ================= */
  PAGES.audit = function (mint) {
    const page = { el: el(`<section>${navBack('Auditor')}<div data-c></div></section>`) };
    const root = page.el; bindBack(root);
    const box = $('[data-c]', root);
    let built = false, cursor = 1, busy = false, done = false, timers = [];
    const later = (fn, ms) => timers.push(setTimeout(fn, ms));
    const L = {}; // n -> layer
    let meta = null;

    const DX = 150, R = 104, RY = 17, TH = 9, Y0 = 26;
    const gap = () => Math.min(37, 259 / Math.max(1, NL - 1)); // 8 or 9 discs fit the same stack
    function disc(n) {
      const cy = Y0 + (n - 1) * gap();
      return `<g data-disc="${n}">
        <path d="M${DX - R} ${cy} v${TH} a${R} ${RY} 0 0 0 ${2 * R} 0 v-${TH} z" fill="#232325" data-side/>
        <ellipse cx="${DX}" cy="${cy}" rx="${R}" ry="${RY}" fill="url(#face)" stroke="#3a3a3c" stroke-width="1" data-face/>
        <ellipse cx="${DX}" cy="${cy}" rx="${R * 0.62}" ry="${RY * 0.62}" fill="none" stroke="rgba(235,235,245,0.08)" stroke-width="1"/>
        <text x="${DX}" y="${cy + 6}" text-anchor="middle" font-size="12" font-weight="700" fill="rgba(235,235,245,0.3)" data-num>${n}</text>
        <g class="disc-mark" opacity="0" data-mark transform="translate(${DX + R + 22} ${cy + 4})"><circle r="11" data-mc/><path data-mp fill="none" stroke="#000" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></g>
      </g>`;
    }
    function build() {
      const name = meta && meta.name ? meta.name : '';
      $('.nav-center', root).textContent = name ? `Auditor · ${name}` : 'Auditor';
      navShare(root, 'audit', { mint });
      box.innerHTML = `
        <div class="verdict" data-v></div>
        <div class="stackbox"><svg viewBox="0 0 300 310" aria-hidden="true">
          <defs><linearGradient id="face" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a3a3c"/><stop offset="1" stop-color="#2a2a2c"/></linearGradient></defs>
          ${Array.from({ length: NL }, (_, i) => NL - i).map(disc).join('')}
          <g data-beam opacity="0">
            <rect x="18" y="${Y0 - 21}" width="264" height="42" rx="10" fill="rgba(100,210,255,0.12)"/>
            <rect x="18" y="${Y0 + 4}" width="264" height="2" rx="1" fill="#64d2ff"/>
          </g></svg></div>
        <div class="list layer-rows" data-rows>${Array.from({ length: NL }, (_, i) => layerRow(i + 1)).join('')}</div>
        <div class="group-h">Auditor</div>
        <div class="group"><div class="cell"><div class="cl">Take apart before buying<small>${NL} parts per coin, every coin</small></div><label class="switch"><input type="checkbox" data-en><i></i></label></div></div>
        <div class="group-f">Off means GROMO buys without the 9 checks. Not advised.</div>
        <div class="credit"><i></i>Analysis powered by <b>GROKHOOD</b></div>`;
      built = true;
      bindToggle();
      paintCta();
    }
    function layerRow(n) {
      const l = L[n];
      const st = !l ? 'pending' : l.pass === true ? 'ok' : l.pass === false ? 'bad' : 'na';
      const shown = n < cursor || done;
      const icon = !shown ? '' : st === 'ok' ? `<span class="up">${I.check}</span>` : st === 'bad' ? `<span class="down">${I.x}</span>` : '';
      return `<div class="row ${shown ? '' : 'pending'}" data-row="${n}"><span class="ln ${shown ? (st === 'ok' ? 'ok' : st === 'bad' ? 'bad' : '') : n === cursor && busy ? 'reading' : ''}">${n}</span>
        <div class="rt"><b>${esc((l && l.name) || defaultName(n))}</b><span>${shown ? (l && l.pass === null ? 'skipped' : esc(l && l.source ? 'source: ' + l.source : '')) : n === cursor ? 'reading...' : 'waiting'}</span></div>
        <div class="rv" style="display:flex;align-items:center;gap:8px"><b class="${shown && st === 'bad' ? 'down' : shown && st === 'na' ? 'faint' : ''}">${shown && l ? esc(l.value == null || l.value === '' ? 'n/a' : l.value) : '...'}</b><span style="width:16px;height:16px;margin:0">${icon}</span></div></div>`;
    }
    function defaultName(n) {
      const chain = meta && meta.chain;
      const sol = ['Mint authority', 'Freeze authority', 'Dev wallet', 'Top 10 holders', 'LP locked', 'Pool age', 'Bundled wallets', 'Delegations', 'Dev history'];
      const rh = ['Safety Score', 'Rings', 'Known operator', 'Dev wallet', 'Top 10 holders', 'Exempt and snipe wallets', 'Bundled clusters', 'Dump alerts', 'X activity'];
      return (chain === 'robinhood' ? rh : sol)[n - 1];
    }
    function paintVerdict() {
      const v = $('[data-v]', box); if (!v) return;
      const name = esc((meta && (meta.name || meta.symbol)) || 'this coin');
      if (!done) { v.innerHTML = `<h1 style="color:var(--cyan)">Taking apart</h1><p>${name} · part ${Math.min(cursor, NL)} of ${NL}</p>`; return; }
      const failed = Object.values(L).filter((l) => l.pass === false);
      const checked = Object.values(L).filter((l) => l.pass === true).length;
      if (meta && meta.passed === false || failed.length) {
        const reason = (meta && meta.reason) || (failed[0] ? `${failed[0].name.toLowerCase()} ${failed[0].value}` : 'failed');
        v.innerHTML = `<h1 class="bad">Rejected · ${esc(reason)}</h1><p>GROMO won't buy ${name}</p>`;
      } else {
        v.innerHTML = `<h1 class="ok">Cleared · ${checked}/${NL} parts</h1><p>${name} goes to the Analyst</p>`;
      }
    }
    const beam = { y: 0, op: 0, raf: 0 };
    function setBeam(n, show) {
      const b = $('[data-beam]', box); if (!b) return;
      const y1 = (n - 1) * gap(), o1 = show ? 1 : 0;
      const y0 = beam.y, o0 = beam.op, t0 = performance.now();
      cancelAnimationFrame(beam.raf);
      const tick = () => {
        const k = Math.min(1, (performance.now() - t0) / 350), e = 1 - Math.pow(1 - k, 3);
        beam.y = y0 + (y1 - y0) * e; beam.op = o0 + (o1 - o0) * Math.min(1, k * 1.75);
        b.setAttribute('transform', `translate(0 ${beam.y.toFixed(2)})`); b.setAttribute('opacity', beam.op.toFixed(3));
        if (k < 1) beam.raf = requestAnimationFrame(tick);
      };
      tick();
    }
    function reveal(n) {
      const g = $(`[data-disc="${n}"]`, box); if (!g) return;
      const l = L[n];
      const col = l.pass === true ? '#00d35a' : l.pass === false ? '#ff453a' : '#636366';
      $('[data-side]', g).setAttribute('fill', l.pass === true ? '#0f3d24' : l.pass === false ? '#4a1916' : '#2c2c2e');
      $('[data-face]', g).setAttribute('stroke', col);
      $('[data-num]', g).setAttribute('fill', col);
      const m = $('[data-mark]', g); m.setAttribute('opacity', 1);
      $('[data-mc]', g).setAttribute('fill', col);
      $('[data-mp]', g).setAttribute('d', l.pass === true ? 'M-4.5 0.2 -1.3 3.4 4.8-3.6' : l.pass === false ? 'M-3.8-3.8 3.8 3.8M3.8-3.8-3.8 3.8' : 'M-4 0H4');
    }
    function paintRow(n) { const r = $(`[data-row="${n}"]`, box); if (r) r.outerHTML = layerRow(n); }
    function step() {
      if (done || busy) return;
      if (cursor > NL) { finish(); return; }
      if (!L[cursor]) { setBeam(cursor, true); paintRow(cursor); return; } // beam waits for data
      busy = true; setBeam(cursor, true); paintRow(cursor); paintVerdict();
      later(() => {
        later(() => {
          reveal(cursor); const n = cursor; cursor++; busy = false; paintRow(n); paintRow(cursor); paintVerdict(); step();
        }, 150);
      }, 350);
    }
    function finish() { done = true; setBeam(NL, false); for (let n = 1; n <= NL; n++) { if (L[n]) reveal(n); paintRow(n); } paintVerdict(); }
    function finalized() { return meta && typeof meta.passed === 'boolean'; }
    function ingest(a) {
      meta = Object.assign(meta || {}, a);
      for (const l of a.layers || []) L[+l.n] = Object.assign({}, L[+l.n], l);
      if (S.layerLive[mint]) for (const [n, l] of Object.entries(S.layerLive[mint])) L[+n] = Object.assign({}, L[+n], l);
      if (finalized()) for (let n = 1; n <= NL; n++) if (!L[n]) L[n] = { n, name: defaultName(n), pass: null, value: 'n/a' };
    }
    function paintCta() {
      let bar = $('.cta-bar', root);
      if (!bar) { bar = el('<div class="cta-bar"></div>'); root.appendChild(bar); }
      const c = S.coin[mint];
      const url = (meta && meta.coinUrl) || (c && c.coinUrl);
      const gh = c && c.grokhoodUrl;
      bar.innerHTML = (url ? `<a class="cta" href="${esc(url)}">Open on FOMO</a>` : '')
        + ((meta && meta.chain === 'robinhood' && gh) ? `<a class="cta ghost" href="${esc(gh)}" target="_blank" rel="noopener">Open on GROKHOOD</a>` : '');
      if (!bar.innerHTML) bar.remove();
    }
    async function bindToggle() {
      const inp = $('[data-en]', box);
      const sync = () => { const s = S.settings && S.settings['auditor.enabled']; inp.checked = s ? !!s.value : true; inp.disabled = !s; };
      sync();
      if (!S.settings) { try { S.settings = await api('/api/settings'); sync(); } catch (e) { /* keep disabled */ } }
      inp.addEventListener('change', async () => {
        if (!inp.checked) {
          inp.checked = true;
          const a = await sheet({ title: 'Turn off the Auditor?', text: 'GROMO will stop taking coins apart before buying.', ok: 'Turn off' });
          if (!a) return;
          const b = await sheet({ title: 'Are you sure?', text: 'Without the 9 checks, rugs with a dev holding 30% get through.', ok: 'Yes, turn it off' });
          if (!b) return;
          inp.checked = false;
        }
        try { await api('/api/settings', { method: 'POST', body: { key: 'auditor.enabled', value: inp.checked } }); S.settings['auditor.enabled'].value = inp.checked; toast(inp.checked ? 'Auditor on' : 'Auditor off'); }
        catch (e) { inp.checked = !inp.checked; toast(e.message); }
      });
    }
    page.load = async function load() {
      if (!built) box.innerHTML = `<div class="verdict">${skel('70%', 30)}${skel('45%', 16, 'margin-top:10px')}</div><div class="stackbox" style="align-items:center">${skel('240px', 280, 'border-radius:120px/40px')}</div>`;
      try {
        const a = await api('/api/audit/' + encodeURIComponent(mint));
        net.ok();
        if (!a || !a.layers || (!a.layers.length && !S.layerLive[mint])) {
          box.innerHTML = '<div class="empty" style="padding-top:80px"><div class="dash"></div><b>No coin taken apart yet</b>The Auditor has not opened this one</div>';
          paintCtaFromCoin(); return;
        }
        if (a.layers.length) NL = Math.max(...a.layers.map((l) => l.n || 0), a.layers.length);
        ingest(a); build();
        if (reduced()) { cursor = 9; finish(); } else { paintVerdict(); later(step, 250); }
        api('/api/coin/' + encodeURIComponent(mint)).then((c) => { S.coin[mint] = c; paintCta(); }).catch(() => {});
      } catch (e) { if (e.status === 404) { box.innerHTML = '<div class="empty" style="padding-top:80px"><div class="dash"></div><b>No coin taken apart yet</b></div>'; return; } net.fail(load, e); }
    };
    function paintCtaFromCoin() { api('/api/coin/' + encodeURIComponent(mint)).then((c) => { S.coin[mint] = c; meta = meta || { chain: c.chain, coinUrl: c.coinUrl }; paintCta(); }).catch(() => {}); }
    page.onEvent = (t, d) => {
      if (!d || d.mint !== mint) return;
      if (t === 'audit_layer') {
        if (!built) { page.load(); return; }
        L[+d.n] = Object.assign({}, L[+d.n], d);
        if (reduced()) { reveal(+d.n); paintRow(+d.n); } else step();
      }
      if (t === 'audit') { ingest(d); if (reduced()) finish(); else step(); }
    };
    page.destroy = () => timers.forEach(clearTimeout);
    return page;
  };

  /* ================= ACCOUNT ================= */
  PAGES.account = function () {
    const page = { el: el(`<section><header class="nav"><div class="nav-center">Account</div><div class="grow"></div></header>
      <h1 class="large-title">Account</h1><div class="offline"></div><div data-c>
      <div class="group">${Array.from({ length: 3 }, () => `<div class="cell">${skel('60%', 18)}</div>`).join('')}</div>
      <div class="group" style="margin-top:28px">${Array.from({ length: 4 }, () => `<div class="cell">${skel('70%', 18)}</div>`).join('')}</div></div></section>`) };
    const root = page.el;
    const box = $('[data-c]', root);
    const GROUPS = [['scanner', 'Scanner'], ['auditor', 'Auditor'], ['analyst', 'Analyst'], ['executor', 'Executor'], ['risk', 'Risk'], ['desk', 'GROMO']];
    const SKIP = new Set(['desk.mode', 'analyst.mode', 'auditor.enabled', 'desk.live_stack', 'desk.paper_start_stack']);
    function paint() {
      const a = S.account; if (!a) return;
      const st = S.settings || {};
      const stackKey = a.mode === 'LIVE' ? 'desk.live_stack' : 'desk.paper_start_stack';
      const pushRow = standalone()
        ? (a.webpush ? '<div class="cell"><div class="cl">Notifications</div><span class="cv up">On</span></div>' : '<button class="cell btn green" data-push>Enable notifications</button>')
        : '';
      box.innerHTML = `
        <div class="group"><div class="cell" style="min-height:76px">
          <span class="avatar" style="width:52px;height:52px"><img src="icons/favicon.svg" alt=""></span>
          <div class="cl"><b style="font-size:20px">${esc(a.handle ? '@' + String(a.handle).replace(/^@/, '') : 'GROMO')}</b><small>${a.mode === 'LIVE' ? 'LIVE stack' : 'PAPER stack'} · ${usd(a.stack)}</small></div>${modePill()}</div></div>

        <div class="group-h">Morning post</div>
        <div data-post></div>

        <div class="group-h">Mode</div>
        <div class="group">
          <div class="cell"><div class="cl">Trading mode</div><div class="seg" data-mode><button data-m="PAPER" class="${a.mode === 'PAPER' ? 'on' : ''}">PAPER</button><button data-m="LIVE" class="${a.mode === 'LIVE' ? 'on' : ''}">LIVE</button></div></div>
          <div class="cell"><div class="cl">Stack</div><span class="muted">$</span><input class="textf" style="flex:0 0 120px" inputmode="decimal" data-stack value="${esc(st[stackKey] ? st[stackKey].value : (a.stack != null ? a.stack : ''))}"></div>
        </div>
        <div class="group-f" data-stackerr>${a.mode === 'LIVE' ? 'LIVE sizes tickets from this stack. You still tap Buy on FOMO yourself.' : 'PAPER trades a simulated stack. No real money moves.'}</div>

        <div class="group-h">FOMO</div>
        <div class="group">
          ${a.profileUrl ? `<a class="cell link" href="${esc(a.profileUrl)}" target="_blank" rel="noopener"><div class="cl">Your FOMO profile<small>${esc(a.handle ? '@' + String(a.handle).replace(/^@/, '') : '')}</small></div><span class="chev">›</span></a>`
            : '<div class="cell"><div class="cl">Your FOMO profile<small>Set your handle in GROMO settings below</small></div></div>'}
          <button class="cell link" data-invite style="text-align:left"><div class="cl">Invite a friend<small>Your FOMO referral link</small></div><span class="chev">›</span></button>
        </div>

        <div class="group-h">Judge</div>
        <div class="group">
          <div class="cell"><div class="cl">Analyst judge</div><div class="seg" data-judge><button data-j="grok" class="${a.judge === 'grok' ? 'on' : ''}">Grok</button><button data-j="rules" class="${a.judge === 'rules' ? 'on' : ''}">Rules</button></div></div>
          ${a.grokToday ? `<div class="cell"><div class="cl">Grok today</div><span class="cv">${intf(a.grokToday.calls)} calls · ${usd(a.grokToday.costUsd)}</span></div>` : ''}
        </div>
        <div class="group-f" data-judgeerr></div>

        <div class="group-h">Alerts</div>
        <div class="group">
          <div class="cell"><div class="cl">Telegram</div><span class="cv ${a.telegram ? 'up' : ''}">${a.telegram ? 'Connected' : 'Not connected'}</span></div>
          ${pushRow}
          <button class="cell btn" data-test>Send test push</button>
        </div>
        ${standalone() ? '' : '<div class="group-f">Add GROMO to your Home Screen to turn on push notifications.</div>'}

        ${GROUPS.map(([g, label]) => {
          const keys = Object.keys(st).filter((k) => k.startsWith(g + '.') && !SKIP.has(k));
          if (!keys.length) return '';
          return `<div class="group-h">${glyphMini(g)}${label}</div><div class="group">${keys.map((k) => settingCell(k, st[k])).join('')}</div>`;
        }).join('')}
        ${S.settings ? '' : `<div class="group-h">Settings</div><div class="group">${Array.from({ length: 4 }, () => `<div class="cell">${skel('80%', 18)}</div>`).join('')}</div>`}
        <div class="about"><img src="icons/glyph.svg" alt=""><div class="about-n">GROMO</div>GROMO never holds keys and never places orders.<br>You tap Buy and Sell on FOMO.</div>
        <div class="credit"><i></i>Analysis powered by <b>GROKHOOD</b></div>`;
      bind();
      paintPost();
    }
    let showPreview = false, previewBusy = false;
    function postDate(d) {
      const m = String(d || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (!m) return d ? String(d) : 'n/a';
      return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    }
    function paintPost() {
      const box2 = $('[data-post]', box); if (!box2) return;
      const p = showPreview && S.nightPreview ? S.nightPreview : S.nightPost;
      if (p === null || p === undefined) {
        box2.innerHTML = !S.nightPostFailed ? `<div class="npost"><div class="np-img"></div><div style="padding:14px 16px">${skel('40%', 13)}${skel('100%', 90, 'margin-top:10px')}</div></div>` : '<div class="group"><div class="cell"><div class="cl muted">Could not load the last post</div></div></div>';
        return;
      }
      const prevBtn = `<button class="cta ghost" data-preview ${previewBusy ? 'disabled style="opacity:.5"' : ''}>${previewBusy ? 'Building...' : showPreview ? 'Last post' : 'Preview tonight'}</button>`;
      if (!p.date) {
        box2.innerHTML = `<div class="npost"><div class="empty" style="padding:30px 16px 8px"><div class="dash"></div><b>No morning post yet</b>First post arrives after the 08:00 report</div><div class="np-btns">${prevBtn}</div></div>`;
      } else {
        box2.innerHTML = `<div class="npost">
          ${p.cardUrl ? `<a class="np-img" href="${esc(p.cardUrl)}" target="_blank" rel="noopener"><img src="${esc(p.cardUrl)}" alt="Morning post card"></a>` : ''}
          <div class="np-meta">${showPreview ? '<span class="pill orange">Preview</span><span>Tonight so far · not sent</span>' : `<span>${esc(postDate(p.date))} · 08:00 report</span>`}</div>
          <div class="np-text">${p.text ? esc(p.text) : '<span class="muted">n/a</span>'}</div>
          <div class="np-btns">${p.text ? '<button class="cta lime" data-copytext>Copy text</button>' : ''}${prevBtn}</div></div>`;
      }
      const ct = $('[data-copytext]', box2);
      if (ct) ct.onclick = async () => { try { await navigator.clipboard.writeText(p.text); toast('Text copied'); } catch (e) { toast('Could not copy'); } };
      $('[data-preview]', box2).onclick = async () => {
        if (showPreview) { showPreview = false; paintPost(); return; }
        previewBusy = true; paintPost();
        try { S.nightPreview = await api('/api/night-post/preview', { method: 'POST' }); showPreview = true; }
        catch (e) { toast(e.message); }
        previewBusy = false; paintPost();
      };
    }
    function glyphMini(g) { return AG[g] ? `<span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:${AG[g].hex};margin-right:6px"></span>` : ''; }
    function scale(s) {
      const lo = +s.min, hi = +s.max;
      const wide = hi - lo > 2000 || (lo > 0 && hi / lo > 200);
      return {
        toT: (v) => (wide ? Math.cbrt(clamp((v - lo) / (hi - lo), 0, 1)) : clamp((v - lo) / (hi - lo), 0, 1)) * 1000,
        toV: (t) => { const k = t / 1000; const v = lo + (hi - lo) * (wide ? k * k * k : k); return niceRound(v, hi - lo, Number.isInteger(s.default)); },
      };
    }
    function niceRound(v, span, int) {
      if (int || span > 50) {
        const mag = Math.pow(10, Math.max(0, Math.floor(Math.log10(Math.max(1, Math.abs(v)))) - 1));
        return Math.round(v / mag) * mag;
      }
      return span <= 1 ? Math.round(v * 100) / 100 : Math.round(v * 10) / 10;
    }
    function settingCell(k, s) {
      const id = 'set-' + k.replace(/\./g, '-');
      if (s.choices && s.choices.length === 2 && s.choices.every((c) => typeof c === 'boolean')) {
        return `<div class="cell" data-k="${k}"><div class="cl">${esc(s.label || k)}<small class="err" hidden></small></div><label class="switch"><input type="checkbox" data-bool ${s.value ? 'checked' : ''}><i></i></label></div>`;
      }
      if (s.choices) {
        return `<div class="cell" data-k="${k}"><div class="cl">${esc(s.label || k)}<small class="err" hidden></small></div><div class="seg">${s.choices.map((c) => `<button data-choice="${esc(c)}" class="${c === s.value ? 'on' : ''}">${esc(c)}</button>`).join('')}</div></div>`;
      }
      if (s.min != null && s.max != null && typeof s.value === 'number') {
        const sc = scale(s); const t = sc.toT(s.value);
        return `<div class="cell stack" data-k="${k}"><div style="display:flex;justify-content:space-between"><span>${esc(s.label || k)}</span><span class="faint" style="font-size:13px">${fmtN(s.min)} to ${fmtN(s.max)}</span></div>
          <div class="setrow"><input type="range" id="${id}" min="0" max="1000" step="1" value="${t}" style="--p:${t / 10}%" aria-label="${esc(s.label)}"><input class="numf" inputmode="decimal" value="${esc(s.value)}" data-num></div><small class="err" hidden></small></div>`;
      }
      return `<div class="cell" data-k="${k}"><div class="cl">${esc(s.label || k)}<small class="err" hidden></small></div><input class="textf" data-text value="${esc(s.value == null ? '' : s.value)}" placeholder="not set" autocapitalize="off" spellcheck="false"></div>`;
    }
    const fmtN = (v) => (Math.abs(v) >= 1000 ? (+v).toLocaleString('en-US') : String(v));
    async function save(k, value, cell) {
      const err = cell && $('.err', cell);
      try {
        const r = await api('/api/settings', { method: 'POST', body: { key: k, value } });
        if (S.settings && S.settings[k]) S.settings[k].value = r && r.new !== undefined ? r.new : value;
        if (err) { err.hidden = true; err.textContent = ''; }
        toast('Saved');
        return true;
      } catch (e) {
        if (err) { err.hidden = false; err.textContent = e.message; } else toast(e.message);
        return false;
      }
    }
    function bind() {
      $$('[data-mode] button', box).forEach((b) => b.onclick = async () => {
        const m = b.dataset.m; if (m === S.account.mode) return;
        if (m === 'LIVE') {
          const ok = await sheet({ title: 'Switch to LIVE', text: 'Tickets are sized from your real stack. GROMO still never trades for you. Type LIVE to confirm.', ok: 'Switch to LIVE', okClass: '', input: 'LIVE' });
          if (!ok) return;
        }
        try { await api('/api/mode', { method: 'POST', body: m === 'LIVE' ? { mode: 'LIVE', confirm: 'LIVE' } : { mode: 'PAPER' } }); S.account.mode = m; if (S.state) S.state.mode = m; toast(`${m} mode`); paint(); }
        catch (e) { toast(e.message); }
      });
      $$('[data-judge] button', box).forEach((b) => b.onclick = async () => {
        const j = b.dataset.j; if (j === S.account.judge) return;
        try { await api('/api/settings', { method: 'POST', body: { key: 'analyst.mode', value: j } }); S.account.judge = j; paint(); toast(j === 'grok' ? 'Judge: Grok' : 'Judge: rules'); }
        catch (e) { $('[data-judgeerr]', box).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
      });
      const stackIn = $('[data-stack]', box);
      stackIn.addEventListener('change', async () => {
        const key = S.account.mode === 'LIVE' ? 'desk.live_stack' : 'desk.paper_start_stack';
        try { await api('/api/settings', { method: 'POST', body: { key, value: stackIn.value } }); toast('Stack saved'); $('[data-stackerr]', box).textContent = 'Saved.'; }
        catch (e) { $('[data-stackerr]', box).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
      });
      $('[data-invite]', box).onclick = async () => {
        const url = S.account.signupUrl; if (!url) return;
        if (navigator.share) { try { await navigator.share({ title: 'FOMO', url }); return; } catch (e) { if (e && e.name === 'AbortError') return; } }
        try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch (e) { toast(url); }
      };
      $('[data-test]', box).onclick = async () => { try { await api('/api/test-push', { method: 'POST' }); toast('Test push sent'); } catch (e) { toast(e.message); } };
      const pb = $('[data-push]', box); if (pb) pb.onclick = enablePush;
      $$('[data-k]', box).forEach((cell) => {
        const k = cell.dataset.k; const s = S.settings[k];
        const bool = $('[data-bool]', cell);
        if (bool) bool.addEventListener('change', async () => { if (!(await save(k, bool.checked, cell))) bool.checked = !bool.checked; });
        $$('[data-choice]', cell).forEach((b) => b.onclick = async () => { if (await save(k, b.dataset.choice, cell)) { $$('[data-choice]', cell).forEach((x) => x.classList.toggle('on', x === b)); } });
        const txt = $('[data-text]', cell); if (txt) txt.addEventListener('change', () => save(k, txt.value.trim(), cell));
        const rng = $('input[type=range]', cell), nf = $('[data-num]', cell);
        if (rng && nf) {
          const sc = scale(s); let t = null;
          rng.addEventListener('input', () => { const v = sc.toV(+rng.value); nf.value = v; rng.style.setProperty('--p', rng.value / 10 + '%'); clearTimeout(t); t = setTimeout(async () => { if (!(await save(k, v, cell))) { nf.value = s.value; rng.value = sc.toT(s.value); rng.style.setProperty('--p', rng.value / 10 + '%'); } }, 450); });
          nf.addEventListener('change', async () => { if (await save(k, nf.value, cell)) { const v = S.settings[k].value; rng.value = sc.toT(+v); rng.style.setProperty('--p', rng.value / 10 + '%'); nf.value = v; } });
        }
      });
    }
    async function enablePush() {
      const a = S.account;
      if (!a.vapidPublicKey) { toast('Push is not set up on this GROMO server'); return; }
      try {
        const reg = await navigator.serviceWorker.register('sw.js');
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') { toast('Notifications blocked'); return; }
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(a.vapidPublicKey) });
        await api('/api/push', { method: 'POST', body: sub.toJSON() });
        a.webpush = true; paint(); toast('Notifications on');
      } catch (e) { toast(e.message || 'Could not enable'); }
    }
    function b64(s) { const p = '='.repeat((4 - (s.length % 4)) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(r, (c) => c.charCodeAt(0)); }
    page.load = async function load() {
      paint();
      try {
        api('/api/night-post').then((p) => { S.nightPost = p; S.nightPostFailed = false; paintPost(); }).catch(() => { S.nightPostFailed = true; paintPost(); });
        const [a, s] = await Promise.all([api('/api/account'), api('/api/settings')]);
        S.account = a; S.settings = s; net.ok(); paint();
        if (!S.state) api('/api/state').then((st) => { S.state = st; paint(); }).catch(() => {});
      } catch (e) { net.fail(load, e); }
    };
    return page;
  };

  /* =====================================================================
     DEMO fixture generator (?demo=1). Design work only. Every screen shows
     a grey DEMO pill. Real mode never touches anything below.
     ===================================================================== */
  var Demo = DEMO ? (function () {
    let seed = 20261102;
    const rnd = () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
    const mintOf = (chain) => chain === 'robinhood' ? '0x' + Array.from({ length: 40 }, () => '0123456789abcdef'[Math.floor(rnd() * 16)]).join('') : Array.from({ length: 43 }, () => B58[Math.floor(rnd() * 58)]).join('') + 'pump'.slice(0, 1);
    const coinDefs = [
      ['Laser Frog', 'LFROG', 'solana'], ['Moon Otter', 'OTTER', 'solana'], ['Sir Pump', 'SIRP', 'solana'], ['Byte Bear', 'BYTE', 'solana'],
      ['Hoodie Cat', 'HCAT', 'solana'], ['Duck Norris', 'DUCK', 'solana'], ['Turbo Goose', 'GOOSE', 'solana'], ['Glitch Ape', 'GLAPE', 'solana'],
      ['Ronin Rat', 'RONIN', 'robinhood'], ['Mochi Dog', 'MOCHI', 'robinhood'], ['Pixel Pug', 'PUG', 'solana'], ['Neon Newt', 'NEWT', 'solana'],
      ['Comet Corgi', 'CORGI', 'solana'], ['Velvet Yak', 'YAK', 'robinhood'], ['Quantum Quokka', 'QUOK', 'solana'], ['Gravy Train', 'GRAVY', 'solana'],
    ];
    const coins = {};
    const bySym = {};
    coinDefs.forEach(([name, symbol, chain]) => {
      const c = { name, symbol, chain, mint: mintOf(chain), image: null, price: Math.pow(10, -3 - rnd() * 3) * (1 + rnd() * 8), ageH: 2 + rnd() * 30,
        marketCap: 2e5 + rnd() * 4e6, liquidity: 4e4 + rnd() * 3e5, volume24h: 1e5 + rnd() * 5e6, holders: Math.floor(400 + rnd() * 6000) };
      if (chain === 'robinhood') c.safetyScore = Math.floor(60 + rnd() * 35);
      coins[c.mint] = c; bySym[symbol] = c;
    });
    bySym.GOOSE.holders = null;
    const NOW = Date.now();
    const T0 = NOW - 330 * 60000;
    const START_ALL = STORY ? STORY_STACK : 1000;
    const NIGHT = STORY ? STORY_STACK : 1113.2;
    const stackPct = 8;
    // scripted night
    const plan = [
      { t: 12, k: 'buy', s: 'LFROG' }, { t: 20, k: 'rej', s: 'DUCK', why: 'Auditor · dev holds 31%' },
      { t: 64, k: 'sell', s: 'LFROG', pct: 80.8, how: 'Risk · trailing stop' }, { t: 80, k: 'buy', s: 'OTTER' },
      { t: 95, k: 'rej', s: 'GOOSE', why: 'Analyst · chase 0.41' }, { t: 101, k: 'stop', s: 'OTTER', pct: -6.0, how: 'Risk · -6% stop' },
      { t: 118, k: 'buy', s: 'BYTE' }, { t: 140, k: 'buy', s: 'SIRP' }, { t: 170, k: 'rej', s: 'GLAPE', why: 'Auditor · top 10 hold 44%' },
      { t: 212, k: 'sell', s: 'SIRP', pct: 43.3, how: 'Risk · trailing stop' }, { t: 296, k: 'timeout', s: 'BYTE', pct: 1.2, how: 'Risk · dead money' },
      { t: 248, k: 'buy', s: 'HCAT' }, { t: 318, k: 'rej', s: 'RONIN', why: 'Auditor · Safety Score 41' },
    ].sort((a, b) => a.t - b.t);
    // equity simulation per minute
    let realized = 0; const open = {}; const series = []; const marks = []; const acts = []; let id = 1000;
    const paths = {};
    plan.forEach((p) => { if (p.k !== 'buy') return; const ex = plan.find((q) => q.s === p.s && q.t > p.t && q.k !== 'buy'); const end = ex ? ex.pct : 27.4; const len = (ex ? ex.t : 330) - p.t; const path = [0]; for (let i = 1; i <= len; i++) { const k = i / len; const target = end * Math.pow(k, 0.8) + (ex ? 0 : Math.sin(k * 7) * 4); path.push(target + (rnd() - 0.5) * Math.abs(end) * 0.12 * (1 - k)); } path[len] = end; paths[p.s] = { from: p.t, path }; });
    for (let m = 0; m <= 330; m++) {
      for (const p of plan.filter((x) => x.t === m)) {
        const c = bySym[p.s]; const ts = new Date(T0 + m * 60000).toISOString();
        if (p.k === 'buy') {
          const size = +(((NIGHT + realized) * stackPct) / 100).toFixed(2); open[p.s] = { size, at: m };
          marks.push({ ts, kind: 'buy', label: 'Bought ' + p.s, mint: c.mint });
          acts.push({ id: id++, ts, kind: 'bought', title: 'Bought ' + p.s, subtitle: 'Executor', value: usd(size), value2: stackPct + '% of stack', mint: c.mint, chain: c.chain, image: null });
        } else if (p.k === 'rej') {
          acts.push({ id: id++, ts, kind: 'rejected', title: 'Rejected ' + p.s, subtitle: p.why, value: 'Skipped', value2: '', mint: c.mint, chain: c.chain, image: null });
        } else {
          const o = open[p.s]; const pnl = +(o.size * p.pct / 100).toFixed(2); realized += pnl; delete open[p.s];
          marks.push({ ts, kind: pnl >= 0 ? 'win' : 'loss', label: pnl >= 0 ? usd(pnl, { sign: true }) : `${p.pct.toFixed(0)}% · ${usd(pnl, { sign: true })}`, mint: c.mint });
          acts.push({ id: id++, ts, kind: p.k === 'sell' ? 'sold' : p.k === 'stop' ? 'stopped' : 'timeout', title: (p.k === 'stop' ? 'Stopped ' : 'Sold ') + p.s, subtitle: p.how, value: usd(pnl, { sign: true }), value2: pct(p.pct, 1), mint: c.mint, chain: c.chain, image: null });
        }
      }
      let unreal = 0; for (const [s, o] of Object.entries(open)) { const pp = paths[s]; unreal += o.size * (pp.path[Math.min(pp.path.length - 1, m - pp.from)] || 0) / 100; }
      series.push([T0 + m * 60000, +(NIGHT + realized + unreal + (rnd() - 0.5) * 0.6).toFixed(2)]);
    }
    acts.reverse();
    // older history for pagination
    for (let i = 0; i < 40; i++) {
      const c = coins[Object.keys(coins)[Math.floor(rnd() * 16)]]; const ts = new Date(T0 - (i + 1) * 47 * 60000).toISOString();
      const k = ['bought', 'sold', 'rejected', 'stopped', 'sold'][i % 5]; const pnl = k === 'sold' ? 5 + rnd() * 60 : -(2 + rnd() * 6);
      acts.push({ id: id++, ts, kind: k, title: { bought: 'Bought ', sold: 'Sold ', rejected: 'Rejected ', stopped: 'Stopped ' }[k] + c.symbol, subtitle: { bought: 'Executor', sold: 'Risk · trailing stop', rejected: 'Auditor · mint authority live', stopped: 'Risk · -6% stop' }[k], value: k === 'bought' ? usd(70 + rnd() * 20) : k === 'rejected' ? 'Skipped' : usd(pnl, { sign: true }), value2: k === 'bought' ? '8% of stack' : k === 'rejected' ? '' : pct(pnl / 0.85, 1), mint: c.mint, chain: c.chain, image: null });
    }
    // ---- demo video (?demo=1&story=1): one scripted PAPER night, $120 to $1,737.60. Fixture only, DEMO pill stays ----
    if (STORY) {
      const ST = [ // minutes after 11:45 PM, kind, symbol, pct, stack after
        [13, 'rej', 'DUCK', 'Auditor · LP not burned'], [36, 'rej', 'GLAPE', 'Auditor · dev holds 31%'],
        [53, 'win', 'LFROG', 52.4, 183.00], [87, 'win', 'OTTER', 61.0, 301.40], [121, 'win', 'SIRP', 44.7, 519.00],
        [139, 'stop', 'GOOSE', -6.0, 461.00], [152, 'stop', 'BYTE', -6.0, 404.00], [166, 'stop', 'PUG', -6.0, 347.00],
        [170, 'rej', 'RONIN', 'Auditor · Safety Score 41'], [190, 'buy', 'HCAT'], [236, 'win', 'HCAT', 316.4, 1051.16],
        [260, 'stop', 'GLAPE', -6.0, 990.10], [303, 'win', 'CORGI', 38.2, 1231.40], [351, 'win', 'NEWT', 21.6, 1350.30],
        [397, 'win', 'QUOK', 47.9, 1662.50], [432, 'win', 'YAK', 18.4, 1737.60],
      ];
      const S0 = NOW - 467 * 60000; const at = (m) => S0 + m * 60000;
      series.length = 0; marks.length = 0; acts.length = 0;
      let prev = 120, mi = 0; const steps = ST.filter((e) => e.length === 5);
      for (let m = 0; m <= 467; m++) {
        const nx = steps.find((e) => e[0] >= m); const pv = steps.filter((e) => e[0] < m).pop();
        const from = pv ? pv[4] : 120, fromM = pv ? pv[0] : 0;
        let v = from;
        if (nx) { const k = (m - fromM) / Math.max(1, nx[0] - fromM); const lead = nx[3] > 0 ? Math.pow(k, 1.8) : k > 0.75 ? (k - 0.75) * 4 : -0.15 * Math.sin(k * 3); v = from + (nx[4] - from) * Math.max(-0.2, Math.min(1, lead)); }
        series.push([at(m), +(v + (m && m < 467 ? (rnd() - 0.5) * v * 0.006 : 0)).toFixed(2)]);
      }
      series[series.length - 1][1] = 1737.6;
      for (const e of ST) {
        const c = bySym[e[2]]; const ts = new Date(at(e[0])).toISOString();
        if (e[1] === 'rej') acts.push({ id: id++, ts, kind: 'rejected', title: 'Rejected ' + e[2], subtitle: e[3], value: 'Skipped', value2: '', mint: c.mint, chain: c.chain, image: null });
        else if (e[1] === 'buy') acts.push({ id: id++, ts, kind: 'bought', title: 'Bought ' + e[2], subtitle: 'Executor · 33 min old · $214K', value: usd(222.55), value2: '', mint: c.mint, chain: c.chain, image: null });
        else {
          const i = steps.indexOf(e); const pnl = +(e[4] - (i ? steps[i - 1][4] : 120)).toFixed(2);
          if (e[1] === 'win' && pnl > 100) marks.push({ ts, kind: 'win', label: usd(pnl, { sign: true }), mint: c.mint });
          if (e[0] === 166) marks.push({ ts, kind: 'loss', label: '3 stops · ' + usd(347), mint: c.mint });
          acts.push({ id: id++, ts, kind: e[1] === 'win' ? 'sold' : 'stopped', title: (e[1] === 'win' ? 'Sold ' : 'Stopped ') + e[2], subtitle: e[1] === 'win' ? 'Risk · trailing stop' : 'Risk · -6% stop', value: usd(pnl, { sign: true }), value2: pct(e[3], 1), mint: c.mint, chain: c.chain, image: null });
        }
      }
      acts.reverse();
      if (STORY_LIVE) { // start of the night: $120, empty feed; the recorder plays the rest through window.__show
        const L0 = new Date(2026, 10, 2, STORY_T0[0], STORY_T0[1]).getTime();
        series.length = 0; marks.length = 0; acts.length = 0;
        for (let i = 30; i >= 0; i--) series.push([L0 - i * 60000, STORY_STACK]);
      }
    }
    const hc = bySym.HCAT; const hpos = open.HCAT;
    const st = {
      balance: STORY_LIVE ? STORY_STACK : series[series.length - 1][1], nightStart: NIGHT, start: START_ALL, mode: 'PAPER', unread: 2, handle: 'zostaff',
      counts: STORY_LIVE ? { scanner: 0, auditor: 0, analyst: 0, executor: 0, risk: 0 } : STORY ? { scanner: 53, auditor: 53, analyst: 12, executor: 12, risk: 12 } : { scanner: 1284, auditor: 312, analyst: 57, executor: 6, risk: 5 },
    };
    function stateObj() {
      const bal = st.balance;
      return {
        balance: bal, todayPct: (bal / NIGHT - 1) * 100, multiplier: bal / START_ALL, mode: st.mode, nightStart: NIGHT, online: true,
        updatedAt: new Date().toISOString(), unread: st.unread, handle: st.handle,
        agents: [
          { name: 'scanner', status: agentStatus.scanner, count: st.counts.scanner, subtitle: 'pumpfun + Robinhood Chain', card: { pools: st.counts.scanner, feed: feed.slice(0, 6) } },
          { name: 'auditor', status: agentStatus.auditor, count: st.counts.auditor, subtitle: NL + ' parts per coin', card: { takenApart: st.counts.auditor, rejected: 41, current: auditCur, last: auditLast } },
          { name: 'analyst', status: agentStatus.analyst, count: st.counts.analyst, subtitle: 'Judge: rules', card: { scoring: scoring, scored: st.counts.analyst, mode: 'rules' } },
          { name: 'executor', status: agentStatus.executor, count: st.counts.executor, subtitle: '8% of stack', card: Object.assign({ trades: st.counts.executor, stackPct: 8 }, exec) },
          { name: 'risk', status: agentStatus.risk, count: 1, subtitle: 'stop -6% · trail 12%', card: { open: 1, guarding: true, last: 'Trailed out', stopPct: 6, trailPct: 12 } },
        ],
      };
    }
    const agentStatus = { scanner: 'running', auditor: 'running', analyst: 'running', executor: 'running', risk: 'running' };
    const killReasons = ['top 10 hold 61%', 'liq $8.2K', '1h +412%', 'mint authority live', 'pool 3 min old', 'freeze authority live'];
    let feed = [];
    for (let i = 0; i < 6; i++) { const c = coins[Object.keys(coins)[(i * 5) % 16]]; const pass = i === 1 || i === 4; feed.push({ name: c.name, symbol: c.symbol, mint: c.mint, chain: c.chain, verdict: pass ? 'pass' : 'kill', reason: pass ? null : killReasons[i % killReasons.length], liq: c.liquidity }); }
    const NEWT = bySym.NEWT;
    let auditCur = { mint: NEWT.mint, name: NEWT.name, layers: [{ n: 1, pass: true }, { n: 2, pass: true }, { n: 3, pass: true }, { n: 4, pass: true }, { n: 5, pass: true }] };
    let auditLast = NL + '/' + NL + ' → Executor';
    let scoring = { mint: hc.mint, name: hc.name, score: 82, passed: true };
    let exec = { lastSize: hpos ? hpos.size : 93.46, state: 'executing', progress: 62, name: hc.symbol };

    const SOL_L = [['Mint authority', 'Revoked', 'rpc'], ['Freeze authority', 'Revoked', 'rpc'], ['Dev wallet', '1.8%', 'rugcheck'], ['Top 10 holders', '18.4%', 'rpc'], ['LP locked', '100% burned', 'rugcheck'], ['Pool age', '26 min', 'rpc'], ['Bundled wallets', '2', 'rugcheck'], ['Delegations', '0 accounts', 'rpc'], ['Dev history', '2 launches · 0 dead', 'rugcheck']];
    const RH_L = [['Safety Score', '82/100', 'grokhood'], ['Rings', 'None', 'grokhood'], ['Known operator', 'None', 'grokhood'], ['Dev wallet', '2.1%, not sold', 'grokhood'], ['Top 10 holders', '21%', 'grokhood'], ['Exempt and snipe wallets', '0', 'grokhood'], ['Bundled clusters', 'top cluster 3.2%', 'grokhood'], ['Dump alerts', 'None', 'grokhood'], ['X activity', 'Organic · 14 posts', 'grokhood']];
    function auditFor(c) {
      const base = (c.chain === 'robinhood' ? RH_L : SOL_L).map((l, i) => ({ n: i + 1, name: l[0], pass: true, value: l[1], source: l[2] }));
      let reason = null;
      if (c.symbol === 'DUCK') { base[2].pass = false; base[2].value = '31%'; reason = 'dev holds 31%'; }
      if (c.symbol === 'GLAPE') { base[3].pass = false; base[3].value = '44%'; reason = 'top 10 hold 44%'; }
      if (c.symbol === 'RONIN') { base[0].pass = false; base[0].value = '41/100'; reason = 'Safety Score 41'; }
      // demo video: the recorder can set layer values per coin, [[layer index, value, pass, reason]]
      for (const [li, v, ok, why] of ((SHOW && window.__auditOv) || {})[c.symbol] || []) { base[li].value = v; base[li].pass = ok; if (!ok) reason = why; }
      // like the real Auditor: it stops at the first failed part, the rest stay "not checked"
      const fi = base.findIndex((l) => l.pass === false);
      if (fi >= 0) for (let i = fi + 1; i < base.length; i++) { base[i].pass = null; base[i].value = 'not checked'; }
      return { agent: 'auditor', mint: c.mint, chain: c.chain, ts: new Date(NOW - 3600e3).toISOString(), passed: !reason, layersChecked: base.filter((l) => l.pass !== null).length, layers: base, holders: c.holders, reason, bundled: 2, safetyScore: c.safetyScore || null, name: c.name, symbol: c.symbol, coinUrl: fomoUrl(c) };
    }
    function fomoUrl(c) { return `#/coin/${encodeURIComponent(c.mint)}`; } // demo only: real links come from the API (desk/links.py)
    function candlesFor(c, entryM, pp) {
      const out = []; let p = c.price; const n = 180; const arr = [];
      if (pp) { for (let i = 0; i < n; i++) { const m = 330 - n + i; const k = m - pp.from; arr.push(k < 0 ? null : pp.path[Math.min(pp.path.length - 1, k)]); } }
      const entry = c.price / (1 + (pp ? pp.path[pp.path.length - 1] : 0) / 100);
      let prev = pp ? entry * (1 - 0.05) : c.price * 0.8;
      for (let i = 0; i < n; i++) {
        const target = arr[i] != null ? entry * (1 + arr[i] / 100) : pp ? entry * (0.97 + 0.03 * Math.sin(i / 9) + (rnd() - 0.5) * 0.02) : prev * (1 + (rnd() - 0.48) * 0.03);
        const o = prev, cl = target; const hi = Math.max(o, cl) * (1 + rnd() * 0.02), lo = Math.min(o, cl) * (1 - rnd() * 0.02);
        out.push([NOW - (n - i) * 60000, o, hi, lo, cl, Math.floor(2000 + rnd() * 30000 + Math.abs(cl - o) / o * 900000)]);
        prev = cl;
      }
      return { candles: out, entry };
    }
    function coinObj(mint) {
      const c = coins[mint]; if (!c) return null;
      const sp = showPos[c.symbol] || showClosed[c.symbol];
      if (sp) return showCoinObj(c, sp, !showPos[c.symbol]);
      const isOpen = c.symbol === 'HCAT';
      const pp = isOpen ? paths.HCAT : null;
      const { candles, entry } = SHOW && !isOpen ? (showBase[c.mint] = showBase[c.mint] || candlesFor(c, 0, null)) : candlesFor(c, 0, pp);
      c.price = candles[candles.length - 1][4];
      const a = auditFor(c);
      const shield = `${a.layers.filter((l) => l.pass === true).length}/${a.layers.length}`;
      let position = null, exitLine = null;
      if (isOpen) {
        const peak = Math.max(...candles.slice(-80).map((x) => x[2]));
        const trail = peak * 0.88; exitLine = trail;
        const pnlPct = (c.price / entry - 1) * 100;
        position = { sizeUsd: hpos.size, entryPrice: entry, stopPrice: entry * 0.94, trailPrice: trail, trailActive: true, timeoutAt: new Date(NOW + 98 * 60000).toISOString(), pnlUsd: hpos.size * pnlPct / 100, pnlPct, mode: 'PAPER' };
      }
      return { mint, chain: c.chain, name: c.name, symbol: c.symbol, image: null, ageH: c.ageH, price: c.price, sinceEntryPct: position ? position.pnlPct : null, shield, marketCap: c.marketCap, liquidity: c.liquidity, volume24h: c.volume24h, holders: c.holders, candles, position, exitLine, coinUrl: fomoUrl(c), grokhoodUrl: c.chain === 'robinhood' ? 'https://grokhood.tech/token/' + c.mint : null, safetyScore: c.safetyScore || null };
    }
    function chartFor(r) {
      if (r === 'LIVE') return { points: series.slice(), start: NIGHT, marks };
      if (r === '1H') return { points: series.slice(-60), start: series[series.length - 61][1], marks };
      if (r === '4H') return { points: series.slice(-240), start: series[series.length - 241][1], marks };
      const days = r === '1D' ? 1 : 7; const pts = []; const stepM = r === '1D' ? 10 : 60;
      const startV = r === '1D' ? NIGHT * 0.985 : START_ALL;
      const tStart = NOW - days * 86400e3; const nightIdx = T0;
      let v = startV;
      for (let t = tStart; t < nightIdx; t += stepM * 60000) { const k = (t - tStart) / (nightIdx - tStart); v = startV + (NIGHT - startV) * k + (rnd() - 0.5) * 6 * Math.sin(k * 9); pts.push([t, +v.toFixed(2)]); }
      for (let i = 0; i < series.length; i += stepM / 2 | 0 || 1) pts.push(series[i]);
      pts.push(series[series.length - 1]);
      return { points: pts, start: startV, marks: r === '1D' ? marks : [] };
    }
    const SETTINGS = {
      'scanner.solana': [true, null, null, [true, false], 'Solana pumpfun'], 'scanner.min_liquidity_usd': [40000, 1000, 5000000, null, 'Min liquidity $'], 'scanner.max_top10_pct': [25, 1, 100, null, 'Max top 10 %'], 'scanner.max_h1_change_pct': [80, 5, 10000, null, 'Max 1h change %'], 'scanner.robinhood': [true, null, null, [true, false], 'Robinhood Chain (GROKHOOD)'],
      'auditor.enabled': [true, null, null, [true, false], 'Take apart before buying'], 'auditor.max_dev_pct': [5, 0, 100, null, 'Max dev %'], 'auditor.max_top10_pct': [25, 1, 100, null, 'Max top 10 %'], 'auditor.min_lp_locked_pct': [99, 0, 100, null, 'Min LP locked %'], 'auditor.max_bundled': [5, 1, 100, null, 'Max bundled wallets'], 'auditor.rh_min_score': [70, 0, 100, null, 'Min GROKHOOD Safety Score'],
      'analyst.mode': ['rules', null, null, ['grok', 'rules'], 'Judge'], 'analyst.min_score': [70, 0, 100, null, 'Min score'], 'analyst.max_chase': [0.3, 0, 1, null, 'Max chase'], 'analyst.min_organic': [0.65, 0, 1, null, 'Min organic'],
      'executor.stack_pct': [8, 0.5, 100, null, 'Stack %'], 'executor.min_ticket_usd': [47.5, 1, 100000, null, 'Min ticket $'], 'executor.below_min': ['bump', null, null, ['bump', 'skip'], 'Below min'], 'executor.max_open': [5, 1, 50, null, 'Max open positions'],
      'risk.stop_pct': [6, 0.5, 90, null, 'Stop %'], 'risk.trail_start_pct': [20, 1, 1000, null, 'Trail starts at +%'], 'risk.trail_pct': [12, 1, 90, null, 'Trail %'], 'risk.timeout_h': [3, 0.25, 168, null, 'Timeout h'],
      'desk.mode': ['PAPER', null, null, ['PAPER', 'LIVE'], 'Mode'], 'desk.paper_start_stack': [1000, 10, 10000000, null, 'Paper start stack $'], 'desk.live_stack': [0, 0, 100000000, null, 'Live stack $'], 'desk.push_rejects': [false, null, null, [true, false], 'Push rejects'], 'desk.fomo_handle': ['zostaff', null, null, null, 'FOMO handle'],
    };
    const settings = {}; for (const [k, v] of Object.entries(SETTINGS)) settings[k] = { value: v[0], default: v[0], min: v[1], max: v[2], choices: v[3], label: v[4] };

    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    async function apiDemo(path, opts) {
      if (DEMO_STATE === 'loading') return new Promise(() => {});
      await wait(40 + rnd() * 60);
      if (DEMO_STATE === 'error') { const e = new Error('offline'); e.status = 0; throw e; }
      const empty = DEMO_STATE === 'empty';
      const u = new URL(path, 'http://x'); const p = u.pathname; const m = (opts.method || 'GET').toUpperCase();
      if (p === '/api/state') {
        if (empty) { const s = stateObj(); s.balance = 1000; s.todayPct = 0; s.multiplier = 1; s.unread = 0; s.agents.forEach((a) => { a.count = 0; }); s.agents[0].card = { pools: 0, feed: [] }; s.agents[1].card = { takenApart: 0, rejected: 0, current: null, last: null }; s.agents[2].card = { scoring: null, scored: 0, mode: 'rules' }; s.agents[3].card = { trades: 0, lastSize: null, stackPct: 8, state: 'idle', progress: 0, name: null }; s.agents[4].card = { open: 0, guarding: false, last: null, stopPct: 6, trailPct: 12 }; return s; }
        return stateObj();
      }
      if (p === '/api/chart') return empty ? { points: [], start: 1000, marks: [] } : chartFor(u.searchParams.get('range') || 'LIVE');
      if (p === '/api/activity') {
        if (empty) return { rows: [], next: null };
        const before = u.searchParams.get('before'); const i = before ? acts.findIndex((a) => String(a.id) === before) + 1 : 0;
        const rows = acts.slice(i, i + 30); return { rows, next: i + 30 < acts.length ? rows[rows.length - 1].id : null };
      }
      let mm;
      if ((mm = p.match(/^\/api\/coin\/(.+)$/))) { const c = coinObj(decodeURIComponent(mm[1])); if (!c) { const e = new Error('not found'); e.status = 404; throw e; } return c; }
      if ((mm = p.match(/^\/api\/audit\/(.+)$/))) { const c = coins[decodeURIComponent(mm[1])]; if (!c || empty) return { layers: [] }; return auditFor(c); }
      if ((mm = p.match(/^\/api\/agents\/(\w+)\/decisions$/))) {
        if (empty) return { rows: [] };
        const n = mm[1]; const keys = Object.keys(coins);
        const rows = Array.from({ length: 20 }, (_, i) => { const c = coins[keys[(i * 3 + n.length) % keys.length]]; const pass = (i + n.length) % 3 !== 0; return { ts: new Date(NOW - i * 9 * 60000).toISOString(), mint: c.mint, name: c.name, verdict: n === 'risk' ? (pass ? 'trail' : 'stop') : n === 'executor' ? 'filled' : pass ? 'pass' : n === 'scanner' ? 'kill' : 'reject', reason: n === 'scanner' ? (pass ? 'liq ' + compact(c.liquidity) : killReasons[i % 6]) : n === 'auditor' ? (pass ? NL + '/' + NL + ' parts' : 'dev holds 31%') : n === 'analyst' ? (pass ? 'score 78' : 'chase 0.41') : n === 'executor' ? usd(70 + i) + ' · 8% of stack' : pass ? 'trailed out +18%' : '-6% stop', coinUrl: fomoUrl(c) }; });
        return { rows };
      }
      if ((mm = p.match(/^\/api\/agents\/(\w+)\/(pause|resume)$/)) && m === 'POST') { agentStatus[mm[1]] = mm[2] === 'pause' ? 'paused' : 'running'; return { ok: true }; }
      if (p === '/api/account') return { mode: st.mode, stack: st.balance, handle: st.handle, profileUrl: '#/account', signupUrl: '#/account', telegram: true, webpush: false, vapidPublicKey: null, judge: settings['analyst.mode'].value, grokToday: { calls: 0, costUsd: 0 } };
      if (p === '/api/settings' && m === 'GET') return JSON.parse(JSON.stringify(settings));
      if (p === '/api/settings' && m === 'POST') {
        const { key, value } = opts.body || {}; const s = settings[key];
        if (!s) { const e = new Error(`unknown setting '${key}'`); e.status = 400; throw e; }
        let v = value;
        if (s.min != null) { v = parseFloat(String(value).replace(/[$,%]/g, '')); if (!isFinite(v)) { const e = new Error(`${key}: not a valid value: '${value}'`); e.status = 400; throw e; } if (v < s.min || v > s.max) { const e = new Error(`${key}: range ${s.min} to ${s.max}`); e.status = 400; throw e; } }
        const old = s.value; s.value = v; return { key, old, new: v };
      }
      if (p === '/api/mode' && m === 'POST') { st.mode = opts.body.mode; return { mode: st.mode }; }
      if (p === '/api/search') { const q = (u.searchParams.get('q') || '').toLowerCase(); return { rows: Object.values(coins).filter((c) => (c.name + ' ' + c.symbol + ' ' + c.mint).toLowerCase().includes(q)).slice(0, 12).map((c) => ({ mint: c.mint, name: c.name, symbol: c.symbol, chain: c.chain })) }; }
      if (p === '/api/read') { st.unread = 0; return { unread: 0 }; }
      if (p === '/api/test-push' || p === '/api/push') return { ok: true };
      if (p === '/api/copy') return copyObj(empty ? 'no_traders' : qs.get('copy'));
      if (p === '/api/replay') return replayObj(+u.searchParams.get('h') || 24, empty);
      if (p === '/api/night-post' && m === 'GET') return nightPostObj(false, empty || qs.get('post') === 'none');
      if (p === '/api/night-post/preview' && m === 'POST') { await wait(500); return nightPostObj(true, false); }
      const e = new Error('not found'); e.status = 404; throw e;
    }
    function streamDemo(emit) {
      // balance ticks
      if (!SHOW) setInterval(() => { st.balance = +(st.balance + (rnd() - 0.42) * 0.9).toFixed(2); series.push([Date.now(), st.balance]); emit('balance', { balance: st.balance, todayPct: (st.balance / NIGHT - 1) * 100, multiplier: st.balance / START_ALL, ts: Date.now() }); }, 2600);
      // scanner feed
      const keys = Object.keys(coins); let si = 3;
      if (!STORY || STORY_LIVE) setInterval(() => { const c = coins[keys[si++ % keys.length]]; const pass = rnd() < 0.3; st.counts.scanner++; feed.unshift({ name: c.name, symbol: c.symbol, mint: c.mint, chain: c.chain, verdict: pass ? 'pass' : 'kill', reason: pass ? null : killReasons[si % 6], liq: c.liquidity }); feed = feed.slice(0, 6); emit('scan', { agent: 'scanner', chain: c.chain, mint: c.mint, name: c.name, symbol: c.symbol, verdict: pass ? 'pass' : 'kill', reason: pass ? null : killReasons[si % 6], liquidityUsd: c.liquidity }); }, STORY_LIVE ? 900 : SHOW ? 700 : 3200);
      // auditor: finish NEWT live, then loop on new coins
      let ai = 0;
      const runAudit = (c, from) => {
        const a = auditFor(c);
        auditCur = { mint: c.mint, name: c.name, layers: a.layers.slice(0, from - 1).map((l) => ({ n: l.n, pass: l.pass })) };
        let n = from;
        const tick = () => {
          if (n > NL) { auditLast = a.passed ? NL + '/' + NL + ' → Executor' : a.reason; st.counts.auditor++; emit('audit', a); if (!SHOW) setTimeout(() => runAudit(coins[keys[(7 + ai++) % keys.length]], 1), 5000); return; }
          const l = a.layers[n - 1]; auditCur.layers.push({ n: l.n, pass: l.pass }); emit('audit_layer', { mint: c.mint, n: l.n, name: l.name, pass: l.pass, value: l.value }); n++; setTimeout(tick, SHOW ? 330 : 700);
        };
        setTimeout(tick, SHOW ? 200 : 900);
      };
      if (!SHOW) runAudit(NEWT, 6);
      if (SHOW) window.__show = showApi(emit, runAudit);
      // executor progress
      if (!SHOW) setInterval(() => { if (exec.state === 'executing') { exec.progress = Math.min(100, exec.progress + 7); if (exec.progress >= 100) { exec.state = 'filled'; emit('fill', { mint: hc.mint, name: hc.symbol, state: 'filled', progress: 100 }); setTimeout(() => { exec.state = 'executing'; exec.progress = 8; }, 6000); } else emit('signal', { mint: hc.mint, name: hc.symbol, state: 'executing', progress: exec.progress }); } }, 900);
      // analyst
      if (!SHOW) setInterval(() => { const c = coins[keys[Math.floor(rnd() * keys.length)]]; const sc = Math.floor(48 + rnd() * 46); scoring = { mint: c.mint, name: c.name, score: sc, passed: sc >= 70 }; st.counts.analyst++; emit('verdict', { agent: 'analyst', mint: c.mint, name: c.name, score: sc, passed: sc >= 70 }); }, 7000);
    }
    // ---- demo video (?demo=1&show=1): a fast scripted session the recorder drives. Same DEMO pill, nothing touches the backend ----
    const showPos = {}; const showClosed = {}; const showBase = {};
    let showCash = null; // balance without open show positions
    function showCoinObj(c, sp, closed) {
      const base = showBase[c.mint] = showBase[c.mint] || candlesFor(c, 0, null);
      const candles = base.candles.slice(sp.candles.length + 60).concat(sp.candles);
      c.price = candles[candles.length - 1][4];
      const peakP = sp.entry * (1 + sp.peak / 100);
      const trailOn = sp.peak >= 20; const trail = peakP * 0.88;
      const position = { sizeUsd: sp.size, entryPrice: sp.entry, stopPrice: sp.entry * 0.94, trailPrice: trailOn ? trail : null, trailActive: trailOn, timeoutAt: new Date(sp.t0 + 3 * 3600e3).toISOString(), pnlUsd: sp.size * sp.pct / 100, pnlPct: sp.pct, mode: 'PAPER' };
      return { mint: c.mint, chain: c.chain, name: c.name, symbol: c.symbol, image: null, ageH: c.ageH, price: c.price, sinceEntryPct: closed ? null : sp.pct, shield: NL + '/' + NL, marketCap: c.marketCap * (1 + sp.pct / 100), liquidity: c.liquidity, volume24h: c.volume24h, holders: c.holders, candles, position: closed ? null : position, exitLine: closed ? null : trailOn ? trail : sp.entry * 0.94, coinUrl: fomoUrl(c), grokhoodUrl: null, safetyScore: c.safetyScore || null };
    }
    function showApi(emit, runAudit) {
      const nowMs = () => (window.__simNow ? window.__simNow() : Date.now()); const nowISO = () => new Date(nowMs()).toISOString();
      const unreal = () => Object.values(showPos).reduce((t, p) => t + p.size * p.pct / 100, 0);
      let simT = null; // session clock: each tick is ~80s of night, so the chart draws a slope, not a wall
      const pushBal = () => { st.balance = +(showCash + unreal()).toFixed(2); simT = window.__simNow ? Math.max(window.__simNow(), (simT || 0) + 1000) : (simT || series[series.length - 1][0]) + 80000; series.push([simT, st.balance]); emit('balance', { balance: st.balance, todayPct: (st.balance / NIGHT - 1) * 100, multiplier: st.balance / START_ALL, ts: simT }); };
      const act = (row) => { const r = Object.assign({ id: id++, ts: nowISO(), image: null }, row); acts.unshift(r); emit('activity', r); };
      return {
        scan: (sym, pass, reason) => { const c = bySym[sym]; emit('scan', { agent: 'scanner', chain: c.chain, mint: c.mint, name: c.name, symbol: c.symbol, verdict: pass ? 'pass' : 'kill', reason: reason || null, liquidityUsd: c.liquidity }); },
        audit: (sym) => runAudit(bySym[sym], 1),
        verdict: (sym, score) => { const c = bySym[sym]; scoring = { mint: c.mint, name: c.name, score, passed: score >= 70 }; st.counts.analyst++; emit('verdict', { agent: 'analyst', mint: c.mint, name: c.name, score, passed: score >= 70 }); },
        mint: (sym) => bySym[sym].mint,
        coin: (sym, o) => Object.assign(bySym[sym], o), // demo video: set a coin's stats for the story
        buy: (sym, forceSize, silent) => {
          if (showCash == null) showCash = st.balance;
          const c = bySym[sym]; const base = showBase[c.mint] = showBase[c.mint] || candlesFor(c, 0, null);
          const entry = base.candles[base.candles.length - 1][4];
          const size = forceSize || +(st.balance * stackPct / 100).toFixed(2);
          showPos[sym] = { size, entry, pct: 0, peak: 0, candles: [], t0: Date.now() };
          st.counts.executor++; exec = { lastSize: size, state: 'filled', progress: 100, name: sym };
          if (!silent) marks.push({ ts: nowISO(), kind: 'buy', label: 'Bought ' + sym, mint: c.mint });
          if (!silent) act({ kind: 'bought', title: 'Bought ' + sym, subtitle: 'Executor', value: usd(size), value2: stackPct + '% of stack', mint: c.mint, chain: c.chain });
          emit('fill', { mint: c.mint, name: sym, state: 'filled', progress: 100, sizeUsd: size });
          return size;
        },
        tick: (sym, pct) => {
          const sp = showPos[sym]; if (!sp) return; const c = bySym[sym];
          const prev = sp.candles.length ? sp.candles[sp.candles.length - 1][4] : sp.entry;
          const cl = sp.entry * (1 + pct / 100); const o = prev;
          sp.candles.push([nowMs(), o, Math.max(o, cl) * (1 + rnd() * 0.012), Math.min(o, cl) * (1 - rnd() * 0.012), cl, Math.floor(20000 + rnd() * 60000)]);
          sp.pct = pct; sp.peak = Math.max(sp.peak, pct);
          emit('price', { mint: c.mint, price: cl }); pushBal();
        },
        reject: (sym, why) => { const c = bySym[sym]; st.counts.auditor++; act({ kind: 'rejected', title: 'Rejected ' + sym, subtitle: why, value: 'Skipped', value2: '', mint: c.mint, chain: c.chain }); },
        sell: (sym, how) => {
          const sp = showPos[sym]; if (!sp) return; const c = bySym[sym];
          const pnl = +(sp.size * sp.pct / 100).toFixed(2); showCash += pnl; showClosed[sym] = sp; delete showPos[sym];
          st.counts.risk = (st.counts.risk || 0) + 1;
          if (Math.abs(pnl) > 100 || sym === 'PUG') marks.push({ ts: nowISO(), kind: pnl >= 0 ? 'win' : 'loss', label: usd(pnl, { sign: true }), mint: c.mint });
          act({ kind: pnl >= 0 ? 'sold' : 'stopped', title: (pnl >= 0 ? 'Sold ' : 'Stopped ') + sym, subtitle: how || (pnl >= 0 ? 'Risk · trailing stop' : 'Risk · -6% stop'), value: usd(pnl, { sign: true }), value2: pct(sp.pct, 1), mint: c.mint, chain: c.chain });
          pushBal(); emit('exit', { mint: c.mint, name: sym, pnlUsd: pnl });
          return { pnl, pct: sp.pct, size: sp.size };
        },
        state: () => ({ balance: st.balance, night: NIGHT }),
      };
    }
    // ---- round 3 fixtures: copy bot, replay, morning post. Own seeded generators, so the fixtures above stay the same ----
    const rngOf = (s0) => { let a = s0 | 0; return () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; };
    const r3 = rngOf(31337);
    const b58 = (r, n) => Array.from({ length: n }, () => B58[Math.floor(r() * 58)]).join('');
    const extraCoins = {};
    [['Pocket Panda', 'PANDA', 'solana', 1.9e6], ['Zen Zebra', 'ZEBRA', 'solana', 6.4e5], ['Sigma Snail', 'SNAIL', 'solana', 3.1e5]].forEach(([name, symbol, chain, mc]) => { extraCoins[symbol] = { name, symbol, chain, mint: b58(r3, 43) + 'p', marketCap: mc }; });
    const ccoin = (sy) => bySym[sy] || extraCoins[sy];
    const DESKV = {
      LFROG: ['cleared', null], OTTER: ['cleared', null], SIRP: ['cleared', null], HCAT: ['cleared', null], BYTE: ['cleared', null],
      DUCK: ['rejected', 'Auditor · dev holds 31%'], GLAPE: ['rejected', 'Auditor · top 10 hold 44%'], GOOSE: ['rejected', 'Analyst · chase 0.41'], RONIN: ['rejected', 'Auditor · Safety Score 41'],
      NEWT: ['watching', 'Scanner · waiting'], CORGI: ['watching', 'Scanner · waiting'],
    };
    const deskOf = (sy) => { const v = DESKV[sy]; if (!v) return { verdict: 'unseen', label: null }; return { verdict: v[0], label: v[0] === 'cleared' ? NL + '/' + NL + ' cleared' : v[1] }; };
    const TRADERS = [['moonmaxi', 'Moon Maxi'], ['solsniper', 'Sol Sniper'], ['degenpriya', 'Priya'], ['kookcap', 'Kook Capital'], ['larpwhale', 'Larp Whale'], ['gemhunter', 'Gem Hunter'], ['nightowl', 'Night Owl']];
    const copyTrades = (() => {
      const scripted = [[2, 'moonmaxi', 'DUCK'], [4, 'solsniper', 'LFROG'], [7, 'kookcap', 'DUCK'], [11, 'degenpriya', 'PANDA'], [14, 'larpwhale', 'LFROG'], [18, 'gemhunter', 'DUCK'],
        [23, 'moonmaxi', 'HCAT'], [29, 'nightowl', 'PANDA'], [33, 'solsniper', 'DUCK'], [41, 'degenpriya', 'LFROG'], [52, 'kookcap', 'HCAT']];
      const pool = ['OTTER', 'GLAPE', 'ZEBRA', 'NEWT', 'SIRP', 'GOOSE', 'SNAIL', 'RONIN', 'BYTE', 'CORGI'];
      const out = scripted.map(([m, h, sy]) => ({ m, h, sy, side: 'buy' }));
      for (let i = 0; i < 39; i++) {
        const sy = pool[Math.floor(r3() * pool.length)];
        out.push({ m: 60 + i * 8 + Math.floor(r3() * 6), h: TRADERS[Math.floor(r3() * TRADERS.length)][0], sy, side: r3() < 0.45 ? 'sell' : 'buy' });
      }
      // a coin only counts as hot from the scripted buys: random pool buys go to one trader per coin
      const owner = {}; out.forEach((t) => { if (t.side === 'buy' && pool.includes(t.sy)) { owner[t.sy] = owner[t.sy] || t.h; t.h = owner[t.sy]; } });
      return out.map((t, i) => { const c = ccoin(t.sy); return { id: 9000 - i, handle: t.h, ts: new Date(NOW - t.m * 60000).toISOString(), side: t.side, address: c.mint, chain: c.chain, symbol: c.symbol, name: c.name,
        usd: Math.round((t.side === 'buy' ? 60 + r3() * 840 : 40 + r3() * 1200) * 100) / 100, mcap: c.marketCap * (0.7 + r3() * 0.6), coinUrl: DESKV[t.sy] ? fomoUrl(c) : '#/copy', desk: deskOf(t.sy) }; });
    })();
    function copyObj(state) {
      state = ['no_token', 'no_traders', 'stale'].includes(state) ? state : 'running';
      if (state === 'no_token') return { state, verified: false, lastPoll: null, traders: [], hot: [], trades: [] };
      if (state === 'no_traders') return { state, verified: true, lastPoll: new Date(NOW - 40e3).toISOString(), traders: [], hot: [], trades: [] };
      const trades = copyTrades.slice(0, 50);
      const by = {};
      trades.forEach((t) => { if (t.side !== 'buy') return; const b = by[t.address] = by[t.address] || { t, hs: [], usd: 0, last: 0 }; if (!b.hs.includes(t.handle)) b.hs.push(t.handle); b.usd += t.usd; b.last = Math.max(b.last, Date.parse(t.ts)); });
      const hot = Object.values(by).filter((b) => b.hs.length >= 2).sort((a, b) => b.hs.length - a.hs.length || b.last - a.last)
        .map((b) => ({ address: b.t.address, chain: b.t.chain, symbol: b.t.symbol, name: b.t.name, buyers: b.hs.length, handles: b.hs, usd: Math.round(b.usd * 100) / 100, lastTs: new Date(b.last).toISOString(), coinUrl: b.t.coinUrl, desk: b.t.desk }));
      const traders = TRADERS.map(([h, name]) => { const mine = trades.filter((t) => t.handle === h); return { id: h, handle: h, name, trades24h: mine.length, lastTs: mine.length ? mine[0].ts : null, profileUrl: '#/copy' }; })
        .sort((a, b) => b.trades24h - a.trades24h);
      return { state, verified: true, lastPoll: new Date(NOW - (state === 'stale' ? 14 * 60e3 : 50e3)).toISOString(), traders, hot, trades };
    }

    const ADJ = ['Crispy', 'Lunar', 'Wobbly', 'Atomic', 'Sleepy', 'Golden', 'Feral', 'Cosmic', 'Soggy', 'Turbo', 'Velvet', 'Rusty', 'Hyper', 'Sneaky', 'Fuzzy', 'Mega'];
    const ANI = [['Mole', 'MOLE'], ['Crab', 'CRAB'], ['Lemur', 'LEMUR'], ['Bison', 'BISON'], ['Gecko', 'GECKO'], ['Koala', 'KOALA'], ['Moth', 'MOTH'], ['Shrimp', 'SHRMP'], ['Llama', 'LLAMA'], ['Squid', 'SQUID'], ['Toad', 'TOAD'], ['Wasp', 'WASP'], ['Heron', 'HERON'], ['Tapir', 'TAPIR']];
    const RSN = {
      scanner: ['top 10 hold 61%', 'liq $8.2K', '1h +412%', 'mint authority live', 'pool 3 min old', 'freeze authority live'],
      auditor: ['dev holds 31%', 'top 10 hold 44%', 'LP 12% locked', '14 bundled wallets', 'Safety Score 41', 'dev has 3 dead launches'],
      analyst: ['chase 0.41', 'score 58', 'organic 0.42', 'score 61'],
    };
    const replayCache = {};
    function replayObj(h, empty) {
      h = clamp(Math.round(h), 6, 168);
      if (empty) return { windowH: h, horizonH: 6, pending: 0, noData: 0, evaluated: 0, updatedAt: null, summary: { refused: 0, dodged: 0, missed: 0, dumped: 0, runners: 0, avgRefusedPct: null, signals: 0, signalAvgPct: null }, byStage: [], rows: [] };
      if (replayCache[h]) return replayCache[h];
      const r = rngOf(4000 + h);
      const total = h === 24 ? 47 : Math.round(h * 1.75 + 8);
      const known = [['DUCK', 'auditor', 'dev holds 31%'], ['GLAPE', 'auditor', 'top 10 hold 44%'], ['GOOSE', 'analyst', 'chase 0.41'], ['RONIN', 'auditor', 'Safety Score 41'],
        ['LFROG', 'executor'], ['OTTER', 'executor'], ['SIRP', 'executor'], ['BYTE', 'executor']];
      const all = []; const usedSym = {};
      for (let i = 0; i < total; i++) {
        let c, stage, reason;
        if (i < known.length) { c = bySym[known[i][0]]; stage = known[i][1]; reason = known[i][2]; }
        else {
          const a = ANI[Math.floor(r() * ANI.length)], ad = ADJ[Math.floor(r() * ADJ.length)];
          const chain = r() < 0.15 ? 'robinhood' : 'solana';
          let sy = a[1]; if (usedSym[sy]) sy = (ad.charAt(0) + a[1]).slice(0, 5); if (usedSym[sy]) sy = (ad.slice(0, 2).toUpperCase() + a[1]).slice(0, 5); usedSym[sy] = 1;
          c = { name: ad + ' ' + a[0], symbol: sy, chain, mint: chain === 'robinhood' ? '0x' + Array.from({ length: 40 }, () => '0123456789abcdef'[Math.floor(r() * 16)]).join('') : b58(r, 43) + 'p' };
          if (!coins[c.mint]) { Object.assign(c, { image: null, price: Math.pow(10, -3 - r() * 3), ageH: 1 + r() * 20, marketCap: 1e5 + r() * 2e6, liquidity: 2e4 + r() * 2e5, volume24h: 5e4 + r() * 2e6, holders: Math.floor(200 + r() * 3000) }); coins[c.mint] = c; }
          const x = r(); stage = i < known.length + Math.round(total * 0.06) ? 'executor' : x < 0.5 ? 'scanner' : x < 0.8 ? 'auditor' : 'analyst';
          reason = stage === 'executor' ? null : RSN[stage][Math.floor(r() * RSN[stage].length)];
        }
        // price path after the decision and the Risk replay (stop -6%, trail 12% from +20%, timeout 3h, ~1.2% fees)
        const kind = r(); let maxPct, minPct, endPct, simPct, simExit;
        const ex = stage === 'executor';
        if (kind < (ex ? 0.3 : 0.64)) { maxPct = 1 + r() * 14; minPct = -(55 + r() * 42); endPct = minPct * (0.85 + r() * 0.15); simPct = -7.2 + (r() - 0.5) * 0.8; simExit = 'stop'; }
        else if (kind < (ex ? 0.55 : 0.91)) { maxPct = 2 + r() * 12; minPct = -(2 + r() * 3.5); endPct = -15 + r() * 18; simPct = -5 + r() * 7.5; simExit = 'timeout'; }
        else { maxPct = 30 + Math.pow(r(), 1.6) * (ex ? 300 : 170); minPct = -(r() * 4); endPct = maxPct * (0.2 + r() * 0.6); simPct = ((1 + maxPct / 100) * 0.88 - 1) * 100 - 1.2; simExit = maxPct < 20 ? 'timeout' : 'trail'; }
        const outcome = stage === 'executor' ? (simPct >= 0 ? 'win' : 'loss') : (simPct < 0 ? 'dodged' : 'missed');
        const decidedAt = new Date(NOW - (6 + r() * (h - 6)) * 3600e3).toISOString();
        all.push({ mint: c.mint, chain: c.chain, symbol: c.symbol, name: c.name, image: null, stage, reason, decidedAt, maxPct: +maxPct.toFixed(1), minPct: +minPct.toFixed(1), endPct: +endPct.toFixed(1), simPct: +simPct.toFixed(1), simExit, outcome, coinUrl: fomoUrl(c) });
      }
      const refused = all.filter((x) => x.stage !== 'executor'), sig = all.filter((x) => x.stage === 'executor');
      const avg = (a) => (a.length ? +(a.reduce((t, x) => t + x.simPct, 0) / a.length).toFixed(1) : null);
      const summary = { refused: refused.length, dodged: refused.filter((x) => x.outcome === 'dodged').length, missed: refused.filter((x) => x.outcome === 'missed').length,
        dumped: all.filter((x) => x.endPct <= -50).length, runners: all.filter((x) => x.maxPct >= 100).length, avgRefusedPct: avg(refused), signals: sig.length, signalAvgPct: avg(sig) };
      const byStage = ['scanner', 'auditor', 'analyst', 'executor'].map((st) => { const a = all.filter((x) => x.stage === st); return { stage: st, n: a.length, dodged: a.filter((x) => x.outcome === 'dodged').length, avgPct: avg(a) }; });
      const rows = all.slice().sort((a, b) => Math.abs(b.simPct) - Math.abs(a.simPct)).slice(0, 60);
      return (replayCache[h] = { windowH: h, horizonH: 6, pending: Math.round(h / 4) + 1, noData: Math.round(h / 10) + 1, evaluated: all.length, updatedAt: new Date(NOW - 4 * 60e3).toISOString(), summary, byStage, rows });
    }

    async function nightPostObj(preview, none) {
      if (none) return { date: null, text: null, cardUrl: null };
      const d = new Date(NOW);
      const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      let cardUrl = null;
      try { if (window.Share) { const cv = await window.Share.render('night', await window.Share.data.night(), 'land'); cardUrl = cv.toDataURL('image/png'); } } catch (e) { cardUrl = null; }
      const rp = replayObj(24).summary;
      const bal = st.balance;
      const ch = (bal / NIGHT - 1) * 100;
      const text = (preview ? 'GROMO · tonight so far (PAPER)\n\n' : 'GROMO · last night (PAPER)\n\n')
        + `${usd(NIGHT)} to ${usd(bal)}, ${pct(ch)} on the night\n`
        + '3 wins, 1 loss. Best: $LFROG +80.8% on a trailing stop\n\n'
        + `The Auditor took apart ${intf(st.counts.auditor)} coins.\nReplayed 6h later: GROMO said no to ${rp.refused}, and ${rp.dodged} of them would have lost money.\n\n`
        + 'Every buy is a tap on FOMO. GROMO never trades for you.';
      return { date, text, cardUrl };
    }
    return { api: apiDemo, stream: streamDemo, coins, bySym };
  })() : null;

  /* ================= boot ================= */
  if ('serviceWorker' in navigator && standalone() && !DEMO) navigator.serviceWorker.register('sw.js').catch(() => {});
  if (!location.hash || location.hash === '#' || location.hash === '#/') history.replaceState(null, '', location.pathname + location.search + '#/home');
  route();
  if (window.Share) window.Share.init({ api, S, DEMO, toast, coins: DEMO ? (s) => Demo.bySym[s] : null });
  if (!DEMO_STATE) startStream();
  if (DEMO) {
    window.__desk = { S, go };
    // screenshot helpers (design only): &scroll=px scrolls the page, &card=n scrolls the agent carousel
    const sc = +qs.get('scroll') || 0, cd = +qs.get('card') || 0;
    if (sc || cd) setTimeout(() => {
      if (cd) { const car = document.querySelector('.carousel'); if (car) { car.style.scrollBehavior = 'auto'; car.scrollLeft = cd * (car.firstElementChild.offsetWidth + 10); car.dispatchEvent(new Event('scroll')); } }
      if (sc && current) current.el.scrollTop = sc;
    }, 700);
    if (qs.get('search') != null) setTimeout(() => { openSearch(); const i = document.querySelector('.searchbar input'); i.value = qs.get('search'); i.dispatchEvent(new Event('input')); }, 300);
  }

})();
