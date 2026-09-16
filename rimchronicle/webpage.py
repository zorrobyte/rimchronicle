"""The single-page reader. Inline CSS and JS; Google Fonts for the display and body faces."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RimChronicle</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%238a3b2a'/%3E%3Ctext x='16' y='22' font-family='Georgia,serif' font-size='18' fill='%23fffdf8' text-anchor='middle'%3ERC%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400;1,6..72,500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f3ede1;--paper:#fffdf8;--ink:#28221d;--ink-2:#4d453c;--muted:#7a7064;--rule:#dfd5c4;--rule-2:#eee7d9;
  --accent:#8a3b2a;--accent-ink:#fffdf8;--ok:#3f7a4a;--warn:#b0731e;--bad:#a63c2b;
  --shadow:0 1px 2px rgba(60,40,20,.08),0 10px 28px rgba(60,40,20,.12);
  --card:#fffdf8;--pill:#ebe3d3;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#15130e;--paper:#1d1a14;--ink:#ece4d4;--ink-2:#cfc4b2;--muted:#a1968a;--rule:#3a3429;--rule-2:#2a251c;
  --accent:#e08a5a;--accent-ink:#1a140f;--ok:#7dc48a;--warn:#e0a44a;--bad:#e0705a;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 32px rgba(0,0,0,.45);--card:#1d1a14;--pill:#2b261d;
}}
:root[data-theme="dark"]{
  --bg:#15130e;--paper:#1d1a14;--ink:#ece4d4;--ink-2:#cfc4b2;--muted:#a1968a;--rule:#3a3429;--rule-2:#2a251c;
  --accent:#e08a5a;--accent-ink:#1a140f;--ok:#7dc48a;--warn:#e0a44a;--bad:#e0705a;
  --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 32px rgba(0,0,0,.45);--card:#1d1a14;--pill:#2b261d;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Newsreader",Georgia,"Times New Roman",serif;font-size:19px;line-height:1.6;min-height:100vh}
a{color:inherit}
button{font:inherit;color:inherit}
.display{font-family:"Fraunces",Georgia,serif;font-weight:600;letter-spacing:-.005em}
.sc{font-variant:all-small-caps;letter-spacing:.14em;font-weight:500}
.muted{color:var(--muted)}
.hidden{display:none !important}

/* top bar */
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--rule)}
.top .in{max-width:1100px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{font-family:"Fraunces",Georgia,serif;font-weight:700;font-size:1.25em;text-decoration:none;display:flex;align-items:center;gap:10px}
.brand i{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--accent)}
.grow{flex:1}
.pill{display:inline-flex;align-items:center;gap:8px;background:var(--pill);border-radius:999px;padding:4px 12px;font-size:.78em;color:var(--ink-2);white-space:nowrap;max-width:55vw;overflow:hidden}
.pill span:last-child{overflow:hidden;text-overflow:ellipsis}
.pill .dot{flex:none}
.pill .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.pill.on .dot{background:var(--ok)}
.pill.busy .dot{background:var(--warn);animation:pulse 1.2s infinite}
.pill.off .dot{background:var(--bad)}
@keyframes pulse{0%,100%{opacity:.35}50%{opacity:1}}
.btn{background:transparent;border:1px solid var(--rule);border-radius:999px;padding:6px 14px;cursor:pointer;font-size:.82em;color:var(--ink-2);transition:background .15s,border-color .15s}
.btn:hover{border-color:var(--ink-2)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:500}
.btn.primary:hover{filter:brightness(1.08)}
.btn:disabled{opacity:.5;cursor:default}
.icon{width:34px;height:34px;padding:0;display:inline-grid;place-items:center;border-radius:50%}

/* library */
.wrap{max-width:1100px;margin:0 auto;padding:0 16px}
.lib-head{padding:44px 0 18px}
.lib-head h1{margin:0 0 6px;font-size:2.4em;line-height:1.1}
.lib-head p{margin:0;color:var(--muted);font-style:italic}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:22px;padding-bottom:80px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;overflow:hidden;box-shadow:var(--shadow);text-decoration:none;display:flex;flex-direction:column;transition:transform .15s,box-shadow .15s}
.card:hover{transform:translateY(-2px)}
.card .cover{aspect-ratio:4/3;background:linear-gradient(135deg,var(--rule-2),var(--rule));display:grid;place-items:center;overflow:hidden;position:relative}
.card .cover img{width:100%;height:100%;object-fit:cover;display:block}
.card .cover .empty{font-family:"Fraunces",Georgia,serif;color:var(--muted);font-style:italic}
.card .badge{position:absolute;top:10px;left:10px;background:color-mix(in srgb,var(--paper) 85%,transparent);border-radius:999px;padding:2px 10px;font-size:.72em}
.card .badge.live{color:var(--ok)}
.card .body{padding:14px 16px 16px}
.card h3{margin:0 0 4px;font-size:1.25em;line-height:1.2}
.card .sub{font-size:.82em;color:var(--muted)}
.card .last{font-size:.9em;margin-top:8px;font-style:italic;color:var(--ink-2);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty-lib{padding:60px 0;text-align:center;color:var(--muted);font-style:italic}

/* reader */
.reader{max-width:1100px;margin:0 auto;padding:0 16px 100px}
.book-head{padding:36px 0 14px;display:flex;gap:18px;align-items:flex-end;flex-wrap:wrap}
.book-head h1{margin:0;font-size:2.2em;line-height:1.05;overflow-wrap:anywhere}
.book-head>div:first-child{min-width:0;max-width:100%}
.book-head .meta{color:var(--muted);font-style:italic;font-size:.95em}
.book-head .actions{display:flex;gap:8px;flex-wrap:wrap}
.strip{position:sticky;top:57px;z-index:15;background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:8px 14px;display:flex;gap:6px 18px;align-items:center;flex-wrap:wrap;font-size:.8em;box-shadow:var(--shadow);margin:8px 0 22px}
.strip .lbl{font-variant:all-small-caps;letter-spacing:.12em;color:var(--muted)}
.strip b{font-weight:500;color:var(--ink)}
.strip .kv{display:inline-flex;gap:6px;align-items:baseline;white-space:nowrap}
.layout{display:grid;grid-template-columns:minmax(0,1fr);gap:28px}
@media (min-width:980px){.layout{grid-template-columns:230px minmax(0,1fr)}}
.toc{font-size:.84em;position:sticky;top:120px;align-self:start;max-height:calc(100vh - 140px);overflow:auto;padding-right:6px}
.toc .lbl{font-variant:all-small-caps;letter-spacing:.14em;color:var(--muted);margin-bottom:8px}
.toc a{display:block;text-decoration:none;padding:5px 8px;border-radius:6px;color:var(--ink-2);border-left:2px solid transparent;line-height:1.35}
.toc a small{display:block;color:var(--muted);font-size:.85em}
.toc a.cur{border-left-color:var(--accent);color:var(--ink);background:var(--rule-2)}
.toc a:hover{background:var(--rule-2)}
.chapter{max-width:min(65ch,100%);overflow-wrap:anywhere}
.chapter .kicker{font-variant:all-small-caps;letter-spacing:.14em;color:var(--muted);font-size:.9em;margin-bottom:4px}
.chapter h2{font-size:2em;line-height:1.12;margin:0 0 18px}
figure{margin:22px 0}
@media (min-width:980px){figure{margin:24px -8ch;width:calc(100% + 16ch)}}
figure img{width:100%;height:auto;display:block;border-radius:8px;box-shadow:var(--shadow);background:var(--rule-2)}
figcaption{font-size:.8em;color:var(--muted);text-align:center;margin-top:8px;font-style:italic}
.prose p{margin:0 0 1.05em}
.prose p:first-of-type::first-letter{font-family:"Fraunces",Georgia,serif;font-size:3.3em;float:left;line-height:.78;padding:7px 8px 0 0;color:var(--accent);font-weight:600}
.stateline{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72em;color:var(--muted);border-top:1px solid var(--rule);padding-top:10px;margin-top:22px}
.epitaph{margin:36px 0 0;padding:26px;border:1px solid var(--rule);border-radius:10px;text-align:center;font-style:italic;background:var(--paper)}
.nav{display:flex;justify-content:space-between;gap:12px;margin-top:40px;padding-top:18px;border-top:1px solid var(--rule)}
.nav a{text-decoration:none;color:var(--ink-2);max-width:48%}
.nav a .sc{display:block;font-size:.8em;color:var(--muted)}
.nav a:hover{color:var(--accent)}
.nochap{padding:40px 0;color:var(--muted);font-style:italic}

/* toast */
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);opacity:0;background:var(--ink);color:var(--bg);padding:10px 18px;border-radius:999px;font-size:.85em;box-shadow:var(--shadow);transition:all .25s;z-index:50;text-decoration:none;max-width:calc(100vw - 32px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid var(--rule);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header class="top"><div class="in">
  <a class="brand" href="#/"><i></i>RimChronicle</a>
  <span class="grow"></span>
  <span id="status" class="pill"><span class="dot"></span><span id="status-text">connecting</span></span>
  <button class="btn icon" id="theme" title="Toggle light and dark" aria-label="Toggle theme">&#9680;</button>
</div></header>

<main id="view"></main>
<a id="toast" class="toast" href="#/"></a>

<script>
(function(){
  const $ = (s, el=document) => el.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const view = $('#view');
  let status = {}, chronicles = [], current = null, currentK = null, esrc = null;

  // ---- theme
  const root = document.documentElement;
  try { const t = localStorage.getItem('rc-theme'); if (t) root.dataset.theme = t; } catch (e) {}
  const forced = new URLSearchParams(location.search).get('theme'); if (forced === 'light' || forced === 'dark') root.dataset.theme = forced;
  $('#theme').onclick = () => {
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const cur = root.dataset.theme || (dark ? 'dark' : 'light');
    root.dataset.theme = cur === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('rc-theme', root.dataset.theme); } catch (e) {}
  };

  // ---- status pill
  function renderStatus(){
    const p = $('#status'), t = $('#status-text');
    p.className = 'pill';
    if (!status || status.online === undefined) { t.textContent = 'connecting'; return; }
    if (!status.online) { p.classList.add('off'); t.textContent = 'RimBridge offline'; return; }
    const g = status.game || {};
    if (status.writing) { p.classList.add('busy'); t.textContent = 'writing a chapter'; return; }
    if (g.state !== 'playing') { t.textContent = 'game in ' + (g.state || 'menu'); return; }
    p.classList.add('on');
    const pend = status.pending_events ? ', ' + status.pending_events + ' events waiting' : '';
    t.textContent = (g.seed || 'game') + ', day ' + g.day + pend;
  }
  async function fetchStatus(){ try { status = await (await fetch('/api/status')).json(); } catch (e) { status = {online:false}; } renderStatus(); }

  // ---- data
  async function loadLibrary(){ chronicles = await (await fetch('/api/chronicles')).json(); }
  async function loadChronicle(id){ const r = await fetch('/api/chronicles/' + encodeURIComponent(id)); if (!r.ok) return null; return await r.json(); }

  // ---- router
  function route(){
    const h = location.hash || '#/';
    const m = h.match(/^#\/read\/([^\/]+)(?:\/(\d+))?/);
    if (m) renderReader(decodeURIComponent(m[1]), m[2] ? parseInt(m[2], 10) : null);
    else renderLibrary();
  }
  addEventListener('hashchange', route);

  // ---- library
  async function renderLibrary(){
    current = null; currentK = null;
    await loadLibrary();
    let html = '<div class="wrap"><div class="lib-head"><h1 class="display">The library</h1><p>Every colony the chronicler has watched, newest first.</p></div>';
    if (!chronicles.length) {
      html += '<div class="empty-lib">No chronicles yet. Start a game with RimBridge loaded and the first chapter will appear here.</div>';
    } else {
      html += '<div class="grid">';
      for (const c of chronicles) {
        const cover = c.cover ? `<img src="/api/chronicles/${encodeURIComponent(c.id)}/images/${encodeURIComponent(c.cover)}" alt="" loading="lazy">` : '<span class="empty">no picture yet</span>';
        const badge = c.live ? '<span class="badge live">live</span>' : (c.status === 'ended' ? '<span class="badge">ended</span>' : '<span class="badge">paused</span>');
        const meta = [c.scenario, c.storyteller].filter(Boolean).join(', ');
        html += `<a class="card" href="#/read/${encodeURIComponent(c.id)}">
          <div class="cover">${cover}${badge}</div>
          <div class="body"><h3 class="display">${esc(c.seed)}, ${c.days} day${c.days === 1 ? '' : 's'}</h3>
          <div class="sub">${esc(meta)}${meta ? ' &middot; ' : ''}${c.chapters} chapter${c.chapters === 1 ? '' : 's'}${c.colonists != null ? ' &middot; ' + c.colonists + ' colonist' + (c.colonists === 1 ? '' : 's') : ''}</div>
          ${c.last_title ? `<div class="last">${esc(c.last_title)}</div>` : ''}</div></a>`;
      }
      html += '</div>';
    }
    html += '</div>';
    view.innerHTML = html;
    document.title = 'RimChronicle';
  }

  // ---- reader
  function paragraphs(body){
    let lines = String(body || '').trim().split('\n');
    let state = '';
    if (lines.length && lines[lines.length - 1].trim().startsWith('State of the colony:')) { state = lines.pop().trim(); }
    const paras = lines.join('\n').split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
    return { html: paras.map(p => '<p>' + esc(p).replace(/\n/g, '<br>') + '</p>').join(''), state };
  }
  function stripHtml(c){
    const ch = c.chapters[c.chapters.length - 1];
    const s = (c.live && status.state && Object.keys(status.state).length) ? status.state : (ch ? ch.state : c.last_state) || {};
    const kv = (l, v) => v == null || v === '' ? '' : `<span class="kv"><span class="lbl">${l}</span><b>${esc(v)}</b></span>`;
    const food = s.food_days != null ? Number(s.food_days).toFixed(1) + ' d' : null;
    return `<div class="strip"><span class="lbl">State of the colony</span>
      ${kv('day', s.day)}${kv('colonists', s.colonists)}${kv('food', food)}${kv('mood', s.mood_avg != null ? Math.round(s.mood_avg) : null)}${kv('wealth', s.wealth != null ? Math.round(s.wealth).toLocaleString() : null)}${kv('threat', s.danger ? String(s.danger).toLowerCase() : null)}${kv('weather', s.weather)}${kv('season', s.season)}
      <span class="grow"></span><span class="muted">${c.status === 'ended' ? 'the record is closed' : (c.live ? 'live' : 'not the live game')}</span></div>`;
  }
  async function renderReader(id, k){
    const c = await loadChronicle(id);
    if (!c) { view.innerHTML = '<div class="wrap"><div class="empty-lib">No such chronicle.</div></div>'; return; }
    current = c;
    const n = c.chapters.length;
    if (k == null || k < 1 || k > n) k = n || 1;
    currentK = k;
    const ch = c.chapters[k - 1];
    const meta = [c.scenario, c.storyteller ? 'storyteller ' + c.storyteller : '', c.difficulty].filter(Boolean).join(', ');
    let html = `<div class="reader"><div class="book-head"><div><div class="sc muted">A RimWorld chronicle</div><h1 class="display">${esc(c.seed)}</h1>
      <div class="meta">${esc(meta)}${meta ? '. ' : ''}${c.days} days, ${n} chapter${n === 1 ? '' : 's'}${c.status === 'ended' ? ', ended' : ''}.</div></div>
      <span class="grow"></span><div class="actions">
        <button class="btn primary" id="write-now" ${(!c.live || !status.online || status.writing) ? 'disabled' : ''}>${status.writing ? '<span class="spinner"></span> Writing' : 'Write a chapter now'}</button>
        <a class="btn" href="/api/chronicles/${encodeURIComponent(c.id)}/book" download>Download book</a>
      </div></div>`;
    html += stripHtml(c);
    html += '<div class="layout"><nav class="toc"><div class="lbl">Contents</div>';
    for (const x of c.chapters) html += `<a href="#/read/${encodeURIComponent(c.id)}/${x.k}" class="${x.k === k ? 'cur' : ''}">${x.k}. ${esc(x.title)}<small>day ${x.day}</small></a>`;
    html += '</nav><div>';
    if (!ch) {
      html += '<div class="nochap">No chapters yet. The chronicler is watching; the first chapter arrives when something worth telling has happened, or when you ask for one.</div>';
    } else {
      const p = paragraphs(ch.body);
      html += `<article class="chapter"><div class="kicker">Chapter ${ch.k} &middot; day ${ch.day}, hour ${String(ch.hour).padStart(2, '0')}</div><h2 class="display">${esc(ch.title)}</h2>`;
      for (const im of ch.images || []) html += `<figure><img src="/api/chronicles/${encodeURIComponent(c.id)}/images/${encodeURIComponent(im.file)}" alt="${esc(im.caption)}"><figcaption>${esc(im.caption)}</figcaption></figure>`;
      html += `<div class="prose">${p.html}</div>`;
      if (p.state) html += `<div class="stateline">${esc(p.state)}</div>`;
      if (k === n && c.status === 'ended' && c.epitaph) html += `<div class="epitaph">${esc(c.epitaph)}</div>`;
      const prev = k > 1 ? c.chapters[k - 2] : null, next = k < n ? c.chapters[k] : null;
      html += '<div class="nav">' + (prev ? `<a href="#/read/${encodeURIComponent(c.id)}/${prev.k}"><span class="sc">Previous</span>${esc(prev.title)}</a>` : '<span></span>')
        + (next ? `<a href="#/read/${encodeURIComponent(c.id)}/${next.k}" style="text-align:right"><span class="sc">Next</span>${esc(next.title)}</a>` : '<span></span>') + '</div>';
      html += '</article>';
    }
    html += '</div></div></div>';
    view.innerHTML = html;
    document.title = (ch ? ch.title + ' | ' : '') + c.seed + ' | RimChronicle';
    scrollTo({ top: 0 });
    const wb = $('#write-now');
    if (wb) wb.onclick = async () => {
      wb.disabled = true; wb.innerHTML = '<span class="spinner"></span> Writing';
      const r = await fetch('/api/chronicles/' + encodeURIComponent(c.id) + '/write', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!j.ok) { toast(j.error || 'could not request a chapter'); wb.disabled = false; wb.textContent = 'Write a chapter now'; }
    };
  }

  // ---- live updates
  let toastTimer = null;
  function toast(text, href){
    const t = $('#toast'); t.textContent = text; t.href = href || '#'; if (!href) t.onclick = e => e.preventDefault(); else t.onclick = null;
    t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 6000);
  }
  function connect(){
    if (esrc) esrc.close();
    esrc = new EventSource('/stream');
    esrc.addEventListener('chapter', async e => {
      const d = JSON.parse(e.data);
      await fetchStatus();
      if (current && current.id === d.id) {
        const wasLast = currentK === current.chapters.length;
        if (wasLast) { location.hash = '#/read/' + encodeURIComponent(d.id) + '/' + d.k; if (location.hash.endsWith('/' + d.k)) route(); }
        else toast('Chapter ' + d.k + ' landed: ' + d.title, '#/read/' + encodeURIComponent(d.id) + '/' + d.k);
      } else if (!current) { renderLibrary(); toast('Chapter ' + d.k + ' landed: ' + d.title, '#/read/' + encodeURIComponent(d.id) + '/' + d.k); }
      else toast('Chapter ' + d.k + ' landed: ' + d.title, '#/read/' + encodeURIComponent(d.id) + '/' + d.k);
    });
    esrc.addEventListener('writing', async () => { await fetchStatus(); const wb = $('#write-now'); if (wb) { wb.disabled = true; wb.innerHTML = '<span class="spinner"></span> Writing'; } });
    esrc.addEventListener('idle', async e => { const d = JSON.parse(e.data); await fetchStatus(); const wb = $('#write-now'); if (wb && current && current.live) { wb.disabled = !status.online; wb.textContent = 'Write a chapter now'; } if (d.error) toast('The chronicler stumbled: ' + d.error); });
    esrc.addEventListener('state', e => { if (current && current.live) { status.state = JSON.parse(e.data); const old = $('.strip'); if (old) { const tmp = document.createElement('div'); tmp.innerHTML = stripHtml(current); old.replaceWith(tmp.firstElementChild); } } });
    esrc.addEventListener('chronicle', async () => { await fetchStatus(); if (!current) renderLibrary(); });
    esrc.addEventListener('bridge', fetchStatus);
    esrc.addEventListener('game', fetchStatus);
    esrc.onerror = () => { setTimeout(fetchStatus, 2000); };
  }

  fetchStatus().then(route);
  connect();
  setInterval(fetchStatus, 10000);
})();
</script>
</body>
</html>
"""
