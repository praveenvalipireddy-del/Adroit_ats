// Job Hunt Autofill - the resume Autofill will attach, shown as the real Word document. The side panel puts it in
// chrome.storage.session ({filename, mime, base64, original, changes}); lines not in the original resume are highlighted.
const $ = (id) => document.getElementById(id);
const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();

function toBlob(r) {
  const bin = atob(r.base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: r.mime || 'application/octet-stream' });
}

function download(r) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(toBlob(r));
  a.download = r.filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

function markChanges(r) {
  const orig = new Set(String(r.original || '').split('\n').map(norm).filter(Boolean));
  const on = $('pv-toggle').checked;
  let n = 0;
  document.querySelectorAll('#pv-doc p, #pv-doc li, #pv-doc .pv-line').forEach((p) => {
    const t = norm(p.textContent);
    const changed = on && !!t && orig.size > 0 && !orig.has(t);
    p.classList.toggle('pv-changed', changed);
    if (changed) n++;
  });
  return n;
}

async function show() {
  const { jhPreview: r } = await chrome.storage.session.get('jhPreview');
  if (!r || !r.base64) { $('pv-doc').innerHTML = '<p class="warn">Nothing to preview - open it again from the Job Hunt panel.</p>'; return; }
  document.title = `${r.filename} - Job Hunt`;
  $('pv-name').textContent = r.filename;
  $('pv-download').addEventListener('click', () => download(r));
  const box = $('pv-doc');
  const isPdf = /pdf/i.test(r.mime) || /\.pdf$/i.test(r.filename);
  const isDocx = /wordprocessingml/i.test(r.mime) || /\.docx$/i.test(r.filename);
  if (isPdf) {
    $('pv-toggle-wrap').hidden = true;
    $('pv-note').textContent = 'The original resume on file (PDF).';
    box.innerHTML = '';
    const frame = document.createElement('iframe');
    frame.className = 'pv-pdf';
    frame.src = URL.createObjectURL(toBlob(r));
    box.append(frame);
    return;
  }
  if (!isDocx || !window.docx) {
    $('pv-toggle-wrap').hidden = true;
    box.innerHTML = '<p class="warn">This file type can\'t be shown here - download it to open it.</p>';
    return;
  }
  try {
    box.innerHTML = '';
    await window.docx.renderAsync(toBlob(r), box, null, { inWrapper: true, breakPages: true, ignoreLastRenderedPageBreak: true });
  } catch (e) {
    box.innerHTML = '<p class="warn">The Word file couldn\'t be shown here - download it to open it in Word.</p>';
    return;
  }
  const changes = (r.changes || []).length;
  if (!r.original) {
    $('pv-toggle-wrap').hidden = true;
    $('pv-note').textContent = 'The original resume on file - this is what Autofill attaches.';
    return;
  }
  const lines = markChanges(r);
  $('pv-note').textContent = `Tailored for this job: ${changes} change${changes === 1 ? '' : 's'} made, ${lines} line${lines === 1 ? '' : 's'} differ from the original. This is the file Autofill attaches.`;
  $('pv-toggle').addEventListener('change', () => markChanges(r));
}

show();
