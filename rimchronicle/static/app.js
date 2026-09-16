/* RimChronicle reader: library, reader, people, timeline, settings. One IIFE, no build step, no libraries. */
(function(){
  const $ = (s, el=document) => el.querySelector(s);
  const $$ = (s, el=document) => Array.from(el.querySelectorAll(s));
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const enc = encodeURIComponent;
  const view = $('#view');
  let status = {}, chronicles = [], voices = [], esrc = null;
  let page = 'library', current = null, currentId = null, currentK = null, routeSeq = 0;
  let tl = null, peopleTimer = null, settingsState = null;

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

  // ---- small helpers
  async function api(path, opts){
    const r = await fetch(path, opts);
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const err = new Error((j && (j.error || j.detail)) || ('HTTP ' + r.status)); err.status = r.status; err.body = j; throw err; }
    return j;
  }
  const postJson = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const putJson = (path, body) => api(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const hh = h => String(h == null ? 0 : h).padStart(2, '0');
  const voiceName = id => { const v = voices.find(x => x.id === id); return v ? v.name : (id || 'The Chronicler'); };
  const effectiveVoice = c => (c && c.voice) || status.default_voice || 'chronicler';
  const imgUrl = (id, im) => `/api/chronicles/${enc(id)}/${im.frame ? 'frames' : 'images'}/${enc(im.file)}`;
  const coverUrl = (id, file) => `/api/chronicles/${enc(id)}/${/^d\d{3}h\d{2}-/.test(file) ? 'frames' : 'images'}/${enc(file)}`;
  const lowerFirst = s => s ? s[0].toLowerCase() + s.slice(1) : s;
  const humanize = s => String(s || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
  const xOf = r => (r && r.day != null) ? Number(r.day) + (Number(r.hour) || 0) / 24 : null;
  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  async function loadVoices(){ if (!voices.length) { try { voices = await api('/api/voices'); } catch (e) { voices = []; } } return voices; }

  // ---- status pill and nav
  function renderStatus(){
    const p = $('#status'), t = $('#status-text');
    p.className = 'pill';
    if (!status || status.online === undefined) { t.textContent = 'connecting'; return; }
    if (!status.online) { p.classList.add('off'); t.textContent = 'RimBridge offline'; return; }
    const g = status.game || {};
    if (status.writing) { p.classList.add('busy'); t.textContent = 'writing a chapter'; return; }
    if (g.state !== 'playing') { t.textContent = 'game in ' + (g.state || 'menu'); return; }
    p.classList.add('on');
    const colony = status.colony || g.seed || 'game';
    const pend = status.pending_events ? ', ' + status.pending_events + ' events waiting' : '';
    t.textContent = matchMedia('(max-width:700px)').matches ? `${colony} · day ${g.day}` : colony + ', day ' + g.day + pend;
  }
  async function fetchStatus(){ try { status = await api('/api/status'); } catch (e) { status = {online:false}; } renderStatus(); }
  function renderNav(){
    const nav = $('#nav');
    if (!currentId) { nav.innerHTML = ''; }
    else {
      const id = enc(currentId);
      const link = (p, label, href) => `<a href="${href}" class="${page === p ? 'cur' : ''}">${label}</a>`;
      nav.innerHTML = link('read', 'Read', `#/read/${id}${currentK ? '/' + currentK : ''}`) + '<span class="sep">&middot;</span>'
        + link('people', 'People', `#/people/${id}`) + '<span class="sep">&middot;</span>' + link('timeline', 'Timeline', `#/timeline/${id}`);
    }
    $('#nav-settings').classList.toggle('cur', page === 'settings');
  }

  // ---- popovers and the lightbox
  let openPopEl = null;
  function closePop(){ if (openPopEl) { openPopEl.remove(); openPopEl = null; } }
  function openPop(anchor, html, right){
    if (openPopEl && openPopEl.parentElement === anchor) { closePop(); return null; }
    closePop();
    const p = document.createElement('div'); p.className = 'pop' + (right ? ' right' : ''); p.innerHTML = html;
    anchor.appendChild(p); openPopEl = p; return p;
  }
  document.addEventListener('click', e => { if (openPopEl && !openPopEl.parentElement.contains(e.target)) closePop(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { closePop(); closeLightbox(); } });
  const lightbox = $('#lightbox');
  function openLightbox(src, caption){ lightbox.querySelector('img').src = src; lightbox.querySelector('.cap').textContent = caption || ''; lightbox.classList.remove('hidden'); }
  function closeLightbox(){ lightbox.classList.add('hidden'); lightbox.querySelector('img').removeAttribute('src'); }
  lightbox.onclick = closeLightbox;
  view.addEventListener('click', e => {
    const im = e.target.closest('img[data-full]');
    if (im) { e.preventDefault(); openLightbox(im.dataset.full, im.dataset.caption || im.alt); }
  });

  // ---- data
  async function loadLibrary(){ chronicles = await api('/api/chronicles'); }
  async function loadChronicle(id){ try { return await api('/api/chronicles/' + enc(id)); } catch (e) { return null; } }

  // ---- router
  function leavePage(){
    closePop();
    view.oninput = null; view.onchange = null;
    if (tl && tl.timer) { clearInterval(tl.timer); tl.timer = null; }
    if (peopleTimer) { clearInterval(peopleTimer); peopleTimer = null; }
  }
  function route(){
    leavePage();
    const seq = ++routeSeq;
    const h = location.hash || '#/';
    let m;
    if ((m = h.match(/^#\/read\/([^\/]+)(?:\/(\d+))?/))) renderReader(decodeURIComponent(m[1]), m[2] ? parseInt(m[2], 10) : null, seq);
    else if ((m = h.match(/^#\/people\/([^\/]+)/))) renderPeople(decodeURIComponent(m[1]), seq);
    else if ((m = h.match(/^#\/timeline\/([^\/]+)/))) renderTimeline(decodeURIComponent(m[1]), seq);
    else if (h.startsWith('#/settings')) renderSettings(seq);
    else renderLibrary(seq);
  }
  addEventListener('hashchange', route);
  const stale = seq => seq !== routeSeq;

  // ---- library
  async function renderLibrary(seq){
    page = 'library'; current = null; currentId = null; currentK = null; renderNav();
    await loadLibrary();
    if (stale(seq)) return;
    let html = '<div class="wrap"><div class="lib-head"><h1 class="display">The library</h1><p>Every colony the chronicler has watched, newest first.</p></div>';
    if (!chronicles.length) {
      html += '<div class="empty-lib">No chronicles yet. Start a game with RimBridge loaded and the first chapter will appear here.</div>';
    } else {
      html += '<div class="grid">';
      for (const c of chronicles) {
        const cover = c.cover ? `<img src="${coverUrl(c.id, c.cover)}" alt="" loading="lazy">` : '<span class="empty">no picture yet</span>';
        const badge = c.live ? '<span class="badge live">live</span>' : (c.status === 'ended' ? '<span class="badge">ended</span>' : '<span class="badge">paused</span>');
        const meta = [c.faction, c.scenario, c.storyteller, c.name ? 'seed ' + c.seed : ''].filter(Boolean).join(', ');
        const counts = [plural(c.days, 'day'), plural(c.chapters, 'chapter'), c.colonists != null ? plural(c.colonists, 'colonist') : ''].filter(Boolean).join(' &middot; ');
        const tail = c.pull_quote ? `<div class="quote">${esc(c.pull_quote)}</div>` : (c.last_title ? `<div class="last">${esc(c.last_title)}</div>` : '');
        html += `<a class="card" href="#/read/${enc(c.id)}">
          <div class="cover">${cover}${badge}</div>
          <div class="body"><h3 class="display">${esc(c.title)}</h3>
          <div class="sub">${counts}</div>${meta ? `<div class="sub">${esc(meta)}</div>` : ''}
          ${tail}</div></a>`;
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
  function voiceChipHtml(c){
    const eff = effectiveVoice(c);
    return `Voice: <b>${esc(voiceName(eff))}</b>${c.voice ? '' : ' <span class="muted">(default)</span>'} <span class="caret">&#9662;</span>`;
  }
  function voiceMenuHtml(selected, opts){
    opts = opts || {};
    let h = `<div class="lbl">${esc(opts.title || 'Voice')}</div><div class="menu">`;
    if (opts.clearLabel) h += `<button class="item ${!selected ? 'cur' : ''}" data-voice=""><span class="check">${!selected ? '&#10003;' : ''}</span>${esc(opts.clearLabel)}</button>`;
    for (const v of voices) h += `<button class="item ${selected === v.id ? 'cur' : ''}" data-voice="${esc(v.id)}"><span class="check">${selected === v.id ? '&#10003;' : ''}</span>${esc(v.name)}<small>${esc(v.blurb)}</small></button>`;
    return h + '</div>';
  }
  function chapterHtml(c, ch, k, ver){
    const n = c.chapters.length;
    const shown = ver ? { title: ver.title, body: ver.body, voice: ver.voice } : ch;
    const p = paragraphs(shown.body);
    let html = `<article class="chapter" id="chapter"><div class="kicker">Chapter ${ch.k} &middot; day ${ch.day}, hour ${hh(ch.hour)} &middot; ${esc(voiceName(shown.voice))}</div><h2 class="display">${esc(shown.title)}</h2>`;
    if (ver) html += `<div class="earlier"><span>Earlier rendering by <b>${esc(voiceName(ver.voice))}</b>, read-only.</span><button class="btn small" id="back-current">Back to the current chapter</button></div>`;
    const ims = ch.images || [];
    if (ims[0]) html += `<figure><img src="${imgUrl(c.id, ims[0])}" data-full="${imgUrl(c.id, ims[0])}" data-caption="${esc(ims[0].caption)}" alt="${esc(ims[0].caption)}"><figcaption>${esc(ims[0].caption)}</figcaption></figure>`;
    if (ims.length > 1) {
      html += '<div class="photos">';
      for (const im of ims.slice(1)) html += `<figure><img src="${imgUrl(c.id, im)}" data-full="${imgUrl(c.id, im)}" data-caption="${esc(im.caption)}" alt="${esc(im.caption)}" loading="lazy"><figcaption>${esc(im.caption)}</figcaption></figure>`;
      html += '</div>';
    }
    html += `<div class="prose">${p.html}</div>`;
    if (p.state) html += `<div class="stateline">${esc(p.state)}</div>`;
    if (k === n && c.status === 'ended' && c.epitaph && !ver) html += `<div class="epitaph">${esc(c.epitaph)}</div>`;
    html += '<div class="chapter-tools">';
    html += `<span class="pop-anchor"><button class="btn small" id="rewrite-btn" ${status.writing ? 'disabled' : ''}>Rewrite as&hellip;</button></span>`;
    if (ver) html += `<span class="muted">viewing an earlier rendering</span>`;
    if ((ch.versions || []).length) {
      html += `<details class="versions"><summary>${plural(ch.versions.length, 'earlier rendering')}</summary><ul>`;
      ch.versions.forEach((v, i) => { html += `<li><button data-ver="${i}">${esc(voiceName(v.voice))}: ${esc(v.title)}</button></li>`; });
      html += '</ul></details>';
    }
    html += '</div>';
    const prev = k > 1 ? c.chapters[k - 2] : null, next = k < n ? c.chapters[k] : null;
    html += '<div class="nav">' + (prev ? `<a href="#/read/${enc(c.id)}/${prev.k}"><span class="sc">Previous</span>${esc(prev.title)}</a>` : '<span></span>')
      + (next ? `<a href="#/read/${enc(c.id)}/${next.k}" style="text-align:right"><span class="sc">Next</span>${esc(next.title)}</a>` : '<span></span>') + '</div>';
    return html + '</article>';
  }
  function wireChapter(c, ch, k){
    const rb = $('#rewrite-btn');
    if (rb) rb.onclick = e => {
      e.stopPropagation();
      const pop = openPop(rb.parentElement, voiceMenuHtml(ch.voice, { title: 'Rewrite this chapter as' }));
      if (!pop) return;
      pop.onclick = async ev => {
        const it = ev.target.closest('[data-voice]'); if (!it) return;
        const voice = it.dataset.voice; closePop();
        try {
          await postJson(`/api/chronicles/${enc(c.id)}/chapters/${ch.k}/rewrite`, { voice });
          toast('Rewriting chapter ' + ch.k + ' as ' + voiceName(voice));
          rb.disabled = true;
        } catch (err) { toast(err.message || 'could not request a rewrite'); }
      };
    };
    $$('[data-ver]').forEach(b => b.onclick = () => showVersion(c, ch, k, ch.versions[parseInt(b.dataset.ver, 10)]));
    const back = $('#back-current'); if (back) back.onclick = () => showVersion(c, ch, k, null);
  }
  function showVersion(c, ch, k, ver){
    const old = $('#chapter'); if (!old) return;
    const tmp = document.createElement('div'); tmp.innerHTML = chapterHtml(c, ch, k, ver);
    old.replaceWith(tmp.firstElementChild);
    wireChapter(c, ch, k);
    $('#chapter').scrollIntoView({ block: 'start' });
  }
  async function renderReader(id, k, seq){
    await loadVoices();
    const c = await loadChronicle(id);
    if (stale(seq)) return;
    page = 'read'; currentId = id;
    if (!c) { current = null; renderNav(); view.innerHTML = '<div class="wrap"><div class="empty-lib">No such chronicle.</div></div>'; return; }
    current = c;
    const n = c.chapters.length;
    if (k == null || k < 1 || k > n) k = n || 1;
    currentK = k; renderNav();
    const ch = c.chapters[k - 1];
    const meta = [c.faction ? 'of the faction ' + c.faction : '', c.scenario, c.storyteller ? 'storyteller ' + c.storyteller : '', c.difficulty, c.name ? 'seed ' + c.seed : ''].filter(Boolean).join(', ');
    let html = `<div class="reader"><div class="book-head"><div><div class="sc muted">A RimWorld chronicle</div><h1 class="display">${esc(c.title)}</h1>
      <div class="meta full-meta">${esc(meta)}${meta ? '. ' : ''}${c.days} days, ${plural(n, 'chapter')}${c.status === 'ended' ? ', ended' : ''}.</div>
      <div class="meta compact-meta">Day ${c.days} &middot; ${plural(n, 'chapter')}${c.storyteller ? ' &middot; ' + esc(c.storyteller) : ''}${c.status === 'ended' ? ' &middot; ended' : ''}</div>
      <div class="chips"><span class="pop-anchor"><button class="chip" id="voice-chip">${voiceChipHtml(c)}</button></span>${c.directive ? `<span class="muted" style="font-size:.8em;font-style:italic">Directive: ${esc(c.directive)}</span>` : ''}</div></div>
      <span class="grow"></span><div class="actions">
        <span class="pop-anchor"><button class="btn primary" id="write-now" ${(!c.live || !status.online || status.writing) ? 'disabled' : ''}>${status.writing ? '<span class="spinner"></span> Writing' : 'Write a chapter now'}</button></span>
        <a class="btn" href="/api/chronicles/${enc(c.id)}/book" download>Download book</a>
      </div></div>`;
    if (!c.live && status.online && status.game && status.game.state === 'playing' && status.chronicle) {
      html += `<div class="archive-note">This is an earlier book${status.colony === c.title ? ' with the same colony name' : ''}. <a href="#/read/${enc(status.chronicle)}">Open the live colony &rarr;</a></div>`;
    }
    html += stripHtml(c);
    html += '<div class="layout"><nav class="toc" aria-label="Chapters"><div class="lbl">Contents</div>';
    if (n) html += `<button type="button" class="toc-toggle" id="toc-toggle" aria-expanded="false" aria-controls="toc-list"><span class="toc-toggle-label">Chapters <b>${k} of ${n}</b></span><span class="toc-toggle-title">${esc(ch.title)}</span><span class="toc-caret" aria-hidden="true">&#9662;</span></button>`;
    html += '<div class="toc-list" id="toc-list">';
    for (const x of c.chapters) html += `<a href="#/read/${enc(c.id)}/${x.k}" class="${x.k === k ? 'cur' : ''}">${x.k}. ${esc(x.title)}<small>day ${x.day}${x.voice && x.voice !== 'chronicler' ? ' &middot; ' + esc(voiceName(x.voice)) : ''}</small></a>`;
    html += '</div></nav><div>';
    if (!ch) html += '<div class="nochap">No chapters yet. The chronicler is watching; the first chapter arrives when something worth telling has happened, or when you ask for one.</div>';
    else html += chapterHtml(c, ch, k, null);
    html += '</div></div></div>';
    view.innerHTML = html;
    document.title = (ch ? ch.title + ' | ' : '') + c.title + ' | RimChronicle';
    scrollTo({ top: 0 });
    const tocToggle = $('#toc-toggle');
    if (tocToggle) tocToggle.onclick = () => {
      const open = tocToggle.getAttribute('aria-expanded') !== 'true';
      tocToggle.setAttribute('aria-expanded', String(open));
      tocToggle.closest('.toc').classList.toggle('open', open);
    };
    if (ch) wireChapter(c, ch, k);
    // the voice chip: per-chronicle override
    const chip = $('#voice-chip');
    chip.onclick = e => {
      e.stopPropagation();
      const pop = openPop(chip.parentElement, voiceMenuHtml(c.voice || '', { title: 'Voice for this chronicle', clearLabel: 'Use the default (' + voiceName(status.default_voice || 'chronicler') + ')' }));
      if (!pop) return;
      pop.onclick = async ev => {
        const it = ev.target.closest('[data-voice]'); if (!it) return;
        closePop();
        try {
          const r = await putJson(`/api/chronicles/${enc(c.id)}/settings`, { voice: it.dataset.voice });
          c.voice = r.voice; chip.innerHTML = voiceChipHtml(c);
          toast(c.voice ? 'This chronicle now speaks as ' + voiceName(c.voice) : 'Back to the default voice');
        } catch (err) { toast(err.message || 'could not set the voice'); }
      };
    };
    // write now, with an optional focus and a voice pick
    const wb = $('#write-now');
    if (wb) wb.onclick = e => {
      e.stopPropagation();
      const eff = effectiveVoice(c);
      const opts = voices.map(v => `<option value="${esc(v.id)}" ${v.id === eff ? 'selected' : ''}>${esc(v.name)}</option>`).join('');
      const pop = openPop(wb.parentElement, `<div class="lbl">Write a chapter now</div>
        <input type="text" id="wf" placeholder="Focus on… (optional)" maxlength="300">
        <div class="row"><select id="wv">${opts}</select></div>
        <div class="row"><button class="btn primary small" id="wgo">Write</button><span class="muted">from the ledger since the last chapter</span></div>`, true);
      if (!pop) return;
      $('#wf', pop).focus();
      const go = async () => {
        const body = { voice: $('#wv', pop).value, focus: $('#wf', pop).value.trim() };
        closePop();
        wb.disabled = true; wb.innerHTML = '<span class="spinner"></span> Writing';
        try { await postJson('/api/chronicles/' + enc(c.id) + '/write', body); }
        catch (err) { toast(err.message || 'could not request a chapter'); wb.disabled = false; wb.textContent = 'Write a chapter now'; }
      };
      $('#wgo', pop).onclick = go;
      $('#wf', pop).onkeydown = ev => { if (ev.key === 'Enter') go(); };
    };
  }
  function syncBusyButtons(){
    const wb = $('#write-now');
    if (wb && current) {
      if (status.writing) { wb.disabled = true; wb.innerHTML = '<span class="spinner"></span> Writing'; }
      else { wb.disabled = !(current.live && status.online); wb.textContent = 'Write a chapter now'; }
    }
    const rb = $('#rewrite-btn'); if (rb) rb.disabled = !!status.writing;
  }

  // ---- people
  function personHtml(id, d, kind){
    const who = [d.age != null ? String(d.age) : '', d.gender ? ({ Female: 'woman', Male: 'man' }[d.gender] || String(d.gender).toLowerCase()) : ''].filter(Boolean).join(', ');
    const back = [d.childhood, d.adulthood].filter(Boolean);
    const backTxt = back.length === 2 ? `${back[0]}, then ${lowerFirst(back[1])}.` : (back[0] ? back[0] + '.' : '');
    const portrait = d.portrait ? `<img src="/api/chronicles/${enc(id)}/frames/${enc(d.portrait)}" alt="" data-full="/api/chronicles/${enc(id)}/frames/${enc(d.portrait)}" data-caption="${esc(d.name)}, day ${esc(d.portrait_day)}">` : `<span class="mono">${esc(String(d.name || '?').slice(0, 1))}</span>`;
    const mood = d.mood != null ? Math.round(Number(d.mood)) : null;
    const line = (l, v) => v ? `<div class="line"><span class="lbl">${l}</span><span>${v}</span></div>` : '';
    const sub = [who, d.room ? 'sleeps in the ' + String(d.room).toLowerCase() : '', d.job && kind === 'here' ? 'now ' + String(d.job).replace(/\.$/, '') : ''].filter(Boolean).map(esc).join(' &middot; ');
    let h = `<div class="person ${kind !== 'here' ? 'gone' : ''}"><div class="head"><div class="portrait">${portrait}</div><div><h3 class="display">${esc(d.name)}</h3><div class="sub">${sub}</div></div></div>`;
    if (kind === 'fallen') { const died = d.died || {}; h += `<div class="died">Died on day ${esc(died.day ?? '?')}${died.cause ? ', ' + esc(humanize(died.cause)) : ''}${died.text ? '. ' + esc(died.text) : ''}</div>`; }
    if (kind === 'gone') h += `<div class="died">Gone from the colony${d.absent_day != null ? ' since day ' + esc(d.absent_day) : ''}, not known dead.</div>`;
    if (backTxt) h += `<p class="back">${esc(backTxt)}</p>`;
    if ((d.traits || []).length) h += `<div class="pills">${d.traits.map(t => `<span>${esc(t)}</span>`).join('')}</div>`;
    h += line('Skills', esc(d.skills || d.top_skills || ''));
    h += line('Relations', esc((d.relations || []).join('; ')));
    if (mood != null && kind === 'here') h += line('Mood', `<span class="mood-n ${mood < 35 ? 'low' : (mood >= 65 ? 'high' : '')}">${mood}</span>${(d.thoughts || []).length ? ' &mdash; ' + esc(d.thoughts.map(t => String(t).toLowerCase()).join(', ')) : ''}`);
    h += line('Health', esc((d.health || []).join(', ')));
    if (d.weapon) h += line('Carries', esc(d.weapon));
    const arc = d.arc || [];
    if (arc.length) {
      h += '<div class="arc"><div class="lbl">So far</div><ul>';
      for (const a of arc.slice(-8).reverse()) h += `<li><small>day ${esc(a.day ?? '?')}</small><span>${esc(a.text)}</span></li>`;
      h += '</ul></div>';
    }
    return h + '</div>';
  }
  async function renderPeople(id, seq, quiet){
    const [c, p] = await Promise.all([loadChronicle(id), api('/api/chronicles/' + enc(id) + '/people').catch(() => null)]);
    if (stale(seq)) return;
    page = 'people'; currentId = id; current = null; renderNav();
    if (!c) { view.innerHTML = '<div class="wrap"><div class="empty-lib">No such chronicle.</div></div>'; return; }
    const cols = (p && p.colonists) || {}, fallen = (p && p.fallen) || {};
    const here = Object.values(cols).filter(d => !d.absent), gone = Object.values(cols).filter(d => d.absent);
    let html = `<div class="page"><div class="page-head"><div class="sc">The people of</div><h1 class="display">${esc(c.title)}</h1><p>${plural(here.length, 'colonist')}${Object.keys(fallen).length ? ', ' + plural(Object.keys(fallen).length, 'fallen') : ''}${gone.length ? ', ' + gone.length + ' gone' : ''}. Dossiers refresh with every chapter.</p></div>`;
    if (!here.length && !Object.keys(fallen).length) html += '<div class="empty-lib">Nobody on record yet. The dossiers are gathered when the first chapter is written.</div>';
    else html += '<div class="people-grid">' + here.map(d => personHtml(id, d, 'here')).join('') + '</div>';
    if (Object.keys(fallen).length) {
      html += `<h2 class="section display">The fallen</h2><div class="people-grid">` + Object.values(fallen).map(d => personHtml(id, d, 'fallen')).join('') + '</div>';
    }
    if (gone.length) html += `<h2 class="section display">Gone</h2><div class="people-grid">` + gone.map(d => personHtml(id, d, 'gone')).join('') + '</div>';
    html += '</div>';
    const y = quiet ? scrollY : 0;
    view.innerHTML = html;
    document.title = 'People of ' + c.title + ' | RimChronicle';
    scrollTo({ top: y });
    if (!peopleTimer) peopleTimer = setInterval(() => { if (page === 'people' && currentId === id) renderPeople(id, routeSeq, true); }, 60000);
  }

  // ---- timeline
  const FAMILIES = ['death', 'hostile', 'social', 'health', 'build', 'letter', 'other'];
  function evKind(r){ return String(r.event_kind || r.ev_kind || r.type || '').toLowerCase(); }
  function family(r){
    const k = evKind(r), d = r.data || {};
    const blob = (k + ' ' + (d.def || '') + ' ' + (d.category || '') + ' ' + (r.text || '')).toLowerCase();
    if (k === 'colonist_died' || k === 'colonist_downed' || k === 'pawn_died' || /\b(died|dead|death|killed|downed)\b/.test(blob)) return 'death';
    if (k === 'hostile_group' || k === 'hostile_group_gone' || k === 'incident' || k === 'manhunter' || k === 'danger' || /\b(raid|hostile|manhunter|attack|siege|infestation|mechanoid|danger|ambush|flashstorm|fire)\b/.test(blob)) return 'hostile';
    if (k === 'social' || k === 'relation' || k === 'tale' || d.initiator || d.pawns || /\b(insult|lover|marri|breakup|social fight|chatted|deep talk|romance|proposal|slight|kind words|tale)\b/.test(blob)) return 'social';
    if (k === 'health' || k === 'mental_break' || d.part || /\b(infection|plague|flu|disease|wound|surgery|sick|malaria|carcinoma|missing|mental break|broke|tantrum|binge|berserk)\b/.test(blob)) return 'health';
    if (k === 'built' || k === 'research_finished' || k === 'construction_failed' || k === 'building_lost' || /\b(built|research|construct)\b/.test(blob)) return 'build';
    if (k === 'letter' || k === 'quest' || d.quest || /\b(letter|quest)\b/.test(blob)) return 'letter';
    return 'other';
  }
  function tlDerive(){
    const items = tl.items;
    tl.frames = items.filter(r => r.kind === 'frame' && r.shot !== 'portrait' && r.file).sort((a, b) => (a.t || 0) - (b.t || 0));
    tl.events = items.filter(r => r.kind === 'event' && xOf(r) != null);
    tl.chapters = items.filter(r => r.kind === 'chapter');
    const xs = [];
    for (const r of items) { const x = xOf(r); if (x != null) xs.push(x); }
    tl.x0 = xs.length ? Math.floor(Math.min(...xs)) : 0;
    tl.x1 = xs.length ? Math.max(Math.max(...xs), tl.x0 + 1) : 1;
    if (tl.idx >= tl.frames.length) tl.idx = Math.max(0, tl.frames.length - 1);
  }
  async function tlLoadAll(){
    for (let i = 0; i < 500; i++) {
      const r = await api(`/api/chronicles/${enc(tl.id)}/timeline?since=${tl.next}&limit=2000`);
      tl.overview = r.overview;
      if (r.items.length) tl.items.push(...r.items);
      if (r.next <= tl.next || !r.items.length) { tl.next = Math.max(tl.next, r.next); break; }
      tl.next = r.next;
    }
  }
  let tlSyncTimer = null;
  function tlSyncSoon(){ if (page !== 'timeline' || !tl) return; clearTimeout(tlSyncTimer); tlSyncTimer = setTimeout(tlSync, 1200); }
  async function tlSync(){
    if (page !== 'timeline' || !tl) return;
    const before = tl.items.length, id = tl.id;
    try { await tlLoadAll(); tl.series = await api(`/api/chronicles/${enc(id)}/timeline/series`); } catch (e) { return; }
    if (page !== 'timeline' || !tl || tl.id !== id) return;
    if (tl.items.length !== before) { tlDerive(); renderSparks(); renderTicks(); renderEvList(); renderPlayerCount(); if (!tl.playing) setFrame(tl.idx); }
  }
  const cursorX = () => tl.frames.length ? xOf(tl.frames[tl.idx]) : (tl.series.length ? xOf(tl.series[tl.series.length - 1]) : tl.x1);
  const pct = x => ((x - tl.x0) / (tl.x1 - tl.x0) * 100);
  async function renderTimeline(id, seq){
    view.innerHTML = '<div class="page"><div class="page-head"><div class="sc">Timeline</div><h1 class="display"><span class="spinner"></span> Reading the record</h1></div></div>';
    const c = await loadChronicle(id);
    if (stale(seq)) return;
    page = 'timeline'; currentId = id; current = null; renderNav();
    if (!c) { view.innerHTML = '<div class="wrap"><div class="empty-lib">No such chronicle.</div></div>'; return; }
    tl = { id, items: [], next: 0, overview: null, series: [], frames: [], events: [], chapters: [], idx: 0, playing: false, speed: 1, timer: null, x0: 0, x1: 1 };
    try { await tlLoadAll(); tl.series = await api(`/api/chronicles/${enc(id)}/timeline/series`); } catch (e) { view.innerHTML = '<div class="wrap"><div class="empty-lib">The record could not be read.</div></div>'; return; }
    if (stale(seq)) return;
    tlDerive();
    tl.idx = Math.max(0, tl.frames.length - 1);
    const ov = tl.overview || {}, kinds = ov.kinds || {};
    const span = ov.first_day != null ? `days ${ov.first_day} to ${ov.last_day}` : 'no days yet';
    let html = `<div class="page"><div class="page-head"><div class="sc">Timeline</div><h1 class="display">${esc(c.title)}</h1>
      <p>${ov.records || 0} records over ${span}: ${plural(kinds.event || 0, 'event')}, ${plural(kinds.frame || 0, 'frame')}, ${plural(kinds.chapter || 0, 'chapter')}, ${plural(kinds.state || 0, 'state sample')}.</p></div>`;
    html += `<div class="player" id="player"><div class="screen"><img alt="" id="pl-img"><div class="caption"><span id="pl-label"></span><b id="pl-when"></b></div></div>
      <div class="controls">
        <button class="btn small" id="pl-prev" title="Previous frame" aria-label="Previous frame">&#9664;</button>
        <button class="btn small primary" id="pl-play" title="Play">Play</button>
        <button class="btn small" id="pl-next" title="Next frame" aria-label="Next frame">&#9654;</button>
        <span class="speed"><button data-speed="1" class="cur">1&times;</button><button data-speed="2">2&times;</button><button data-speed="4">4&times;</button></span>
        <input type="range" id="pl-range" aria-label="Time-lapse frame" min="0" max="${Math.max(0, tl.frames.length - 1)}" value="${tl.idx}" ${tl.frames.length ? '' : 'disabled'}>
        <span class="count" id="pl-count"></span>
      </div></div>`;
    html += '<div class="sparks" id="sparks"></div>';
    html += `<div class="ticks-box" id="ticks-box"><div class="lbl"><span>Events by in-game time</span><span id="ticks-note"></span></div><div class="ticks" id="ticks"></div>
      <div class="legend"><span class="death">death, downed</span><span class="hostile">hostiles, incidents</span><span class="social">social, relations, tales</span><span class="health">health, breaks</span><span class="build">built, research</span><span class="letter">letters, quests</span><span>other</span></div></div>`;
    html += '<div class="evlist" id="evlist"></div></div>';
    view.innerHTML = html;
    document.title = 'Timeline of ' + c.title + ' | RimChronicle';
    scrollTo({ top: 0 });
    // player wiring
    $('#pl-prev').onclick = () => { stopPlay(); setFrame(tl.idx - 1); };
    $('#pl-next').onclick = () => { stopPlay(); setFrame(tl.idx + 1); };
    $('#pl-play').onclick = () => tl.playing ? stopPlay() : startPlay();
    $('#pl-range').oninput = e => { stopPlay(); setFrame(parseInt(e.target.value, 10)); };
    $$('#player [data-speed]').forEach(b => b.onclick = () => { tl.speed = parseInt(b.dataset.speed, 10); $$('#player [data-speed]').forEach(x => x.classList.toggle('cur', x === b)); if (tl.playing) { stopPlay(); startPlay(); } });
    renderSparks(); renderTicks(); renderEvList(); renderPlayerCount(); setFrame(tl.idx);
  }
  function startPlay(){
    if (!tl.frames.length) return;
    if (tl.idx >= tl.frames.length - 1) tl.idx = -1;
    tl.playing = true; $('#pl-play').textContent = 'Pause';
    tl.timer = setInterval(() => { if (tl.idx >= tl.frames.length - 1) { stopPlay(); return; } setFrame(tl.idx + 1); }, 700 / tl.speed);
  }
  function stopPlay(){ if (!tl) return; tl.playing = false; if (tl.timer) clearInterval(tl.timer); tl.timer = null; const b = $('#pl-play'); if (b) b.textContent = 'Play'; }
  function renderPlayerCount(){ const c = $('#pl-count'); if (c) c.textContent = tl.frames.length ? `${tl.idx + 1} / ${tl.frames.length}` : 'no frames yet'; const r = $('#pl-range'); if (r) { r.max = Math.max(0, tl.frames.length - 1); r.disabled = !tl.frames.length; } }
  function setFrame(i){
    if (!tl) return;
    const img = $('#pl-img'); if (!img) return;
    if (!tl.frames.length) { img.removeAttribute('src'); $('#pl-label').textContent = 'No frames yet; the camera shoots the base daily and the moments as they happen.'; $('#pl-when').textContent = ''; updateCursors(); renderEvList(); return; }
    tl.idx = Math.max(0, Math.min(tl.frames.length - 1, i));
    const f = tl.frames[tl.idx];
    img.src = `/api/chronicles/${enc(tl.id)}/frames/${enc(f.file)}`;
    img.dataset.full = img.src; img.dataset.caption = f.label || '';
    $('#pl-label').textContent = (f.label || f.shot || '') + (f.shot && f.shot !== 'daily' && f.shot !== 'base' ? ' (' + f.shot + ')' : '');
    $('#pl-when').textContent = f.day != null ? `day ${f.day}, hour ${hh(f.hour)}` : '';
    const r = $('#pl-range'); if (r && parseInt(r.value, 10) !== tl.idx) r.value = tl.idx;
    renderPlayerCount();
    const nx = tl.frames[tl.idx + 1]; if (nx) { const pre = new Image(); pre.src = `/api/chronicles/${enc(tl.id)}/frames/${enc(nx.file)}`; }
    updateCursors(); renderEvList();
  }
  function updateCursors(){
    const x = cursorX(); if (x == null) return;
    const p = Math.max(0, Math.min(100, pct(x)));
    $$('.spark line.cursor').forEach(l => { l.setAttribute('x1', p * 3); l.setAttribute('x2', p * 3); });
    const cur = $('#ticks .cursor'); if (cur) cur.style.left = p + '%';
  }
  const SERIES = [['mood_avg', 'mood', v => Math.round(v)], ['food_days', 'food (days)', v => Number(v).toFixed(1)], ['wealth', 'wealth', v => Math.round(v).toLocaleString()], ['colonists', 'colonists', v => String(v)], ['threat_points', 'threat points', v => Math.round(v)]];
  function renderSparks(){
    const box = $('#sparks'); if (!box) return;
    const W = 300, H = 54, x = cursorX();
    let html = '';
    for (const [key, label, fmt] of SERIES) {
      const pts = tl.series.map(r => [xOf(r), r[key]]).filter(([px, v]) => px != null && v != null && isFinite(Number(v))).map(([px, v]) => [px, Number(v)]);
      const last = pts.length ? pts[pts.length - 1][1] : null;
      let path = '', area = '';
      if (pts.length) {
        let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
        if (hi === lo) { lo -= 1; hi += 1; }
        const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
        const X = px => (px - tl.x0) / (tl.x1 - tl.x0) * W, Y = v => H - 4 - (v - lo) / (hi - lo) * (H - 8);
        const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
        path = d; area = d + ` L${X(pts[pts.length - 1][0]).toFixed(1)} ${H} L${X(pts[0][0]).toFixed(1)} ${H} Z`;
      }
      html += `<div class="spark"><div class="lbl"><span>${label}</span><b>${last != null ? fmt(last) : '–'}</b></div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${area ? `<path class="area" d="${area}"></path><path class="line" d="${path}"></path>` : ''}${x != null ? `<line class="cursor" x1="${pct(x) * 3}" x2="${pct(x) * 3}" y1="0" y2="${H}"></line>` : ''}</svg></div>`;
    }
    box.innerHTML = html;
  }
  function renderTicks(){
    const box = $('#ticks'); if (!box) return;
    const days = Math.max(1, tl.x1 - tl.x0);
    box.style.minWidth = Math.max(100, Math.round(days * 26)) + 'px';
    let evs = tl.events, note = '';
    if (evs.length > 5000) {
      const step = Math.ceil(evs.length / 5000);
      evs = evs.filter((r, i) => i % step === 0 || family(r) === 'death');
      note = `showing ${evs.length} of ${tl.events.length} events`;
    } else note = plural(evs.length, 'event');
    $('#ticks-note').textContent = note;
    const parts = ['<div class="axis"></div>'];
    const boxW = Math.max(box.clientWidth || 0, Math.round(days * 26));
    const every = Math.max(1, Math.ceil(days / Math.max(1, boxW / 64)));
    for (let d = Math.ceil(tl.x0); d <= Math.floor(tl.x1); d += every) parts.push(`<span class="daylbl" style="left:${pct(d)}%">d${d}</span>`);
    evs.forEach(r => { parts.push(`<span class="tick ${family(r)}" style="left:${pct(xOf(r)).toFixed(3)}%" data-i="${r.i}" title="${esc(tickText(r))}"></span>`); });
    for (const ch of tl.chapters) { const x = xOf(ch); if (x != null) parts.push(`<a class="flag" href="#/read/${enc(tl.id)}/${ch.k}" title="${esc('Chapter ' + ch.k + ': ' + (ch.title || ''))}" style="left:${pct(x)}%">${ch.k}</a>`); }
    parts.push('<div class="cursor"></div>');
    box.innerHTML = parts.join('');
    const tip = document.createElement('div'); tip.className = 'tip hidden'; box.appendChild(tip);
    const show = t => { const r = tl.events.find(e => e.i === parseInt(t.dataset.i, 10)); if (!r) return; tip.textContent = tickText(r); tip.style.left = t.style.left; tip.classList.remove('hidden'); };
    box.onmouseover = e => { const t = e.target.closest('.tick'); if (t) show(t); };
    box.onmouseout = e => { if (e.target.closest('.tick')) tip.classList.add('hidden'); };
    box.onclick = e => {
      const t = e.target.closest('.tick'); if (!t) return;
      show(t);
      const r = tl.events.find(x => x.i === parseInt(t.dataset.i, 10)); if (!r || !tl.frames.length) return;
      stopPlay();
      const x = xOf(r); let best = 0, bd = Infinity;
      tl.frames.forEach((f, i) => { const fx = xOf(f); if (fx == null) return; const d = Math.abs(fx - x); if (d < bd) { bd = d; best = i; } });
      setFrame(best);
    };
    updateCursors();
  }
  function tickText(r){ return `day ${r.day}, ${hh(r.hour)}h: ${r.text || evKind(r) || 'event'}`; }
  function renderEvList(){
    const box = $('#evlist'); if (!box) return;
    const x = cursorX();
    const rows = tl.events.concat(tl.chapters.filter(c => xOf(c) != null));
    if (!rows.length) { box.innerHTML = '<div class="ev"><span class="text muted">No events recorded yet.</span></div>'; return; }
    const ranked = rows.map(r => ({ r, d: x == null ? 0 : Math.abs(xOf(r) - x) })).sort((a, b) => a.d - b.d).slice(0, 40).map(o => o.r);
    ranked.sort((a, b) => (xOf(a) - xOf(b)) || ((a.t || 0) - (b.t || 0)));
    let nearest = null, bd = Infinity;
    for (const r of ranked) { const d = Math.abs(xOf(r) - (x == null ? 0 : x)); if (d < bd) { bd = d; nearest = r; } }
    box.innerHTML = ranked.map(r => {
      if (r.kind === 'chapter') return `<div class="ev ${r === nearest ? 'near' : ''}"><span class="when">d${r.day} ${hh(r.hour)}h</span><span class="k" style="background:var(--accent)"></span><span class="text"><a href="#/read/${enc(tl.id)}/${r.k}">Chapter ${r.k}: ${esc(r.title || '')}</a>${r.voice ? ' <span class="muted">(' + esc(voiceName(r.voice)) + ')</span>' : ''}</span></div>`;
      return `<div class="ev ${r === nearest ? 'near' : ''}"><span class="when">d${r.day} ${hh(r.hour)}h</span><span class="k ${family(r)}"></span><span class="text">${esc(r.text || evKind(r) || 'event')}</span></div>`;
    }).join('');
    const near = box.querySelector('.near'); if (near) box.scrollTop = Math.max(0, near.offsetTop - box.clientHeight / 2);
  }

  // ---- settings
  const NUM = new Set(['min_events', 'max_hours_between', 'min_real_seconds_between', 'base_width_cells', 'event_width_cells', 'moments_per_chapter', 'max_per_chapter', 'debounce_seconds', 'moment_width_cells', 'portrait_width_cells', 'frame_max_px', 'max_tokens', 'temperature', 'timeout_s', 'poll_seconds']);
  function field(section, key, label, type, extra){
    const path = section + '.' + key, v = settingsState.orig[section] ? settingsState.orig[section][key] : '';
    extra = extra || {};
    if (type === 'check') return `<label class="field check"><input type="checkbox" data-path="${path}" ${v ? 'checked' : ''}><span>${label}</span></label>`;
    if (type === 'textarea') return `<div class="field wide"><label for="f-${path}">${label}</label><textarea id="f-${path}" data-path="${path}" placeholder="${esc(extra.placeholder || '')}">${esc(v == null ? '' : v)}</textarea>${extra.hint ? `<small>${extra.hint}</small>` : ''}</div>`;
    const attrs = type === 'number' ? `type="number" ${extra.step ? 'step="' + extra.step + '"' : ''} ${extra.min != null ? 'min="' + extra.min + '"' : ''}` : `type="${type}"`;
    return `<div class="field ${extra.wide ? 'wide' : ''}"><label for="f-${path}">${label}</label><input id="f-${path}" ${attrs} data-path="${path}" value="${esc(v == null ? '' : v)}" ${extra.placeholder ? 'placeholder="' + esc(extra.placeholder) + '"' : ''} ${type === 'password' ? 'autocomplete="new-password"' : ''}>${extra.hint ? `<small>${extra.hint}</small>` : ''}</div>`;
  }
  function readForm(){
    const out = {};
    for (const el of $$('[data-path]', view)) {
      const [section, key] = el.dataset.path.split('.');
      let v;
      if (el.type === 'checkbox') v = el.checked;
      else if (el.type === 'radio') { if (!el.checked) continue; v = el.value; }
      else if (el.type === 'number' || NUM.has(key)) { v = el.value === '' ? null : Number(el.value); if (v != null && !isFinite(v)) v = null; }
      else v = el.value;
      (out[section] = out[section] || {})[key] = v;
    }
    return out;
  }
  function diffForm(){
    const cur = readForm(), orig = settingsState.orig, patch = {};
    for (const section in cur) for (const key in cur[section]) {
      const v = cur[section][key], o = orig[section] ? orig[section][key] : undefined;
      if (v == null) continue;
      if (key === 'api_key' && (v === '' || String(v).startsWith('••'))) continue;
      if (typeof o === 'number' && typeof v === 'number' ? Math.abs(o - v) < 1e-9 : String(o ?? '') === String(v)) continue;
      (patch[section] = patch[section] || {})[key] = v;
    }
    return patch;
  }
  function updateDirty(){
    const dirty = Object.keys(diffForm()).length > 0;
    const b = $('#save-btn'), d = $('#dirty'); if (b) b.disabled = !dirty; if (d) d.textContent = dirty ? 'unsaved changes' : '';
    const custom = $('#custom-wrap'); if (custom) custom.classList.toggle('hidden', (readForm().narrator || {}).voice !== 'custom');
    $$('.voice-card', view).forEach(c => c.classList.toggle('cur', c.querySelector('input').checked));
  }
  async function renderSettings(seq){
    page = 'settings'; current = null; renderNav();
    let s;
    try { s = await api('/api/settings'); } catch (e) { view.innerHTML = '<div class="wrap"><div class="empty-lib">Settings could not be read.</div></div>'; return; }
    if (stale(seq)) return;
    voices = s.voices || voices;
    settingsState = { orig: s };
    const n = s.narrator || {}, cam = s.camera || {}, llm = s.llm || {}, ov = s.overseer || {};
    let html = `<div class="page settings"><div class="page-head"><div class="sc">RimChronicle</div><h1 class="display">Settings</h1><p>Written to config.local.yaml and applied immediately; the next chapter uses them.</p></div>`;
    html += `<section class="section"><h2 class="display">Voice</h2><p class="blurb">Who tells the story. A chronicle can override this from its reader page.</p><div class="voice-cards">`;
    for (const v of (s.voices || [])) {
      html += `<label class="voice-card ${n.voice === v.id ? 'cur' : ''}"><input type="radio" name="voice" data-path="narrator.voice" value="${esc(v.id)}" ${n.voice === v.id ? 'checked' : ''}>
        <div class="name">${esc(v.name)}${v.first_person ? '<span class="tag">first person</span>' : ''}</div><div class="blurb">${esc(v.blurb)}</div><div class="sample">${esc(v.sample)}</div><button type="button" class="sample-toggle" aria-expanded="false">Read full sample</button></label>`;
    }
    html += `</div><div id="custom-wrap" class="${n.voice === 'custom' ? '' : 'hidden'}" style="margin-top:12px"><div class="fields">${field('narrator', 'custom_prompt', 'Custom system prompt', 'textarea', { placeholder: 'You are…', hint: 'Used by the Custom voice. The core rules (no invented events, the state line, the JSON shape) are appended.' })}</div></div></section>`;
    html += `<section class="section"><h2 class="display">Directive</h2><p class="blurb">An author's note appended to every chapter prompt, in any voice.</p><div class="fields">${field('narrator', 'directive', 'Directive', 'textarea', { placeholder: 'e.g. Dwell on the animals. Never mention the weather twice.' })}</div></section>`;
    html += `<section class="section"><h2 class="display">Cadence</h2><p class="blurb">When a chapter is due. Deaths, raids, arrivals and departures always earn one.</p><div class="fields">
      ${field('narrator', 'min_events', 'Events a finished day needs', 'number', { min: 0 })}${field('narrator', 'max_hours_between', 'In-game hours between chapters at most', 'number', { min: 1 })}
      ${field('narrator', 'min_real_seconds_between', 'Real seconds between chapters at least', 'number', { min: 0 })}${field('narrator', 'opening_chapter', 'Write an opening chapter when a colony is first seen', 'check')}</div></section>`;
    html += `<section class="section"><h2 class="display">Pictures</h2><p class="blurb">The off-screen camera. The player's view never moves.</p><div class="fields">
      ${field('camera', 'moments', 'Shoot the cell where a notable event happens', 'check')}${field('camera', 'follow_fight', 'Follow-up frames on the hostiles during a raid', 'check')}
      ${field('camera', 'daily', 'One wide shot of the base per in-game day', 'check')}${field('camera', 'portraits', 'A portrait of each colonist once per day', 'check')}
      ${field('camera', 'max_per_chapter', 'Moments per chapter at most', 'number', { min: 0 })}${field('camera', 'debounce_seconds', 'Seconds between moments of one kind', 'number', { min: 0 })}
      ${field('camera', 'moment_width_cells', 'Moment width (cells)', 'number', { min: 8 })}${field('camera', 'portrait_width_cells', 'Portrait width (cells)', 'number', { min: 4 })}
      ${field('camera', 'frame_max_px', 'Frame width at most (px)', 'number', { min: 256 })}${field('narrator', 'moments_per_chapter', 'Moments attached to a chapter', 'number', { min: 0 })}
      ${field('narrator', 'base_width_cells', 'Wide shot width (cells)', 'number', { min: 10 })}${field('narrator', 'event_width_cells', 'Event shot width (cells)', 'number', { min: 8 })}</div></section>`;
    html += `<section class="section"><h2 class="display">Model</h2><p class="blurb">Any OpenAI-compatible chat endpoint with vision.</p><div class="fields">
      ${field('llm', 'base_url', 'Base URL', 'text', { wide: true, placeholder: 'http://127.0.0.1:8000/v1' })}${field('llm', 'model', 'Model', 'text', { wide: true })}
      ${field('llm', 'api_key', 'API key', 'password', { hint: 'Leave as shown to keep the current key.' })}${field('llm', 'max_tokens', 'Completion budget (tokens)', 'number', { min: 100 })}
      ${field('llm', 'temperature', 'Temperature', 'number', { step: 0.05, min: 0 })}${field('llm', 'timeout_s', 'Timeout (s)', 'number', { min: 5 })}
      ${field('llm', 'disable_thinking', 'Disable thinking (Qwen-style servers)', 'check')}</div>
      <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="btn small" id="test-llm">Test connection</button><span class="muted" style="font-size:.8em">Sends one tiny prompt with the values above.</span></div><div class="testresult" id="test-result"></div></section>`;
    html += `<section class="section"><h2 class="display">Overseer</h2><p class="blurb">Optional: quote an AI agent's step notes from its dashboard. Never depended on.</p><div class="fields">
      ${field('overseer', 'enabled', 'Quote the overseer when reachable', 'check')}${field('overseer', 'url', 'Dashboard URL', 'text')}${field('overseer', 'poll_seconds', 'Poll every (s)', 'number', { min: 1 })}</div></section>`;
    html += `<section class="section"><h2 class="display">Storage</h2><p class="blurb">Read-only. Set RIMCHRONICLE_HOME to run against another project directory.</p>
      <div class="kv-list"><span>project</span><span>${esc(s.root || '')}</span><span>chronicles</span><span>${esc(s.storage || '')}</span></div></section>`;
    html += `<div class="savebar"><button class="btn primary" id="save-btn" disabled>Save</button><span class="dirty" id="dirty"></span><span class="grow"></span><span class="muted">Only the changed keys are written.</span></div></div>`;
    view.innerHTML = html;
    document.title = 'Settings | RimChronicle';
    scrollTo({ top: 0 });
    $$('.sample-toggle', view).forEach(b => b.onclick = e => {
      e.preventDefault(); e.stopPropagation();
      const expanded = b.getAttribute('aria-expanded') !== 'true';
      b.setAttribute('aria-expanded', String(expanded));
      b.closest('.voice-card').classList.toggle('expanded', expanded);
      b.textContent = expanded ? 'Show less' : 'Read full sample';
    });
    view.oninput = updateDirty; view.onchange = updateDirty;
    $('#save-btn').onclick = async () => {
      const patch = diffForm();
      if (!Object.keys(patch).length) return;
      const b = $('#save-btn'); b.disabled = true; b.innerHTML = '<span class="spinner"></span> Saving';
      try {
        await putJson('/api/settings', patch);
        toast('Settings saved; they apply from the next chapter.');
        await fetchStatus();
        renderSettings(++routeSeq);
      } catch (err) { toast(err.message || 'could not save'); b.disabled = false; b.textContent = 'Save'; }
    };
    $('#test-llm').onclick = async () => {
      const out = $('#test-result'), b = $('#test-llm');
      out.className = 'testresult'; out.innerHTML = '<span class="spinner"></span> asking the model';
      b.disabled = true;
      try {
        const r = await postJson('/api/settings/test-llm', { llm: (readForm().llm || {}) });
        out.className = 'testresult ' + (r.ok ? 'ok' : 'bad');
        out.textContent = r.ok ? `ok: ${r.model} answered in ${r.elapsed}s${r.reply ? ' ("' + r.reply + '")' : ''}` : `failed after ${r.elapsed}s: ${r.error}`;
      } catch (err) { out.className = 'testresult bad'; out.textContent = err.message || 'request failed'; }
      b.disabled = false;
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
      const href = '#/read/' + enc(d.id) + '/' + d.k;
      const msg = d.rewrite ? 'Chapter ' + d.k + ' rewritten as ' + voiceName(d.voice) + ': ' + d.title : 'Chapter ' + d.k + ' landed: ' + d.title;
      if (page === 'read' && current && current.id === d.id) {
        if (d.rewrite) { if (currentK === d.k) route(); else toast(msg, href); }
        else if (currentK === current.chapters.length) { location.hash = href; if (location.hash.endsWith('/' + d.k)) route(); }
        else toast(msg, href);
      } else if (page === 'library') { renderLibrary(++routeSeq); toast(msg, href); }
      else if (page === 'people' && currentId === d.id) { renderPeople(d.id, routeSeq, true); toast(msg, href); }
      else if (page === 'timeline' && currentId === d.id) { tlSyncSoon(); toast(msg, href); }
      else toast(msg, href);
    });
    esrc.addEventListener('writing', async () => { await fetchStatus(); status.writing = true; renderStatus(); syncBusyButtons(); });
    esrc.addEventListener('idle', async e => { const d = JSON.parse(e.data); await fetchStatus(); syncBusyButtons(); if (d.error) toast('The chronicler stumbled: ' + d.error); });
    esrc.addEventListener('state', e => {
      if (page === 'read' && current && current.live) { status.state = JSON.parse(e.data); const old = $('.strip'); if (old) { const tmp = document.createElement('div'); tmp.innerHTML = stripHtml(current); old.replaceWith(tmp.firstElementChild); } }
      if (page === 'timeline' && status.chronicle === currentId) tlSyncSoon();
    });
    esrc.addEventListener('frame', e => { const d = JSON.parse(e.data); if (page === 'timeline' && d.id === currentId) tlSyncSoon(); });
    esrc.addEventListener('event', () => { if (page === 'timeline' && status.chronicle === currentId) tlSyncSoon(); });
    esrc.addEventListener('error', e => { try { const d = JSON.parse(e.data); if (d && d.text) toast('The chronicler stumbled: ' + d.text); } catch (x) {} });
    esrc.addEventListener('chronicle', async () => { await fetchStatus(); if (page === 'library') renderLibrary(++routeSeq); });
    esrc.addEventListener('bridge', fetchStatus);
    esrc.addEventListener('game', fetchStatus);
    esrc.onerror = () => { setTimeout(fetchStatus, 2000); };
  }

  fetchStatus().then(() => loadVoices()).then(route);
  connect();
  setInterval(fetchStatus, 10000);
})();
