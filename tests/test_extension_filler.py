"""Browser tests of the extension's form filler. Skipped when Playwright or its browser is missing."""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "tests" / "fixtures" / "forms"
FILLER = (ROOT / "extension" / "filler.js").read_text(encoding="utf-8")
PROFILE = {
    "salutation": "Herr", "academic_title": "", "first_name": "Max", "last_name": "Muster",
    "email": "max@example.org", "phone": "+49 151 000000", "street": "Beispielweg", "house_number": "7",
    "postal_code": "12345", "city": "Musterstadt", "country": "Deutschland", "birth_date": "02.03.1990",
    "birth_place": "", "nationality": "", "linkedin": "https://www.linkedin.com/in/max", "xing": "", "github": "",
    "website": "", "start_date": "", "salary": "", "notice_period": "", "disability": "No",
    "german_level": "B2", "english_level": "C1",
    "highest_education": "M.Sc. International Marketing, University of Example, grade 17.12/20, 2016",
    "current_employment": "Research assistant, Example Research Center, since April 2025",
}
PDF = {"name": "Lebenslauf_Max_Muster.pdf", "type": "application/pdf", "base64": base64.b64encode(b"%PDF-1.4 test").decode()}


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Exception as error:  # browser binary not installed
            pytest.skip(f"Chromium is not available: {error}")
        yield instance
        instance.close()


def _open(browser, name):
    page = browser.new_page()
    page.goto((FORMS / name).as_uri())
    for frame in page.frames:
        frame.evaluate(FILLER)
    return page


def _by_label(fields):
    return {field["label"]: field for field in fields}


def test_full_form_is_filled_without_consent_or_submit(browser):
    page = _open(browser, "form_full.html")
    fields = _by_label(page.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [PROFILE, {"letter_text": "Sehr geehrte Damen und Herren,\n\nText."}]))
    values = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('input:not([type=file]),select,textarea')].map(e => [e.id || e.name, e.type === 'checkbox' ? e.checked : e.value]))")
    assert values["a"] == "Herr" and values["v"] == "Max" and values["n"] == "Muster"
    assert values["e"] == values["e2"] == "max@example.org"
    assert values["t"] == "+49151000000"
    assert (values["s"], values["h"], values["p"], values["o"]) == ("Beispielweg", "7", "12345", "Musterstadt")
    assert values["l"] == "DE"  # "Deutschland" matched the option "Germany"
    assert values["g"] == "1990-03-02"
    assert values["an"].startswith("Sehr geehrte")
    # Not in the profile: left empty and reported, never guessed.
    assert values["gh"] == "" and fields["Gehaltsvorstellung (brutto/Jahr)"]["status"] == "no_value"
    assert values["st"] == "" and values["et"] == ""
    # Questions the rules do not know stay open for the user or the Copilot hand-over.
    assert fields["Warum möchten Sie bei uns arbeiten?"]["status"] == "unknown"
    assert fields["Wie sind Sie auf uns aufmerksam geworden?"]["status"] == "no_value"
    assert fields["Sind Sie umzugsbereit?"]["status"] == "unknown"
    assert fields["Sind Sie umzugsbereit?"]["options"] == ["Ja", "Nein"]
    # Consent stays unticked, passwords untouched, nothing submitted.
    assert values["ds"] is False and values["pw"] == ""
    assert page.evaluate("() => window.submitted") is None
    kinds = {field["label"]: field.get("key") for field in fields.values() if field["kind"] == "file"}
    assert kinds == {"Lebenslauf *": "cv", "Anschreiben (PDF)": "letter", "Weitere Dokumente (Zeugnisse)": "attachments"}
    more = fields["Weitere Dokumente (Zeugnisse)"]
    assert (more["maxFiles"], more["maxMb"]) == (3, 4) and fields["Lebenslauf *"]["maxFiles"] is None
    # A search box is typed into, then the user picks the suggestion; dial-code selects are left alone.
    assert fields["Adresse suchen"]["status"] == "pick" and values["as"] == "Beispielweg"
    assert "Ländervorwahl" not in fields
    # A consent box keeps its own (shortened) text and never borrows another field's label.
    assert any(label.startswith("Ich bin mir bewusst") and field["kind"] == "checkbox" for label, field in fields.items())
    assert values["long"] is False
    page.close()


def test_files_are_attached_and_wrong_types_refused(browser):
    page = _open(browser, "form_full.html")
    fields = _by_label(page.evaluate("() => window.__jfFiller.describe()"))
    cv = fields["Lebenslauf *"]["id"]
    assert page.evaluate("([id, f]) => window.__jfFiller.setFiles(id, [f])", [cv, PDF]) == "filled"
    assert page.evaluate("() => [document.getElementById('cv').files[0].name, document.getElementById('cv').files[0].size]") == [PDF["name"], 13]
    docx = dict(PDF, name="x.docx", type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert page.evaluate("([id, f]) => window.__jfFiller.setFiles(id, [f])", [fields["Anschreiben (PDF)"]["id"], docx]) == "wrong_type"
    more = fields["Weitere Dokumente (Zeugnisse)"]["id"]
    assert page.evaluate("([id, f]) => window.__jfFiller.setFiles(id, [f, {...f, name: 'b.pdf'}])", [more, PDF]) == "filled"
    assert page.evaluate("() => document.getElementById('w2').files.length") == 2
    page.close()


def test_closed_component_upload_buttons_are_reported_as_manual(browser):
    page = browser.new_page()
    page.set_content("""
      <form>
        <section class="upload-control">
          <div>Application Documents - No files attached. (required)</div>
          <button type="button" title="Select one or several files to be uploaded">Upload files</button>
        </section>
        <section class="upload-control">
          <div>Application picture - No files attached.</div>
          <button type="button" title="Select one or several files to be uploaded">Upload files</button>
        </section>
      </form>
    """)
    page.evaluate(FILLER)
    fields = page.evaluate("() => window.__jfFiller.describe()")
    uploads = {field["label"]: field for field in fields if field["kind"] == "file"}
    assert set(uploads) == {"Application Documents", "Application picture"}
    assert uploads["Application Documents"] | {"key": "documents", "required": True, "manualOnly": True, "multiple": True} == uploads["Application Documents"]
    assert uploads["Application picture"]["key"] == "photo"
    assert uploads["Application picture"]["required"] is False
    assert uploads["Application picture"]["manualOnly"] is True
    page.close()


def test_short_form_placeholders_frames_shadow_dom_and_existing_values(browser):
    page = _open(browser, "form_short.html")
    results = []
    for frame in page.frames:
        results += frame.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [PROFILE, {}])
    by_label = _by_label(results)
    main = page.evaluate("() => [...document.querySelectorAll('form input:not([type=file]):not([type=checkbox])')].map(e => e.value)")
    assert main == ["Max", "Muster", "+49151000000", "schon@da.de", "Beispielweg 7, 12345 Musterstadt"]
    assert by_label["E-Mail*"]["status"] == "kept"  # an existing value is never overwritten
    assert page.evaluate("() => window.modelValue()") == "Max"  # framework listeners saw the input event
    assert page.evaluate("() => document.getElementById('host').shadowRoot.getElementById('li').value") == PROFILE["linkedin"]
    inner = page.frames[1]
    assert inner.evaluate("() => [document.getElementById('x').value, document.getElementById('y').value]") == ["Max Muster", "12345 Musterstadt"]
    upload = next(field for field in results if field["kind"] == "file")
    assert upload["key"] == "cv"
    assert page.evaluate("() => document.querySelector('[type=checkbox]').checked") is False
    assert page.evaluate("() => window.submitted") is None
    page.close()


def test_answers_can_be_inserted_by_id(browser):
    page = _open(browser, "form_full.html")
    fields = _by_label(page.evaluate("() => window.__jfFiller.describe()"))
    assert page.evaluate("([id]) => window.__jfFiller.setValue(id, 'Sonstiges', true)", [fields["Wie sind Sie auf uns aufmerksam geworden?"]["id"]]) == "filled"
    assert page.evaluate("([id]) => window.__jfFiller.setValue(id, 'Ja', true)", [fields["Sind Sie umzugsbereit?"]["id"]]) == "filled"
    assert page.evaluate("([id]) => window.__jfFiller.setValue(id, 'Vielleicht', true)", [fields["Sind Sie umzugsbereit?"]["id"]]) == "no_match"
    assert page.evaluate("() => [document.getElementById('q').value, document.querySelector('[name=umzug]:checked').value]") == ["Sonstiges", "1"]
    page.close()


def test_language_levels_and_source_use_only_matching_select_options(browser):
    page = browser.new_page()
    page.set_content("""
      <label>Deutschkenntnisse (Niveau nach GER)*<select id="de"><option>Bitte wählen</option><option>A1</option><option>B1</option><option>B2 - Selbständige Sprachverwendung</option><option>C1</option></select></label>
      <label>English proficiency<select id="en"><option>Please choose</option><option>A1</option><option>B2</option><option>C1</option></select></label>
      <label>Deutsch (Niveau)<select id="bad"><option>Bitte wählen</option><option>gut</option><option>sehr gut</option></select></label>
      <label>Aufmerksam geworden durch<select id="source"><option>Bitte wählen</option><option>Agentur für Arbeit</option><option>LinkedIn</option></select></label>
    """)
    page.evaluate(FILLER)
    fields = page.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [PROFILE, {"source": "Bundesagentur für Arbeit"}])
    values = page.evaluate("() => ['de','en','bad','source'].map(id => document.getElementById(id).value)")
    assert values == ["B2 - Selbständige Sprachverwendung", "C1", "Bitte wählen", "Agentur für Arbeit"]
    bad = next(field for field in fields if field["label"].startswith("Deutsch (Niveau)"))
    assert bad["status"] == "no_match"
    page.close()


def test_trivago_profile_fields_are_filled_and_open_question_stays_for_checked_answer(browser):
    page = _open(browser, "trivago_fields.html")
    profile = PROFILE | {
        "country": "Deutschland", "english_level": "C1", "disability": "No",
        "start_date": "01/12/2026", "salary": "52,000.00 EUR gross per year",
    }
    fields = _by_label(page.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [profile, {}]))
    values = page.evaluate("""() => ({
      start: document.getElementById('start').value,
      salary: document.getElementById('salary').value,
      country: document.getElementById('country').value,
      english: document.getElementById('english').value,
      disability: document.getElementById('disability').value,
      disabledDe: document.querySelector('[name=disabled_de]:checked')?.value || '',
      motivation: document.getElementById('motivation').value,
    })""")
    assert values == {
        "start": "01/12/2026", "salary": "52000", "country": "DE",
        "english": "C1", "disability": "no", "disabledDe": "nein", "motivation": "",
    }
    assert fields["What excites you the most about this role?"]["status"] == "unknown"
    page.close()


def test_slash_date_is_converted_only_for_native_date_input(browser):
    page = browser.new_page()
    page.set_content('<label>Earliest possible start date<input id="start" type="date"></label>')
    page.evaluate(FILLER)
    fields = page.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [PROFILE | {"start_date": "01/12/2026"}, {}])
    assert page.evaluate("() => document.getElementById('start').value") == "2026-12-01"
    assert fields[0]["status"] == "filled"
    page.close()


def _node(script: str) -> str:
    try:
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, cwd=ROOT / "extension")
    except FileNotFoundError:
        pytest.skip("node is not installed")
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_node_trivago_labels_and_explicit_profile_options():
    script = r"""
global.window = {};
eval(require('fs').readFileSync('filler.js', 'utf8'));
const f = window.__jfFiller;
console.log(JSON.stringify({
  keys: [
    'What is your earliest possible start date for this role? (please use DD/MM/YYYY format)',
    'What are your salary expectations for this role? (gross/annual - based on 40hrs/week)',
    'In which Country do you currently live?',
    'What is your level of English?',
    'Do you have a disability?',
    'Haben Sie eine Schwerbehinderung?',
    'Are you legally authorized to work in the country you are applying to?',
    'Are you authorized to work in the country for which you applied?',
  ].map((label) => f.classifyLabel(label)),
  country: f.matchOption(['Please select', 'Germany', 'France'], 'Deutschland'),
  english: f.matchOption(['Please select', 'B2', 'C1'], 'C1'),
  disabilityEn: f.matchOption(['Please select', 'Yes', 'No, I do not have a disability'], 'No'),
  disabilityDe: f.matchOption(['Ja', 'Nein'], 'No'),
  fiSource: f.matchOption(['', 'Computerwoche', 'Finanz Informatik Website', 'LinkedIn'], 'Karriereseite des Unternehmens'),
}));
"""
    assert json.loads(_node(script)) == {
        "keys": ["start_date", "salary", "country", "english_level", "disability", "disability", "work_authorisation_text", "work_authorisation_text"],
        "country": 1, "english": 2, "disabilityEn": 2, "disabilityDe": 1, "fiSource": 2,
    }


def test_node_date_formats_and_claude_field_modes():
    script = r"""
global.window = {};
eval(require('fs').readFileSync('filler.js', 'utf8'));
const f = window.__jfFiller;
const JF = require('./rules.js');
console.log(JSON.stringify({
  de:f.formatProfileDate('15.03.1990',{pageLang:'de'}),
  en:f.formatProfileDate('15.03.1990',{pageLang:'en'}),
  us:f.formatProfileDate('15.03.1990',{placeholder:'MM/DD/YYYY'}),
  iso:f.formatProfileDate('15.03.1990',{inputType:'date'}),
  salary:f.formatSalary('52,000.20 EUR gross per year',{label:'Jahresbrutto'}),
  salaryText:f.formatSalary('52000 EUR',{label:'Amount and currency / Währung'}),
  invalid:f.parseProfileDate('31.02.2020'),
  modes:[
    JF.claudeFieldMode({label:'What excites you?',kind:'textarea'}),
    JF.claudeFieldMode({label:'English level',kind:'select'}),
    JF.claudeFieldMode({label:'Salary expectation',kind:'select'}),
    JF.claudeFieldMode({label:'Privacy consent',kind:'checkbox'}),
    JF.claudeFieldMode({label:'How many years of experience do you have working with Workday?',kind:'textarea'}),
    JF.claudeFieldMode({label:'How would you rate your programming skills on a scale of 1-5?',kind:'textarea'}),
  ],
}));
"""
    assert json.loads(_node(script)) == {
        "de": "15.03.1990", "en": "15/03/1990", "us": "03/15/1990", "iso": "1990-03-15",
        "salary": "52000", "salaryText": "52000 EUR",
        "invalid": None, "modes": ["answer", "suggest", "yours", "yours", "bank", "bank"],
    }


def test_flix_rewritten_query_url_matches_the_unique_package_job_id():
    script = r"""
const JF = require('./rules.js');
const packages = [{
  id:'flix', title:'Workday Consultant - Automation & AI (m/f/d)', employer:'Flix',
  urls:['https://flix.careers/job/8924866002?gh_jid=8924866002'], hosts:['flix.careers'],
}];
const result = JF.matchPackage(
  packages,
  'https://flix.careers/job/?jobid=8924866002?gh_jid=8924866002&gh_jid=8924866002#JobForm',
  'Job Detail - Flix Career', {},
);
console.log(JSON.stringify({id:result.pkg && result.pkg.id,reason:result.reason}));
"""
    assert json.loads(_node(script)) == {"id": "flix", "reason": "job ID in the link"}


def test_panel_rules_match_packages_and_plan_files():
    script = r"""
const JF = require('./rules.js');
const file = (n) => ({name: n});
const pkgs = [
 {id:'a', employer:'DIS AG Perm', title:'IT Business Analyst / Requirements Manager – Finanzwesen (m/w/d)', urls:['https://www.xing.com/jobs/x-1?utm=1'], hosts:['xing.com'], cv:file('cv'), letter:file('l'), attachments:[file('z1'),file('z2')], letter_text:'L', cv_text:'C', job_excerpt:'', location:''},
 {id:'b', employer:'Finanz Informatik GmbH & Co. KG', title:'Consultant (m/w/d) IT-Storytelling & Präsentation', urls:['https://www.arbeitsagentur.de/jobsuche/jobdetail/1'], hosts:['arbeitsagentur.de'], cv:file('cv'), letter:file('l'), attachments:[], letter_text:'L', cv_text:'C', job_excerpt:'', location:''},
 {id:'c', employer:'Finanz Informatik GmbH & Co. KG', title:'Business Analyst (m/w/d) mit Erfahrung im Aufsichtsrecht', urls:['https://www.arbeitsagentur.de/jobsuche/jobdetail/2'], hosts:['arbeitsagentur.de'], cv:file('cv'), letter:file('l'), attachments:[], letter_text:'L', cv_text:'C', job_excerpt:'', location:''},
];
const out = {};
out.exact = JF.matchPackage(pkgs, 'https://www.xing.com/jobs/x-1?other=2', '', {}).pkg.id;
out.employer = JF.matchPackage(pkgs, 'https://jobs.de.dis-ag.com/e1/bewerben/?utm_campaign=IT_Business_Analyst_/_Requirements_Manager', 'Bewerben', {}).pkg.id;
out.title = JF.matchPackage(pkgs, 'https://www.f-i.de/karriere/stelle/123', 'Consultant (m/w/d) IT-Storytelling & Präsentation | Finanz Informatik', {}).pkg.id;
out.none = JF.matchPackage(pkgs, 'https://example.org/', 'Startseite', {}).pkg;
const fields = [{uid:'u1', kind:'file', key:'cv'}, {uid:'u2', kind:'file', key:'letter'}, {uid:'u3', kind:'file', key:'attachments', multiple:true}];
const plan = JF.assignFiles(fields, pkgs[0]);
out.plan = Object.fromEntries(Object.entries(plan).map(([k, v]) => [k, v.files.map((f) => f.name)]));
out.single = JF.assignFiles([{uid:'u9', kind:'file', key:'attachments', multiple:true}], pkgs[0])['u9'].files.map((f) => f.name);
const big = {...pkgs[0], attachments:[{name:'a', size:3e6}, {name:'huge', size:5e6}, {name:'b', size:1e6}, {name:'c', size:1e6}]};
const limited = JF.assignFiles([{uid:'u1', kind:'file', key:'cv'}, {uid:'u3', kind:'file', key:'attachments', multiple:true, maxFiles:3, maxMb:4}], big)['u3'];
out.limited = [limited.files.map((f) => f.name), limited.left.map((f) => f.name)];
console.log(JSON.stringify(out));
"""
    out = json.loads(_node(script))
    assert out["exact"] == "a" and out["employer"] == "a" and out["title"] == "b" and out["none"] is None
    assert out["plan"] == {"u1": ["cv"], "u2": ["l"], "u3": ["z1", "z2"]}
    assert out["single"] == ["cv", "l", "z1", "z2"]
    # A certificate slot receives certificates only; size and count limits are kept without duplicating files.
    assert out["limited"] == [["a", "b", "c"], ["huge"]]


def test_pending_letter_holds_the_one_slot_that_will_receive_the_letter():
    script = r"""
const JF = require('./rules.js');
const f = (name) => ({name});
const pkg = {letter_pending:true, cv:f('cv.pdf'), attachments:[f('zeugnis.pdf')], attachments_combined:f('anlagen.pdf')};
const fields = [
  {uid:'cv',kind:'file',label:'Lebenslauf',key:'cv'},
  {uid:'letter',kind:'file',label:'Anschreiben',key:'letter'},
  {uid:'docs',kind:'file',label:'Application documents',key:'documents',multiple:true},
  {uid:'extras',kind:'file',label:'Zeugnisse',key:'attachments',multiple:true},
  {uid:'generic',kind:'file',label:'Upload'}
];
const plan = JF.assignFiles(fields, pkg);
console.log(JSON.stringify(Object.fromEntries(Object.entries(plan).map(([key,value]) => [key,{held:!!value.held,files:value.files.map(x=>x.name)}]))));
"""
    assert json.loads(_node(script)) == {
        "cv": {"held": False, "files": ["cv.pdf"]},
        "letter": {"held": True, "files": []},
        "docs": {"held": False, "files": ["zeugnis.pdf"]},
        "extras": {"held": False, "files": []},
        "generic": {"held": False, "files": []},
    }


def test_letter_ready_handler_refills_without_replacing_an_existing_cv():
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    core = (ROOT / "extension" / "core.js").read_text(encoding="utf-8")
    assert "payload.action === 'reattach' || payload.action === 'letter-ready'" in background
    assert "field.autoFilled && wantsLetter && !autoHasLetter" in core
    assert "the field already contains a file added by you" in core
    assert "Letter attached" in background


def test_round17_cover_letter_fallbacks_hold_pending_slots_and_never_duplicate_files():
    script = r"""
const JF = require('./rules.js');
const f = (name) => ({name, path:name});
const pkg = {cv:f('cv.pdf'),letter:f('letter.pdf'),attachments:[f('cert.pdf')],
  combined:f('all.pdf'),letter_cv_combined:f('letter-cv.pdf'),attachments_combined:f('cert.pdf')};
const cases = {
  singleCv:[{uid:'cv',kind:'file',label:'CV'}],
  multipleCv:[{uid:'cv',kind:'file',label:'CV',multiple:true}],
  cvCertificates:[{uid:'cv',kind:'file',label:'CV'},{uid:'cert',kind:'file',label:'Certificates',multiple:true}],
  letterPresent:[{uid:'cv',kind:'file',label:'CV'},{uid:'letter',kind:'file',label:'Cover letter'}],
  generic:[{uid:'generic',kind:'file',label:'Upload'}],
  documents:[{uid:'documents',kind:'file',label:'Application documents'}],
};
const names = (plan) => Object.fromEntries(Object.entries(plan).map(([key,value]) => [key,{held:!!value.held,files:value.files.map(x=>x.name)}]));
const ready = Object.fromEntries(Object.entries(cases).map(([key,fields]) => [key,names(JF.assignFiles(fields,pkg))]));
const pendingPkg = {...pkg,letter_pending:true,letter:undefined,combined:undefined,letter_cv_combined:undefined};
const pending = Object.fromEntries(Object.entries(cases).map(([key,fields]) => [key,names(JF.assignFiles(fields,pendingPkg))]));
console.log(JSON.stringify({ready,pending}));
"""
    out = json.loads(_node(script))
    assert out["ready"] == {
        "singleCv": {"cv": {"held": False, "files": ["letter-cv.pdf"]}},
        "multipleCv": {"cv": {"held": False, "files": ["cv.pdf", "letter.pdf", "cert.pdf"]}},
        "cvCertificates": {
            "cv": {"held": False, "files": ["cv.pdf"]},
            "cert": {"held": False, "files": ["cert.pdf", "letter.pdf"]},
        },
        "letterPresent": {
            "cv": {"held": False, "files": ["cv.pdf"]},
            "letter": {"held": False, "files": ["letter.pdf"]},
        },
        "generic": {"generic": {"held": False, "files": ["all.pdf"]}},
        "documents": {"documents": {"held": False, "files": ["all.pdf"]}},
    }
    expected_held = {
        "singleCv": "cv", "multipleCv": "cv", "cvCertificates": "cert",
        "letterPresent": "letter", "generic": "generic", "documents": "documents",
    }
    for case, target in expected_held.items():
        assert out["pending"][case][target] == {"held": True, "files": []}
    for plan in out["ready"].values():
        flat = [name for slot in plan.values() for name in slot["files"]]
        assert len(flat) == len(set(flat))


def test_automatic_filler_decides_per_page_and_never_follows_job_board_buttons():
    script = r"""
const JF = require('./rules.js');
const look = (extra) => ({known: 0, files: 0, password: false, captcha: false, links: [], ...extra});
const at = (host, hops = 0) => ({host, hops});
const out = {
  form: JF.decide(look({known: 5, captcha: true}), at('jobs.example.com')).action,
  upload: JF.decide(look({files: 1}), at('jobs.example.com')).action,
  login: JF.decide(look({known: 1, password: true, links: ['https://x.example/apply']}), at('jobs.example.com')),
  follow: JF.decide(look({links: ['https://x.example/apply', 'https://x.example/other']}), at('karriere.example.com')),
  board: JF.decide(look({links: ['https://x.example/apply']}), at('de.indeed.com')).action,
  xing: JF.decide(look({links: ['https://x.example/apply']}), at('www.xing.com')).action,
  loop: JF.decide(look({links: ['https://x.example/apply']}), at('karriere.example.com', 2)).action,
  captcha: JF.decide(look({captcha: true}), at('www.arbeitsagentur.de')).reason,
  linkedin: JF.looksLikeApplication('https://linkedin.com/jobs/view/1', 'Apply now'),
  spiegel: JF.looksLikeApplication('https://spiegel.de/karriere/a-1', 'Karriere und Beruf'),
  spiegelApply: JF.looksLikeApplication('https://spiegel.de/karriere/a-1', 'Bewerbung beim Verlag'),
  youtube: JF.looksLikeApplication('https://youtube.com/watch?v=1', 'Talent show'),
  mail: JF.looksLikeApplication('https://mail.google.com/mail/u/0', 'Inbox'),
  db: JF.looksLikeApplication('https://jobs.deutschebahngroup.careers/de_DE/jobsGlobal/Register?jobId=1', 'Registrierung | DB Group'),
  career: JF.looksLikeApplication('https://karriere.example.de/stelle/123', 'Offene Position'),
  avature: JF.looksLikeApplication('https://example.avature.net/register', 'Registration'),
  ashby: JF.looksLikeApplication('https://jobs.ashbyhq.com/example/1', 'Open role'),
  helix: JF.looksLikeApplication('https://helixjobs.com/job/1', 'Open role'),
  surveyedOwn: [
    JF.looksLikeApplication('https://commerzbank.de/konzern/karriere/', 'Karriere'),
    JF.looksLikeApplication('https://dsv.com/de-de/karriere', 'Karriere'),
    JF.looksLikeApplication('https://gpanrw.de/karriere/karriere', 'Karriere'),
  ],
  sum: JF.summary([
    {status: 'filled', kind: 'text'}, {status: 'pick', kind: 'text'}, {status: 'attached', kind: 'file'},
    {status: 'unknown', kind: 'textarea'}, {status: 'no_value', kind: 'text'}, {status: 'manual', kind: 'file'},
    {status: 'consent', kind: 'checkbox'}, {status: 'kept', kind: 'text'},
  ]),
};
console.log(JSON.stringify(out));
"""
    out = json.loads(_node(script))
    assert (out["form"], out["upload"]) == ("fill", "fill")
    assert out["login"]["action"] == "ask" and "login" in out["login"]["reason"]
    assert out["follow"] == {"action": "follow", "url": "https://x.example/apply"}
    assert (out["board"], out["xing"], out["loop"]) == ("ask", "ask", "ask")
    assert "CAPTCHA" in out["captcha"]
    assert (out["linkedin"], out["spiegel"], out["youtube"], out["mail"]) == (False, False, False, False)
    assert (out["spiegelApply"], out["db"], out["career"], out["avature"], out["ashby"], out["helix"]) == (True, True, True, True, True, True)
    assert out["surveyedOwn"] == [True, True, True]
    assert out["sum"] == {"filled": 2, "attached": 1, "open": 2, "consent": 1, "manualUploads": 1}


def test_node_option_matching_for_levels_and_source_synonyms():
    script = r"""
global.window = {};
require('./filler.js');
const match = window.__jfFiller.matchOption;
console.log(JSON.stringify({
  exactLevel: match(['Bitte wählen','A1','B1','B2','C1'], 'B2'),
  describedLevel: match(['Bitte wählen','B2 - Selbständige Sprachverwendung'], 'B2'),
  noWordLevel: match(['Bitte wählen','gut','sehr gut'], 'B2'),
  source: match(['Bitte wählen','Agentur für Arbeit','LinkedIn'], 'Bundesagentur für Arbeit'),
}));
"""
    out = json.loads(_node(script))
    assert out == {"exactLevel": 3, "describedLevel": 1, "noWordLevel": -1, "source": 1}


def test_node_rules_cover_real_round4_survey_labels():
    script = r"""
global.window = {};
require('./filler.js');
const classify = window.__jfFiller.classifyLabel;
console.log(JSON.stringify({
  location: classify('Current location ✱'),
  source: classify('Select... LinkedIn Indeed Stepstone XING Startup Insider Social Media Advertisement I was looking for a car subscription Career Page Personal Recommendation ...'),
  german: classify('Deutsch: – Bitte wählen – Grundkenntnisse fließend verhandlungssicher Muttersprache'),
}));
"""
    assert json.loads(_node(script)) == {"location": "city", "source": "source", "german": "german_level"}


def test_node_bkl_bare_name_becomes_last_name_when_vorname_exists():
    script = r"""
global.window = {};
require('./filler.js');
const fields = window.__jfFiller.adjustBareNameFields([
  {label:'Vorname*', key:'first_name'}, {label:'Name*', key:'full_name'}
]);
const lone = window.__jfFiller.adjustBareNameFields([{label:'Ihr Name', key:'full_name'}]);
console.log(JSON.stringify({fields, lone}));
"""
    out = json.loads(_node(script))
    assert [field["key"] for field in out["fields"]] == ["first_name", "last_name"]
    assert out["lone"][0]["key"] == "full_name"


def test_node_surveyed_generic_upload_slots_get_safe_file_sets():
    script = r"""
const JF = require('./rules.js');
const file = (name) => ({name});
const pkg = {cv:file('cv.pdf'), letter:file('letter.pdf'), attachments:[file('zeugnis.pdf'),file('anlage.pdf')]};
const fields = [
  {uid:'size', kind:'file', key:null, label:'Maximal 50 MB'},
  {uid:'drop', kind:'file', key:null, label:'Hier kannst du Dateien ziehen und ablegen', multiple:true},
  {uid:'attach', kind:'file', key:null, label:'Anhängen', multiple:true},
  {uid:'upload', kind:'file', key:null, label:'UPLOAD FILE', multiple:true},
  {uid:'singleLater', kind:'file', key:null, label:'Attach'},
  {uid:'letter', kind:'file', key:'letter', label:'Bewerbungsanschreiben'},
];
const plan = JF.assignFiles(fields, pkg);
console.log(JSON.stringify(Object.fromEntries(Object.entries(plan).map(([key,value]) => [key,value.files.map((f) => f.name)]))));
"""
    out = json.loads(_node(script))
    assert out["size"] == ["cv.pdf"]
    assert out["drop"] == ["zeugnis.pdf", "anlage.pdf"]
    assert out["attach"] == out["upload"] == out["singleLater"] == []
    assert out["letter"] == ["letter.pdf"]


def test_node_round8_upload_assignment_has_no_duplicates_and_uses_combined_pdf():
    script = r"""
const JF = require('./rules.js');
const f = (name) => ({name, path:name});
const pkg = {cv:f('cv.pdf'), letter:f('letter.pdf'), attachments:[f('one.pdf'),f('two.pdf')],
  combined:f('all.pdf'), attachments_combined:f('extras.pdf')};
const names = (plan) => Object.fromEntries(Object.entries(plan).map(([k,v]) => [k,v.files.map(x => x.name)]));
const out = {};
out.two = names(JF.assignFiles([{uid:'a',kind:'file',label:'Upload'},{uid:'b',kind:'file',label:'Datei'}], pkg));
out.one = names(JF.assignFiles([{uid:'a',kind:'file',label:'Upload',multiple:false}], pkg));
out.explicit = names(JF.assignFiles([{uid:'a',kind:'file',label:'Upload'},{uid:'b',kind:'file',label:'Cover letter'}], pkg));
out.multi = names(JF.assignFiles([{uid:'a',kind:'file',label:'Upload',multiple:true}], pkg));
console.log(JSON.stringify(out));
"""
    out = json.loads(_node(script))
    assert out["two"] == {"a": ["cv.pdf"], "b": ["letter.pdf"]}
    assert out["one"] == {"a": ["all.pdf"]}
    assert out["explicit"] == {"b": ["letter.pdf"], "a": ["cv.pdf"]}
    assert out["multi"] == {"a": ["cv.pdf", "letter.pdf", "one.pdf", "two.pdf"]}
    for plan in out.values():
        flat = [name for files in plan.values() for name in files]
        assert len(flat) == len(set(flat))


def test_restricted_residence_pdf_is_never_used_for_generic_or_combined_uploads():
    script = r"""
global.window = {};
require('./filler.js');
const JF = require('./rules.js');
const f = (name) => ({name, path:name});
const pkg = {
  cv:f('cv.pdf'), letter:f('letter.pdf'), attachments:[f('Anhang_Max_Muster.pdf')],
  residence_documents:[f('Passport, Visum, Aufenthaltstitel.pdf')],
  combined:f('Bewerbungsunterlagen.pdf'), attachments_combined:f('Anhang_Max_Muster.pdf'),
};
const names = (fields) => Object.fromEntries(Object.entries(JF.assignFiles(fields, pkg)).map(([k,v]) => [k,v.files.map(x => x.name)]));
console.log(JSON.stringify({
  labels: [
    window.__jfFiller.classifyFileLabel('Passport / visa / residence permit'),
    window.__jfFiller.classifyFileLabel('Aufenthaltstitel oder Visum'),
    window.__jfFiller.classifyFileLabel('Weitere Dokumente'),
  ],
  genericSingle: names([{uid:'g',kind:'file',label:'Upload',multiple:false}]),
  genericMulti: names([{uid:'g',kind:'file',label:'Upload',multiple:true}]),
  residenceEn: names([{uid:'r',kind:'file',label:'Proof of residence permit',multiple:false}]),
  residenceDe: names([{uid:'r',kind:'file',label:'Reisepass, Visum oder Aufenthaltstitel',multiple:false}]),
  mixed: names([
    {uid:'d',kind:'file',label:'Application documents',key:'documents',multiple:true},
    {uid:'r',kind:'file',label:'Work authorization document',multiple:false},
  ]),
}));
"""
    out = json.loads(_node(script))
    restricted = "Passport, Visum, Aufenthaltstitel.pdf"
    assert out["labels"] == ["residence", "residence", "attachments"]
    assert out["genericSingle"] == {"g": ["Bewerbungsunterlagen.pdf"]}
    assert out["genericMulti"] == {"g": ["cv.pdf", "letter.pdf", "Anhang_Max_Muster.pdf"]}
    assert out["residenceEn"] == {"r": [restricted]}
    assert out["residenceDe"] == {"r": [restricted]}
    assert out["mixed"] == {
        "d": ["cv.pdf", "letter.pdf", "Anhang_Max_Muster.pdf"], "r": [restricted],
    }


def test_mixed_residence_and_certificate_uploads_attach_or_report_both_parts():
    script = r"""
const JF = require('./rules.js');
const f = (name) => ({name, path:name});
const pkg = {
  cv:f('cv.pdf'), letter:f('letter.pdf'), attachments:[f('Anhang.pdf')],
  residence_documents:[f('Aufenthaltstitel.pdf')], attachments_combined:f('Anhang.pdf'),
};
const mixed = 'Zeugnisse, Visum und sonstige Anlagen';
const multi = JF.assignFiles([{uid:'m',kind:'file',label:mixed,multiple:true}], pkg).m;
const single = JF.assignFiles([{uid:'s',kind:'file',label:mixed,multiple:false}], pkg).s;
const residence = JF.assignFiles([{uid:'r',kind:'file',label:'Visum oder Aufenthaltstitel',multiple:true}], pkg).r;
console.log(JSON.stringify({
  multi:{files:multi.files.map(x=>x.name),manual:(multi.manual||[]).map(x=>x.name)},
  single:{files:single.files.map(x=>x.name),manual:(single.manual||[]).map(x=>x.name)},
  residence:{files:residence.files.map(x=>x.name),manual:(residence.manual||[]).map(x=>x.name)},
}));
"""
    assert json.loads(_node(script)) == {
        "multi": {"files": ["Anhang.pdf", "Aufenthaltstitel.pdf"], "manual": []},
        "single": {"files": ["Anhang.pdf"], "manual": ["Aufenthaltstitel.pdf"]},
        "residence": {"files": ["Aufenthaltstitel.pdf"], "manual": []},
    }


def test_claude_answer_guards_detect_page_and_label_changes():
    script = r"""
const JF = require('./rules.js');
console.log(JSON.stringify({
  key:JF.pageKey('https://www.example.org/apply/step-1?token=secret#form'),
  samePage:JF.pageKey('https://example.org/apply/step-1?other=1') === JF.pageKey('https://example.org/apply/step-1'),
  movedPage:JF.pageKey('https://example.org/apply/step-2') === JF.pageKey('https://example.org/apply/step-1'),
  sameLabel:JF.sameFieldLabel('Why us? *', 'Why us?'),
  movedLabel:JF.sameFieldLabel('Why us?', 'Notice period'),
}));
"""
    assert json.loads(_node(script)) == {
        "key": "example.org/apply/step-1", "samePage": True, "movedPage": False,
        "sameLabel": True, "movedLabel": False,
    }
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    filler = (ROOT / "extension" / "filler.js").read_text(encoding="utf-8")
    page = (ROOT / "jobfilter" / "app_page.html").read_text(encoding="utf-8")
    assert "currentPage !== expectedPage" in background and "window.__jfFiller.setAnswer" in background
    assert "status: 'page_changed'" in filler and "pageKey:message.pageKey" in page
    assert "fields:(message.fields || []).map" in page


def test_claude_panel_availability_and_ten_minute_cache_are_pure_and_explicit():
    script = r"""
const JF = require('./rules.js');
const at = 1000000;
const statuses = {
  ready:{connected:true,writer:'claude',installed:true,signed_in:true,plan:true},
  unknown:{connected:true,writer:'claude',installed:true,signed_in:null,plan:false},
  disconnected:{connected:false,writer:'claude'},
  standard:{connected:true,writer:'standard'},
  missing:{connected:true,writer:'claude',installed:false},
  signedOut:{connected:true,writer:'claude',installed:true,signed_in:false,login:'claude auth login'},
  paid:{connected:true,writer:'claude',installed:true,signed_in:true,plan:false,login:'claude auth login'},
  limit:{connected:true,writer:'claude',kind:'limit',reason:'Claude plan limit reached'},
};
console.log(JSON.stringify({
  mapped:Object.fromEntries(Object.entries(statuses).map(([key,value]) => [key,JF.claudeAvailability(value)])),
  within:JF.cachedClaudeAvailability({at,payload:statuses.ready},at+599999),
  expired:JF.cachedClaudeAvailability({at,payload:statuses.ready},at+600000),
}));
"""
    out = json.loads(_node(script))
    assert out["mapped"]["ready"] == {"state": "ready", "enabled": True, "reason": "Claude Haiku is ready."}
    assert out["mapped"]["unknown"]["state"] == "unknown" and out["mapped"]["unknown"]["enabled"] is True
    for key in ("disconnected", "standard", "missing", "signedOut", "paid", "limit"):
        assert out["mapped"][key]["state"] == "blocked" and out["mapped"][key]["enabled"] is False
        assert out["mapped"][key]["reason"]
    assert out["within"]["state"] == "ready" and out["expired"] is None


def test_panel_auto_checks_claude_and_disabled_ask_buttons_always_show_a_reason():
    panel = (ROOT / "extension" / "panel.js").read_text(encoding="utf-8")
    assert "await checkClaude(false)" in panel and panel.count("await checkClaude(false)") >= 2
    assert "cachedClaudeAvailability" in panel and "chrome.storage.session.remove(CLAUDE_CACHE)" in panel
    assert "ask.disabled = !state.claude.enabled || !state.pkg" in panel
    assert "if (ask.disabled)" in panel and "ask-disabled-reason" in panel and "packageReason || state.claude.reason" in panel
    assert "$('ask-claude').disabled = !state.claude.enabled || !state.pkg" in panel
    sending = panel.index("$('claude-status').textContent = 'Sending the recognised questions")
    disabled = panel.index("$('ask-claude').disabled = true", sending)
    assert sending < disabled and "claudeReady" not in panel


def test_panel_never_silently_asks_without_a_package_and_uses_its_own_edge_window():
    panel = (ROOT / "extension" / "panel.js").read_text(encoding="utf-8")
    assert "chrome.windows.getCurrent()" in panel and "getLastFocused" not in panel
    assert "!state.claude.enabled || !state.pkg" in panel
    assert "Choose the application at the top before asking Claude." in panel
    assert "if (!state.pkg) throw new Error" in panel
    assert "state.remembered[host]" in panel and "renderResults(false)" in panel


def test_node_ulm_fields_and_combined_application_documents():
    classify = r"""
global.window = {};
require('./filler.js');
console.log(JSON.stringify([
  window.__jfFiller.classifyLabel('Highest level of education (e.g. university degree)'),
  window.__jfFiller.classifyLabel('Current employment'),
]));
"""
    assert json.loads(_node(classify)) == ["highest_education", "current_employment"]

    files = r"""
const JF = require('./rules.js');
const file = (name) => ({name});
const pkg = {cv:file('Lebenslauf.pdf'), letter:file('Anschreiben.pdf'), attachments:[file('Zeugnisse.pdf'),file('Zertifikate.pdf')]};
const plan = JF.assignFiles([
  {uid:'documents', kind:'file', key:'documents', multiple:true},
  {uid:'picture', kind:'file', key:'photo'},
], pkg);
console.log(JSON.stringify(Object.fromEntries(Object.entries(plan).map(([key,value]) => [key,value.files.map((f) => f.name)]))));
"""
    assert json.loads(_node(files)) == {
        "documents": ["Lebenslauf.pdf", "Anschreiben.pdf", "Zeugnisse.pdf", "Zertifikate.pdf"],
        "picture": [],
    }


def test_single_upload_falls_back_to_letter_and_cv_when_full_bundle_exceeds_limit():
    script = r"""
const JF = require('./rules.js');
const file = (name, mb) => ({name, size:mb * 1024 * 1024});
const pkg = {
  cv:file('CV.pdf', .5), letter:file('Letter.pdf', .2), attachments:[file('Certificates.pdf', 3)],
  combined:file('Application.pdf', 6), letter_cv_combined:file('Letter-and-CV.pdf', .7),
};
const plan = JF.assignFiles([{uid:'one',kind:'file',label:'Upload',multiple:false,maxMb:5}], pkg).one;
console.log(JSON.stringify({files:plan.files.map(f=>f.name),manual:plan.manual.map(f=>f.name)}));
"""
    assert json.loads(_node(script)) == {
        "files": ["Letter-and-CV.pdf"],
        "manual": ["Certificates.pdf"],
    }


def test_greenhouse_letter_text_uses_resume_only_and_existing_letter_has_no_false_warning():
    script = r"""
const JF = require('./rules.js');
const file = (name) => ({name});
const pkg = {cv:file('CV.pdf'), letter:file('Cover-letter.pdf'), letter_cv_combined:file('Letter-and-CV.pdf'), attachments:[]};
const figma = JF.assignFiles([
  {uid:'resume',kind:'file',key:'cv',label:'Resume/CV',multiple:false},
  {uid:'info',kind:'textarea',key:'cover_letter_text',label:'Additional Information',filled:true},
], pkg);
const celonis = JF.assignFiles([
  {uid:'resume',kind:'file',key:'cv',label:'Resume/CV',multiple:false,filled:true,autoFiles:['Cover-letter.pdf']},
], pkg);
console.log(JSON.stringify({figma:figma.resume.files.map(f=>f.name),celonisWarning:celonis.warning || ''}));
"""
    assert json.loads(_node(script)) == {"figma": ["CV.pdf"], "celonisWarning": ""}


def test_package_choice_prefers_exact_then_current_and_never_remembers_shared_hosts():
    script = r"""
const JF = require('./rules.js');
const pkgs = [
  {id:'a', urls:['https://f-i.de/karriere/stelle/a'], hosts:['f-i.de']},
  {id:'b', urls:['https://f-i.de/karriere/stelle/b'], hosts:['f-i.de']},
  {id:'c', urls:['https://karriere.unique.example/stelle/c'], hosts:['karriere.unique.example']},
  {id:'d', urls:['https://arbeitsagentur.de/jobsuche/jobdetail/d'], hosts:['arbeitsagentur.de']},
];
const remembered = {
  'f-i.de': {id:'a', page:'https://f-i.de/karriere/stelle/shared'},
  'karriere.unique.example': {id:'c', page:'https://karriere.unique.example/stelle/shared'},
};
const id = (result) => result.pkg && result.pkg.id;
const own = JF.pickPackage(pkgs, 'https://www.f-i.de/karriere/stellenangebote/x', 'Offene Position', 'b', remembered);
console.log(JSON.stringify({
  exact: id(JF.pickPackage(pkgs, 'https://f-i.de/karriere/stelle/a?x=1', 'Bewerbung', 'b', remembered)),
  current: id(JF.pickPackage(pkgs, 'https://f-i.de/karriere/stelle/shared', 'Bewerbung', 'b', remembered)),
  shared: id(JF.pickPackage(pkgs, 'https://f-i.de/karriere/stelle/shared', 'Bewerbung', '', remembered)),
  unique: id(JF.pickPackage(pkgs, 'https://karriere.unique.example/stelle/shared', 'Offene Position', '', remembered)),
  ownHost: id(own),
  ownReason: own.reason,
  otherHost: id(JF.pickPackage(pkgs, 'https://www.f-i.de/karriere/stellenangebote/x', 'Offene Position', 'c', remembered)),
  jobBoardHost: id(JF.pickPackage(pkgs, 'https://arbeitsagentur.de/jobsuche/suche', 'Jobsuche', 'd', remembered)),
  board: JF.isJobBoard('www.linkedin.com'),
}));
"""
    out = json.loads(_node(script))
    assert out == {
        "exact": "a", "current": "b", "shared": None, "unique": "c",
        "ownHost": "b", "ownReason": "current package on its own host",
        "otherHost": None, "jobBoardHost": None, "board": True,
    }


def test_extension_scripts_parse_and_the_worker_never_clicks_or_submits():
    names = ["rules.js", "core.js", "background.js", "panel.js", "filler.js", "relay.js"]
    _node("for (const f of %s) new Function(require('fs').readFileSync(f, 'utf8'));" % json.dumps(names))
    for name in ("background.js", "core.js", "relay.js"):
        code = (ROOT / "extension" / name).read_text(encoding="utf-8")
        assert "fetch(" not in code and ".click(" not in code and ".submit(" not in code and "requestSubmit" not in code
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    core = (ROOT / "extension" / "core.js").read_text(encoding="utf-8")
    assert "getLastFocused" in background and "url: ['http://*/*'" not in background
    assert "isJobBoard(host)" in core
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    assert "notifications" in manifest["permissions"] and (ROOT / "extension" / manifest["icons"]["128"]).is_file()


def test_relay_runs_only_on_the_jobfilter_page_and_uses_no_network():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    (script,) = manifest["content_scripts"]
    assert script["js"] == ["relay.js"] and set(script["matches"]) == {"http://127.0.0.1/*", "http://localhost/*"}
    relay = (ROOT / "extension" / "relay.js").read_text(encoding="utf-8")
    assert 'meta[name="jobfilter-app"]' in relay and "fetch(" not in relay
    assert "pending.catch(() => {})" in relay and "reconnect on the next page refresh" in relay
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "fetch(" not in background  # results reach jobfilter through its own window, never over the network
    page = (ROOT / "jobfilter" / "app_page.html").read_text(encoding="utf-8")
    assert '<meta name="jobfilter-app"' in page and "jfFromExtension" in page and "jfToExtension" in page


def test_side_panel_uses_local_claude_haiku_flow_instead_of_copilot_paste():
    panel_html = (ROOT / "extension" / "panel.html").read_text(encoding="utf-8")
    panel_js = (ROOT / "extension" / "panel.js").read_text(encoding="utf-8")
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    app_page = (ROOT / "jobfilter" / "app_page.html").read_text(encoding="utf-8")

    assert "Answer open fields with Claude" in panel_html and 'id="claude-instruction"' in panel_html
    assert 'id="ask-claude"' in panel_html and "Claude Haiku" in panel_html
    for old in ("Copilot", 'id="copy-prompt"', 'id="ai-answer"', 'id="check-answers"'):
        assert old not in panel_html
    assert "jf-panel-questions" in panel_js and "jf-panel-result" in panel_js
    assert "claudeInstruction" in panel_js and "pageKey: JF.pageKey" in panel_js and "force: Boolean(payload.force)" in background
    assert "source: 'panel'" in background and "jf-panel-result" in background
    assert "instruction: message.instruction" in app_page and "force:Boolean(message.force)" in app_page


def test_answer_bank_rows_are_saved_locally_and_show_their_source():
    panel_js = (ROOT / "extension" / "panel.js").read_text(encoding="utf-8")
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    app_page = (ROOT / "jobfilter" / "app_page.html").read_text(encoding="utf-8")
    assert "Add to answer bank" in panel_js and "Save for later forms" in panel_js
    assert "saved.source" in panel_js and "answer.source" in panel_js
    assert "jf-panel-save-answer" in panel_js and "save-answer-bank" in background
    assert "/api/answer-bank-save" in app_page and 'id="answer-bank-card"' in app_page
    assert "Antwort mit Claude" in app_page and "Überspringen" in app_page and "/api/answer-bank-draft" in app_page
    assert 'id="doctor"' in app_page and "/api/doctor" in app_page


def test_config_rejects_non_haiku_claude_models(tmp_path):
    import pytest

    from jobfilter.config import Config

    config = tmp_path / "config.yaml"
    config.write_text('contact_email: "max@example.org"\nclaude_model: "sonnet"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Haiku"):
        Config.load(config)
    for model in ("haiku", "claude-haiku-5-5", "claude-haiku-4-5-20251001"):
        config.write_text(f'contact_email: "max@example.org"\nclaude_model: "{model}"\n', encoding="utf-8")
        assert Config.load(config).claude_model == model
    config.write_text('contact_email: "max@example.org"\nanswer_language: "fr"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="answer_language"):
        Config.load(config)


def test_greenhouse_dropdowns_dummy_inputs_and_reformatted_phone(browser):
    page = _open(browser, "greenhouse_remix.html")
    fields = page.evaluate("([p, e]) => window.__jfFiller.fill(p, e)", [{**PROFILE, "phone": "+49 151 2345678"}, {}])
    labels = [field["label"] for field in fields]
    assert "" not in labels  # the invisible "required" twins of the dropdowns are not questions
    by = _by_label(fields)
    assert by["Phone*"]["status"] == "filled"  # the widget rewrote +49 to 0, same digits
    assert by["Will you now or in the future require immigration sponsorship for employment visa status?*"]["kind"] == "select"
    assert by['What does "The Best Team Wins" mean to you?']["kind"] == "textarea"


def test_round20_real_upload_structures_and_fi_source(browser):
    fi = _open(browser, "round20_fi_uploads.html")
    fields = _by_label(fi.evaluate("([p,e]) => window.__jfFiller.fill(p,e)", [PROFILE, {"source":"Bundesagentur für Arbeit"}]))
    assert fields["Aufmerksam geworden durch...*"]["status"] == "filled"
    assert fi.locator("#source").input_value() == "Finanz Informatik Website"
    uploads = [field for field in fields.values() if field["kind"] == "file"]
    assert [(field["key"], field["multiple"]) for field in uploads] == [("cv", True), ("letter", True), ("attachments", True)]
    assert fi.evaluate("([id,f]) => window.__jfFiller.setFiles(id,[f])", [uploads[0]["id"], PDF]) == "filled"
    fi.close()

    bite = _open(browser, "round20_bite_uploads.html")
    uploads = [field for field in bite.evaluate("() => window.__jfFiller.describe()") if field["kind"] == "file"]
    assert [(field["key"], field["manualOnly"]) for field in uploads] == [("documents", True), ("photo", True)]
    bite.close()

    figma = _open(browser, "round20_figma_uploads.html")
    fields = _by_label(figma.evaluate("() => window.__jfFiller.describe()"))
    assert fields["Resume/CV"]["key"] == "cv" and fields["Resume/CV"]["manualOnly"] is False
    assert fields["Additional Information"]["kind"] == "textarea" and fields["Additional Information"]["key"] is None
    assert figma.evaluate("([id,f]) => window.__jfFiller.setFiles(id,[f])", [fields["Resume/CV"]["id"], PDF]) == "filled"
    figma.close()


def test_work_authorisation_precedes_country_phone_is_compact_and_matching_select_is_kept(browser):
    page = browser.new_page()
    page.set_content('''
      <label for="work">Are you legally authorized to work in the country you are applying to?</label>
      <select id="work"><option></option><option>Yes</option><option>No</option></select>
      <label for="work2">Are you authorized to work in the country for which you applied?</label>
      <select id="work2"><option></option><option>Yes</option><option>No</option></select>
      <label for="phone">Phone</label><input id="phone">
      <label for="english">English skills</label><select id="english"><option></option><option>C1</option><option>B2</option></select>
    ''')
    page.evaluate(FILLER)
    fields = _by_label(page.evaluate("([p,e]) => window.__jfFiller.fill(p,e)", [{**PROFILE, "phone":"+49 176 123 456 78", "work_authorisation_text":"Yes"}, {}]))
    assert fields["Are you legally authorized to work in the country you are applying to?"]["key"] == "work_authorisation_text"
    assert fields["Are you authorized to work in the country for which you applied?"]["key"] == "work_authorisation_text"
    assert page.locator("#work").input_value() == "Yes" and page.locator("#work2").input_value() == "Yes"
    assert page.locator("#phone").input_value() == "+4917612345678"
    again = _by_label(page.evaluate("([p,e]) => window.__jfFiller.fill(p,e)", [PROFILE, {}]))
    assert again["English skills"]["status"] == "kept"
    page.close()


def test_panel_multi_question_chat_round_trips_through_one_fake_app_relay(browser):
    page = browser.new_page()
    page.add_init_script(r"""
      (() => {
        const listeners = [];
        const noopEvent = { addListener(){} };
        const fields = Array.from({length:5}, (_,i) => ({
          id:`q${i+1}`, label:`Question ${i+1}?`, kind:'textarea', key:null,
          required:true, filled:false, options:[], maxLength:600,
        }));
        class FakeXHR {
          open(){} set responseType(value){}
          send(){ this.status=200; this.response=JSON.stringify({
            generated:'2026-11-17T21:00:00', profile:{first_name:'Max'}, packages:[{
              id:'p1', title:'Analyst', employer:'Example', urls:['https://jobs.example/apply'], hosts:['jobs.example'],
              cv:{name:'CV.pdf',disk_path:'C:/CV.pdf'}, letter:{name:'Letter.pdf',disk_path:'C:/Letter.pdf'}, attachments:[], letter_text:'Letter',
            }],
          }); queueMicrotask(() => this.onload && this.onload()); }
        }
        window.XMLHttpRequest = FakeXHR;
        const store = {};
        window.chrome = {
          windows:{getCurrent:async()=>({id:1})},
          tabs:{query:async()=>[{id:7,windowId:1,url:'https://jobs.example/apply',title:'Application'}],get:async()=>({id:7,url:'https://jobs.example/apply',title:'Application'}),onActivated:noopEvent,onUpdated:noopEvent},
          scripting:{executeScript:async(options)=> options.files ? [] : [{frameId:0,result:{url:'https://jobs.example/apply',title:'Application',top:true,fields}}]},
          storage:{
            local:{get:async(defaults)=>({...defaults}),set:async()=>{}},
            session:{get:async(defaults)=>({...defaults,...store}),set:async(values)=>Object.assign(store,values),remove:async(key)=>delete store[key]},
          },
          runtime:{
            getURL:(path)=>`mock://${path}`,
            getManifest:()=>({version:'2.0.0'}),
            onMessage:{addListener:(fn)=>listeners.push(fn)},
            sendMessage:async(message)=>{
              if(message.type==='jf-panel-claude-check') {
                queueMicrotask(()=>listeners.forEach(fn=>fn({type:'jf-panel-claude-status',payload:{requestId:message.requestId,writer:'claude',installed:true,signed_in:true,plan:true}},{})));
                return {accepted:true,connected:true};
              }
              if(message.type==='jf-panel-chat') {
                const questions=message.payload.message.split(/\n/).filter(Boolean);
                queueMicrotask(()=>listeners.forEach(fn=>fn({type:'jf-panel-chat-answer',payload:{
                  requestId:message.payload.requestId,has_job_description:true,
                  answers:questions.map((q,i)=>({number:i+1,question:q.replace(/^\d+[.)]\s*/,''),answer:`Answer ${i+1}`,warnings:[]})),
                }},{})));
                return {accepted:true,connected:true};
              }
              return {accepted:true,connected:true};
            },
          },
        };
      })();
    """)
    page.goto((ROOT / "extension" / "panel.html").as_uri())
    page.wait_for_function("() => document.querySelector('#status').textContent.includes('applications loaded')")
    page.locator("#scan").click()
    page.locator("#chat-input").fill("\n".join(f"{i}. Question {i}?" for i in range(1, 6)))
    page.locator("#chat-send").click()
    page.wait_for_function("() => document.querySelectorAll('.chat-msg.claude').length === 5")
    assert page.locator(".chat-msg.claude").count() == 5
    assert page.locator(".chat-msg.claude select").count() == 5
    assert page.locator("#chat-status").text_content() == ""
    page.close()


def test_panel_confirmation_is_visible_relayed_and_retries_pending_chat():
    html = (ROOT / "extension" / "panel.html").read_text(encoding="utf-8")
    panel = (ROOT / "extension" / "panel.js").read_text(encoding="utf-8")
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    app_page = (ROOT / "jobfilter" / "app_page.html").read_text(encoding="utf-8")
    assert 'id="confirm-apply"' in html and 'id="confirm-inline-button"' in html
    assert "jf-panel-confirm" in panel and "if (chat.retry) sendChat(true)" in panel
    assert "action:'confirm'" in background and "jf-panel-confirmed" in background
    assert 'message.action === "confirm"' in app_page and 'source || "app"' in app_page
