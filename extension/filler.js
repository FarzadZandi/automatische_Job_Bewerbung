// jobfilter form filler. Runs inside the page (every frame). Local only: no network, no submit.
(() => {
  if (window.__jfFiller && window.__jfFiller.version === 1) return;

  const norm = (value) => (value || '').toString().toLowerCase()
    .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
    .replace(/[­​]/g, '').replace(/[*:()\[\]"„“”]/g, ' ').replace(/[\s ]+/g, ' ').trim();

  // Order matters: the first rule that matches wins.
  const RULES = [
    ['skip', /captcha|passwor[dt]|kennwort|suchbegriff|\bsuche\b|\bsearch\b|newsletter|gutschein|coupon/],
    ['skip', /vorwahl|country code|dial(l)?ing code|dial code/],
    ['email_repeat', /(mail).*(wiederhol|bestaetig|confirm|repeat|erneut)|(wiederhol|bestaetig|confirm|repeat).*(mail)/],
    ['email', /e-?mail|\bmail\b/],
    ['phone', /telefon|handy|mobil|\bphone\b|\btel\b|rufnummer/],
    ['full_name', /vor- und nachname|vor und nachname|vor- & nachname|full ?name|vollstaendiger name|^(ihr |dein )?name$/],
    ['first_name', /vorname|first ?name|given ?name|\bfname\b|rufname/],
    ['last_name', /nachname|familienname|last ?name|surname|family ?name|\blname\b|zuname/],
    ['salutation', /anrede|salutation/],
    ['academic_title', /akademischer titel|akad\. titel|^titel$|^title$/],
    ['birth_place', /geburtsort|place of birth/],
    ['birth_day', /^(?:geburts)?tag$|^birth day$|^day of birth$/],
    ['birth_month', /^(?:geburts)?monat$|^birth month$|^month of birth$/],
    ['birth_year', /^(?:geburts)?jahr$|^birth year$|^year of birth$/],
    ['birth_date', /geburtsdatum|geburtstag|date of birth|birthday|\bdob\b|geboren am/],
    ['nationality', /staatsangehoerigkeit|nationalitaet|nationality|citizenship/],
    ['street_full', /strasse.*(haus|nr)|street.*(number|no\b)|address ?line ?1|adresszeile ?1|anschrift ?1/],
    ['house_number', /hausnummer|house ?number|hausnr|haus-nr/],
    ['street', /strasse|\bstreet\b/],
    ['postal_city', /(plz|postleitzahl).*(ort|stadt)|zip.*city|postal.*city/],
    ['postal_code', /\bplz\b|postleitzahl|postal ?code|postcode|\bzip\b/],
    ['city', /^current location\b/],
    ['city', /wohnort|\bort\b|\bstadt\b|\bcity\b|\btown\b/],
    ['work_authorisation_text', /authori[sz]ed to work|eligible to work|permitted to work|entitled to work|work authori[sz]ation|work permit|arbeitserlaubnis|arbeitsberechtigung/],
    ['country', /\bland\b|\bcountry\b/],
    ['address_full', /adresse|anschrift|\baddress\b/],
    ['source', /aufmerksam.*(?:geworden|durch)|wie.*(?:von uns|stelle).*(?:erfahren|gefunden)|how did you hear|referral source|linkedin.*indeed.*stepstone.*xing/],
    ['linkedin', /linkedin/],
    ['xing', /xing/],
    ['github', /github/],
    ['website', /website|homepage|portfolio|webseite/],
    ['german_level', /deutschkenntnisse|deutsch.*niveau|^deutsch.*(?:grundkenntnisse|fliessend|verhandlungssicher|muttersprache)|german.*(?:skills|level|proficiency)/],
    ['english_level', /englischkenntnisse|englisch.*niveau|english.*(?:skills|level|proficiency)|(?:skills|level|proficiency).*english/],
    ['disability', /behinderung|schwerbehindert|schwerbehinderung|disabilit(?:y|ies)|disabled/],
    ['highest_education', /highest (?:level of )?education|highest degree|hoechster bildungsabschluss|hochschulabschluss/],
    ['current_employment', /current employment|current position|derzeitige beschaeftigung|aktuelle beschaeftigung/],
    ['other_languages', /other languages|additional languages|weitere sprachen|anderen sprachen|ausser.*(?:deutsch|englisch).*sprachen/],
    ['notice_period', /kuendigungsfrist|notice period/],
    ['start_day', /^(?:eintritts|start )?tag$|^available day$/],
    ['start_month', /^(?:eintritts|start )?monat$|^available month$/],
    ['start_year', /^(?:eintritts|start )?jahr$|^available year$/],
    ['start_date', /fruehestmoeglicher eintrittstermin|eintrittsdatum|verfuegbar ab|earliest start date|available from|notice period\s*\/\s*availability|starttermin|start ?date|startdatum|moeglicher beginn|\bstarten\b|wann.*(?:beginnen|starten|anfangen)/],
    ['salary', /gehalt|salary|verguetung|compensation|verdienen|jahresbrutto|gesamtverguetung/],
    ['cover_letter_text', /anschreiben|motivationsschreiben|motivation letter|cover ?letter/],
  ];
  const AUTOCOMPLETE = {
    'given-name': 'first_name', 'family-name': 'last_name', 'name': 'full_name', 'email': 'email',
    'tel': 'phone', 'tel-national': 'phone', 'street-address': 'address_street', 'address-line1': 'street_full',
    'postal-code': 'postal_code', 'address-level2': 'city', 'country-name': 'country', 'country': 'country',
    'bday': 'birth_date', 'bday-day': 'birth_day', 'bday-month': 'birth_month', 'bday-year': 'birth_year',
    'honorific-prefix': 'salutation',
  };
  const FILE_RULES = [
    ['residence', /passport|reisepass|\bvisum\b|\bvisa\b|aufenthaltstitel|aufenthaltserlaubnis|aufenthaltsdokument|residence (?:permit|document)|proof of (?:residence|work authori[sz]ation)|work (?:permit|authori[sz]ation)/],
    ['documents', /application documents|bewerbungsunterlagen/],
    ['photo', /application picture|bewerbungsfoto|application photo|portrait/],
    ['cv', /lebenslauf|\bcv\b|resume|curriculum|vita/],
    ['letter', /anschreiben|motivationsschreiben|cover ?letter|bewerbungs?schreiben/],
    ['attachments', /zeugnis|zertifikat|anlage|weitere|sonstige|certificate|additional|other|nachweis|unterlagen|dokument|document/],
  ];
  const SYNONYMS = {
    deutschland: ['germany', 'deutschland', 'de', 'deu', 'ger', 'bundesrepublik deutschland'],
    no: ['no', 'nein'],
    herr: ['herr', 'mr', 'mr.', 'mann', 'maennlich', 'male'],
    frau: ['frau', 'ms', 'ms.', 'mrs', 'mrs.', 'weiblich', 'female'],
    'bundesagentur fuer arbeit': ['bundesagentur fuer arbeit', 'agentur fuer arbeit', 'arbeitsagentur', 'jobboerse', 'jobboerse der bundesagentur fuer arbeit'],
    xing: ['xing'], linkedin: ['linkedin'], indeed: ['indeed'], stepstone: ['stepstone'],
    'karriereseite des unternehmens': ['company website', 'company career site', 'careers page', 'career site', 'company careers page',
      'karriereseite', 'karriereseite des unternehmens', 'unternehmenswebsite', 'finanz informatik website',
      'karriereportal', 'webseite', 'website', 'homepage', 'internet'],
  };

  const allElements = (root, selector, out = []) => {
    root.querySelectorAll(selector).forEach((el) => out.push(el));
    root.querySelectorAll('*').forEach((el) => { if (el.shadowRoot) allElements(el.shadowRoot, selector, out); });
    return out;
  };

  const visible = (el) => {
    if (el.type === 'file') return true; // upload inputs are usually hidden behind a button
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 1 && box.height > 1;
  };

  const shortText = (node, limit = 140) => {
    const text = (node && (node.innerText || node.textContent) || '').replace(/\s+/g, ' ').trim();
    return text.length <= limit ? text : '';
  };

  const isUploadHint = (text) => {
    const value = norm(text);
    return /^(?:attach|anhaengen|upload file|max(?:imal)?\.?\s*\d+(?:[.,]\d+)?\s*mb|hier kannst du dateien ziehen und ablegen)$/.test(value)
      || /^(?:drag|drop).*(?:file|datei)|^(?:file|datei).*(?:drag|drop)/.test(value);
  };

  const labelTexts = (el) => {
    const texts = [];
    const push = (text) => { const t = (text || '').replace(/\s+/g, ' ').trim(); if (t && !texts.includes(t)) texts.push(t); };
    // Stable order: ARIA reference, explicit label, wrapping label, ARIA label,
    // nearest preceding label, placeholder, then technical name/id.
    (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).forEach((id) => {
      const root = el.getRootNode();
      const node = root.getElementById ? root.getElementById(id) : document.getElementById(id);
      push(shortText(node, 200));
    });
    const root = el.getRootNode();
    if (el.id && root.querySelectorAll) {
      for (const label of root.querySelectorAll('label[for]')) {
        if (label.htmlFor === el.id) push(shortText(label, 1000));
      }
    }
    const wrapping = el.closest && el.closest('label');
    if (wrapping) push(shortText(wrapping, 1000));
    push(el.getAttribute('aria-label'));
    let node = el.parentElement;
    for (let depth = 0; depth < 2 && node; depth += 1, node = node.parentElement) {
      if (node.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 1) break;
      const preceding = Array.from(node.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6'))
        .filter((candidate) => candidate !== wrapping && !candidate.contains(el) && (candidate.compareDocumentPosition(el) & 4))
        .map((candidate) => shortText(candidate, 200)).filter(Boolean).reverse();
      const useful = preceding.find((text) => !isUploadHint(text));
      if (useful) { push(useful); break; }
    }
    push(el.getAttribute('placeholder'));
    push(technicalText(el));
    const useful = texts.filter((text) => !isUploadHint(text));
    if (useful.length) return useful;
    if (el.type === 'file') return texts.slice(0, 1);
    return [];
  };

  const adjustBareNameFields = (fields) => {
    if (!fields.some((field) => field.key === 'first_name')) return fields;
    for (const field of fields) {
      if (field.key === 'full_name' && /^(?:ihr |dein )?name$/.test(norm(field.label))) field.key = 'last_name';
    }
    return fields;
  };

  const uploadLimits = (el) => {
    let node = el;
    for (let depth = 0; depth < 5 && node.parentElement; depth += 1) {
      node = node.parentElement;
      if (node.querySelectorAll('input[type="file"]').length > 1) break;
      const text = norm(node.innerText || node.textContent || '');
      if (text.length > 700) break;
      const count = /(?:bis zu|maximal|max\.?|hoechstens|up to)\s*(\d+)\s*(?:zusaetzliche |weitere |additional )?(?:dateien|dokumente|files|documents|anhaenge)/.exec(text);
      const size = /(\d+(?:[.,]\d+)?)\s*mb/.exec(text);
      if (count || size) return { maxFiles: count ? Number(count[1]) : null, maxMb: size ? Number(size[1].replace(',', '.')) : null };
    }
    return { maxFiles: null, maxMb: null };
  };

  const technicalText = (el) => [el.getAttribute('name'), el.id, el.getAttribute('data-testid'), el.getAttribute('data-name')]
    .filter(Boolean).join(' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_\-.\[\]]+/g, ' ');

  const matchRules = (rules, text) => {
    const value = norm(text);
    if (!value) return null;
    for (const [key, pattern] of rules) if (pattern.test(value)) return key;
    return null;
  };

  const classify = (el, labels) => {
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).pop();
    if (AUTOCOMPLETE[auto]) return AUTOCOMPLETE[auto];
    if (el.type === 'email') return matchRules(RULES, labels.join(' ')) === 'email_repeat' ? 'email_repeat' : 'email';
    if (el.type === 'tel') return 'phone';
    for (const text of labels) { const key = matchRules(RULES, text); if (key) return key; }
    return matchRules(RULES, technicalText(el));
  };

  const classifyFile = (el, labels) => {
    for (const text of labels.concat([technicalText(el)])) { const key = matchRules(FILE_RULES, text); if (key) return key; }
    return null;
  };

  let counter = 0;
  const registry = new Map();
  const idFor = (el) => {
    if (!el.dataset.jfId) { counter += 1; el.dataset.jfId = `jf${counter}`; }
    registry.set(el.dataset.jfId, el);
    return el.dataset.jfId;
  };

  const isRequired = (el, labels) => el.required || el.getAttribute('aria-required') === 'true' || labels.some((t) => /\*\s*$|\(pflicht|pflichtfeld|required/i.test(t));

  // Some portals (including the University of Ulm) hide the real file input
  // inside a closed web component. The page exposes only an "Upload files"
  // button, so an extension cannot attach files through the DOM. Report such
  // controls explicitly as manual fields instead of silently omitting them.
  const manualUploadFields = (existing) => {
    const fields = [];
    const uploadWords = /\bupload files?\b|\bdatei(?:en)? hochladen\b|\bdokument(?:e)? hochladen\b/i;
    for (const button of allElements(document, 'button, [role="button"]')) {
      if (!visible(button) || button.disabled) continue;
      const buttonText = [shortText(button, 120), button.getAttribute('aria-label'), button.getAttribute('title')]
        .filter(Boolean).join(' ');
      if (!uploadWords.test(buttonText)) continue;
      let node = button.parentElement;
      let candidate = '';
      let container = null;
      for (let depth = 0; depth < 5 && node; depth += 1, node = node.parentElement) {
        // A normal file input in the same control is already handled above.
        if (node.querySelector('input[type="file"]')) { container = null; break; }
        const lines = (node.innerText || node.textContent || '').split(/[\r\n]+/)
          .map((line) => line.replace(/\s+/g, ' ').trim()).filter(Boolean);
        candidate = lines.find((line) => !uploadWords.test(line) && matchRules(FILE_RULES, line)) || '';
        if (candidate) { container = node; break; }
      }
      if (!container && !button.closest('form')) continue;
      const rawLabel = candidate || shortText(button, 120) || 'Upload files';
      const label = rawLabel.replace(/\s*-\s*(?:no files? attached|\d+ files? attached).*$/i, '').trim();
      const key = matchRules(FILE_RULES, rawLabel);
      if (existing.some((field) => field.kind === 'file' && field.key === key && norm(field.label) === norm(label))) continue;
      const context = container ? (container.innerText || container.textContent || '') : rawLabel;
      const attached = /\b[1-9]\d*\s+files?\s+attached\b/i.test(context);
      fields.push({
        id: idFor(button), label, kind: 'file', key, required: /required|pflicht/i.test(context),
        filled: attached, manualOnly: true, multiple: key !== 'photo', accept: '', maxFiles: null, maxMb: null,
      });
    }
    return fields;
  };

  const describe = () => {
    const fields = [];
    const radios = new Map();
    for (const el of allElements(document, 'input, textarea, select')) {
      const type = (el.type || el.tagName).toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search', 'range', 'color'].includes(type)) continue;
      if (el.disabled || el.readOnly || !visible(el)) continue;
      // React dropdowns (Greenhouse and others) add an invisible "required" twin input next to the
      // real control. It has no label and must never be treated as a question.
      if (type !== 'file' && (el.getAttribute('aria-hidden') === 'true' || el.classList.contains('requiredInput')
        || (el.tabIndex === -1 && Number(getComputedStyle(el).opacity) === 0))) continue;
      const labels = labelTexts(el);
      const label = labels[0] || technicalText(el) || '';
      if (type === 'radio') {
        const name = el.name || idFor(el);
        if (!radios.has(name)) radios.set(name, []);
        radios.get(name).push({ el, label });
        continue;
      }
      const base = { id: idFor(el), label, required: isRequired(el, labels), filled: false };
      if (type === 'checkbox') { fields.push({ ...base, kind: 'checkbox', key: null, filled: el.checked }); continue; }
      if (type === 'file') {
        fields.push({ ...base, kind: 'file', key: classifyFile(el, labels), ...uploadLimits(el), manualOnly: false, multiple: el.multiple, accept: el.getAttribute('accept') || '', filled: el.files.length > 0,
          autoFilled: el.dataset.jfManaged === '1', autoFiles: String(el.dataset.jfFiles || '').split('|').filter(Boolean) });
        continue;
      }
      const key = classify(el, labels);
      if (key === 'skip') continue;
      const field = {
        ...base, kind: el.tagName === 'SELECT' ? 'select' : (el.tagName === 'TEXTAREA' ? 'textarea' : 'text'),
        key, inputType: type, maxLength: el.maxLength > 0 ? el.maxLength : null, filled: !!el.value,
        placeholder: el.getAttribute('placeholder') || '', pattern: el.getAttribute('pattern') || '',
        pageLang: document.documentElement.lang || '',
      };
      if (field.kind === 'select' && !['salutation', 'academic_title', 'country', 'nationality', 'work_authorisation_text', 'german_level', 'english_level', 'disability', 'source', 'birth_day', 'birth_month', 'birth_year', 'start_day', 'start_month', 'start_year'].includes(field.key)) field.key = null;
      if (field.kind === 'select') {
        field.optionCount = el.options.length;
        field.options = Array.from(el.options).map((o) => o.text.trim()).filter(Boolean).slice(0, 60);
      }
      if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') || /suchen|aus der liste|search|select from/.test(norm(labels.join(' ')))) field.custom = true;
      // A dropdown without a profile rule is a choice: never a place for free text from Claude.
      if (el.getAttribute('role') === 'combobox' && !field.key) { field.kind = 'select'; field.combobox = true; field.options = []; }
      if (field.key === 'cover_letter_text' && field.kind !== 'textarea') field.key = null;
      fields.push(field);
    }
    for (const [name, group] of radios) {
      const first = group[0].el;
      const box = first.closest('fieldset, [role="radiogroup"], [role="group"]') || first.parentElement?.parentElement;
      const legend = box && (box.querySelector('legend') || box.querySelector('label:not(:has(input)), [class*="label" i]'));
      const label = (legend && shortText(legend, 160)) || first.getAttribute('aria-label') || name;
      const id = idFor(first);
      first.dataset.jfGroup = name;
      fields.push({ id, label, kind: 'radio', key: matchRules(RULES, label) || matchRules(RULES, name.replace(/[_\-.\[\]]+/g, ' ')), required: group.some((g) => g.el.required), options: group.map((g) => g.label), filled: group.some((g) => g.el.checked) });
    }
    // One "street" field without a house-number field means the form wants both in one line.
    const keys = new Set(fields.map((f) => f.key));
    for (const field of fields) {
      if (field.key === 'street' && !keys.has('house_number')) field.key = 'street_full';
      if (field.key === 'address_street') field.key = keys.has('house_number') ? 'street' : 'street_full';
      if (field.key === 'address_full' && (keys.has('postal_code') || keys.has('postal_city') || keys.has('city'))) field.key = keys.has('house_number') ? 'street' : 'street_full';
    }
    const files = fields.filter((f) => f.kind === 'file');
    if (files.length === 1 && !files[0].key) files[0].key = 'cv';
    fields.push(...manualUploadFields(fields));
    return adjustBareNameFields(fields);
  };

  const fire = (el, names) => names.forEach((name) => el.dispatchEvent(new Event(name, { bubbles: true, composed: true })));

  const setText = (el, value) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    el.focus({ preventScroll: true });
    setter.call(el, value);
    fire(el, ['input', 'change']);
    el.dispatchEvent(new FocusEvent('blur', { bubbles: true }));
    if (el.value === value || (el.maxLength > 0 && el.value === value.slice(0, el.maxLength))) return true;
    // Phone widgets reformat the number ("+49 176 …" becomes "0176 …"); the same digits count as success.
    const digits = (text) => String(text || '').replace(/\D/g, '');
    const wanted = digits(value);
    const got = digits(el.value);
    return /^[+\d\s()/.-]{6,}$/.test(String(value)) && wanted.length >= 6 && got.length >= 6 && wanted.endsWith(got.slice(-6));
  };

  const candidates = (value) => {
    if (Array.isArray(value)) return value.map(norm).filter(Boolean);
    const key = norm(value);
    return SYNONYMS[key] || [key];
  };

  const parseProfileDate = (value) => {
    const match = /^(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})$/.exec(String(value || '').trim());
    if (!match) return null;
    const day = Number(match[1]); const month = Number(match[2]); const year = Number(match[3]);
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
      ? { day, month, year } : null;
  };

  const monthNames = (month) => {
    const de = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
    const en = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    return [String(month), String(month).padStart(2, '0'), de[month - 1], en[month - 1], de[month - 1].slice(0, 3), en[month - 1].slice(0, 3)];
  };

  const dateComponent = (key, value) => {
    const parsed = parseProfileDate(value);
    if (!parsed) return '';
    if (key.endsWith('_day')) return [String(parsed.day), String(parsed.day).padStart(2, '0')];
    if (key.endsWith('_month')) return monthNames(parsed.month);
    if (key.endsWith('_year')) return [String(parsed.year)];
    return '';
  };

  const formatProfileDate = (value, field = {}) => {
    const parsed = parseProfileDate(value);
    if (!parsed) return value || '';
    const dd = String(parsed.day).padStart(2, '0'); const mm = String(parsed.month).padStart(2, '0'); const yyyy = String(parsed.year);
    const hint = `${field.placeholder || ''} ${field.pattern || ''}`.toLowerCase();
    if (field.inputType === 'date' || /yyyy\s*[-/]\s*mm\s*[-/]\s*dd|jjjj\s*[-/]\s*mm\s*[-/]\s*tt/.test(hint)) return `${yyyy}-${mm}-${dd}`;
    if (/mm\s*\/\s*dd\s*\/\s*yyyy|mm\s*\/\s*dd\s*\/\s*jjjj/.test(hint)) return `${mm}/${dd}/${yyyy}`;
    if (/dd\s*\/\s*mm\s*\/\s*yyyy|tt\s*\/\s*mm\s*\/\s*jjjj/.test(hint)) return `${dd}/${mm}/${yyyy}`;
    if (/dd\s*\.\s*mm\s*\.\s*yyyy|tt\s*\.\s*mm\s*\.\s*jjjj/.test(hint)) return `${dd}.${mm}.${yyyy}`;
    return /^de(?:-|$)/i.test(field.pageLang || '') ? `${dd}.${mm}.${yyyy}` : `${dd}/${mm}/${yyyy}`;
  };

  const formatSalary = (value, field = {}) => {
    const text = String(value || '').trim();
    const label = `${field.label || ''} ${field.placeholder || ''}`;
    if (/currency|waehrung|mit waehrung|text|betrag und/i.test(label)) return text;
    let amount = (text.match(/[\d.,]+/) || [''])[0];
    const decimal = amount.match(/[.,](\d{2})$/);
    if (decimal && /[.,].*[.,]/.test(amount)) amount = amount.slice(0, -3);
    return (amount.match(/\d/g) || []).join('');
  };

  const isPastProfileDate = (value) => {
    const parsed = parseProfileDate(value);
    if (!parsed) return false;
    const today = new Date();
    return new Date(parsed.year, parsed.month - 1, parsed.day) < new Date(today.getFullYear(), today.getMonth(), today.getDate());
  };

  const bestOption = (labels, value) => {
    const wanted = candidates(value);
    const normalized = labels.map(norm);
    for (const want of wanted) { const i = normalized.indexOf(want); if (i >= 0) return i; }
    for (const want of wanted) {
      if (['no', 'yes', 'nein', 'ja'].includes(want)) {
        const i = normalized.findIndex((label) => label === want || label.startsWith(`${want},`) || label.startsWith(`${want} `));
        if (i >= 0) return i;
      }
      if (/^[abc][12]$/i.test(want)) {
        const i = normalized.findIndex((label) => new RegExp(`(^|[^a-z0-9])${want}([^a-z0-9]|$)`, 'i').test(label));
        if (i >= 0) return i;
      }
      if (want.length < 4) continue;
      const i = normalized.findIndex((label) => label.startsWith(want) || label.includes(` ${want}`));
      if (i >= 0) return i;
    }
    return -1;
  };

  const setSelect = (el, value) => {
    const options = Array.from(el.options);
    let index = bestOption(options.map((o) => o.text), value);
    if (index < 0) index = bestOption(options.map((o) => o.value), value);
    if (index < 0) return false;
    el.focus({ preventScroll: true });
    el.selectedIndex = index;
    fire(el, ['input', 'change']);
    return true;
  };

  const radioGroup = (first) => allElements(document, 'input[type="radio"]').filter((el) => (el.name || el.dataset.jfId) === (first.dataset.jfGroup || first.name));

  const setRadio = (first, value) => {
    const group = radioGroup(first);
    const index = bestOption(group.map((el) => labelTexts(el)[0] || el.value), value);
    if (index < 0) return false;
    group[index].click();
    return group[index].checked;
  };

  const toDateInput = (value) => {
    const match = /^(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})$/.exec(value.trim());
    return match ? `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}` : value;
  };

  const mark = (el, ok) => { el.style.outline = ok ? '2px solid #2e9e5b' : '2px solid #e08a00'; el.style.outlineOffset = '1px'; };

  const setValue = (id, value, force = false) => {
    const el = registry.get(id) || document.querySelector(`[data-jf-id="${id}"]`);
    if (!el || !el.isConnected) return 'gone';
    if (value === undefined || value === null || value === '') return 'no_value';
    const type = (el.type || '').toLowerCase();
    if (type === 'checkbox' || type === 'file') return 'manual';
    let ok;
    if (type === 'radio') { if (!force && radioGroup(el).some((r) => r.checked)) return 'kept'; ok = setRadio(el, value); }
    else if (el.tagName === 'SELECT') {
      if (el.selectedIndex > 0) {
        const current = el.options[el.selectedIndex];
        if (bestOption([current.text, current.value], value) >= 0) return 'kept';
        if (!force) return 'kept';
      }
      ok = setSelect(el, value);
    }
    else { if (!force && el.value) return 'kept'; ok = setText(el, type === 'date' ? toDateInput(value) : value); }
    mark(el, ok);
    return ok ? 'filled' : 'no_match';
  };

  const currentLabel = (id) => {
    const el = registry.get(id) || document.querySelector(`[data-jf-id="${id}"]`);
    if (!el || !el.isConnected) return '';
    return labelTexts(el)[0] || technicalText(el);
  };

  // Check and write in one page call so a wizard cannot change the field between them.
  const setAnswer = (id, expectedLabel, value) => {
    const label = currentLabel(id);
    if (!label || norm(label) !== norm(expectedLabel)) return { status: 'page_changed', label };
    return { status: setValue(id, value, false), label };
  };

  const setFiles = (id, files) => {
    const el = registry.get(id) || document.querySelector(`[data-jf-id="${id}"]`);
    if (!el || !el.isConnected) return 'gone';
    if (!files.length) return 'no_value';
    const transfer = new DataTransfer();
    const accept = (el.getAttribute('accept') || '').toLowerCase();
    for (const file of el.multiple ? files : files.slice(0, 1)) {
      const extension = `.${file.name.split('.').pop().toLowerCase()}`;
      if (accept && !accept.includes(extension) && !accept.includes(file.type) && !/\*\/\*|application\/\*/.test(accept)) return 'wrong_type';
      const bytes = Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0));
      transfer.items.add(new File([bytes], file.name, { type: file.type, lastModified: Date.now() }));
    }
    const names = Array.from(transfer.files).map((file) => file.name);
    el.files = transfer.files;
    el.dataset.jfManaged = '1';
    el.dataset.jfFiles = names.join('|');
    // Native and React widgets normally listen for change. Drop-zone wrappers may instead listen
    // for a drop event; dispatching both is harmless and does not submit the form.
    fire(el, ['change', 'input']);
    let replacement = document.getElementById(el.id) || document.querySelector(`[name="${CSS.escape(el.name || '')}"]`);
    const nativeKept = el.files.length > 0 || Boolean(replacement && replacement.files && replacement.files.length);
    const zone = !nativeKept && el.closest('[class*="drop" i], [data-testid*="drop" i], [role="button"]');
    if (zone) {
      try { zone.dispatchEvent(new DragEvent('drop', { bubbles:true, composed:true, dataTransfer:transfer })); }
      catch (error) { /* older pages do not construct DragEvent with DataTransfer */ }
    }
    replacement = document.getElementById(el.id) || document.querySelector(`[name="${CSS.escape(el.name || '')}"]`);
    const widgetText = (el.closest('label, [class*="upload" i], [class*="file" i], div')?.textContent || '').toLowerCase();
    const ok = el.files.length > 0 || Boolean(replacement && replacement.files && replacement.files.length)
      || names.some((name) => widgetText.includes(name.toLowerCase()));
    mark(el.offsetParent ? el : (el.closest('label, div') || el), ok);
    return ok ? 'filled' : 'no_match';
  };

  const valueFor = (key, profile, extra) => {
    const p = profile;
    const streetFull = [p.street, p.house_number].filter(Boolean).join(' ');
    const postalCity = [p.postal_code, p.city].filter(Boolean).join(' ');
    const map = {
      ...p,
      phone: String(p.phone || '').replace(/\s+/g, ''),
      email_repeat: p.email,
      full_name: [p.first_name, p.last_name].filter(Boolean).join(' '),
      street_full: streetFull,
      postal_city: postalCity,
      address_full: [streetFull, postalCity].filter(Boolean).join(', '),
      cover_letter_text: extra.letter_text || '',
      source: extra.source === 'Karriereseite des Unternehmens' && !/^de/i.test(document.documentElement.lang || '')
        ? 'Company website' : (extra.source || ''),
    };
    if (/^(?:birth|start)_(?:day|month|year)$/.test(key || '')) {
      return dateComponent(key, key.startsWith('birth_') ? p.birth_date : p.start_date);
    }
    return map[key] || '';
  };

  const currentValue = (el) => {
    const type = (el.type || '').toLowerCase();
    if (el.getAttribute('role') === 'combobox') {
      // The chosen option of a React dropdown is shown next to the (then empty) search input.
      let node = el.parentElement;
      for (let depth = 0; depth < 5 && node; depth += 1, node = node.parentElement) {
        const chosen = node.querySelector('[class*="single-value"], [class*="singleValue"], [class*="multi-value__label"]');
        if (chosen) return (chosen.textContent || '').trim();
      }
    }
    if (type === 'radio') { const hit = radioGroup(el).find((r) => r.checked); return hit ? (labelTexts(hit)[0] || hit.value || '') : ''; }
    if (el.tagName === 'SELECT') {
      const option = el.options[el.selectedIndex];
      return option && (el.selectedIndex > 0 || el.value !== '') ? option.text.trim() : '';
    }
    return el.value || '';
  };

  // Reports what is in each watched field: once now, on change, on leaving the field, and
  // one second after typing stops. It only reads; it never changes a value.
  const observe = (list, pkg, page) => {
    let count = 0;
    for (const item of list || []) {
      const el = registry.get(item.id) || document.querySelector(`[data-jf-id="${item.id}"]`);
      if (!el || !el.isConnected || el.dataset.jfObserved === pkg) continue;
      const type = (el.type || '').toLowerCase();
      if (['password', 'file', 'hidden', 'checkbox'].includes(type)) continue;
      el.dataset.jfObserved = pkg;
      let timer = null;
      let last = null;
      const send = () => {
        clearTimeout(timer);
        const value = String(currentValue(el)).slice(0, 2000);
        if (value === last) return;
        last = value;
        try {
          chrome.runtime.sendMessage({ type: 'jf-observe', id: pkg, page, field: {
            label: item.label, kind: item.kind, key: item.key || '', options: (item.options || []).slice(0, 60), value,
          } });
        } catch (error) { /* the extension was reloaded */ }
      };
      const later = () => { clearTimeout(timer); timer = setTimeout(send, 1000); };
      for (const target of type === 'radio' ? radioGroup(el) : [el]) {
        target.addEventListener('input', later);
        target.addEventListener('change', send);
        target.addEventListener('blur', send);
      }
      send();
      count += 1;
    }
    return count;
  };

  window.__jfFiller = {
    version: 1,
    observe,
    scan: () => ({ url: location.href, title: document.title, top: window.top === window, fields: describe() }),
    // Fills text, select and radio fields. Never ticks a checkbox, never clicks a button, never submits.
    fill: (profile, extra = {}) => describe().map((field) => {
      if (field.kind === 'checkbox' || field.kind === 'file') return { ...field, status: field.filled ? 'kept' : 'manual' };
      if (!field.key) return { ...field, status: field.filled ? 'kept' : 'unknown' };
      if (field.custom) {
        const value = valueFor(field.key, profile, extra);
        if (field.filled || field.kind !== 'text') return { ...field, status: field.filled ? 'kept' : 'manual', value };
        const status = setValue(field.id, value);
        return { ...field, status: status === 'filled' ? 'pick' : status, value };
      }
      if (field.key === 'notice_period') return { ...field, status: field.filled ? 'kept' : 'manual', value: '' };
      let value = valueFor(field.key, profile, extra);
      if (field.key === 'start_date' && isPastProfileDate(profile.start_date)) value = '';
      if ((field.key || '').startsWith('start_') && field.key !== 'start_date' && isPastProfileDate(profile.start_date)) value = '';
      if (field.key === 'start_date' && field.kind === 'select') return { ...field, status: field.filled ? 'kept' : 'manual', value: '' };
      if (field.key === 'birth_date' || field.key === 'start_date') value = formatProfileDate(value, field);
      if (field.key === 'salary') value = formatSalary(value, field);
      // A portal may preselect B2. The user's explicit English level is C1, so
      // this one verified profile field must correct a lower default.
      const force = field.key === 'english_level';
      let status = setValue(field.id, value, force);
      if (field.key === 'source' && status === 'no_match' && extra.source) {
        value = !/^de/i.test(document.documentElement.lang || '') ? 'Company website' : 'Karriereseite des Unternehmens';
        status = setValue(field.id, value, true);
      }
      return { ...field, status, value };
    }),
    setValue,
    setAnswer,
    currentLabel,
    setFiles,
    describe,
    matchOption: (labels, value) => bestOption(labels, value),
    classifyLabel: (label) => matchRules(RULES, label),
    classifyFileLabel: (label) => matchRules(FILE_RULES, label),
    adjustBareNameFields,
    parseProfileDate,
    formatProfileDate,
    formatSalary,
    isPastProfileDate,
  };
})();
