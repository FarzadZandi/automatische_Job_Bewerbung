// Background worker: opens the panel, and fills the forms of the user's own applications on its own.
// It acts only on pages that belong to an application package. It never ticks a consent box,
// never clicks a button and never submits; it only follows plain links to the application form.
importScripts('rules.js', 'core.js');

if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
} else {
  chrome.action.onClicked.addListener(() => {
    chrome.windows.create({ url: 'panel.html', type: 'popup', width: 440, height: 860 });
  });
}

const busy = new Set();
const RETRY_MS = [3000, 8000, 20000];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const strip = (url) => (url || '').split(/[?#]/)[0].replace(/\/$/, '');
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (error) { return ''; } };
const isAfa = (host) => /(^|\.)arbeitsagentur\.de$/.test(host);

async function session(key, fallback) { return (await chrome.storage.session.get({ [key]: fallback }))[key]; }
async function tabsState() { return session('tabs', {}); }
async function saveEntry(tabId, entry) {
  const tabs = await tabsState();
  if (entry) tabs[tabId] = entry; else delete tabs[tabId];
  await chrome.storage.session.set({ tabs });
}

// Hosts of the open applications, cached for two minutes: any other page is left alone at once.
async function knownHosts() {
  const cache = await session('hosts', null);
  if (cache && Date.now() - cache.at < 120000) return cache.list;
  try {
    const data = await JFCore.loadData();
    const list = [...new Set(data.packages.flatMap((p) => p.hosts.concat(p.urls.map(hostOf))).filter(Boolean))];
    await chrome.storage.session.set({ hosts: { at: Date.now(), list } });
    return list;
  } catch (error) {
    return [];
  }
}

// The jobfilter window registers itself through relay.js; results are handed to it there.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return;
  if (message.type === 'jf-panel-chat') {
    const payload = message.payload || {};
    toApp({
      action: 'chat', requestId: String(payload.requestId || ''), id: String(payload.id || ''),
      message: String(payload.message || '').slice(0, 4000),
      history: (Array.isArray(payload.history) ? payload.history : []).slice(-6).map((item) => ({
        role: item.role === 'user' ? 'user' : 'claude', text: String(item.text || '').slice(0, 1500),
      })),
    }).then((connected) => sendResponse({ accepted: true, connected })).catch((error) => sendResponse({ accepted: false, error: error.message }));
    return true;
  }
  if (message.type === 'jf-panel-confirm') {
    const payload = message.payload || {};
    toApp({
      action:'confirm', id:String(payload.id || ''), requestId:String(payload.requestId || ''), source:'panel',
    }).then((connected) => sendResponse({ accepted:connected, connected, error:connected ? '' : 'The jobfilter app is not connected. Refresh the jobfilter window.' }))
      .catch((error) => sendResponse({ accepted:false, error:error.message }));
    return true;
  }
  if (message.type === 'jf-panel-questions') {
    const payload = message.payload || {};
    const fields = Array.isArray(payload.fields) ? payload.fields.slice(0, 30).map((field) => ({
      uid: String(field.uid || ''), frameId: Number(field.frameId || 0), label: String(field.label || '').slice(0, 300),
      kind: String(field.kind || ''), options: Array.isArray(field.options) ? field.options.slice(0, 60) : [],
      maxLength: field.maxLength || null, required: Boolean(field.required),
    })) : [];
    toApp({
      action: 'questions', id: String(payload.id || ''), tabId: Number(payload.tabId), fields,
      pageKey: String(payload.pageKey || ''),
      instruction: String(payload.instruction || '').slice(0, 1200), force: Boolean(payload.force),
      mode: ['suggest', 'resolve'].includes(payload.mode) ? payload.mode : 'answer',
      source: 'panel', requestId: String(payload.requestId || ''),
    }).then((connected) => sendResponse({ accepted: true, connected })).catch((error) => sendResponse({ accepted: false, error: error.message }));
    return true;
  }
  if (message.type === 'jf-panel-save-answer') {
    const payload = message.payload || {};
    toApp({
      action:'save-answer-bank', requestId:String(payload.requestId || ''), kind:String(payload.kind || 'saved'),
      label:String(payload.label || '').slice(0, 300), value:String(payload.value || '').slice(0, 600),
      language:String(payload.language || ''), scope:String(payload.scope || 'any employer'),
      employer:String(payload.employer || '').slice(0, 200), pattern:String(payload.pattern || '').slice(0, 500),
    }).then((connected) => sendResponse({ accepted:connected })).catch((error) => sendResponse({ accepted:false, error:error.message }));
    return true;
  }
  if (message.type === 'jf-panel-fill-result') {
    const messages = Array.isArray(message.payload) ? message.payload.slice(0, 3) : [];
    Promise.all(messages.map((payload) => toApp(payload)))
      .then((sent) => sendResponse({ accepted: true, connected: sent.every(Boolean) }))
      .catch((error) => sendResponse({ accepted: false, error: error.message }));
    return true;
  }
  if (message.type === 'jf-panel-claude-check') {
    const requestId = String(message.requestId || '');
    toApp({ action: 'claude-check', requestId }).then((connected) => sendResponse({ accepted: connected, connected }));
    return true;
  }
  if (message.type === 'jf-panel-use-suggestion') {
    const payload = message.payload || {};
    const match = /^f(\d+)-(.+)$/.exec(String(payload.uid || ''));
    (async () => {
      if (!match) return { status: 'page changed' };
      const tab = await chrome.tabs.get(Number(payload.tabId));
      if (JF.pageKey(tab.url || '') !== String(payload.pageKey || '')) return { status: 'page changed' };
      const [result] = await JFCore.inPage({ tabId: tab.id, frameIds: [Number(match[1])] },
        (id, label, value) => window.__jfFiller.setAnswer(id, label, value),
        [match[2], String(payload.label || ''), String(payload.value || '')]);
      return { status: result && result.result && result.result.status === 'filled' ? 'inserted' : 'page changed' };
    })().then(sendResponse).catch(() => sendResponse({ status: 'page changed' }));
    return true;
  }
  if (!sender.tab) return;
  if (message.type === 'jf-app-ready') registerApp(sender.tab.id);
  if (message.type === 'jf-contact') onContact(sender.tab.id, message);
  if (message.type === 'jf-fields-grown') handle(sender.tab.id, 0, true);
  if (message.type === 'jf-observe' && message.id && message.field) {
    toApp({ action: 'observe', id: String(message.id), page: String(message.page || ''), fields: [message.field] });
  }
  if (message.type === 'jf-from-app') onAppMessage(message.payload || {});
});
// After the extension is reloaded, connect again to a jobfilter window that is already open.
chrome.runtime.onInstalled.addListener(async () => {
  for (const tab of await chrome.tabs.query({ url: ['http://127.0.0.1/*', 'http://localhost/*'] })) {
    chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['relay.js'] }).catch(() => {});
  }
});

async function toApp(payload) {
  const appTab = await session('appTab', null);
  try {
    if (appTab === null) throw new Error('no jobfilter window');
    await chrome.tabs.sendMessage(appTab, { type: 'jf-to-app', payload });
    return true;
  } catch (error) {
    if (payload.action === 'observe') {
      // Only the latest value per field matters; kept apart so it never pushes out other messages.
      const pending = await session('observeQueue', {});
      for (const field of payload.fields || []) pending[`${payload.id}\u0000${payload.page}\u0000${field.label}`] = { ...payload, fields: [field] };
      const keys = Object.keys(pending);
      for (const key of keys.slice(0, Math.max(0, keys.length - 300))) delete pending[key];
      await chrome.storage.session.set({ observeQueue: pending });
      return false;
    }
    const queued = await session('appQueue', []);
    queued.push(payload);
    await chrome.storage.session.set({ appQueue: queued.slice(-50) });
    return false;
  }
}

async function registerApp(tabId) {
  await chrome.storage.session.set({ appTab: tabId });
  const queued = await session('appQueue', []);
  const left = [];
  for (const payload of queued) {
    try { await chrome.tabs.sendMessage(tabId, { type: 'jf-to-app', payload }); }
    catch (error) { left.push(payload); }
  }
  await chrome.storage.session.set({ appQueue: left.slice(-50) });
  const observed = await session('observeQueue', {});
  const remaining = {};
  for (const [key, payload] of Object.entries(observed)) {
    try { await chrome.tabs.sendMessage(tabId, { type: 'jf-to-app', payload }); }
    catch (error) { remaining[key] = payload; }
  }
  await chrome.storage.session.set({ observeQueue: remaining });
}

async function onAppMessage(payload) {
  if (payload.action === 'chat-answer') {
    chrome.runtime.sendMessage({ type: 'jf-panel-chat-answer', payload }).catch(() => {});
    return;
  }
  if (payload.action === 'claude-status') {
    chrome.runtime.sendMessage({ type: 'jf-panel-claude-status', payload }).catch(() => {});
    return;
  }
  if (payload.action === 'ping') {
    toApp({ action: 'pong', version: chrome.runtime.getManifest().version });
    return;
  }
  if (payload.action === 'current') {
    const current = String(payload.id || '');
    const previous = await session('current', '');
    await chrome.storage.session.set({ current, currentConfirmed:Boolean(payload.confirmed), currentConfirmedAt:String(payload.confirmed_at || '') });
    chrome.runtime.sendMessage({ type:'jf-panel-current', payload:{ id:current, confirmed:Boolean(payload.confirmed), confirmed_at:String(payload.confirmed_at || '') } }).catch(() => {});
    if (current && current !== previous) {
      try {
        const focused = await chrome.windows.getLastFocused();
        const [active] = await chrome.tabs.query({ active: true, windowId: focused.id });
        if (active) handle(active.id);
      } catch (error) { /* tabs.onUpdated handles the page when no active tab is available */ }
    }
    return;
  }
  if (payload.action === 'confirmed') {
    const pkgId = String(payload.id || '');
    const tabs = await tabsState();
    for (const [tabId, entry] of Object.entries(tabs)) {
      if (!entry || entry.id !== pkgId) continue;
      entry.confirmed = true;
      entry.done = {};
      entry.questions = {};
      entry.fieldSets = {};
      await saveEntry(Number(tabId), entry);
      handle(Number(tabId), 0, true);
    }
    chrome.runtime.sendMessage({ type:'jf-panel-confirmed', payload }).catch(() => {});
    return;
  }
  if (payload.action === 'answer-bank-saved') {
    chrome.runtime.sendMessage({ type:'jf-panel-bank-saved', payload }).catch(() => {});
    return;
  }
  if (payload.action === 'reattach' || payload.action === 'letter-ready') {
    const tabs = await tabsState();
    for (const [tabId, entry] of Object.entries(tabs)) {
      if (entry && entry.id === String(payload.id || '')) {
        entry.done = {}; entry.asked = {};
        entry.lastRefill = 0;
        entry.fieldSets = {};
        await saveEntry(Number(tabId), entry);
        handle(Number(tabId), 0, true);
        if (payload.action === 'letter-ready') {
          notice(Number(tabId), 'Letter attached', 'Check the cover letter and every form field before you submit.');
        }
      }
    }
    return;
  }
  if (payload.action !== 'answers') return;
  let filled = 0;
  const pageChanged = [];
  const expectedPage = String(payload.pageKey || '');
  const labels = new Map((payload.fields || []).map((field) => [String(field.uid || ''), String(field.label || '')]));
  let currentPage = '';
  try { currentPage = JF.pageKey((await chrome.tabs.get(Number(payload.tabId))).url || ''); }
  catch (error) { /* a closed tab is a changed page */ }
  for (const answer of payload.answers || []) {
    const uid = String(answer.uid || '');
    const match = /^f(\d+)-(.+)$/.exec(uid);
    const expectedLabel = labels.get(uid) || String(answer.label || '');
    if (!match || !answer.value) continue;
    if (!expectedPage || currentPage !== expectedPage) {
      pageChanged.push(expectedLabel);
      continue;
    }
    try {
      const [result] = await JFCore.inPage(
        { tabId: Number(payload.tabId), frameIds: [Number(match[1])] },
        (id, label, value) => window.__jfFiller.setAnswer(id, label, value),
        [match[2], expectedLabel, answer.value],
      );
      if (result && result.result && result.result.status === 'filled') filled += 1;
      else if (!result || !result.result || result.result.status === 'page_changed') pageChanged.push(expectedLabel);
    } catch (error) { pageChanged.push(expectedLabel); }
  }
  if (filled) {
    const bySource = {};
    for (const answer of payload.answers || []) {
      if (!answer.value || pageChanged.includes(labels.get(String(answer.uid || '')) || String(answer.label || ''))) continue;
      const name = String(answer.source || 'Claude');
      bySource[name] = (bySource[name] || 0) + 1;
    }
    const parts = Object.entries(bySource).map(([name, count]) => `${count} from ${name}`).join(', ');
    notice(Number(payload.tabId), `Filled ${filled} open question(s)`, `${parts}. Check every answer before you submit.`);
  }
  if (pageChanged.length) notice(Number(payload.tabId), 'Claude answers skipped: page changed', pageChanged.join(', ').slice(0, 300));
  toApp({ action: 'answers-inserted', id: String(payload.id || ''), inserted: filled, page_changed: pageChanged });
  if (payload.source === 'panel' && payload.requestId) {
    chrome.runtime.sendMessage({ type: 'jf-panel-result', payload: {
      requestId: payload.requestId, answers: payload.answers || [], rejected: payload.rejected || {},
      suggestions: payload.suggestions || [],
      needs_bank: payload.needs_bank || [], yours: payload.yours || [], reason: payload.reason || '', kind: payload.kind || '', model: payload.model || 'haiku', inserted: filled,
      pageChanged,
    } }).catch(() => {});
  } else {
    chrome.runtime.sendMessage({ type:'jf-panel-auto-result', payload: {
      id:String(payload.id || ''), answers:payload.answers || [], rejected:payload.rejected || {},
      suggestions:payload.suggestions || [], needs_bank:payload.needs_bank || [], yours:payload.yours || [],
      reason:payload.reason || '', kind:payload.kind || '', model:payload.model || 'haiku', inserted:filled,
      pageChanged, fields:payload.fields || [],
    } }).catch(() => {});
  }
}

async function notice(tabId, title, message, pkgId) {
  const id = `jf-${tabId === null ? 'x' : tabId}-${Date.now()}`;
  const options = { type: 'basic', iconUrl: 'icon128.png', title, message, priority: 2, requireInteraction: Boolean(pkgId) };
  if (pkgId) options.buttons = [{ title: '✅ Ich bewerbe mich' }, { title: 'Skip this job' }];
  chrome.notifications.create(id, options);
  if (pkgId) {
    const notes = await session('notes', {});
    notes[id] = { id: pkgId, action: 'confirm' };
    await chrome.storage.session.set({ notes });
  }
  if (tabId !== null) chrome.action.setBadgeText({ tabId, text: '!' }).catch(() => {});
}

chrome.notifications.onClicked.addListener(async (id) => {
  const tabId = Number(id.split('-')[1]);
  if (Number.isInteger(tabId)) {
    try {
      const tab = await chrome.tabs.update(tabId, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
    } catch (error) { /* the tab is gone */ }
  }
  chrome.notifications.clear(id);
});

// The two buttons of a "form ready" notification: the user's own record of what happened.
chrome.notifications.onButtonClicked.addListener(async (id, index) => {
  const notes = await session('notes', {});
  const saved = notes[id];
  chrome.notifications.clear(id);
  if (!saved) return;
  delete notes[id];
  await chrome.storage.session.set({ notes });
  const pkgId = typeof saved === 'string' ? saved : saved.id;
  toApp(index === 0 ? { action: 'confirm', id: pkgId } : { action: 'outcome', id: pkgId, outcome: 'skipped' });
});

// Runs in an Arbeitsagentur job page: waits until the user has solved the CAPTCHA and the way to
// apply is shown, then reports the e-mail address or the employer's link. It reads only.
function watchContact() {
  if (window.__jfWatching) return;
  window.__jfWatching = true;
  const EMAIL = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/;
  const started = Date.now();
  const look = () => {
    let email = '';
    let link = '';
    for (const root of document.querySelectorAll('[id^="detail-bewerbung"], [id*="kontakt" i], [class*="kontakt" i]')) {
      const mail = root.querySelector('a[href^="mailto:"]');
      if (!email && mail) email = mail.getAttribute('href').slice(7).split('?')[0];
      if (!email) { const found = (root.innerText || '').match(EMAIL); if (found) email = found[0]; }
      for (const anchor of root.querySelectorAll('a[href^="http"]')) {
        let host = '';
        try { host = new URL(anchor.href).hostname; } catch (error) { continue; }
        if (!link && !/(^|\.)arbeitsagentur\.de$/.test(host)) link = anchor.href;
      }
    }
    if (/@arbeitsagentur\.de$/i.test(email)) email = '';
    if (email || link) { chrome.runtime.sendMessage({ type: 'jf-contact', email, link }); return; }
    if (Date.now() - started < 15 * 60 * 1000) setTimeout(look, 1500);
  };
  look();
}

// Runs in each frame after the first fill. It only counts visible form controls and asks the
// background worker to re-fill when a collapsed section or wizard step reveals more fields.
function watchFields() {
  if (window.__jfFieldWatcher) return;
  window.__jfFieldWatcher = true;
  const count = () => Array.from(document.querySelectorAll('input, select, textarea')).filter((el) => {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 1 && box.height > 1;
  }).length;
  let previous = count();
  const started = Date.now();
  const look = () => {
    const current = count();
    if (current > previous) chrome.runtime.sendMessage({ type: 'jf-fields-grown' });
    previous = current;
    if (Date.now() - started < 15 * 60 * 1000) setTimeout(look, 2000);
  };
  setTimeout(look, 2000);
}

async function onContact(tabId, found) {
  const entry = (await tabsState())[tabId];
  if (!entry || entry.contactSent) return;
  entry.contactSent = true;
  if (found.link && !found.email) {
    // The employer's own form: open it in this tab, where it is filled as usual.
    Object.assign(entry, { hops: 0, done: {}, asked: {}, questions: {} });
    await saveEntry(tabId, entry);
    toApp({ action: 'contact', id: entry.id, email: '', link: found.link });
    chrome.tabs.update(tabId, { url: found.link });
    return;
  }
  await saveEntry(tabId, entry);
  toApp({ action: 'contact', id: entry.id, email: found.email || '', link: found.link || '' });
}

async function handle(tabId, attempt = 0, refill = false) {
  const { auto } = await chrome.storage.local.get({ auto: true });
  if (!auto || busy.has(tabId)) return;
  busy.add(tabId);
  let retry = false;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!/^https?:/.test(tab.url || '')) return;
    const host = hostOf(tab.url);
    let entry = (await tabsState())[tabId];
    if (refill && entry && Date.now() - Number(entry.lastRefill || 0) < 5000) return;
    if (refill && entry) entry.lastRefill = Date.now();
    const current = await session('current', '');
    if (!entry) {
      const hosts = await knownHosts();
      const packageHost = hosts.some((known) => host === known || host.endsWith(`.${known}`));
      if (!packageHost && !(current && JF.looksLikeApplication(tab.url, tab.title))) return;
    }
    const data = await JFCore.loadData();
    const { remembered = {} } = await chrome.storage.local.get({ remembered: {} });
    const page = strip(tab.url);
    const choice = JF.pickPackage(data.packages, tab.url, tab.title, current, remembered);
    if (choice.pkg && (!entry || entry.id !== choice.pkg.id)) {
      entry = { id: choice.pkg.id, hops: 0, done: {}, asked: {}, questions: {}, fieldSets: {} };
    } else if (!choice.pkg && (!entry || (current && entry.id !== current))) {
      return;
    }
    const pkg = entry && data.packages.find((p) => p.id === entry.id);
    if (!pkg || (entry.done[page] && !refill)) return;
    entry.asked = entry.asked || {};
    entry.questions = entry.questions || {};
    entry.fieldSets = entry.fieldSets || {};
    const look = await JFCore.inspect(tabId);
    const step = JF.decide(look, { hops: entry.hops, host: new URL(tab.url).hostname });
    const who = `${pkg.employer}: ${pkg.title}`.slice(0, 90);
    if (step.action === 'fill') {
      const fields = await JFCore.fillTab(tab, pkg, data);
      const fieldSet = fields.filter((field) => !field.uploadWarning)
        .map((field) => `${field.frameId}:${field.kind}:${String(field.label || '').toLowerCase().replace(/\s+/g, ' ').trim()}`)
        .sort().join('|');
      if (refill && entry.fieldSets[page] === fieldSet) {
        entry.done[page] = true;
        await saveEntry(tabId, entry);
        return;
      }
      entry.fieldSets[page] = fieldSet;
      const bundle = JFCore.fillMessages(tab, pkg, fields, look, entry.questions[page] || []);
      const sum = bundle.summary;
      entry.done[page] = true;
      const yours = bundle.yours;
      bundle.messages.forEach((message) => toApp(message));
      entry.questions[page] = bundle.seen;
      if (!refill) {
        const uploadWarning = bundle.messages[0].upload_warning ? ` ${bundle.messages[0].upload_warning}.` : '';
        notice(tabId, sum.open || look.password ? 'Form filled, needs you' : 'Form ready for your check',
          `${who}\n${sum.filled} field(s) filled, ${sum.attached} upload(s) attached.${uploadWarning} Yours: ${yours.concat('final check', 'submit').join(', ')}.`, pkg.id);
      } else if (sum.filled + sum.attached > 0 && Date.now() - (entry.lastMore || 0) >= 30000) {
        entry.lastMore = Date.now();
        notice(tabId, 'More fields filled', `${who}\nCheck the newly revealed fields before you submit.`);
      }
      await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: watchFields });
    } else if (step.action === 'follow') {
      entry.hops += 1;
      await saveEntry(tabId, entry);
      await chrome.tabs.update(tabId, { url: step.url });
      return;
    } else if (isAfa(host)) {
      // The way to apply appears only after the CAPTCHA: watch for it and hand it to jobfilter.
      await chrome.scripting.executeScript({ target: { tabId }, func: watchContact });
      if (!entry.asked[page]) {
        entry.asked[page] = true;
        toApp({ action: 'form-state', id: pkg.id, state: 'needs', reason: 'CAPTCHA' });
        notice(tabId, 'Arbeitsagentur: solve the CAPTCHA once',
          `${who}\nShow the contact details with the CAPTCHA. jobfilter then reads the e-mail address or link and prepares the application.`);
      }
    } else if (attempt < RETRY_MS.length && !entry.asked[page]) {
      retry = true;  // the form may still be loading
    } else if (!entry.asked[page]) {
      entry.asked[page] = true;  // said once; the page is looked at again whenever it reloads
      toApp({ action: 'form-state', id: pkg.id, state: 'needs', reason: step.reason });
      notice(tabId, 'jobfilter needs you', `${who}\n${step.reason}`);
    }
    await saveEntry(tabId, entry);
  } catch (error) {
    /* pages the browser protects (store, settings) cannot be read: nothing to do */
  } finally {
    busy.delete(tabId);
  }
  if (retry) { await sleep(RETRY_MS[attempt]); handle(tabId, attempt + 1); }
}

chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === 'complete') handle(tabId); });
// A form that opens in a new tab keeps the application of the tab it came from.
chrome.tabs.onCreated.addListener(async (tab) => {
  if (!tab.openerTabId) return;
  const parent = (await tabsState())[tab.openerTabId];
  if (parent) await saveEntry(tab.id, { id: parent.id, hops: parent.hops, done: {}, asked: {}, questions: {} });
});
chrome.tabs.onRemoved.addListener((tabId) => { saveEntry(tabId, null); });
