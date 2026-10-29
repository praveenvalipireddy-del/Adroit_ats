// Job Hunt Autofill - side panel. Talks to the Job Hunt server with the connection key and drives filler.js in the page.
const $ = (id) => document.getElementById(id);
const st = { server: '', key: '', user: null, consultants: [], cid: null, profile: null, job: null, tabId: null, lastFill: null };

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const show = (view) => ['connect', 'main', 'settings'].forEach((v) => { $('view-' + v).hidden = v !== view; });

async function store(obj) { await chrome.storage.local.set(obj); }

async function api(path, opts = {}) {
  const res = await fetch(st.server + path, {
    method: opts.method || 'GET',
    headers: { Authorization: 'Bearer ' + st.key, ...(opts.body ? { 'Content-Type': 'application/json' } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { const e = new Error(data.error || 'Not connected'); e.auth = true; throw e; }
  if (!res.ok) throw new Error(data.error || `Job Hunt error ${res.status}`);
  return data;
}

// ------------------------------------------------------------- connect
async function connect(server, key) {
  st.server = (server || '').trim().replace(/\/+$/, '');
  st.key = (key || '').trim();
  const me = await api('/api/ext/me');
  st.user = me.user;
  st.consultants = me.consultants || [];
  await store({ server: st.server, key: st.key });
  return me;
}

async function start() {
  if (!$('in-server').value && typeof JH_DEFAULT_SERVER !== 'undefined') $('in-server').value = JH_DEFAULT_SERVER;
  const saved = await chrome.storage.local.get(['server', 'key', 'cid']);
  $('in-server').value = saved.server || (typeof JH_DEFAULT_SERVER !== 'undefined' ? JH_DEFAULT_SERVER : '');
  if (!saved.key) { show('connect'); return; }
  try {
    await connect(saved.server, saved.key);
  } catch (e) {
    show('connect');
    $('connect-msg').textContent = e.auth ? 'Your connection key has expired. Copy a new one from Job Hunt.' : `Couldn't reach Job Hunt: ${e.message}`;
    return;
  }
  st.cid = st.consultants.some((c) => c.id === saved.cid) ? saved.cid : (st.consultants[0] || {}).id || null;
  renderMain();
  readPage();
}

$('btn-connect').addEventListener('click', async () => {
  const btn = $('btn-connect');
  btn.disabled = true;
  $('connect-msg').textContent = '';
  try {
    await connect($('in-server').value, $('in-key').value);
    st.cid = (st.consultants[0] || {}).id || null;
    renderMain();
    readPage();
  } catch (e) {
    $('connect-msg').textContent = e.auth ? 'That key is not valid. Copy it again from Job Hunt.' : `Couldn't reach Job Hunt: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------- main view
function renderMain() {
  show('main');
  const sel = $('sel-consultant');
  if (!st.consultants.length) {
    sel.innerHTML = '<option value="">No consultants yet - add one in Job Hunt</option>';
    $('btn-autofill').disabled = true;
  } else {
    sel.innerHTML = st.consultants.map((c) => `<option value="${c.id}" ${c.id === st.cid ? 'selected' : ''}>${esc(c.name)}${c.title ? ' - ' + esc(c.title) : ''}</option>`).join('');
  }
  loadProfile();
}

$('sel-consultant').addEventListener('change', async (e) => {
  st.cid = parseInt(e.target.value, 10) || null;
  await store({ cid: st.cid });
  resetTailor();
  $('match-box').hidden = true;
  $('result-box').hidden = true;
  $('applied-card').hidden = true;
  loadProfile();
});

async function loadProfile() {
  st.profile = null;
  $('info-box').innerHTML = '';
  if (!st.cid) return;
  try {
    const d = await api(`/api/ext/consultants/${st.cid}`);
    st.profile = d;
    const p = d.profile;
    $('resume-name').textContent = p.resume_filename || 'No resume on file - upload one in Job Hunt';
    const rows = [['Name', p.full_name], ['Email', p.email], ['Phone', p.phone], ['LinkedIn', p.linkedin_url], ['Location', p.location],
      ['Work authorization', p.visa_status], ['Authorized to work', p.work_authorized], ['Needs sponsorship', p.needs_sponsorship],
      ['Experience', p.years_experience ? p.years_experience + ' years' : ''], ['Rate / salary', p.expected_salary], ['Notice period', p.notice_period], ['Current CTC', p.current_ctc], ['F2F interview', p.f2f_ready],
      ['Relocate', p.willing_to_relocate], ['Degree', [p.highest_degree, p.field_of_study].filter(Boolean).join(', ')], ['University', p.university]];
    $('info-box').innerHTML = rows.map(([k, v]) => `<div class="kv"><span>${k}</span><b>${esc(v || '—')}</b></div>`).join('') +
      (d.missing && d.missing.length ? `<p class="warn">Not filled in Job Hunt yet: ${esc(d.missing.join(', '))}. Those questions will be left for you.</p>` : '') +
      `<button class="btn wide" id="btn-edit-info">Edit in Job Hunt ↗</button>`;
    $('btn-edit-info').addEventListener('click', () => chrome.tabs.create({ url: d.edit_url }));
  } catch (e) {
    if (e.auth) return start();
    $('info-box').innerHTML = `<p class="warn">${esc(e.message)}</p>`;
  }
}

for (const [row, box] of [['row-info', 'info-box'], ['row-resume', 'resume-box']]) {
  $(row).addEventListener('click', () => { $(box).hidden = !$(box).hidden; $(row).classList.toggle('open', !$(box).hidden); });
}

// ------------------------------------------------------------- the page
async function targetTab() {
  const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (active && /^https?:/.test(active.url || '')) return active;
  // the panel opened as a normal tab (or a non-web page is active): use the most recent web page tab
  const tabs = (await chrome.tabs.query({ lastFocusedWindow: true })).filter((t) => /^https?:/.test(t.url || ''));
  tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  return tabs[0] || null;
}

async function inject(tabId, allFrames) {
  await chrome.scripting.executeScript({ target: { tabId, allFrames }, files: ['filler.js'] });
}

async function readPage() {
  const tab = await targetTab();
  if (!tab) { renderJob(null, 'Open a job page'); return; }
  const prev = st.job;
  try {
    await inject(tab.id, false);
    const [r] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => window.JH.readJob() });
    let job = r && r.result;
    // an application form page (e.g. Lever /apply) shows less of the job: keep what we read on the job page
    if (prev && st.tabId === tab.id && job && (job.isApplyStep || (job.description || '').length < 300) && new URL(prev.url).host === new URL(job.url).host) {
      job = { ...prev, url: job.url };
    }
    st.tabId = tab.id;
    if (!prev || !job || prev.title !== job.title || prev.company !== job.company) resetTailor();   // another job: its own resume
    st.job = job;
    renderJob(job);
  } catch (e) {
    st.job = null;
    renderJob(null, 'Job Hunt can\'t read this page');
  }
}

function renderJob(job, emptyText) {
  $('btn-save').classList.remove('on');
  if (!job || !job.title) {
    $('job-logo').textContent = '?';
    $('job-company').textContent = emptyText || 'Open a job page';
    $('job-title').textContent = '';
    $('job-chips').innerHTML = '';
    return;
  }
  const initials = (job.company || job.title).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  $('job-logo').textContent = initials || '?';
  $('job-company').textContent = job.company || '';
  $('job-title').textContent = job.title;
  let host = '';
  try { host = new URL(job.url).hostname.replace(/^www\./, ''); } catch (e) { /* ignore */ }
  $('job-chips').innerHTML = [job.location, host].filter(Boolean).map((c) => `<span class="chip">${esc(c)}</span>`).join('');
}

chrome.tabs.onActivated.addListener(() => { $('result-box').hidden = true; $('applied-card').hidden = true; $('match-box').hidden = true; readPage(); });
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === 'complete') readPage(); });

// ------------------------------------------------------------- match / save
$('btn-match').addEventListener('click', async () => {
  const box = $('match-box');
  box.hidden = false;
  if (!st.job || !st.cid) { box.innerHTML = '<p class="warn">Open a job page and pick a consultant.</p>'; return; }
  box.innerHTML = '<p class="hint">Checking the match…</p>';
  try {
    const m = await api('/api/ext/match', { method: 'POST', body: { candidate_id: st.cid, job: st.job } });
    if (m.score == null) { box.innerHTML = `<p class="warn">${esc(m.note)}</p>`; return; }
    const col = m.score >= 75 ? 'var(--green)' : m.score >= 50 ? 'var(--blue)' : 'var(--amber)';
    const word = m.score >= 75 ? 'STRONG MATCH' : m.score >= 50 ? 'GOOD MATCH' : 'STRETCH';
    box.innerHTML = `<div class="match"><div class="ring" style="background:conic-gradient(${col} ${m.score}%, #e5e7eb 0)"><span>${m.score}%</span></div>
      <div><b style="color:${col};font-size:12px">${word}</b><div class="hint" style="text-align:left;margin:2px 0">${esc(m.note || '')}</div></div></div>
      ${m.matched && m.matched.length ? `<div class="tags">${m.matched.map((t) => `<span class="tag ok">✓ ${esc(t)}</span>`).join('')}</div>` : ''}
      ${m.missing && m.missing.length ? `<div class="tags">${m.missing.map((t) => `<span class="tag no">✗ ${esc(t)}</span>`).join('')}</div>` : ''}
      <button class="btn wide" id="btn-tailor">✨ Tailor resume for this job</button>`;
    $('btn-tailor').addEventListener('click', tailorAnalyze);
  } catch (e) {
    box.innerHTML = `<p class="warn">${esc(e.message)}</p>`;
  }
});

// ------------------------------------------------------------- tailor the resume for this job
// Same steps as Job Hunt: the match + suggested changes (ticked when the resume shows them) -> only the ticked
// changes are made -> a preview of what changed -> Autofill attaches that tailored resume (or the original).
function resetTailor() {
  st.tailored = null;
  st.tailorReview = null;
  $('tailor-box').hidden = true;
  $('tailor-box').innerHTML = '';
  if (st.profile) $('resume-name').textContent = st.profile.profile.resume_filename || 'No resume on file - upload one in Job Hunt';
}

function tailorChecked() { return [...document.querySelectorAll('#tailor-box input[data-ch]:checked')].map((i) => i.dataset.ch); }

function tailorProjected() {
  const r = st.tailorReview || { items: [], initial: 0, target: 0 };
  const on = new Set(tailorChecked());
  const counted = r.items.filter((i) => i.kind !== 'missing');
  const share = counted.length ? counted.filter((i) => on.has(i.id)).length / counted.length : 0;
  const extra = r.items.filter((i) => i.kind === 'missing' && on.has(i.id)).length * 3;
  return Math.min(98, Math.round(r.initial + (r.target - r.initial) * share + extra));
}

function tailorChanged() {
  const n = tailorChecked().length;
  $('tl-proj').textContent = n ? `about ${tailorProjected()}%` : 'no change';
  $('btn-tl-apply').textContent = n ? `Apply ${n} change${n === 1 ? '' : 's'}` : 'Tick a change first';
  $('btn-tl-apply').disabled = !n;
}

async function tailorAnalyze() {
  const box = $('tailor-box');
  box.hidden = false;
  if (!st.job || !st.cid) { box.innerHTML = '<p class="warn">Open a job page and pick a consultant.</p>'; return; }
  box.innerHTML = '<p class="hint">Checking what to change…</p>';
  try {
    const d = await api('/api/ext/tailor', { method: 'POST', body: { candidate_id: st.cid, job: st.job, mode: 'analyze' } });
    st.tailorReview = d.review;
    const why = (i) => i.kind === 'align' ? 'Rewords his summary and latest project - no new facts.'
      : i.kind === 'safe' ? `In his resume: "${esc(i.evidence)}"`
      : i.kind === 'unproven' ? "His resume doesn't say it - tick only if it's true."
      : 'Not in his resume - only if he really has it (Skills only).';
    const item = (i) => `<label class="tl-item"><input type="checkbox" data-ch="${esc(i.id)}"${i.checked ? ' checked' : ''}><span>${esc(i.label)}<small>${why(i)}</small></span></label>`;
    const group = (title, color, list) => list.length ? `<div class="tl-group" style="color:${color}">${title}</div>${list.map(item).join('')}` : '';
    const items = d.review.items;
    box.innerHTML = `<b>Tailor the resume</b> <span class="hint">- only the ticked changes are made</span>
      ${group('Backed by his resume', 'var(--green)', items.filter((i) => i.kind === 'align' || i.kind === 'safe'))}
      ${group('Not stated in his resume', 'var(--amber)', items.filter((i) => i.kind === 'unproven'))}
      ${group('Not in the resume', 'var(--red)', items.filter((i) => i.kind === 'missing'))}
      <div class="tl-proj">Match ${esc(d.review.initial)}% → <b id="tl-proj"></b></div>
      <div class="row"><button class="btn primary" id="btn-tl-apply">Apply</button><button class="btn" id="btn-tl-cancel">Keep the original</button></div>`;
    box.querySelectorAll('input[data-ch]').forEach((i) => i.addEventListener('change', tailorChanged));
    $('btn-tl-apply').addEventListener('click', tailorApply);
    $('btn-tl-cancel').addEventListener('click', resetTailor);
    tailorChanged();
  } catch (e) {
    box.innerHTML = `<p class="warn">${esc(e.message)}</p>`;
  }
}

async function tailorApply() {
  const ids = tailorChecked();
  if (!ids.length) return;
  const btn = $('btn-tl-apply');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Making the changes…';
  try {
    const d = await api('/api/ext/tailor', { method: 'POST', body: { candidate_id: st.cid, job: st.job, mode: 'apply', approved: ids } });
    const box = $('tailor-box');
    if (!d.changed) { box.innerHTML = `<p class="warn">${esc(d.reason)}</p><p class="hint">Autofill attaches the original resume.</p>`; return; }
    st.tailored = { filename: d.filename, mime: d.mime, base64: d.base64 };
    $('resume-name').textContent = `${d.filename} (tailored for this job)`;
    const list = d.changes.map((c) => `<li>${c.before ? `<s>${esc(c.before)}</s><br>` : ''}<span>${esc(c.after)}</span></li>`).join('');
    box.innerHTML = `<b style="color:var(--green)">✓ ${d.changes.length} change${d.changes.length === 1 ? '' : 's'} made</b>
      <p class="hint">Autofill attaches <b>${esc(d.filename)}</b> (saved in Job Hunt).</p><ul class="tl-changes">${list}</ul>
      <button class="link-btn" id="btn-tl-orig">Attach the original resume instead</button>`;
    $('btn-tl-orig').addEventListener('click', resetTailor);
  } catch (e) {
    flash(e.message, true);
    btn.disabled = false;
    tailorChanged();
  }
}

$('btn-save').addEventListener('click', async () => {
  if (!st.job || !st.cid) return;
  try {
    await api('/api/ext/save-job', { method: 'POST', body: { candidate_id: st.cid, job: st.job } });
    $('btn-save').classList.add('on');
    $('btn-save').title = 'Saved to Job Hunt';
    flash('Saved to the consultant\'s Applications.');
  } catch (e) { flash(e.message, true); }
});

function flash(text, bad) {
  const h = $('autofill-hint');
  h.textContent = text;
  h.style.color = bad ? 'var(--red)' : 'var(--green)';
  setTimeout(() => { h.textContent = 'Reads this page and fills the application form. You check it and click Submit.'; h.style.color = ''; }, 4000);
}

// ------------------------------------------------------------- autofill
$('btn-autofill').addEventListener('click', () => autofill());

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// fields in any frame of the tab (0 while a new step is still loading)
async function fieldCount(tabId) {
  try {
    await inject(tabId, true);
    const out = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: () => (window.JH ? window.JH.formFieldCount() : 0) });
    return out.reduce((n, f) => n + (f.result || 0), 0);
  } catch (e) { return 0; }
}

async function waitForForm(tabId, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await sleep(700);
    if (await fieldCount(tabId)) return true;
  }
  return false;
}

async function nextStepText(tabId) {
  const out = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: () => (window.JH ? window.JH.hasNext() : '') });
  return (out.find((f) => f.result) || {}).result || '';
}

async function goNext() {
  const tab = await targetTab();
  if (!tab) return;
  await inject(tab.id, true);
  const out = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: () => (window.JH ? window.JH.clickNext() : { clicked: false }) });
  if (!out.some((f) => f.result && f.result.clicked)) { flash('No Next button on this page.', true); return; }
  await sleep(1200);
  await waitForForm(tab.id, 10000);
  await readPage();
  autofill();
}

async function autofill(opened) {
  const btn = $('btn-autofill');
  const box = $('result-box');
  if (!st.cid) return;
  const tab = await targetTab();
  if (!tab) { flash('Open the application form first.', true); return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>Filling…';
  box.hidden = true;
  try {
    if (!st.profile) await loadProfile();
    let resume = null;
    if ($('chk-resume').checked && st.tailored) {
      resume = st.tailored;                           // the resume tailored for this job (Tailor resume)
    } else if ($('chk-resume').checked && st.profile && st.profile.profile.resume_filename) {
      try { resume = await api(`/api/ext/consultants/${st.cid}/resume`); } catch (e) { resume = null; }
    }
    await inject(tab.id, true);
    const frames = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: (p, r) => window.JH && window.JH.fill(p, r), args: [st.profile.profile, resume] });
    const res = { filled: [], questions: [], needsYou: [], resumeAttached: false, fieldsSeen: 0 };
    for (const f of frames) {
      const r = f.result;
      if (!r) continue;
      res.filled.push(...r.filled); res.questions.push(...r.questions); res.needsYou.push(...r.needsYou);
      res.resumeAttached = res.resumeAttached || r.resumeAttached; res.fieldsSeen += r.fieldsSeen;
    }
    if (!res.fieldsSeen) {
      // a job page with no form yet: click the site's Apply button once, wait for the form, fill it
      if (!opened) {
        const clicked = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => window.JH.clickApply() });
        const c = clicked[0] && clicked[0].result;
        if (c && c.clicked) {
          btn.innerHTML = `<span class="spin"></span>Opening "${esc(c.text)}"…`;
          await sleep(1500);
          const target = await targetTab();
          if (target && await waitForForm(target.id, 12000)) {
            btn.disabled = false;
            btn.textContent = 'Autofill';
            await readPage();
            return autofill(true);
          }
        }
      }
      const next = await nextStepText(tab.id).catch(() => '');
      box.hidden = false;
      box.innerHTML = next
        ? `<p class="warn">Nothing to fill on this step.</p><button class="btn primary wide" id="btn-next-step">Next page › (${esc(next)})</button>`
        : '<p class="warn">No application form on this page yet. Click the site\'s <b>Apply</b> button (or sign in), then Autofill again.</p>';
      if (next) $('btn-next-step').addEventListener('click', goNext);
      return;
    }
    let ai = { filled: [], left: res.questions.map((q) => q.id) };
    if (res.questions.length) {
      btn.innerHTML = '<span class="spin"></span>Answering questions…';
      try {
        const d = await api('/api/ext/answers', { method: 'POST', body: { candidate_id: st.cid, job: st.job || {}, questions: res.questions } });
        const out = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: (a) => window.JH && window.JH.fillAnswers(a), args: [d.answers || {}] });
        const done = new Set(out.flatMap((o) => (o.result ? o.result.filled : [])));
        ai = { filled: [...done], left: res.questions.filter((q) => !done.has(q.id)).map((q) => q.id) };
      } catch (e) {
        res.aiError = e.message;
      }
    }
    const requiredLeft = res.questions.filter((q) => q.required && ai.left.includes(q.id));
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: (ids) => window.JH && window.JH.markUnanswered(ids), args: [requiredLeft.map((q) => q.id)] });
    st.lastFill = res;
    const lines = [
      [`var(--green)`, `${res.filled.length} field${res.filled.length === 1 ? '' : 's'} filled from ${esc(st.profile.profile.full_name)}'s profile`],
      resume ? [res.resumeAttached ? 'var(--green)' : 'var(--red)', res.resumeAttached ? `Resume attached (${esc(resume.filename)})` : 'Resume not attached - upload it yourself'] : null,
      ai.filled.length ? ['var(--amber)', `${ai.filled.length} answer${ai.filled.length === 1 ? '' : 's'} written by AI - please check them`] : null,
      res.aiError ? ['var(--red)', `AI answers unavailable: ${esc(res.aiError)}`] : null,
      requiredLeft.length || res.needsYou.length ? ['var(--red)', `Needs you: ${esc([...requiredLeft.map((q) => q.label), ...res.needsYou].slice(0, 6).join(' · '))}`] : null,
    ].filter(Boolean);
    const next = await nextStepText(tab.id).catch(() => '');
    box.hidden = false;
    box.innerHTML = lines.map(([c, t]) => `<div class="res-line"><span class="dot" style="background:${c}"></span><span>${t}</span></div>`).join('') +
      (next
        ? `<p class="hint" style="text-align:left;margin:8px 0">Check this page, then go to the next one - it is filled automatically.</p><button class="btn primary wide" id="btn-next-step">Next page › (${esc(next)})</button>`
        : '<p class="hint" style="text-align:left;margin:8px 0 0">Check the form, then click the site\'s Submit button yourself.</p>');
    if (next) $('btn-next-step').addEventListener('click', goNext);
    $('applied-card').hidden = !!next;
  } catch (e) {
    box.hidden = false;
    box.innerHTML = `<p class="warn">${esc(e.auth ? 'Not connected - open Settings and connect again.' : e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Autofill';
  }
}

// ------------------------------------------------------------- did you apply?
$('btn-applied').addEventListener('click', async () => {
  if (!st.job || !st.cid) return;
  try {
    await api('/api/ext/applied', { method: 'POST', body: { candidate_id: st.cid, job: st.job } });
    $('applied-card').innerHTML = '<b style="color:var(--green)">✓ Marked as Applied in Job Hunt</b>';
  } catch (e) { flash(e.message, true); }
});
$('btn-not-yet').addEventListener('click', () => { $('applied-card').hidden = true; });

// ------------------------------------------------------------- settings
$('btn-settings').addEventListener('click', () => {
  if (!st.key) return;
  $('set-server').textContent = st.server;
  $('set-user').textContent = (st.user && st.user.name) || '';
  show('settings');
});
$('btn-back').addEventListener('click', () => show('main'));
$('btn-open-jh').addEventListener('click', () => chrome.tabs.create({ url: st.server + '/dashboard' }));
$('btn-disconnect').addEventListener('click', async () => {
  await chrome.storage.local.remove(['key', 'cid']);
  st.key = '';
  $('in-key').value = '';
  show('connect');
});

start();
