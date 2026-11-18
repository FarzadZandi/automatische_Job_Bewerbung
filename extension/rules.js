// Pure helpers shared by the panel and background worker: package matching, file assignment and page decisions.
(function (root) {
  const norm = (value) => (value || '').toString().toLowerCase()
    .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss');
  const GENERIC = new Set(['gmbh', 'ag', 'kg', 'co', 'und', 'the', 'der', 'die', 'das', 'eine', 'marke', 'germany', 'deutschland',
    'group', 'holding', 'se', 'mbh', 'ohg', 'perm', 'tech', 'experts', 'for', 'fuer', 'mit', 'von', 'im', 'in', 'm', 'w', 'd']);
  const tokens = (text) => norm(text).split(/[^a-z0-9]+/).filter((t) => t.length >= 3 && !GENERIC.has(t));

  function pageKey(value) {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase().replace(/^www\./, '');
      const path = url.pathname.replace(/\/+$/, '') || '/';
      return `${host}${path}`;
    } catch (error) { return ''; }
  }

  const labelKey = (value) => norm(value).replace(/[*:()\[\]"„“”]/g, ' ').replace(/\s+/g, ' ').trim();
  const sameFieldLabel = (expected, current) => Boolean(labelKey(expected)) && labelKey(expected) === labelKey(current);
  const ANSWER_BANK = /notice period|k[uü]ndigungsfrist|start date|earliest.{0,30}start|eintritt|verf[uü]gbar|startdatum|\bstarten\b|wann.{0,40}(?:beginnen|starten|anfangen)|salary|gehalt|verdienen|jahresbrutto|verg[uü]tung|gesamtverg[uü]tung|other languages|weitere sprachen|relocat|umzug|travel|reise|driving licen[cs]e|f[uü]hrerschein|work authori[sz]ation|work permit|arbeitserlaubnis|years? of experience|how many years|erfahrung in jahren|wie viele.{0,30}jahr|rate your.{0,80}(?:scale|skala)|bewerte.{0,80}(?:scale|skala)|do you have.{0,100}experience|experience (?:working|building|using|with|in)|erfahrung.{0,60}(?:mit|in)|hr tech domain/i;
  const CLAUDE_YOURS = /salary|gehalt|verg[uü]tung|compensation|birth|geburt|nationality|citizenship|staatsangeh[oö]rig|residence|aufenthalt|visa|visum|work permit|arbeitserlaubnis|disabil|behinder|health|gesund|religion|union|gewerkschaft|criminal|vorstraf|marital|familienstand|gender|geschlecht|consent|privacy|datenschutz|terms|bedingungen|captcha|password|passwort/i;

  function claudeFieldMode(field) {
    const kind = String(field && field.kind || '').toLowerCase();
    const label = String(field && field.label || '');
    if (['text', 'textarea'].includes(kind) && ANSWER_BANK.test(label)) return 'bank';
    if (['checkbox', 'file', 'password'].includes(kind) || CLAUDE_YOURS.test(label)) return 'yours';
    if (['text', 'textarea'].includes(kind)) return 'answer';
    if (['select', 'radio'].includes(kind)) return 'suggest';
    return 'yours';
  }

  function claudeAvailability(payload) {
    const status = payload || {};
    const login = status.login || 'claude auth login';
    if (status.connected === false || status.error) {
      return { state: 'blocked', enabled: false, reason: 'jobfilter app not connected: refresh the jobfilter window' };
    }
    if (status.writer !== 'claude') {
      return { state: 'blocked', enabled: false, reason: 'letter_writer is standard: Claude answers are off' };
    }
    if (status.installed === false || status.kind === 'not_installed') {
      return { state: 'blocked', enabled: false, reason: `Claude Code is not installed: run ${status.install || 'irm https://claude.ai/install.ps1 | iex'}` };
    }
    if (status.kind === 'limit' || /limit reached|usage limit/i.test(status.reason || '')) {
      return { state: 'blocked', enabled: false, reason: status.reason || 'Claude plan limit reached; wait for the reset shown by Claude' };
    }
    if (status.kind === 'sign_in') {
      return { state: 'blocked', enabled: false, reason: status.reason || `Claude is not signed in: run ${login}` };
    }
    if (status.kind === 'paid') {
      return { state: 'blocked', enabled: false, reason: status.reason || `Claude is signed in outside the Claude plan: run ${login}` };
    }
    if (status.kind === 'error') {
      return { state: 'blocked', enabled: false, reason: status.reason || 'Claude could not answer; test the Claude connection again.' };
    }
    if (status.signed_in === null) {
      return { state: 'unknown', enabled: true, reason: 'Claude sign-in could not be read; the first request will check it.' };
    }
    if (status.signed_in === false) {
      return { state: 'blocked', enabled: false, reason: `Claude is not signed in: run ${login}` };
    }
    if (status.plan === true) {
      return { state: 'ready', enabled: true, reason: 'Claude Haiku is ready.' };
    }
    return {
      state: 'blocked', enabled: false,
      reason: `Claude is signed in outside the Claude plan: run ${login}`,
    };
  }

  function cachedClaudeAvailability(record, now = Date.now(), ttl = 600000) {
    if (!record || !Number.isFinite(Number(record.at)) || now - Number(record.at) >= ttl) return null;
    return claudeAvailability(record.payload);
  }

  function bestFieldForQuestion(question, fields) {
    const synonyms = (value) => norm(value)
      .replace(/notice period|kuendigungsfrist/g, ' notice ')
      .replace(/why|warum|weshalb/g, ' reason ')
      .replace(/role|position|stelle/g, ' position ')
      .replace(/experience|erfahrung/g, ' experience ');
    const wanted = new Set(tokens(synonyms(question)));
    let best = null; let score = 0;
    for (const field of fields || []) {
      const candidate = new Set(tokens(synonyms(field.label || '')));
      const overlap = [...wanted].filter((word) => candidate.has(word)).length;
      const share = overlap / Math.max(1, new Set([...wanted, ...candidate]).size);
      const exact = labelKey(question) === labelKey(field.label || '') ? 2 : 0;
      if (exact + share > score) { best = field; score = exact + share; }
    }
    return best;
  }

  const stripQuery = (url) => (url || '').split(/[?#]/)[0].replace(/\/$/, '');
  const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };
  // Job boards may submit a stored profile from their own buttons, so they are never recognised
  // as employer application forms and their links are never followed automatically.
  const NO_FOLLOW = /(^|\.)(indeed|xing|linkedin|stepstone|arbeitsagentur|glassdoor|kununu|monster)\./i;
  const ATS_HOSTS = /(^|\.)(avature|successfactors|workday|myworkdayjobs|personio|softgarden|greenhouse|lever|smartrecruiters|join\.com|onlyfy|dvinci|rexx|umantis|concludis|recruitee|teamtailor|icims|taleo|ashbyhq|helixjobs|bewerbermanagement)(\.|$)/i;
  // Exact employer hosts where the read-only Round 4 survey found a form although the
  // editorial-looking /karriere path alone deliberately remains insufficient.
  const SURVEY_APPLICATION_HOSTS = /^(?:www\.)?(?:commerzbank\.de|dsv\.com|gpanrw\.de)$/i;

  function isJobBoard(hostOrUrl) {
    const value = hostOrUrl || '';
    const host = value.includes('://') ? hostOf(value) : value.replace(/^www\./, '');
    return NO_FOLLOW.test(host);
  }

  function looksLikeApplication(url, title) {
    const host = hostOf(url);
    if (!host || isJobBoard(host)) return false;
    if (/bewerb|application|apply/i.test(title || '')) return true;
    if (ATS_HOSTS.test(host)) return true;
    if (SURVEY_APPLICATION_HOSTS.test(host)) return true;
    const hostParts = host.split(/[.\-]+/).filter(Boolean);
    if (hostParts.some((part) => /^(?:bewerb\w*|karriere|career|careers|jobs?|stellen?|recruit\w*|talent|register)$/.test(part))) return true;
    let pathParts = [];
    try { pathParts = new URL(url).pathname.split(/[\/\-]+/).filter(Boolean); } catch (e) { return false; }
    // Generic editorial career sections such as spiegel.de/karriere are deliberately excluded.
    return pathParts.some((part) => /^(?:bewerb\w*|apply|application|jobs?|stellen?|recruit\w*|register)$/i.test(part));
  }

  function packageHasHost(pkg, host) {
    const hosts = (pkg.hosts || []).concat((pkg.urls || []).map(hostOf));
    return hosts.some((known) => known && (host === known || host.endsWith(`.${known}`)));
  }

  // Exact page, then the app's current package, then an unambiguous remembered employer host.
  function pickPackage(packages, url, title, current, remembered) {
    const page = stripQuery(url);
    const host = hostOf(url);
    const exact = packages.find((pkg) => (pkg.urls || []).some((known) => stripQuery(known) === page));
    if (exact) return { pkg: exact, reason: 'exact URL' };
    const currentPackage = current && packages.find((pkg) => pkg.id === current);
    if (currentPackage && !isJobBoard(host) && packageHasHost(currentPackage, host)) {
      return { pkg: currentPackage, reason: 'current package on its own host' };
    }
    if (current && looksLikeApplication(url, title)) {
      if (currentPackage) return { pkg: currentPackage, reason: 'current package' };
    }
    if (!current && remembered && remembered[host]) {
      const onHost = packages.filter((pkg) => packageHasHost(pkg, host));
      if (onHost.length === 1 && onHost[0].id === remembered[host].id) {
        return { pkg: onHost[0], reason: 'unambiguous remembered host' };
      }
    }
    return { pkg: null, reason: 'no safe package match' };
  }

  // Which package belongs to the open page? Exact link first, then employer and job title.
  function matchPackage(packages, url, title, remembered) {
    const page = stripQuery(url);
    const host = hostOf(url);
    const exact = packages.find((p) => p.urls.some((u) => stripQuery(u) === page));
    if (exact) return { pkg: exact, reason: 'link of the job ad' };
    // Some employer pages rewrite a path job ID into a malformed/repeated query.
    // On the same host, a unique long numeric ID is still an exact job identity.
    const ids = new Set((url.match(/\d{7,}/g) || []));
    if (ids.size) {
      const byId = packages.filter((pkg) => packageHasHost(pkg, host) && (pkg.urls || []).some(
        (known) => (known.match(/\d{7,}/g) || []).some((id) => ids.has(id)),
      ));
      if (byId.length === 1) return { pkg: byId[0], reason: 'job ID in the link' };
    }
    if (remembered && remembered[host]) {
      const onHost = packages.filter((pkg) => packageHasHost(pkg, host));
      const kept = onHost.length === 1 && onHost[0].id === remembered[host].id && remembered[host].page === page
        ? onHost[0] : null;
      if (kept) return { pkg: kept, reason: 'last chosen for this page' };
    }
    let decoded = url || '';
    try { decoded = decodeURIComponent(decoded); } catch (e) { /* keep the raw URL */ }
    const haystack = norm(`${decoded} ${title}`);
    const scored = packages.map((pkg) => {
      const employer = tokens(pkg.employer).filter((t) => haystack.includes(t)).length;
      const words = tokens(pkg.title);
      const share = words.length ? words.filter((t) => haystack.includes(t)).length / words.length : 0;
      return { pkg, employer, share, score: share * 3 + Math.min(employer, 2) + (pkg.hosts.includes(host) ? 1 : 0) };
    }).sort((a, b) => b.score - a.score);
    const best = scored[0];
    if (best && (best.share >= 0.7 || (best.employer >= 1 && best.share >= 0.3))) {
      return { pkg: best.pkg, reason: 'job title found on the page' };
    }
    const sameEmployer = scored.filter((item) => item.employer >= 1);
    if (sameEmployer.length === 1) return { pkg: sameEmployer[0].pkg, reason: 'employer found, please check the job' };
    return { pkg: null, reason: 'No application recognised. Choose one at the top.' };
  }

  // Which files go into which upload field, within the limits the form states next to the field.
  function assignFiles(fields, pkg) {
    const uploads = fields.filter((f) => f.kind === 'file');
    const plan = {};
    const used = new Set();
    const letterAlreadyPresent = fields.some((field) =>
      (field.key === 'cover_letter_text' && field.filled)
      || (field.kind === 'file' && field.filled && (
        field.key === 'letter' || (field.autoFiles || []).some((name) => /anschreiben|cover.?letter|motivation/i.test(String(name)))
      )));
    const identity = (file) => file && (file.path || file.disk_path || file.name);
    const accept = (field, file) => {
      if (!file) return false;
      const limitBytes = field.maxMb ? field.maxMb * 1024 * 1024 : Infinity;
      if (file.size && file.size > limitBytes) return false;
      const rule = String(field.accept || '').trim().toLowerCase();
      if (!rule) return true;
      return rule.split(',').some((part) => {
        const token = part.trim();
        return token === 'application/pdf' || token === '.pdf' || token === 'application/*' || token === '*/*';
      });
    };
    const put = (field, wanted, originals = wanted) => {
      const candidates = wanted.filter((file) => file && !used.has(identity(file)));
      const fits = candidates.filter((file) => accept(field, file));
      const files = fits.slice(0, field.multiple ? (field.maxFiles || fits.length) : 1);
      files.forEach((file) => used.add(identity(file)));
      // A combined file represents the original documents, so those cannot be attached again.
      if (files.some((file) => file === pkg.combined)) [pkg.letter, pkg.cv, ...(pkg.attachments || [])].forEach((file) => used.add(identity(file)));
      if (files.some((file) => file === pkg.letter_cv_combined)) [pkg.letter, pkg.cv].forEach((file) => used.add(identity(file)));
      if (files.some((file) => file === pkg.attachments_combined)) (pkg.attachments || []).forEach((file) => used.add(identity(file)));
      plan[field.uid] = { files, left: candidates.filter((file) => !files.includes(file)).concat(
        originals.filter((file) => file && !accept(field, file) && !candidates.includes(file)),
      ) };
    };
    const classify = (field) => {
      const label = norm(field.label);
      if (/^(datei|upload|anhang|anlage|attach|file)(en|e|s)?\*?$/.test(label.trim())) return 'generic';
      const residence = /passport|reisepass|\bvisum\b|\bvisa\b|aufenthaltstitel|aufenthaltserlaubnis|aufenthaltsdokument|residence (permit|document)|proof of (residence|work authori[sz]ation)|work (permit|authori[sz]ation)/.test(label);
      const attachments = /zeugnis|certificate|nachweis|weitere anlage|sonstige anlage|other attachment|additional document/.test(label);
      if (residence && attachments) return 'mixed_residence_attachments';
      if (residence) return 'residence';
      if (/anschreiben|bewerbungsschreiben|motivationsschreiben|cover letter|motivation letter/.test(label)) return 'letter';
      if (/lebenslauf|\bcv\b|resume/.test(label)) return 'cv';
      if (/zeugnis|certificate|nachweis|weitere anlage|additional document/.test(label)) return 'attachments';
      if (/foto|photo|picture|bild/.test(label)) return 'photo';
      if (field.key) return field.key;
      return 'generic';
    };
    const explicitLetter = uploads.find((field) => classify(field) === 'letter');
    const multipleDocuments = uploads.find((field) => field.multiple && ['documents', 'attachments'].includes(classify(field)));
    const multipleCv = uploads.find((field) => field.multiple && classify(field) === 'cv');
    const singleCv = uploads.find((field) => !field.multiple && classify(field) === 'cv');
    const bundled = uploads.find((field) => ['documents', 'generic'].includes(classify(field)));
    const letterTarget = explicitLetter || multipleDocuments || multipleCv || singleCv || bundled || null;
    const holdForLetter = (field) => pkg.letter_pending && letterTarget && field.uid === letterTarget.uid;
    const warn = (message) => Object.defineProperty(plan, 'warning', { value:message, enumerable:false, configurable:true });
    const putApplicationBundle = (field) => {
      if (pkg.combined && accept(field, pkg.combined)) {
        put(field, [pkg.combined]);
        return;
      }
      if (pkg.letter_cv_combined && accept(field, pkg.letter_cv_combined)) {
        put(field, [pkg.letter_cv_combined]);
        plan[field.uid].manual = (pkg.attachments || []).filter(Boolean);
        return;
      }
      put(field, [pkg.combined, pkg.letter_cv_combined].filter(Boolean));
    };
    if (uploads.length === 1) {
      const field = uploads[0];
      const key = classify(field);
      if (holdForLetter(field)) {
        plan[field.uid] = { files: [], left: [], held: true };
        return plan;
      }
      if (key === 'mixed_residence_attachments') {
        if (field.multiple) put(field, [...(pkg.attachments || []), ...(pkg.residence_documents || [])]);
        else {
          put(field, [pkg.attachments_combined || (pkg.attachments || [])[0]]);
          plan[field.uid].manual = (pkg.residence_documents || []).filter(Boolean);
        }
      } else if (key === 'residence') put(field, pkg.residence_documents || []);
      else if (key === 'photo') put(field, []);
      else if (key === 'letter') put(field, [pkg.letter]);
      else if (key === 'cv') put(field, field.multiple
        ? [pkg.cv, pkg.letter, ...(pkg.attachments || [])]
        : [letterAlreadyPresent ? pkg.cv : pkg.letter_cv_combined]);
      else if (key === 'attachments') {
        if (field.multiple) put(field, [pkg.cv, pkg.letter, ...(pkg.attachments || [])]);
        else putApplicationBundle(field);
      } else if (field.multiple) put(field, [pkg.cv, pkg.letter, ...(pkg.attachments || [])]);
      else putApplicationBundle(field);
      const hasLetter = (plan[field.uid].files || []).some((file) =>
        file === pkg.letter || file === pkg.combined || file === pkg.letter_cv_combined);
      if (!pkg.letter_pending && !hasLetter && !letterAlreadyPresent) warn('Cover letter could not be attached: no suitable upload field');
      return plan;
    }
    // Reserve explicit slots first, even when a generic slot appears earlier in DOM order.
    for (const field of uploads.filter((item) => classify(item) !== 'generic')) {
      const key = classify(field);
      if (holdForLetter(field)) {
        plan[field.uid] = { files: [], left: [], held: true };
        continue;
      }
      if (key === 'photo') put(field, []);
      else if (key === 'mixed_residence_attachments') {
        if (field.multiple) put(field, [...(pkg.attachments || []), ...(pkg.residence_documents || [])]);
        else {
          put(field, [pkg.attachments_combined || (pkg.attachments || [])[0]]);
          plan[field.uid].manual = (pkg.residence_documents || []).filter(Boolean);
        }
      }
      else if (key === 'residence') put(field, pkg.residence_documents || []);
      else if (key === 'cv') put(field, [pkg.cv]);
      else if (key === 'letter') put(field, [pkg.letter]);
      else if (key === 'documents') {
        if (field.multiple) put(field, [pkg.cv, ...(field.uid === letterTarget.uid ? [pkg.letter] : []), ...(pkg.attachments || [])]);
        else putApplicationBundle(field);
      }
      else if (key === 'attachments') put(field, field.multiple ? (pkg.attachments || []) : [pkg.attachments_combined]);
      else put(field, []);
    }
    const remaining = () => {
      if (pkg.cv && !used.has(identity(pkg.cv))) return [pkg.cv];
      if (pkg.letter && !used.has(identity(pkg.letter))) return [pkg.letter];
      const extras = (pkg.attachments || []).filter((file) => !used.has(identity(file)));
      return extras;
    };
    for (const field of uploads.filter((item) => classify(item) === 'generic')) {
      if (holdForLetter(field)) {
        plan[field.uid] = { files: [], left: [], held: true };
        continue;
      }
      const candidates = remaining();
      if (!field.multiple && candidates.length > 1 && pkg.attachments_combined) put(field, [pkg.attachments_combined], candidates);
      else put(field, candidates);
    }
    if (pkg.letter_pending) {
      if (!letterTarget) warn('Cover letter could not be attached: no suitable upload field');
      return plan;
    }
    const assigned = Object.values(plan).some((item) => (item.files || []).some((file) =>
      file === pkg.letter || file === pkg.combined || file === pkg.letter_cv_combined));
    if (!assigned && letterTarget) {
      const slot = plan[letterTarget.uid] || { files: [], left: [] };
      if (letterTarget.multiple && pkg.letter && accept(letterTarget, pkg.letter)
          && slot.files.length < (letterTarget.maxFiles || Infinity)) {
        slot.files.push(pkg.letter); used.add(identity(pkg.letter)); plan[letterTarget.uid] = slot;
      } else if (!letterTarget.multiple && classify(letterTarget) === 'cv' && pkg.letter_cv_combined
          && accept(letterTarget, pkg.letter_cv_combined)) {
        slot.files = [pkg.letter_cv_combined]; slot.left = [];
        used.add(identity(pkg.cv)); used.add(identity(pkg.letter)); plan[letterTarget.uid] = slot;
      } else if (!letterTarget.multiple && ['documents', 'generic'].includes(classify(letterTarget))
          && pkg.combined && accept(letterTarget, pkg.combined)) {
        slot.files = [pkg.combined]; slot.left = [];
        [pkg.cv, pkg.letter, ...(pkg.attachments || [])].forEach((file) => used.add(identity(file)));
        plan[letterTarget.uid] = slot;
      } else if (!letterTarget.multiple && ['documents', 'generic'].includes(classify(letterTarget))
          && pkg.letter_cv_combined && accept(letterTarget, pkg.letter_cv_combined)) {
        slot.files = [pkg.letter_cv_combined]; slot.left = [];
        slot.manual = (pkg.attachments || []).filter(Boolean);
        used.add(identity(pkg.cv)); used.add(identity(pkg.letter)); plan[letterTarget.uid] = slot;
      }
    }
    const nowAssigned = Object.values(plan).some((item) => (item.files || []).some((file) =>
      file === pkg.letter || file === pkg.combined || file === pkg.letter_cv_combined));
    if (!nowAssigned && !letterAlreadyPresent) warn('Cover letter could not be attached: no suitable upload field');
    return plan;
  }

  // What the automatic filler does with a page of one of the user's applications.
  function decide(look, context) {
    if (look.known >= 3 || look.files >= 1) return { action: 'fill' };
    if (look.password) return { action: 'ask', reason: 'This page asks for a login. Sign in; the form is filled as soon as it opens.' };
    const link = look.links[0];
    if (link && context.hops < 2 && !NO_FOLLOW.test(context.host || '')) return { action: 'follow', url: link };
    if (look.captcha) return { action: 'ask', reason: 'This page shows a CAPTCHA. Solve it; the form is filled as soon as it opens.' };
    return { action: 'ask', reason: 'No application form found here. Click "Bewerben" yourself; the form is filled as soon as it opens.' };
  }

  function summary(fields) {
    const count = (statuses, kinds) => fields.filter((f) => statuses.includes(f.status) && (!kinds || kinds.includes(f.kind))).length;
    return {
      filled: count(['filled', 'pick']), attached: count(['attached']),
      open: count(['unknown', 'no_match', 'no_value'], ['text', 'textarea', 'select', 'radio']),
      consent: count(['consent']),
      manualUploads: fields.filter((f) => f.kind === 'file' && f.status === 'manual').length,
    };
  }

  // Which filled or open fields may be watched so the answer bank can learn from them.
  // Profile data, uploads, passwords, consent and CAPTCHA fields are never recorded.
  const ID_KEYS = new Set(['first_name', 'last_name', 'full_name', 'email', 'email_repeat', 'phone', 'street',
    'house_number', 'street_full', 'postal_code', 'city', 'postal_city', 'address_full', 'country', 'birth_date',
    'birth_day', 'birth_month', 'birth_year', 'salutation', 'academic_title', 'nationality', 'linkedin', 'xing',
    'github', 'website']);
  const NEVER_LEARN = /passwor|captcha|datenschutz|privacy|consent|einwilligung|terms|bedingungen|agb|i agree|ich stimme|best[aä]tig|confirm|personenbezogen|newsletter|gender|geschlecht|pronoun|sponsorship|visa\b|visum|aufenthalt|disab|behinder|ethnic|race\b|veteran|religio|nationalit|staatsangeh|sexual|orientation/i;
  const JUNK_LABEL = /^\s*(?:(?:bitte\s+)?(?:aus)?w[aä]hlen|select(?:\.\.\.)?|choose|please select|option|(?:sprache|language)(?:\s+(?:aus)?w[aä]hlen|\s+select)?)\s*\*?\s*$|^(\w+)(?:\s+\1){1,}\b/i;
  function observable(field) {
    if (!field || !['text', 'textarea', 'select', 'radio'].includes(field.kind)) return false;
    if (field.key && ID_KEYS.has(field.key)) return false;
    const label = String(field.label || '');
    return (label.match(/\p{L}/gu) || []).length >= 4 && !NEVER_LEARN.test(label) && !JUNK_LABEL.test(label);
  }

  const api = {
    observable,
    matchPackage, pickPackage, assignFiles, tokens, decide, summary, pageKey, sameFieldLabel, claudeFieldMode,
    bestFieldForQuestion,
    claudeAvailability, cachedClaudeAvailability,
    looksLikeApplication, isJobBoard,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.JF = api;
})(typeof window !== 'undefined' ? window : globalThis);
