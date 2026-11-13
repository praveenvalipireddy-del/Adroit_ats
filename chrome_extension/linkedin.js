// Job Hunt - LinkedIn message helper. Injected by background.js into a LinkedIn tab that Job Hunt opened with a draft
// ("Message on LinkedIn" on a Sourcing card). It types the draft into LinkedIn's message / connection-note box.
// It NEVER clicks Send, Connect or anything that sends: the recruiter checks the text and clicks Send themselves.
(() => {
  if (window.__jhLiLoaded) return;
  window.__jhLiLoaded = true;

  const MSG_BOX = ['div.msg-form__contenteditable[contenteditable="true"]', '.msg-form div[role="textbox"][contenteditable="true"]',
    'div[role="textbox"][contenteditable="true"]'];
  const NOTE_BOX = ['textarea[name="message"]', 'textarea#custom-message', 'div[role="dialog"] textarea'];

  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const find = (sels, root = document) => { for (const s of sels) { const el = [...root.querySelectorAll(s)].find(visible); if (el) return el; } return null; };
  const labelOf = (el) => ((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')).trim().replace(/\s+/g, ' ');

  // The profile's own buttons (top card), never the messaging bar's or a "People also viewed" card's.
  function profileButton(re) {
    const main = document.querySelector('main') || document.body;
    return [...main.querySelectorAll('button, a[role="button"], a[href*="/messaging/compose"]')]
      .filter(visible).find((b) => re.test(labelOf(b)) && !b.closest('aside, .pv-browsemap-section, section[data-view-name*="browsemap"]')) || null;
  }

  function typeInto(el, text) {
    el.focus();
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
      setter.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return el.value === text;
    }
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch (e) { ok = false; }
    if (!ok || !el.textContent.trim()) {
      el.textContent = text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    }
    return el.textContent.trim().length > 0;
  }

  const boxText = (el) => (el.tagName === 'TEXTAREA' ? el.value : el.textContent).trim();

  // ------------------------------------------------------------------ the floating Job Hunt box
  const host = document.createElement('div');
  host.id = 'jobhunt-li-helper';
  host.style.cssText = 'position:fixed; right:16px; bottom:16px; z-index:2147483647;';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    .box{width:320px; max-width:calc(100vw - 32px); background:#fff; border:1px solid #cbd5e1; border-radius:14px; box-shadow:0 20px 25px -5px rgba(0,0,0,.18);
      font:14px/1.45 Inter,-apple-system,"Segoe UI",Roboto,sans-serif; color:#334155; overflow:hidden}
    .hd{display:flex; align-items:center; gap:8px; padding:12px 14px; background:#0f172a; color:#f8fafc}
    .logo{width:24px; height:24px; border-radius:7px; background:linear-gradient(135deg,#2563eb,#38bdf8); display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:800}
    .ttl{font-weight:800; letter-spacing:.06em; font-size:13px; flex:1}
    .x{border:0; background:none; color:#94a3b8; font-size:20px; cursor:pointer; line-height:1}
    .bd{padding:12px 14px; display:flex; flex-direction:column; gap:10px}
    .lab{font-size:11px; font-weight:700; letter-spacing:.06em; color:#64748b}
    .name{font-weight:700; color:#0f172a}
    ul{margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:6px; font-size:13px}
    li.ok::before{content:"✓ "; color:#047857; font-weight:700}
    li.todo{color:#b45309; font-weight:600}
    li.todo::before{content:"→ "}
    li.err{color:#b91c1c; font-weight:600}
    textarea{width:100%; box-sizing:border-box; min-height:84px; border:1px solid #cbd5e1; border-radius:8px; padding:8px; font:13px/1.45 inherit; font-family:inherit; resize:vertical}
    .row{display:flex; gap:8px}
    button.b{height:36px; border-radius:10px; border:1px solid #cbd5e1; background:#fff; color:#334155; font:600 13px inherit; font-family:inherit; cursor:pointer; width:100%}
    .row .b{flex:1}
    button.go{background:#059669; border-color:#059669; color:#fff; height:40px; font-weight:700}
    .note{font-size:12px; color:#64748b; margin:0}
  </style>
  <section class="box" role="dialog" aria-label="Job Hunt LinkedIn message">
    <div class="hd"><div class="logo">JH</div><div class="ttl">JOB HUNT</div><button class="x" id="x" aria-label="Close">×</button></div>
    <div class="bd">
      <div class="lab">LINKEDIN MESSAGE</div>
      <div class="name" id="who"></div>
      <ul id="steps"></ul>
      <textarea id="txt" aria-label="Your message"></textarea>
      <div class="row"><button class="b" id="again">Type it again</button><button class="b" id="copy">Copy</button></div>
      <button class="b go" id="sent">I sent it - mark in Job Hunt</button>
      <button class="b" id="nosend">Didn't send</button>
      <p class="note">Job Hunt never clicks Send. You check the text and send it on LinkedIn.</p>
    </div>
  </section>`;
  const $ = (id) => root.getElementById(id);
  const steps = [];
  function step(text, kind = 'ok') {
    steps.push({ text, kind });
    $('steps').innerHTML = '';
    steps.forEach((s) => { const li = document.createElement('li'); li.className = s.kind; li.textContent = s.text; $('steps').appendChild(li); });
  }
  function replaceLast(text, kind = 'ok') { steps.pop(); step(text, kind); }

  let draft = null;
  let sentAs = 'note';
  let observer = null;
  const stop = () => { if (observer) observer.disconnect(); observer = null; };
  const close = () => { stop(); chrome.runtime.sendMessage({ type: 'jh-li-done' }); host.remove(); window.__jhLiLoaded = false; };

  function fill(box, label) {
    const text = $('txt').value.trim();
    const max = parseInt(box.getAttribute('maxlength'), 10);
    if (max && text.length > max) {
      replaceLast(`LinkedIn allows ${max} characters here - your text has ${text.length}. Shorten it above, then Type it again.`, 'err');
      return false;
    }
    const current = boxText(box);
    if (current && current !== text) { replaceLast(`The ${label} already has text - left as it is.`, 'err'); return false; }
    if (!typeInto(box, text)) { replaceLast(`Couldn't type into the ${label} - use Copy and paste it.`, 'err'); return false; }
    replaceLast(`Typed your ${label}`);
    step('Check it and click Send on LinkedIn', 'todo');
    return true;
  }

  function waitFor(sels, ms) {
    return new Promise((resolve) => {
      const now = find(sels);
      if (now) return resolve(now);
      const t0 = Date.now();
      const iv = setInterval(() => {
        const el = find(sels);
        if (el || Date.now() - t0 > ms) { clearInterval(iv); resolve(el || null); }
      }, 250);
    });
  }

  // Connection note: the recruiter clicks Connect > Add a note (that's their choice - free accounts have a monthly
  // note limit); the moment LinkedIn shows the note box, the text goes in.
  function watchForNote() {
    sentAs = 'note';
    const connect = profileButton(/^(connect\b|invite .* to connect)/i);
    if (connect) connect.style.outline = '3px solid #f59e0b';
    step(connect ? 'Click Connect (outlined), then "Add a note" - I\'ll type it' : 'Click Connect (it may be under More), then "Add a note" - I\'ll type it', 'todo');
    stop();
    observer = new MutationObserver(() => {
      const box = find(NOTE_BOX);
      if (box && !boxText(box)) { stop(); if (connect) connect.style.outline = ''; fill(box, 'note'); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  async function run() {
    steps.length = 0;
    step('Opened the profile');
    if (draft.k === 'message') {
      const msgBtn = profileButton(/^message\b/i);
      if (msgBtn) {
        sentAs = 'message';
        msgBtn.click();   // opens LinkedIn's message box - it sends nothing
        step('Opening the message box…', 'todo');
        const box = await waitFor(MSG_BOX, 10000);
        if (box) { fill(box, 'message'); return; }
        replaceLast('The message box did not open - click Message, then Type it again.', 'err');
        return;
      }
      step('No Message button here (not connected) - send a connection note instead', 'err');
      if ($('txt').value.trim().length > 300) step('A note allows 300 characters at most - shorten it above first.', 'err');
    }
    watchForNote();
  }

  $('x').addEventListener('click', close);
  $('nosend').addEventListener('click', close);
  $('copy').addEventListener('click', () => navigator.clipboard.writeText($('txt').value).then(() => step('Copied - paste it into LinkedIn\'s box')));
  $('again').addEventListener('click', () => {
    const box = find(sentAs === 'message' ? MSG_BOX : NOTE_BOX);
    if (box) { if (boxText(box) && boxText(box) !== $('txt').value.trim()) typeInto(box, ''); step('Typing again…', 'todo'); fill(box, sentAs === 'message' ? 'message' : 'note'); }
    else run();
  });
  $('sent').addEventListener('click', () => {
    $('sent').disabled = true;
    chrome.runtime.sendMessage({ type: 'jh-li-sent', p: draft.p, k: sentAs }, (res) => {
      $('sent').disabled = false;
      if (res && res.ok) {
        stop();
        step('Saved in Job Hunt - the team sees "Messaged on LinkedIn"');
        chrome.runtime.sendMessage({ type: 'jh-li-done' });
        setTimeout(() => { host.remove(); window.__jhLiLoaded = false; }, 2500);
      } else {
        step((res && res.error) || 'Could not reach Job Hunt.', 'err');
      }
    });
  });

  chrome.runtime.sendMessage({ type: 'jh-li-get' }, (d) => {
    if (!d || !d.p || !d.m) { window.__jhLiLoaded = false; return; }
    draft = d;
    $('who').textContent = d.n || 'Candidate';
    $('txt').value = d.m;
    document.documentElement.appendChild(host);
    run();
  });
})();
