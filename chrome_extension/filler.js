// Job Hunt Autofill - runs inside the careers page (every frame). The side panel calls:
//   JH.readJob()            -> {url, title, company, location, description}
//   JH.fill(profile, resume) -> fills standard fields + attaches the resume; returns the questions it couldn't answer
//   JH.fillAnswers(answers)  -> fills the AI's answers ({question id: answer})
//   JH.clearMarks()
// It never submits the form. Filled fields get a green outline, AI answers amber, required fields left empty red.
(() => {
  if (window.JH && window.JH.version === 2) return;

  const GREEN = '#16a34a', AMBER = '#f59e0b', RED = '#dc2626';
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const textOf = (n) => (n ? norm(n.innerText || n.textContent) : '');
  const SKIP_TYPES = /^(hidden|submit|button|reset|image|password|search)$/i;

  // ---------------- reading the job
  const ATS = [
    [/(?:job-)?boards(?:\.eu)?\.greenhouse\.io$/, (u) => u.pathname.split('/')[1]],
    [/jobs(?:\.eu)?\.lever\.co$/, (u) => u.pathname.split('/')[1]],
    [/jobs\.ashbyhq\.com$/, (u) => u.pathname.split('/')[1]],
    [/^([a-z0-9-]+)\.(keka\.com|bamboohr\.com|darwinbox\.in|darwinbox\.com|freshteam\.com|zohorecruit\.com|zohorecruit\.in|workable\.com|recruitee\.com)$/, (u, m) => m[1]],
    [/^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$/, (u, m) => m[1]],
    [/^apply\.workable\.com$/, (u) => u.pathname.split('/')[1]],
    [/^jobs\.smartrecruiters\.com$/, (u) => u.pathname.split('/')[1]],
  ];
  const titleCase = (s) => norm(String(s || '').replace(/[-_]+/g, ' ')).replace(/\b\w/g, (c) => c.toUpperCase());

  function companyFromUrl() {
    try {
      const u = new URL(location.href);
      for (const [re, get] of ATS) {
        const m = u.hostname.match(re);
        if (m) { const slug = get(u, m); if (slug) return titleCase(slug); }
      }
    } catch (e) { /* ignore */ }
    return '';
  }

  const SECTION_HEADING = /^(work summary|summary|import (your )?profile|apply|application|my information|personal (info|information|details)|contact (info|information)|my experience|experience|education|review|voluntary disclosures?|self[- ]identify|questions|application questions|additional information|documents|attachments|sign in|create account)$/i;
  const APPLY_STEP_URL = /\/apply(\/|$|\?)|\/application(\/|$)|apply\/section|\/candidate\//i;

  function readJob() {
    const job = { url: location.href, title: '', company: '', location: '', description: '' };
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const d = JSON.parse(s.textContent);
        const arr = Array.isArray(d) ? d : (d['@graph'] || [d]);
        const jp = arr.find((x) => x && (x['@type'] === 'JobPosting' || (Array.isArray(x['@type']) && x['@type'].includes('JobPosting'))));
        if (!jp) continue;
        job.title = norm(jp.title);
        const org = jp.hiringOrganization;
        job.company = norm(org && (org.name || (typeof org === 'string' ? org : '')));
        const loc = [].concat(jp.jobLocation || [])[0];
        const a = loc && loc.address;
        if (a) job.location = [a.addressLocality, a.addressRegion, a.addressCountry && (a.addressCountry.name || a.addressCountry)].filter((x) => typeof x === 'string' && x).join(', ');
        if (jp.description) { const t = document.createElement('div'); t.innerHTML = jp.description; job.description = (t.textContent || '').replace(/\n{3,}/g, '\n\n').trim(); }
        break;
      } catch (e) { /* not JSON */ }
    }
    const atsCompany = companyFromUrl();
    if (!job.company) job.company = atsCompany || norm((document.querySelector('meta[property="og:site_name"]') || {}).content);
    if (!job.title) {
      // a heading like "Work Summary" / "Import your profile" on an application step is not the job title
      const cands = [textOf(document.querySelector('h1')), norm((document.querySelector('meta[property="og:title"]') || {}).content),
        norm(document.title).split(/\s[|–-]\s/)[0]];
      job.title = cands.find((t) => t && t.length > 2 && !SECTION_HEADING.test(t)) || '';
    }
    if (!job.location) {
      const l = document.querySelector('[class*="location" i], [data-qa*="location" i], [data-automation-id*="location" i]');
      if (l) job.location = textOf(l).replace(/^(job )?location\s*:?\s*/i, '').slice(0, 120);
    }
    job.isApplyStep = APPLY_STEP_URL.test(location.pathname + location.hash);
    if (!job.description || job.description.length < 60) {
      const main = document.querySelector('main, [role="main"], #content, .content, article, #app_body') || document.body;
      job.description = (main.innerText || '').trim();
    }
    job.title = job.title.slice(0, 200);
    job.description = job.description.slice(0, 20000);
    return job;
  }

  // ---------------- finding the form fields
  function isShown(el) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none';
  }

  function containerQuestion(el) {
    let c = el.parentElement;
    for (let i = 0; i < 6 && c && c !== document.body; i++, c = c.parentElement) {
      const lg = c.querySelector(':scope > legend');
      if (lg) return textOf(lg);
      const l = c.querySelector('label, legend, [class*="label" i], [class*="question" i], h3, h4');
      if (l && !l.contains(el) && textOf(l) && !l.querySelector('input,select,textarea')) return textOf(l);
    }
    return '';
  }

  function labelOf(el) {
    const parts = [];
    const al = el.getAttribute('aria-label');
    if (al) parts.push(al);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) parts.push(textOf(n)); });
    if (el.id) {
      try { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) parts.push(textOf(l)); } catch (e) { /* ignore */ }
    }
    const wrap = el.closest('label');
    if (wrap) parts.push(textOf(wrap));
    if (norm(parts.join('')).length < 2) parts.push(containerQuestion(el));
    if (el.placeholder) parts.push(el.placeholder);
    if (el.name && norm(parts.join('')).length < 2) parts.push(el.name.replace(/[_\-[\]]+/g, ' '));
    return norm(parts.filter(Boolean).join(' | ')).slice(0, 300);
  }

  function groupQuestion(input) {
    const fs = input.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && textOf(lg)) return textOf(lg); }
    const lb = input.closest('[role="radiogroup"],[role="group"]');
    if (lb) {
      const t = lb.getAttribute('aria-label') || (lb.getAttribute('aria-labelledby') && textOf(document.getElementById(lb.getAttribute('aria-labelledby'))));
      if (t) return norm(t);
    }
    // the nearest question text above the whole group
    const name = input.name;
    let c = input.parentElement;
    for (let i = 0; i < 7 && c && c !== document.body; i++, c = c.parentElement) {
      const all = name ? c.querySelectorAll(`input[name="${CSS.escape(name)}"]`) : [input];
      if (all.length < (name ? document.querySelectorAll(`input[name="${CSS.escape(name)}"]`).length : 1)) continue;
      const l = Array.from(c.querySelectorAll('label, legend, [class*="label" i], [class*="question" i], p, h3, h4, span'))
        .find((n) => !n.querySelector('input') && !n.contains(input) && textOf(n).length > 2 && !all[0].closest('label')?.contains(n) &&
              !Array.from(all).some((r) => optionLabel(r) === textOf(n)));
      if (l) return textOf(l);
    }
    return '';
  }

  function optionLabel(input) {
    if (input.id) {
      try { const l = document.querySelector(`label[for="${CSS.escape(input.id)}"]`); if (l) return textOf(l); } catch (e) { /* ignore */ }
    }
    const wrap = input.closest('label');
    if (wrap) return textOf(wrap);
    const sib = input.nextElementSibling;
    if (sib && textOf(sib)) return textOf(sib);
    return norm(input.value);
  }

  // ---------------- which detail a field asks for
  const RULES = [
    [/\bfirst[\s_-]*name|given name|\bfname\b/, 'first_name'],
    [/\blast[\s_-]*name|surname|family name|\blname\b/, 'last_name'],
    [/preferred (first )?name/, 'first_name'],
    [/e-?mail/, 'email'],
    [/^(full |legal |your |candidate )?name\b|full name|legal name/, 'full_name'],
    [/phone|mobile|contact number|\bcell\b/, 'phone'],
    [/linked\s*in/, 'linkedin_url'],
    [/github|portfolio|website|personal (site|url)|other (url|link)/, 'portfolio_url'],
    [/street|address line|^address|mailing address|home address|current address/, 'address_line'],
    [/zip|postal|pin ?code/, 'postal_code'],
    [/\bcity\b|\btown\b/, 'city'],
    [/\bstate\b|province/, 'state'],
    [/\bcountry\b(?!.*(authori|sponsor|eligible))/, 'country'],
    [/(require|need).{0,60}sponsor|sponsor.{0,60}(require|need)/, 'needs_sponsorship'],
    [/authori[sz]ed to work|legally (authori[sz]ed|eligible|permitted|able)|eligible to work|right to work|work authori[sz]ation/, 'work_authorized'],
    [/sponsor/, 'needs_sponsorship'],
    [/visa (status|type)|work permit|immigration status|citizenship status/, 'visa_status'],
    [/current (company|employer)|present employer|most recent (company|employer)|employer name|^company$/, 'current_company'],
    [/current (job )?title|current (role|position)|^job title|^title$|designation/, 'title'],
    [/years? of (relevant |total |professional |work )?experience|total experience|how many years/, 'years_experience'],
    [/salary|compensation|\bctc\b|expected (pay|rate)|desired (pay|rate)|hourly rate|pay expectation|rate expectation/, 'expected_salary'],
    [/notice period|earliest start|start date|when can you (start|join)|available to (start|join)|joining date/, 'notice_period'],
    [/relocat/, 'willing_to_relocate'],
    [/highest (level of )?(degree|education)|education level|level of education|^degree/, 'highest_degree'],
    [/field of study|\bmajor\b|discipline/, 'field_of_study'],
    [/school|university|college|institution/, 'university'],
    [/graduat/, 'graduation_year'],
    [/hispanic|latino/, 'race'],
    [/gender|\bsex\b/, 'gender'],
    [/\brace\b|ethnic/, 'race'],
    [/veteran/, 'veteran'],
    [/disabilit/, 'disability'],
    [/how did you (hear|find|learn)|where did you (hear|find|see)|source of (application|referral)|referral source|^source$/, 'how_heard'],
    [/(current |preferred )?location|where are you (based|located)|based in/, 'location'],
  ];
  const NEVER = /captcha|password|cover letter.*(upload|attach)|signature|i agree|i certify|i acknowledge|consent|terms|privacy|subscribe|newsletter/i;

  function keyFor(label) {
    const l = label.toLowerCase();
    if (NEVER.test(l)) return null;
    // the label's first part is the real question; placeholders / names come after " | "
    for (const piece of [l.split(' | ')[0], l]) {
      for (const [re, key] of RULES) if (re.test(piece)) return key;
    }
    return null;
  }

  // ---------------- setting values the way a person would (React / Vue forms listen to these events)
  function setValue(el, v) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  function check(input) {
    if (input.checked) return;
    input.click();
    if (!input.checked) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
      setter.call(input, true);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  const YES = /^(yes|y|true|i am|i do|i will)\b/i, NO = /^(no|n|false|i am not|i do not|i don't|i will not)\b/i;
  const DECLINE_RE = /decline|prefer not|not wish|don.?t wish|do not wish|choose not|rather not|not to (say|disclose|answer)/i;
  const PLACEHOLDER = /^(select|choose|please select|--|—|-)\b|^$/i;

  function pickOption(options, value) {
    const v = norm(value).toLowerCase();
    if (!v) return null;
    const opts = options.filter((o) => o.text && !PLACEHOLDER.test(o.text));
    let m = opts.find((o) => o.text.toLowerCase() === v);
    if (!m && (v === 'yes' || v === 'no')) m = opts.find((o) => (v === 'yes' ? YES : NO).test(o.text));
    if (!m && DECLINE_RE.test(v)) m = opts.find((o) => DECLINE_RE.test(o.text));
    if (!m) m = opts.find((o) => o.text.toLowerCase().startsWith(v)) || opts.find((o) => v.length > 2 && o.text.toLowerCase().includes(v));
    if (!m) m = opts.find((o) => o.text.length > 3 && v.includes(o.text.toLowerCase()));
    return m || null;
  }

  function mark(el, color, note) {
    if (!el) return;
    el.style.setProperty('outline', `2px solid ${color}`, 'important');
    el.style.setProperty('outline-offset', '2px', 'important');
    el.setAttribute('data-jh-mark', '1');
    if (note) el.setAttribute('title', note);
  }

  function clearMarks() {
    document.querySelectorAll('[data-jh-mark]').forEach((el) => {
      el.style.removeProperty('outline');
      el.style.removeProperty('outline-offset');
      el.removeAttribute('data-jh-mark');
    });
  }

  let seq = 0;
  const idFor = (el) => {
    if (!el.getAttribute('data-jh-id')) el.setAttribute('data-jh-id', `q${Date.now().toString(36)}${(seq++).toString(36)}${Math.random().toString(36).slice(2, 5)}`);
    return el.getAttribute('data-jh-id');
  };
  const isRequired = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$|\*\s*\|/.test(label) || /\(required\)/i.test(label);

  function attachResume(input, resume) {
    try {
      const bin = atob(resume.base64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const file = new File([bytes], resume.filename, { type: resume.mime || 'application/octet-stream' });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return input.files && input.files.length === 1;
    } catch (e) {
      return false;
    }
  }

  // ---------------- the main fill
  function fill(profile, resume) {
    clearMarks();
    const result = { filled: [], questions: [], needsYou: [], resumeAttached: false, fieldsSeen: 0, frame: location.href };
    const p = profile || {};
    const doneGroups = new Set();
    const els = Array.from(document.querySelectorAll('input, select, textarea'));

    // resume upload
    const files = els.filter((e) => e.type === 'file' && !e.disabled);
    if (resume && resume.base64 && files.length) {
      const target = files.find((f) => /resume|\bcv\b|curriculum/i.test(labelOf(f) + ' ' + (f.name || '') + ' ' + (f.id || '')))
        || (files.length === 1 && !/cover/i.test(labelOf(files[0]) + ' ' + (files[0].name || '')) ? files[0] : null);
      if (target && !(target.files && target.files.length)) {
        result.resumeAttached = attachResume(target, resume);
        const box = target.closest('.field, .form-group, [class*="upload" i], [class*="file" i]') || target.parentElement;
        mark(isShown(target) ? target : box, result.resumeAttached ? GREEN : RED, result.resumeAttached ? 'Resume attached by Job Hunt' : 'Attach the resume here');
      }
    }

    for (const el of els) {
      if (el.disabled || el.readOnly || SKIP_TYPES.test(el.type || '') || el.type === 'file') continue;
      if (el.closest('[data-jh-skip]')) continue;

      // radio groups (Yes / No questions, voluntary disclosures)
      if (el.type === 'radio' || el.type === 'checkbox') {
        const name = el.name || el.id;
        if (!name || doneGroups.has(name)) continue;
        doneGroups.add(name);
        const group = el.name ? Array.from(document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)) : [el];
        const anyShown = group.some((r) => isShown(r) || (r.closest('label') && isShown(r.closest('label'))));
        if (!anyShown) continue;
        if (el.type === 'checkbox' && group.length === 1) continue;          // consents / single ticks: left for you
        result.fieldsSeen++;
        const q = groupQuestion(el);
        const opts = group.map((r) => ({ text: optionLabel(r), el: r }));
        const box = el.closest('fieldset') || el.closest('[role="radiogroup"]') || el.parentElement?.parentElement || el.parentElement;
        if (group.some((r) => r.checked)) continue;
        const key = keyFor(q);
        const val = key ? p[key] : '';
        const m = val ? pickOption(opts, val) : null;
        if (m && el.type === 'radio') {
          check(m.el);
          mark(box, GREEN, `Filled by Job Hunt (${key})`);
          result.filled.push(q.slice(0, 60));
        } else if (el.type === 'radio') {
          result.questions.push({ id: idFor(el), label: q || '(question)', options: opts.map((o) => o.text).filter(Boolean), kind: 'radio', required: group.some((r) => r.required) });
        } else {
          mark(box, RED, 'Job Hunt left this for you');
          result.needsYou.push(q.slice(0, 80));
        }
        continue;
      }

      if (!isShown(el)) continue;
      const label = labelOf(el);
      if (!label || NEVER.test(label)) continue;
      result.fieldsSeen++;
      const hasValue = el.tagName === 'SELECT' ? !!(el.value && !PLACEHOLDER.test(textOf(el.selectedOptions[0]))) : !!norm(el.value);
      if (hasValue) continue;                              // never overwrite what's already there
      const key = keyFor(label);
      let val = key ? p[key] : '';
      if (key === 'full_name' && !val) val = [p.first_name, p.last_name].filter(Boolean).join(' ');

      if (el.tagName === 'SELECT') {
        const opts = Array.from(el.options).map((o) => ({ text: norm(o.text), value: o.value, el: o }));
        const m = val ? pickOption(opts, val) : null;
        if (m) {
          setValue(el, m.value);
          mark(el, GREEN, `Filled by Job Hunt (${key})`);
          result.filled.push(label.split(' | ')[0].slice(0, 60));
        } else {
          result.questions.push({ id: idFor(el), label: label.split(' | ')[0], options: opts.filter((o) => !PLACEHOLDER.test(o.text)).map((o) => o.text), kind: 'select', required: isRequired(el, label) });
        }
        continue;
      }

      if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list') {
        // custom dropdowns (React-Select etc.) need a real click - left for you, marked
        mark(el, RED, 'Pick this one yourself');
        result.needsYou.push(label.split(' | ')[0].slice(0, 80));
        continue;
      }

      if (val) {
        if (el.type === 'number') val = String(val).replace(/[^0-9.]/g, '');
        setValue(el, String(val));
        mark(el, GREEN, `Filled by Job Hunt (${key})`);
        result.filled.push(label.split(' | ')[0].slice(0, 60));
      } else {
        result.questions.push({ id: idFor(el), label: label.split(' | ')[0], options: [], kind: el.tagName === 'TEXTAREA' ? 'long' : 'text', required: isRequired(el, label) });
      }
    }
    return result;
  }

  function fillAnswers(answers) {
    const out = { filled: [], left: [] };
    for (const [id, value] of Object.entries(answers || {})) {
      const el = document.querySelector(`[data-jh-id="${CSS.escape(id)}"]`);
      if (!el) continue;
      if (el.type === 'radio') {
        const group = el.name ? Array.from(document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`)) : [el];
        const m = pickOption(group.map((r) => ({ text: optionLabel(r), el: r })), value);
        const box = el.closest('fieldset') || el.closest('[role="radiogroup"]') || el.parentElement?.parentElement || el.parentElement;
        if (m) { check(m.el); mark(box, AMBER, 'AI answer - please check'); out.filled.push(id); } else out.left.push(id);
      } else if (el.tagName === 'SELECT') {
        const m = pickOption(Array.from(el.options).map((o) => ({ text: norm(o.text), value: o.value, el: o })), value);
        if (m) { setValue(el, m.value); mark(el, AMBER, 'AI answer - please check'); out.filled.push(id); } else out.left.push(id);
      } else if (!norm(el.value)) {
        setValue(el, value);
        mark(el, AMBER, 'AI answer - please check');
        out.filled.push(id);
      }
    }
    return out;
  }

  // required questions nobody answered: red, so you see them before submitting
  function markUnanswered(ids) {
    let n = 0;
    for (const id of ids || []) {
      const el = document.querySelector(`[data-jh-id="${CSS.escape(id)}"]`);
      if (!el) continue;
      const box = el.type === 'radio' ? (el.closest('fieldset') || el.parentElement?.parentElement || el) : el;
      mark(box, RED, 'Job Hunt left this for you');
      n++;
    }
    return n;
  }

  // ---------------- buttons: open the form / go to the next step. Never Submit.
  const FINAL = /submit|send|finish|complete|confirm|review and submit|sign|agree/i;
  const APPLY_BTN = /^(apply|apply now|apply for this job|apply for this position|apply online|start (your )?application|i'?m interested|easy apply|apply on company site)\b/i;
  const NEXT_BTN = /^(next|continue|save (and|&) continue|next step|proceed|save and next|next page)\b/i;

  function buttons() {
    return Array.from(document.querySelectorAll('button, a[href], [role="button"], input[type="button"], input[type="submit"]'))
      .filter((b) => isShown(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true')
      .map((b) => ({ el: b, text: norm(b.innerText || b.value || b.getAttribute('aria-label') || '') }))
      .filter((b) => b.text && b.text.length < 60);
  }

  function formFieldCount() {
    return Array.from(document.querySelectorAll('input, select, textarea'))
      .filter((e) => !SKIP_TYPES.test(e.type || '') && (e.type === 'file' || isShown(e))).length;
  }

  // On a job page with no form yet: click the site's Apply button (it can't submit anything - there is no form).
  function clickApply() {
    if (formFieldCount() > 0) return { clicked: false, reason: 'form' };
    const b = buttons().find((x) => APPLY_BTN.test(x.text) && !FINAL.test(x.text));
    if (!b) return { clicked: false, reason: 'none' };
    b.el.click();
    return { clicked: true, text: b.text };
  }

  function nextButton() {
    return buttons().find((x) => NEXT_BTN.test(x.text) && !FINAL.test(x.text)) || null;
  }

  function hasNext() {
    const b = nextButton();
    return b ? b.text : '';
  }

  function clickNext() {
    const b = nextButton();
    if (!b) return { clicked: false };
    b.el.click();
    return { clicked: true, text: b.text };
  }

  window.JH = { version: 2, readJob, fill, fillAnswers, markUnanswered, clearMarks, clickApply, clickNext, hasNext, formFieldCount,
    _keyFor: keyFor, _labelOf: labelOf };
})();
