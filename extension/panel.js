// Side panel: picks the package, fills the page and sends open text questions to Claude through the local app.
const $ = (id) => document.getElementById(id);
const CLAUDE_CACHE = 'claudeStatus';
const CLAUDE_TTL = 10 * 60 * 1000;
const state = {
  data: null, pkg: null, tab: null, fields: [], open: [], answers: [], remembered: {}, requestId: '',
  requests: new Map(), bankRequests: new Map(), fieldResults: new Map(), confirmRequest: '',
  claude: { state:'blocked', enabled:false, reason:'Checking Claude connection…' },
};

function renderConfirmation(reason = '') {
  const recognised = Boolean(state.pkg);
  const confirmed = Boolean(state.pkg && state.pkg.confirmed_at);
  $('confirm-top').hidden = !recognised;
  $('confirm-apply').hidden = !recognised || confirmed;
  $('confirm-state').textContent = confirmed ? 'Bestätigt' : '';
  $('confirm-inline').hidden = !reason || !recognised || confirmed;
  $('confirm-reason').textContent = reason;
}

async function confirmPackage() {
  if (!state.pkg) return;
  state.confirmRequest = crypto.randomUUID();
  $('confirm-apply').disabled = true; $('confirm-inline-button').disabled = true;
  const response = await chrome.runtime.sendMessage({ type:'jf-panel-confirm', payload:{
    id:state.pkg.id, requestId:state.confirmRequest,
  }}).catch((error) => ({ accepted:false, error:error.message }));
  if (!response || !response.accepted) {
    $('confirm-reason').textContent = (response && response.error) || 'The jobfilter app could not be reached.';
    $('confirm-inline').hidden = false;
    $('confirm-apply').disabled = false; $('confirm-inline-button').disabled = false;
  }
}

const STATUS = {
  filled: ['filled', 'ok'], kept: ['already filled', 'muted'], unknown: ['open', 'warn'],
  no_value: ['missing in profile', 'warn'], no_match: ['option not found', 'warn'], manual: ['by hand', 'muted'],
  wrong_type: ['file type not allowed', 'bad'], gone: ['field disappeared', 'bad'], attached: ['PDF attached', 'ok'],
  waiting_letter: ['waiting for letter', 'warn'],
  consent: ['tick it yourself', 'muted'], pick: ['click the suggestion', 'warn'],
};

const setStatus = (text) => { $('status').textContent = text; };

function applyClaudeStatus(payload, save = true) {
  state.claude = JF.claudeAvailability(payload);
  $('claude-status').textContent = state.claude.reason;
  if (save) chrome.storage.session.set({ [CLAUDE_CACHE]: { at:Date.now(), payload } }).catch(() => {});
  if (state.fields.length) renderResults(false);
}

async function copy(text, button) {
  await navigator.clipboard.writeText(text);
  if (button) { const old = button.textContent; button.textContent = 'Copied'; setTimeout(() => { button.textContent = old; }, 1200); }
}

async function activeTab() {
  const forced = new URLSearchParams(location.search).get('tabId');
  if (forced) return chrome.tabs.get(Number(forced));
  // A separate jobfilter app window may be the last focused Edge window. The
  // side panel must use the tab in its own window, not that unrelated window.
  const win = await chrome.windows.getCurrent();
  const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
  if (!tab) throw new Error('No active page in this Edge window.');
  return tab;
}

async function loadData() {
  state.data = await JFCore.loadData();
  const stored = await chrome.storage.local.get({ remembered: {}, auto: true, claudeInstruction: '' });
  state.remembered = stored.remembered;
  $('auto').checked = stored.auto;
  if (!$('claude-instruction').matches(':focus')) $('claude-instruction').value = stored.claudeInstruction;
}

function renderPackages() {
  const select = $('package');
  select.replaceChildren();
  const none = new Option('Please choose', '');
  select.add(none);
  for (const pkg of state.data.packages) select.add(new Option(`${pkg.employer}: ${pkg.title}`.slice(0, 90), pkg.id));
  select.value = state.pkg ? state.pkg.id : '';
}

function row(name, value, tagText, tagClass, button) {
  const li = document.createElement('li');
  li.className = 'row';
  const left = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'name';
  title.textContent = name;
  left.append(title);
  if (value) { const v = document.createElement('div'); v.className = 'value'; v.textContent = value; left.append(v); }
  li.append(left);
  const right = document.createElement('div');
  if (tagText) { const tag = document.createElement('span'); tag.className = `tag ${tagClass || 'muted'}`; tag.textContent = tagText; right.append(tag); }
  if (button) right.append(button);
  li.append(right);
  return li;
}

function renderCopyLists() {
  const values = $('values');
  const files = $('files');
  values.replaceChildren();
  files.replaceChildren();
  if (!state.data) return;
  const p = state.data.profile;
  const street = [p.street, p.house_number].filter(Boolean).join(' ');
  const entries = [
    ['Salutation', p.salutation], ['First name', p.first_name], ['Last name', p.last_name], ['Email', p.email], ['Phone', p.phone],
    ['Street and house number', street], ['Postal code', p.postal_code], ['City', p.city], ['Country', p.country],
    ['Date of birth', p.birth_date], ['Nationality', p.nationality], ['LinkedIn', p.linkedin], ['Xing', p.xing],
    ['GitHub', p.github], ['Start date', p.start_date], ['Salary expectation', p.salary], ['Notice period', p.notice_period],
  ];
  if (state.pkg) entries.push(['Cover letter as text', state.pkg.letter_text]);
  for (const [name, value] of entries) {
    if (!value) continue;
    const button = document.createElement('button');
    button.className = 'ghost';
    button.textContent = 'Copy';
    button.addEventListener('click', () => copy(value, button));
    values.append(row(name, value, '', '', button));
  }
  if (!state.pkg) return;
  for (const file of [state.pkg.cv, state.pkg.letter, ...state.pkg.attachments]) {
    const button = document.createElement('button');
    button.className = 'ghost';
    button.textContent = 'Copy path';
    button.addEventListener('click', () => copy(file.disk_path, button));
    files.append(row(file.name, file.disk_path, '', '', button));
  }
}

async function refresh() {
  try {
    await loadData();
    state.tab = await activeTab();
    const match = JF.matchPackage(state.data.packages, state.tab.url || '', state.tab.title || '', state.remembered);
    state.pkg = match.pkg;
    renderPackages();
    $('match').textContent = match.pkg ? `Recognised: ${match.reason}.` : match.reason;
    renderConfirmation();
    renderCopyLists();
    setStatus(`${state.data.packages.length} applications loaded, as of ${state.data.generated.replace('T', ' ')}.`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    await checkClaude(false).catch(() => applyClaudeStatus({ connected:false, writer:'claude' }));
    if (typeof loadChat === 'function') loadChat().catch(() => {});
  }
}

const inPage = JFCore.inPage;

async function scan() {
  state.tab = await activeTab();
  state.fields = (await JFCore.scan(state.tab.id)).map((f) => {
    let status = 'unknown';
    if (f.filled) status = 'kept';
    else if (f.kind === 'checkbox') status = 'consent';
    else if (f.key || f.kind === 'file') status = 'manual';
    return { ...f, status };
  });
  renderResults(true);
}

async function fill() {
  if (!state.pkg) { setStatus('Choose the application at the top first.'); return; }
  $('fill').disabled = true;
  try {
    state.tab = await activeTab();
    state.fields = await JFCore.fillTab(state.tab, state.pkg, state.data);
    const bundle = JFCore.fillMessages(state.tab, state.pkg, state.fields);
    await chrome.runtime.sendMessage({ type: 'jf-panel-fill-result', payload: bundle.messages });
    await checkClaude(false);
    renderResults(false);
  } catch (error) {
    setStatus(`This page cannot be filled: ${error.message}`);
  } finally {
    $('fill').disabled = false;
  }
}

function renderResults(preview) {
  const list = $('results');
  list.replaceChildren();
  $('result-box').hidden = false;
  const shown = state.fields.filter((f) => !(f.kind === 'checkbox' && f.filled));
  for (const field of shown) {
    let [text, cls] = STATUS[field.status] || [field.status, 'muted'];
    if (field.key && ['filled', 'kept'].includes(field.status)) { text = 'profile'; cls = 'ok'; }
    if (preview && field.status === 'manual' && field.key && !field.manualOnly) { text = 'will be filled'; cls = 'ok'; }
    const name = `${field.label.replace(/^[\s*]+|[\s*]+$/g, '')}${field.required ? ' *' : ''}`;
    const needs = ['unknown', 'no_match', 'no_value', 'manual'].includes(field.status) && ['text', 'textarea', 'select', 'radio'].includes(field.kind);
    if (!needs) { list.append(row(name.slice(0, 120), field.value && field.status !== 'kept' ? String(field.value).slice(0, 260) : '', text, cls)); continue; }
    const details = document.createElement('details'); details.className = 'field-row';
    const summary = document.createElement('summary'); summary.textContent = `${name.slice(0, 120)} · ${field.kind} · ${text}`; details.append(summary);
    const body = document.createElement('div'); body.className = 'field-body';
    if ((field.options || []).length) { const options = document.createElement('div'); options.className = 'options'; options.textContent = `Options: ${(field.options || []).join(' | ')}`; body.append(options); }
    const mode = JF.claudeFieldMode(field);
    const saved = state.fieldResults.get(field.uid);
    if (saved && saved.source) { summary.textContent = `${name.slice(0, 120)} · ${field.kind} · ${saved.source}`; }
    else if (saved && saved.status === 'add to answer bank') { summary.textContent = `${name.slice(0, 120)} · ${field.kind} · add to answer bank`; }
    if (mode === 'bank' || (saved && saved.status === 'add to answer bank')) {
      const input = document.createElement('input'); input.type = 'text'; input.maxLength = 600; input.placeholder = 'Enter once and save for later forms';
      const scope = document.createElement('select'); scope.append(new Option('Any employer', 'any employer'), new Option('This employer only', 'this employer only'));
      const save = document.createElement('button'); save.textContent = 'Add to answer bank';
      save.addEventListener('click', () => saveBankAnswer(field, input.value, scope.value, save));
      body.append(input, scope, save);
    } else if (mode === 'yours') { const note = document.createElement('div'); note.className = 'hint'; note.textContent = 'yours'; body.append(note); }
    else {
      const packageReason = !state.pkg ? 'Choose the application at the top before asking Claude.' : '';
      const ask = document.createElement('button'); ask.textContent = saved ? 'Try again' : 'Ask Claude'; ask.disabled = !state.claude.enabled || !state.pkg;
      ask.addEventListener('click', () => askFields([field], mode, Boolean(saved), ask).catch((error) => {
        state.fieldResults.set(field.uid, { reason:error.message, status:'rejected' }); renderResults(false);
      })); body.append(ask);
      if (ask.disabled) {
        const reason = document.createElement('div'); reason.className = 'hint ask-disabled-reason';
        reason.textContent = packageReason || state.claude.reason; body.append(reason);
      }
    }
    if (saved) {
      const answer = document.createElement('div'); answer.className = saved.status === 'rejected' ? 'error' : 'value';
      answer.textContent = [saved.value, saved.reason, saved.status].filter(Boolean).join('\n'); body.append(answer);
      if (saved.source === 'Claude' && saved.value) {
        const keep = document.createElement('input'); keep.type = 'checkbox';
        const scope = document.createElement('select'); scope.append(new Option('Any employer', 'any employer'), new Option('This employer only', 'this employer only'));
        const label = document.createElement('label'); label.className = 'hint'; label.append(keep, ' Save for later forms ', scope); body.append(label);
        keep.addEventListener('change', () => { if (keep.checked) saveBankAnswer(field, saved.value, scope.value, keep); });
      }
      if (saved.status === 'suggested') {
        const use = document.createElement('button'); use.className = 'primary'; use.textContent = 'Use this';
        use.addEventListener('click', async () => {
          const response = await chrome.runtime.sendMessage({ type:'jf-panel-use-suggestion', payload:{
            tabId:state.tab.id, pageKey:JF.pageKey(state.tab.url || ''), uid:field.uid, label:field.label, value:saved.value,
          }});
          saved.status = response && response.status || 'page changed'; state.fieldResults.set(field.uid, saved); renderResults(preview);
        });
        body.append(use);
      }
    }
    details.append(body); list.append(details);
  }
  const filled = state.fields.filter((f) => ['filled', 'attached', 'pick'].includes(f.status)).length;
  state.open = state.fields.filter((f) => ['unknown', 'no_match', 'no_value', 'manual'].includes(f.status) && ['text', 'textarea', 'select', 'radio'].includes(f.kind));
  const missing = state.fields.filter((f) => f.status === 'no_value').length;
  const manualUploads = state.fields.filter((f) => f.kind === 'file' && f.status === 'manual').length;
  $('summary').textContent = preview
    ? `(${state.fields.length} fields found)`
    : `(${filled} filled, ${state.open.length} open${manualUploads ? `, ${manualUploads} manual upload${manualUploads === 1 ? '' : 's'}` : ''}${missing ? `, ${missing} missing in profile` : ''})`;
  const claudeOpen = state.open.filter((field) => JF.claudeFieldMode(field) === 'answer');
  $('ai-box').hidden = !claudeOpen.length || !state.pkg;
  $('ask-claude').disabled = !state.claude.enabled || !state.pkg || !claudeOpen.length;
  $('answers').replaceChildren();
  $('ai-error').textContent = '';
  if (state.open.length) $('claude-status').textContent = state.claude.reason;
  if (!state.fields.length) setStatus('No form was found on this page. Is the application form already open?');
}

function renderClaudeResult(result, requestedUids = []) {
  state.answers = result.answers || [];
  const changed = new Set(result.pageChanged || []);
  for (const answer of result.answers || []) state.fieldResults.set(answer.uid, { value:answer.value, source:answer.source || 'Claude', status:changed.has(answer.label) ? 'page changed' : (answer.source || 'Claude') });
  for (const suggestion of result.suggestions || []) state.fieldResults.set(suggestion.uid, { value:suggestion.value, reason:suggestion.reason, status:'suggested' });
  for (const [label, problems] of Object.entries(result.rejected || {})) {
    const field = state.fields.find((item) => item.label === label); if (field) state.fieldResults.set(field.uid, { reason:(problems || []).join('; '), status:'rejected' });
  }
  for (const item of result.needs_bank || []) state.fieldResults.set(item.uid, { reason:'No approved reusable answer yet.', status:'add to answer bank', classification:item });
  if (result.reason && !(result.answers || []).length && !(result.suggestions || []).length) {
    for (const uid of requestedUids) state.fieldResults.set(uid, { reason:result.reason, status:'rejected' });
  }
  for (const label of result.yours || []) {
    const field = state.fields.find((item) => item.label === label);
    if (field) state.fieldResults.set(field.uid, { reason:'This needs your own factual answer.', status:'yours' });
  }
  if (result.kind && result.kind !== 'confirmation') applyClaudeStatus({ connected:true, writer:'claude', kind:result.kind, reason:result.reason });
  renderResults(false);
  const list = $('answers');
  for (const answer of state.answers) {
    const moved = changed.has(answer.label);
    list.append(row(answer.label || 'Open question', answer.value || '', moved ? 'page changed' : (answer.source || 'Claude'), moved ? 'warn' : 'ok'));
  }
  for (const [label, problems] of Object.entries(result.rejected || {})) {
    list.append(row(label, (problems || []).join('; '), 'rejected', 'bad'));
  }
  $('ask-claude').disabled = !state.claude.enabled;
  if (result.reason) $('ai-error').textContent = result.reason;
  if (result.kind === 'confirmation') renderConfirmation(result.reason || 'Confirm this application before asking Claude.');
  const manual = (result.yours || []).length ? ` ${(result.yours || []).length} personal or selection field(s) stayed with you.` : '';
  const skipped = changed.size ? ` ${changed.size} skipped because the page changed.` : '';
  $('claude-status').textContent = `Claude ${result.model || 'Haiku'} returned ${state.answers.length} checked answer(s); ${result.inserted || 0} inserted.${skipped}${manual}`;
}

async function saveBankAnswer(field, value, scope, button) {
  const answer = String(value || '').trim();
  if (!answer) { state.fieldResults.set(field.uid, { reason:'Enter the approved answer first.', status:'add to answer bank' }); renderResults(false); return; }
  const requestId = crypto.randomUUID();
  state.bankRequests.set(requestId, { field, value:answer, button });
  if (button) button.disabled = true;
  const response = await chrome.runtime.sendMessage({ type:'jf-panel-save-answer', payload:{
    requestId, kind:'saved', label:field.label, value:answer,
    language:/\b(wie|warum|welche|haben sie|erfahrung|jahre)\b|[äöüß]/i.test(field.label) ? 'de' : 'en',
    scope, employer:state.pkg && state.pkg.employer || '',
  }});
  if (!response || !response.accepted) {
    state.bankRequests.delete(requestId);
    state.fieldResults.set(field.uid, { reason:(response && response.error) || 'The answer bank could not be reached.', status:'rejected' });
    renderResults(false);
  }
}

async function askFields(fields, mode = 'answer', force = false, button = null) {
  if (!state.pkg) throw new Error('Choose the application at the top before asking Claude.');
  if (!state.tab || !fields.length) throw new Error('Detect the fields again before asking Claude.');
  state.requestId = crypto.randomUUID(); state.requests.set(state.requestId, fields.map((field) => field.uid));
  $('claude-status').textContent = 'Sending the recognised questions to Claude Haiku through the local jobfilter app…';
  $('ask-claude').disabled = true;
  if (button) button.disabled = true;
  $('answers').replaceChildren();
  $('ai-error').textContent = '';
  const response = await chrome.runtime.sendMessage({ type: 'jf-panel-questions', payload: {
    requestId: state.requestId, id: state.pkg.id, tabId: state.tab.id,
    pageKey: JF.pageKey(state.tab.url || ''),
    instruction: $('claude-instruction').value.trim(),
    mode, force,
    fields: fields.slice(0, 30).map((field) => ({
      uid: field.uid, frameId: field.frameId, label: field.label, kind: field.kind,
      options: field.options || [], maxLength: field.maxLength || null, required: Boolean(field.required),
    })),
  } });
  if (!response || !response.accepted) {
    const reason = (response && response.error) || 'The request could not be sent.';
    applyClaudeStatus({ connected:false, writer:'claude', error:reason });
    $('ai-error').textContent = reason;
    return;
  }
  if (response.connected) $('claude-status').textContent = 'Claude Haiku is answering. Keep jobfilter and this form open.';
  else applyClaudeStatus({ connected:false, writer:'claude' });
}

async function askClaude() {
  const fields = state.open.filter((field) => JF.claudeFieldMode(field) === 'answer').slice(0, 30);
  return askFields(fields, 'answer', true);
}

async function checkClaude(force = false) {
  if (!force) {
    const stored = (await chrome.storage.session.get({ [CLAUDE_CACHE]: null }))[CLAUDE_CACHE];
    const cached = JF.cachedClaudeAvailability(stored, Date.now(), CLAUDE_TTL);
    if (cached) {
      state.claude = cached; $('claude-status').textContent = cached.reason;
      if (state.fields.length) renderResults(false);
      return;
    }
  } else await chrome.storage.session.remove(CLAUDE_CACHE);
  const requestId = crypto.randomUUID(); state.claudeCheckId = requestId;
  $('claude-status').textContent = 'Checking Claude without using tokens…';
  const response = await chrome.runtime.sendMessage({ type:'jf-panel-claude-check', requestId });
  if (!response || !response.connected) applyClaudeStatus({ connected:false, writer:'claude' });
}

$('reload').addEventListener('click', refresh);
$('auto').addEventListener('change', () => chrome.storage.local.set({ auto: $('auto').checked }));
$('claude-instruction').addEventListener('input', () => chrome.storage.local.set({ claudeInstruction: $('claude-instruction').value }));
$('scan').addEventListener('click', () => scan().catch((e) => setStatus(`This page cannot be read: ${e.message}`)));
$('fill').addEventListener('click', fill);
$('package').addEventListener('change', async () => {
  state.pkg = state.data.packages.find((p) => p.id === $('package').value) || null;
  $('match').textContent = state.pkg ? 'Chosen by you.' : '';
  if (state.pkg && state.tab) {
    let host = ''; try { host = new URL(state.tab.url).hostname.replace(/^www\./, ''); } catch (error) { /* no page */ }
    if (host && !JF.isJobBoard(host)) {
      state.remembered[host] = { id:state.pkg.id, page:(state.tab.url || '').split(/[?#]/)[0].replace(/\/$/, '') };
      await chrome.storage.local.set({ remembered:state.remembered });
    }
  }
  renderCopyLists();
  renderConfirmation();
  if (state.fields.length) renderResults(false);
});
$('ask-claude').addEventListener('click', () => askClaude().catch((error) => { $('ask-claude').disabled = !state.claude.enabled; $('ai-error').textContent = error.message; }));
$('test-claude').addEventListener('click', () => checkClaude(true).catch(() => applyClaudeStatus({ connected:false, writer:'claude' })));
chrome.runtime.onMessage.addListener((message) => {
  if (message && message.type === 'jf-panel-result' && message.payload && state.requests.has(message.payload.requestId)) {
    const requestedUids = state.requests.get(message.payload.requestId);
    state.requests.delete(message.payload.requestId);
    renderClaudeResult(message.payload, requestedUids);
  }
  if (message && message.type === 'jf-panel-auto-result' && message.payload && state.pkg && message.payload.id === state.pkg.id) {
    renderClaudeResult(message.payload, (message.payload.fields || []).map((field) => field.uid));
  }
  if (message && message.type === 'jf-panel-bank-saved' && message.payload) {
    const pending = state.bankRequests.get(message.payload.requestId);
    if (pending) {
      state.bankRequests.delete(message.payload.requestId);
      if (message.payload.error) state.fieldResults.set(pending.field.uid, { reason:message.payload.error, status:'rejected' });
      else {
        state.fieldResults.set(pending.field.uid, { value:pending.value, source:'saved', status:'saved' });
        chrome.runtime.sendMessage({ type:'jf-panel-use-suggestion', payload:{
          tabId:state.tab.id, pageKey:JF.pageKey(state.tab.url || ''), uid:pending.field.uid, label:pending.field.label, value:pending.value,
        }}).catch(() => {});
      }
      renderResults(false);
    }
  }
  if (message && message.type === 'jf-panel-claude-status' && message.payload && message.payload.requestId === state.claudeCheckId) {
    applyClaudeStatus({ connected:true, ...message.payload });
  }
  if (message && message.type === 'jf-panel-confirmed' && message.payload && state.pkg && message.payload.id === state.pkg.id) {
    $('confirm-apply').disabled = false; $('confirm-inline-button').disabled = false;
    if (message.payload.error) { renderConfirmation(message.payload.error); return; }
    state.pkg.confirmed_at = message.payload.confirmed_at || new Date().toISOString();
    renderConfirmation();
    if (chat.retry) sendChat(true).catch(() => {});
  }
  if (message && message.type === 'jf-panel-current' && message.payload && state.pkg && message.payload.id === state.pkg.id) {
    state.pkg.confirmed_at = message.payload.confirmed ? (message.payload.confirmed_at || new Date().toISOString()) : '';
    renderConfirmation();
  }
});
$('confirm-apply').addEventListener('click', confirmPackage);
$('confirm-inline-button').addEventListener('click', confirmPackage);
chrome.tabs.onActivated.addListener(refresh);
chrome.tabs.onUpdated.addListener((tabId, info) => { if (info.status === 'complete' && state.tab && tabId === state.tab.id) refresh(); });
refresh();


// ---------------------------------------------------------------- chat with Claude
const chat = { history: [], pending: '', active:null, retry:null };
const chatKey = () => `chat:${state.pkg ? state.pkg.id : 'none'}`;

async function loadChat() {
  const stored = await chrome.storage.session.get({ [chatKey()]: [] });
  chat.history = stored[chatKey()] || [];
  renderChat();
}

async function saveChat() {
  await chrome.storage.session.set({ [chatKey()]: chat.history.slice(-20) });
}

function insertTargets() {
  return (state.fields || []).filter((field) => ['text', 'textarea'].includes(field.kind) && field.label && !field.combobox);
}

function renderChat() {
  const log = $('chat-log');
  log.replaceChildren();
  for (const item of chat.history) {
    const box = document.createElement('div');
    box.className = `chat-msg ${item.role === 'user' ? 'user' : 'claude'}`;
    if (item.role !== 'user' && item.question) {
      const heading = document.createElement('b'); heading.textContent = `${item.number || ''}. ${item.question}`.trim();
      const answer = document.createElement('div'); answer.textContent = item.text;
      box.append(heading, answer);
    } else box.textContent = item.text;
    if (item.role !== 'user') {
      if ((item.warnings || []).length) {
        const warn = document.createElement('div'); warn.className = 'hint chat-warning';
        warn.textContent = `Prüfen: ${item.warnings.join('; ')}`; box.append(warn);
      }
      const tools = document.createElement('div'); tools.className = 'chat-tools';
      const copy = document.createElement('button'); copy.className = 'ghost'; copy.textContent = 'Copy';
      copy.addEventListener('click', async () => { await navigator.clipboard.writeText(item.text); copy.textContent = 'Copied'; });
      tools.append(copy);
      const targets = insertTargets();
      if (targets.length && state.tab) {
        const select = document.createElement('select');
        select.append(new Option('Insert into field…', ''));
        for (const field of targets) select.append(new Option(field.label.slice(0, 70), field.uid));
        const matched = JF.bestFieldForQuestion(item.question || item.text, targets);
        if (matched) select.value = matched.uid;
        const insert = document.createElement('button'); insert.textContent = 'Insert';
        insert.addEventListener('click', async () => {
          const field = targets.find((candidate) => candidate.uid === select.value);
          if (!field) return;
          const response = await chrome.runtime.sendMessage({ type: 'jf-panel-use-suggestion', payload: {
            tabId: state.tab.id, pageKey: JF.pageKey(state.tab.url || ''), uid: field.uid, label: field.label, value: item.text,
          } });
          insert.textContent = response && response.status === 'inserted' ? 'Inserted' : 'Field has text or page changed';
        });
        tools.append(select, insert);
      }
      box.append(tools);
    }
    log.append(box);
  }
  log.scrollTop = log.scrollHeight;
}

async function sendChat(retry = false) {
  const text = retry && chat.retry ? chat.retry.text : $('chat-input').value.trim();
  if (!text || chat.pending) return;
  chat.pending = crypto.randomUUID();
  const history = retry && chat.retry ? chat.retry.history : chat.history.map((item) => ({
    role:item.role, text:item.question ? `${item.question}\n${item.text}` : item.text,
  }));
  chat.active = { text, history };
  if (!retry) {
    chat.history.push({ role: 'user', text });
    $('chat-input').value = '';
    renderChat(); await saveChat();
  }
  chat.retry = null;
  $('chat-send').disabled = true;
  $('chat-status').textContent = state.pkg
    ? 'Claude Haiku is answering…'
    : 'No application is recognised on this page. Claude is answering without a job description…';
  const response = await chrome.runtime.sendMessage({ type: 'jf-panel-chat', payload: {
    requestId: chat.pending, id: state.pkg ? state.pkg.id : '', message: text, history,
  } }).catch((error) => ({ accepted: false, error: error.message }));
  if (!response || !response.accepted) {
    chat.pending = ''; $('chat-send').disabled = false;
    $('chat-status').textContent = (response && response.error) || 'The message could not be sent.';
  } else if (!response.connected) {
    $('chat-status').textContent = 'The jobfilter app is not connected: start it or refresh its window. Your message waits.';
  }
}

$('chat-send').addEventListener('click', () => sendChat());
$('chat-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) sendChat(); });
$('chat-clear').addEventListener('click', async () => { chat.history = []; chat.pending = ''; chat.active = null; chat.retry = null; $('chat-send').disabled = false; $('chat-status').textContent = ''; renderChat(); await saveChat(); });
chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== 'jf-panel-chat-answer' || !message.payload || message.payload.requestId !== chat.pending) return;
  const payload = message.payload;
  chat.pending = ''; $('chat-send').disabled = false;
  if (payload.error) {
    $('chat-status').textContent = payload.error;
    if (payload.kind === 'confirmation' && chat.active) {
      chat.retry = chat.active;
      renderConfirmation(payload.error);
    }
    return;
  }
  chat.active = null; chat.retry = null;
  const cards = (payload.answers || []).length ? payload.answers : [{ answer:payload.answer || '', warnings:payload.warnings || [], question:'' }];
  for (const card of cards) chat.history.push({
    role:'claude', text:card.answer || '', question:card.question || '', warnings:card.warnings || [], number:card.number || 1,
  });
  $('chat-status').textContent = payload.has_job_description === false
    ? 'No application was recognised, so this reply used applicant facts without a job description.' : '';
  saveChat();
  // The fields of the page, so the answer can be inserted; reading only, nothing is filled here.
  const fieldsReady = (state.fields || []).length || !state.tab ? Promise.resolve()
    : JFCore.scan(state.tab.id).then((fields) => { state.fields = fields; }).catch(() => {});
  fieldsReady.then(() => renderChat());
});
$('package').addEventListener('change', () => loadChat());
loadChat();
