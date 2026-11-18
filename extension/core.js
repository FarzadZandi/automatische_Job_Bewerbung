// Shared by the panel and the background worker: reads a page, fills it, attaches the PDFs.
// Never ticks a consent box, never clicks a button, never submits.
(function (root) {
  const inPage = (target, func, args = []) => chrome.scripting.executeScript({ target, func, args });

  const readResource = (path, responseType) => new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('GET', chrome.runtime.getURL(path));
    request.responseType = responseType;
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve(request.response) : reject(new Error(`Extension resource is missing: ${path}`));
    request.onerror = () => reject(new Error(`Extension resource cannot be read: ${path}`));
    request.send();
  });

  async function inject(tabId) {
    await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ['filler.js'] });
  }

  function flatten(results, pick) {
    const fields = [];
    for (const frame of results) {
      const list = pick(frame.result) || [];
      for (const field of list) fields.push({ ...field, frameId: frame.frameId, uid: `f${frame.frameId}-${field.id}` });
    }
    return fields;
  }

  async function loadData() {
    try { return JSON.parse(await readResource('data/data.json', 'text')); }
    catch (error) { throw new Error('data/data.json is missing. Run "jobfilter export-autofill" first.'); }
  }

  async function fileAsBase64(file) {
    const bytes = new Uint8Array(await readResource(file.path, 'arraybuffer'));
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return { name: file.name, type: 'application/pdf', base64: btoa(binary) };
  }

  // What is on the page: fillable fields, a login, a CAPTCHA, links that lead to the application form.
  async function inspect(tabId) {
    await inject(tabId);
    const results = await inPage({ tabId, allFrames: true }, () => {
      const fields = window.__jfFiller.describe();
      const shown = (node) => { const box = node.getBoundingClientRect(); return box.width > 0 && box.height > 0; };
      const words = /bewerben|bewerbung starten|zur bewerbung|apply/i;
      const here = location.href.split('#')[0];
      const links = window.top !== window ? [] : Array.from(document.querySelectorAll('a[href]'))
        .filter((a) => /^https?:/.test(a.href) && a.href.split('#')[0] !== here && shown(a))
        .filter((a) => { const text = (a.innerText || a.getAttribute('aria-label') || '').trim(); return text.length < 40 && words.test(text); })
        .map((a) => a.href);
      return {
        known: fields.filter((f) => f.key && f.kind !== 'checkbox' && f.kind !== 'file').length,
        files: fields.filter((f) => f.kind === 'file').length,
        password: !!document.querySelector('input[type=password]'),
        captcha: !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, [class*="captcha" i], [id*="captcha" i]'),
        links,
      };
    });
    const look = { known: 0, files: 0, password: false, captcha: false, links: [] };
    for (const frame of results) {
      const part = frame.result;
      if (!part) continue;
      look.known += part.known; look.files += part.files;
      look.password = look.password || part.password; look.captcha = look.captcha || part.captcha;
      look.links.push(...part.links);
    }
    return look;
  }

  async function scan(tabId) {
    await inject(tabId);
    const results = await inPage({ tabId, allFrames: true }, () => window.__jfFiller.scan());
    return flatten(results, (r) => r && r.fields);
  }

  // Fills the form in the tab from one application package and returns every field with its status.
  async function fillTab(tab, pkg, data) {
    await inject(tab.id);
    const extra = { letter_text: pkg.letter_text, source: pkg.source || '' };
    const results = await inPage({ tabId: tab.id, allFrames: true }, (p, e) => window.__jfFiller.fill(p, e), [data.profile, extra]);
    const fields = flatten(results, (r) => r);
    const plan = root.JF.assignFiles(fields, pkg);
    const uploadWarnings = [];
    for (const field of fields.filter((f) => f.kind === 'file')) {
      const { files: wanted, left, manual = [], held = false } = plan[field.uid] || { files: [], left: [], manual: [] };
      const letterFiles = [pkg.letter, pkg.combined, pkg.letter_cv_combined].filter(Boolean);
      const wantsLetter = wanted.some((file) => letterFiles.includes(file));
      const letterNames = new Set(letterFiles.map((file) => String(file.name || '')));
      const autoHasLetter = (field.autoFiles || []).some((name) => letterNames.has(String(name))
        || /anschreiben|cover.?letter/i.test(String(name)));
      if (field.filled && !(field.autoFilled && wantsLetter && !autoHasLetter)) {
        field.status = 'kept';
        if (wantsLetter && !autoHasLetter) uploadWarnings.push(`Cover letter was not attached to ${field.label}: the field already contains a file added by you`);
        continue;
      }
      if (held) { field.status = 'waiting_letter'; field.value = 'Waiting for the cover letter'; continue; }
      if (field.manualOnly) {
        field.status = 'manual';
        field.value = wanted.map((f) => f.name).join(', ') + (left.length ? `\nAlso needed: ${left.map((f) => f.name).join(', ')}` : '');
        continue;
      }
      if (!wanted.length) { field.status = 'manual'; continue; }
      const payload = [];
      for (const file of wanted) payload.push(await fileAsBase64(file));
      const [answer] = await inPage({ tabId: tab.id, frameIds: [field.frameId] }, (id, files) => window.__jfFiller.setFiles(id, files), [field.id, payload]);
      field.status = answer.result === 'filled' ? (manual.length ? 'manual' : 'attached') : answer.result;
      field.value = wanted.map((f) => f.name).join(', ')
        + (left.length ? `\nNot attached (limit of the form): ${left.map((f) => f.name).join(', ')}` : '')
        + (manual.length ? `\nAttach manually: ${manual.map((f) => f.name).join(', ')}` : '');
    }
    if (plan.warning) uploadWarnings.push(plan.warning);
    for (const warning of uploadWarnings) fields.push({
      id:`upload-warning-${fields.length}`, uid:`upload-warning-${fields.length}`, frameId:0,
      label:'Cover letter', kind:'file', key:'letter', status:'manual', value:warning, uploadWarning:true,
    });
    for (const field of fields) if (field.kind === 'checkbox' && !field.filled) field.status = 'consent';
    await observeFields(tab, pkg, fields);
    const host = new URL(tab.url).hostname.replace(/^www\./, '');
    if (!root.JF.isJobBoard(host)) {
      const stored = await chrome.storage.local.get('remembered');
      const remembered = stored.remembered || {};
      remembered[host] = { id: pkg.id, page: tab.url.split(/[?#]/)[0].replace(/\/$/, '') };
      await chrome.storage.local.set({ remembered });
    }
    return fields;
  }

  // Watch the fields the answer bank can learn from, frame by frame. Never fatal for the fill.
  async function observeFields(tab, pkg, fields) {
    const byFrame = new Map();
    for (const field of fields) {
      if (!root.JF.observable(field)) continue;
      const list = byFrame.get(field.frameId) || [];
      list.push({ id: field.id, label: field.label, kind: field.kind, key: field.key || '', options: field.options || [] });
      byFrame.set(field.frameId, list);
    }
    const page = root.JF.pageKey(tab.url || '');
    for (const [frameId, list] of byFrame) {
      try {
        await inPage({ tabId: tab.id, frameIds: [frameId] }, (items, id, key) => window.__jfFiller.observe(items, id, key), [list, pkg.id, page]);
      } catch (error) { /* a frame that went away */ }
    }
  }

  function fillMessages(tab, pkg, fields, look = {}, seenLabels = []) {
    const sum = root.JF.summary(fields);
    const yours = [sum.open ? `${sum.open} open question(s)` : '', sum.manualUploads ? `${sum.manualUploads} manual upload(s)` : '', sum.consent ? 'consent box(es)' : '', look.captcha ? 'CAPTCHA' : '', look.password ? 'password' : ''].filter(Boolean);
    const result = {
      action: 'fill-result', id: pkg.id, state: yours.length ? 'needs' : 'ready', reason: yours.join(', '),
      filled: sum.filled, attached: sum.attached, open: sum.open, consent: sum.consent,
      manual_uploads: sum.manualUploads,
      upload_warning: fields.filter((field) => field.uploadWarning).map((field) => field.value).join('; '),
      uploads: fields.filter((field) => field.kind === 'file').map((field) => ({
        label: field.label, status: field.status, files: String(field.value || '').split(/, |\n/).filter(Boolean),
      })),
      open_labels: fields.filter((field) => ['unknown', 'no_match', 'no_value', 'manual'].includes(field.status)
        && ['text', 'textarea', 'select', 'radio'].includes(field.kind)).map((field) => field.label),
    };
    const labelKey = (field) => String(field.label || '').toLowerCase().replace(/\s+/g, ' ').trim();
    const seen = new Set(seenLabels || []);
    const open = fields.filter((field) => ['unknown', 'no_match', 'no_value', 'manual'].includes(field.status)
      && ['text', 'textarea', 'select', 'radio'].includes(field.kind));
    const newOpen = open.filter((field) => !seen.has(labelKey(field)));
    newOpen.forEach((field) => seen.add(labelKey(field)));
    let host = ''; let path = '';
    try { const url = new URL(tab.url); host = url.hostname.replace(/^www\./, ''); path = url.pathname; } catch (error) { /* invalid URL */ }
    const messages = [result, {
      action: 'log', host, path, id: pkg.id,
      fields: fields.map((field) => ({
        label: field.label, kind: field.kind, key: field.key || null, status: field.status,
        required: Boolean(field.required),
        files: field.kind === 'file' ? String(field.value || '').split(/, |\n/).filter(Boolean) : undefined,
      })),
    }];
    if (newOpen.length) messages.push({
      action: 'questions', id: pkg.id, tabId: tab.id, pageKey: root.JF.pageKey(tab.url),
      fields: newOpen.slice(0, 30).map((field) => ({
        uid: field.uid, frameId: field.frameId, label: field.label, kind: field.kind,
        options: field.options || [], maxLength: field.maxLength || null, required: Boolean(field.required),
      })),
    });
    return { messages, seen: [...seen], summary: sum, yours };
  }

  root.JFCore = { inPage, inject, flatten, loadData, inspect, scan, fillTab, fillMessages };
})(typeof window !== 'undefined' ? window : globalThis);
