import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jobfilter.answerbank import load_bank, resolve_question, write_bank, empty_bank
from jobfilter.learning import (
    ObservationStore, concepts, detect_scope, edit_learned, learn_submission, match_learned,
    option_for, question_key, resolve_pending, similarity,
)

ROOT = Path(__file__).resolve().parents[1]


def _record(label, value, *, kind="textarea", first_value=None, first_source="user", key="", options=()):
    return {"label": label, "kind": kind, "key": key, "options": list(options), "value": value,
            "first_value": value if first_value is None else first_value, "first_source": first_source}


def test_normalisation_folds_language_gender_and_numbers():
    assert concepts("Kündigungsfrist?") == ["notice"]
    assert concepts("What is your notice period?") == ["notice"]
    assert "startdate" in concepts("When could you start at the earliest?")
    assert concepts("Referent*in (m/w/d) Erfahrung 3 Jahre") == concepts("Referent Erfahrung Jahre")


def test_notice_and_start_date_are_not_the_same_question():
    assert similarity("Kündigungsfrist", "What is your notice period?") == 1.0
    assert similarity("What is your notice period?", "When could you start at the earliest?") < 0.6
    assert similarity("Kündigungsfrist", "What is your notice period, and earliest start date?") < 0.85


def test_scope_detects_employer_name_and_job_title():
    assert detect_scope("Bei Siemens Healthineers reizt mich die Datenarbeit.", "Siemens Healthineers AG") == "this employer only"
    assert detect_scope("I want to work in research analytics.", "Siemens Healthineers AG") == "any employer"
    assert detect_scope("As Data Analyst Controlling I would…", "ACME GmbH", "Data Analyst Controlling (m/w/d)") == "this employer only"


def test_learning_only_on_final_values_and_sources(tmp_path):
    bank = tmp_path / "antworten.yaml"
    observed = [
        _record("Describe a project you are proud of", "Forecast pipeline at Example Pharma."),
        _record("Why this team?", "Edited text", first_value="Claude draft", first_source="claude"),
        _record("Anything else?", "Kept Claude text", first_source="claude"),
        _record("Deleted by user", "", first_value="Claude text", first_source="claude"),
        _record("Vorname", "Max", kind="text", key="first_name"),
        _record("Privacy consent", "yes", kind="text"),
    ]
    counts = learn_submission(bank, observed, employer="ACME GmbH", today="2026-11-16")
    learned = {item["question"]: item for item in load_bank(bank)["learned"]}
    assert counts["learned"] == 3
    assert learned["Why this team?"]["source"] == "user" and learned["Why this team?"]["edited"] is True
    assert learned["Anything else?"]["source"] == "claude"
    assert "Deleted by user" not in learned and "Vorname" not in learned and "Privacy consent" not in learned


def test_single_fact_goes_to_confirmation_not_learned(tmp_path):
    bank = tmp_path / "antworten.yaml"
    counts = learn_submission(bank, [_record("Kündigungsfrist", "3 Monate", kind="text")], employer="A", today="2026-11-16")
    data = load_bank(bank)
    assert counts["pending"] == 1 and not data["learned"]
    resolve_pending(bank, 0, True)
    assert load_bank(bank)["facts"]["notice_period"]["de"] == "3 Monate"


def test_thresholds_fill_suggest_ignore():
    bank = empty_bank()
    bank["learned"] = [{
        "key": question_key("Describe a project you are proud of"), "question": "Describe a project you are proud of",
        "labels": [], "group": "text", "language": "en", "answer": "Forecast pipeline.", "scope": "any employer",
        "employer": "A", "last_used": "2026-11-16",
    }]
    exact = match_learned({"label": "Describe a project you are proud of.", "kind": "textarea"}, bank, language="en", today="2026-11-20")
    close = match_learned({"label": "Please describe one project you are especially proud of", "kind": "textarea"}, bank, language="en", today="2026-11-20")
    loose = match_learned({"label": "Describe your proudest achievement at work", "kind": "textarea"}, bank, language="en", today="2026-11-20")
    other = match_learned({"label": "What are your salary expectations?", "kind": "text"}, bank, language="en", today="2026-11-20")
    assert exact["mode"] == "fill"
    assert close["mode"] in {"fill", "suggest"}
    assert loose["mode"] in {"suggest", ""}
    assert other["mode"] == ""
    assert match_learned({"label": "Describe a project you are proud of", "kind": "textarea"}, bank, language="de", today="2026-11-20")["mode"] == ""


def test_employer_only_answers_stay_with_that_employer():
    bank = empty_bank()
    bank["learned"] = [{
        "key": question_key("Why us?"), "question": "Why us?", "labels": [], "group": "text", "language": "en",
        "answer": "ACME's research unit.", "scope": "this employer only", "employer": "ACME GmbH", "last_used": "2026-11-16",
    }]
    assert match_learned({"label": "Why us?", "kind": "textarea"}, bank, employer="ACME Deutschland GmbH", language="en", today="2026-11-17")["mode"] == "fill"
    assert match_learned({"label": "Why us?", "kind": "textarea"}, bank, employer="Other AG", language="en", today="2026-11-17")["mode"] == ""


def test_stale_and_past_dates_become_suggestions():
    bank = empty_bank()
    bank["learned"] = [
        {"key": question_key("Anmerkungen"), "question": "Anmerkungen", "labels": [], "group": "text", "language": "de",
         "answer": "Keine.", "scope": "any employer", "employer": "A", "last_used": "2026-01-01"},
        {"key": question_key("Verfügbar ab"), "question": "Verfügbar ab", "labels": [], "group": "text", "language": "de",
         "answer": "01.12.2026", "scope": "any employer", "employer": "A", "last_used": "2026-11-16"},
    ]
    assert match_learned({"label": "Anmerkungen", "kind": "textarea"}, bank, language="de", today="2026-11-16")["mode"] == "suggest"
    assert match_learned({"label": "Verfügbar ab", "kind": "text"}, bank, language="de", today="2026-12-05")["mode"] == "suggest"


def test_select_learned_on_one_form_applies_to_other_option_list(tmp_path):
    bank = tmp_path / "antworten.yaml"
    learn_submission(bank, [_record("How did you hear about us?", "LinkedIn", kind="select", options=["Please select", "LinkedIn", "XING"])],
                     employer="A", today="2026-11-16")
    data = load_bank(bank)
    found = match_learned({"label": "How did you hear about this job?", "kind": "select",
                           "options": ["Bitte wählen", "Indeed", "LinkedIn Jobs", "Messe"]}, data, today="2026-11-17")
    assert found["value"] == "LinkedIn Jobs" and found["mode"] in {"fill", "suggest"}
    assert option_for("Ja", ["Nein", "Ja, sofort"]) == "Ja, sofort"
    assert option_for("Ja", ["Ja", "Ja, später"]) == "Ja"


def test_conflict_keeps_history_and_reuse_counts(tmp_path):
    bank = tmp_path / "antworten.yaml"
    learn_submission(bank, [_record("Anything else?", "First.")], employer="A", today="2026-11-10")
    learn_submission(bank, [_record("Anything else?", "Second.")], employer="B", today="2026-11-11")
    learn_submission(bank, [_record("Anything else?", "Second.", first_source="learned")], employer="C", today="2026-11-12")
    item = load_bank(bank)["learned"][0]
    assert item["answer"] == "Second." and item["history"][0]["answer"] == "First." and item["times_used"] == 3


def test_resolve_question_uses_learned_before_saved(tmp_path):
    bank = empty_bank()
    bank["learned"] = [{"key": question_key("Anmerkungen"), "question": "Anmerkungen", "labels": [], "group": "text",
                        "language": "de", "answer": "Keine.", "scope": "any employer", "employer": "A",
                        "last_used": "2026-11-16"}]
    result = resolve_question({"label": "Anmerkungen", "kind": "textarea"}, bank, {}, today="2026-11-17")
    assert result["value"] == "Keine." and result["source"] == "learned"


def test_observation_store_tracks_first_value_source_and_take(tmp_path):
    store = ObservationStore(tmp_path / "observed.json")
    store.produced("p1", [{"label": "Why us?", "value": "Claude text", "source": "Claude"}])
    assert store.observe("p1", {"label": "Why us?", "kind": "textarea", "value": ""})
    store.observe("p1", {"label": "Why us?", "kind": "textarea", "value": "Claude text"})
    store.observe("p1", {"label": "Why us?", "kind": "textarea", "value": "Claude text, edited"})
    assert not store.observe("p1", {"label": "E-Mail", "kind": "text", "key": "email", "value": "x@y.z"})
    assert not store.observe("p1", {"label": "Passwort", "kind": "text", "value": "secret"})
    fields = store.take("p1")
    assert fields[0]["first_source"] == "Claude" and fields[0]["value"].endswith("edited")
    assert store.take("p1") == []


def test_edit_and_delete_learned(tmp_path):
    bank = tmp_path / "antworten.yaml"
    learn_submission(bank, [_record("Anything else?", "One.")], employer="A", today="2026-11-16")
    item = load_bank(bank)["learned"][0]
    edit_learned(bank, item["key"], "text", answer="Two.", scope="this employer only", language=item["language"])
    assert load_bank(bank)["learned"][0]["answer"] == "Two."
    edit_learned(bank, item["key"], "text", delete=True, language=item["language"])
    assert load_bank(bank)["learned"] == []


def test_app_learns_on_submitted_and_forgets_on_skipped(tmp_path, monkeypatch):
    from jobfilter import app as app_module

    class Dummy:
        config = type("C", (), {"application_output_dir": tmp_path / "out", "answer_bank_path": tmp_path / "bank.yaml"})()
        notices = []
        def notice(self, title, text, kind="info"):
            self.notices.append(text)

    dummy = Dummy()
    dummy.observations = lambda: app_module.App.observations(dummy)
    monkeypatch.setattr(app_module, "_queue_rows", lambda output: [{"application_id": "p1", "employer": "ACME GmbH", "title": "Analyst"}])
    store = dummy.observations()
    store.observe("p1", {"label": "Anything else?", "kind": "textarea", "value": "Nothing."})
    store.observe("p2", {"label": "Anything else?", "kind": "textarea", "value": "Draft."})
    counts = app_module.App.record_outcome(dummy, "p1", "submitted")
    app_module.App.record_outcome(dummy, "p2", "skipped")
    assert counts["learned"] == 1 and load_bank(tmp_path / "bank.yaml")["learned"][0]["answer"] == "Nothing."
    assert store.load() == {}
    assert "1 answer(s) learned." in dummy.notices[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_extension_observable_never_records_profile_secrets_or_consent():
    script = (
        "const JF=require(process.argv[1]);const r=[];"
        "for(const f of [{kind:'textarea',label:'Why us?'},{kind:'text',label:'E-Mail',key:'email'},"
        "{kind:'checkbox',label:'Ich stimme zu'},{kind:'text',label:'Datenschutz gelesen'},{kind:'file',label:'CV'},"
        "{kind:'select',label:'How did you hear about us?'},{kind:'text',label:'Passwort'}]) r.push(JF.observable(f));"
        "console.log(JSON.stringify(r));"
    )
    out = subprocess.run(["node", "-e", script, str(ROOT / "extension" / "rules.js")], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [True, False, False, False, False, True, False]


def test_extension_observes_after_every_fill_and_never_writes():
    core = (ROOT / "extension" / "core.js").read_text(encoding="utf-8")
    filler = (ROOT / "extension" / "filler.js").read_text(encoding="utf-8")
    assert "await observeFields(tab, pkg, fields);" in core
    body = filler.split("const observe = (list, pkg, page) => {", 1)[1].split("window.__jfFiller = {", 1)[0]
    for forbidden in (".value =", ".click(", ".submit(", "requestSubmit", "checked ="):
        assert forbidden not in body


def test_bank_facts_answer_recent_employer_title_and_yes_no_dropdowns(tmp_path):
    import json as _json
    from jobfilter.formanswers import answer_fields

    bank = empty_bank()
    bank["facts"]["current_employer"] = {"de": "Beispiel GmbH", "en": "Example Ltd"}
    bank["facts"]["current_title"] = {"de": "Analyst", "en": "Analyst"}
    bank["facts"]["work_authorisation_text"] = {"de": "Ja", "en": "Yes"}
    write_bank(tmp_path / "bank.yaml", bank)
    data = tmp_path / "data.json"
    data.write_text(_json.dumps({"profile": {}, "packages": [{"id": "p1", "employer": "ACME", "title": "Analyst"}]}), encoding="utf-8")
    fields = [
        {"uid": "f0-1", "label": "Most Recent Employer*", "kind": "text"},
        {"uid": "f0-2", "label": "Most Recent Job Title*", "kind": "text"},
        {"uid": "f0-3", "label": "Are you legally authorized to work in Germany?", "kind": "select",
         "options": ["Select...", "Yes", "No"]},
        {"uid": "f0-4", "label": "Jobtitel", "kind": "text"},
    ]
    result = answer_fields(data, "p1", fields, tmp_path / "cache.json", mode="resolve", bank_path=tmp_path / "bank.yaml")
    values = {item["uid"]: item["value"] for item in result["answers"]}
    assert values == {"f0-1": "Example Ltd", "f0-2": "Analyst", "f0-3": "Yes"}


def test_never_learn_consent_identity_sponsorship_or_page_furniture(tmp_path):
    from jobfilter.learning import learnable_label

    for label in ("Sprache auswählen", "Auswählen", "dps dps dps 19", "Geschlecht", "I confirm the information provided",
                  "Damit wir die Anforderungen an den besonderen Schutz von personenbezogenen Daten erfüllen",
                  "Will you now or in the future require immigration sponsorship?"):
        assert not learnable_label(label), label
    for label in ("Hochschule", "Bundesland *", "Reisebereitschaft", "Anmerkungen"):
        assert learnable_label(label), label
    bank = tmp_path / "bank.yaml"
    learn_submission(bank, [_record("Standortwahl (Priorität 1)", "Frankfurt", kind="select", options=["Frankfurt", "Berlin"])],
                     employer="Deloitte GmbH", today="2026-11-16")
    assert load_bank(bank)["learned"][0]["scope"] == "this employer only"


def test_german_nouns_pass_but_invented_names_fail():
    from jobfilter.formanswers import check_answer
    vocabulary = "Python SQL Web-Scraping-Pipeline Example Pharma"
    ok = check_answer({"label": "Q", "kind": "textarea"}, "Meine Fähigkeiten zeige ich in Projekten mit Python und Web-Scraping-Lösungen.", vocabulary, vocabulary)
    assert ok[1] == []
    bad = check_answer({"label": "Q", "kind": "textarea"}, "Ich habe bei Siemens mit Databricks gearbeitet.", vocabulary, vocabulary)
    assert "Siemens" in bad[1][0] and "Databricks" in bad[1][0]


def test_observation_survives_a_locked_replace_and_flushes_on_next_write(tmp_path, monkeypatch):
    import jobfilter.safeio as safeio

    store = ObservationStore(tmp_path / "observed.json")
    real_replace = safeio.os.replace
    calls = {"count": 0}

    def locked_then_free(source, target):
        calls["count"] += 1
        if calls["count"] <= 10:
            raise PermissionError("simulated OneDrive lock")
        return real_replace(source, target)

    monkeypatch.setattr(safeio.os, "replace", locked_then_free)
    assert store.observe("p1", {"label":"Why us?", "kind":"textarea", "value":"First"})
    assert not (tmp_path / "observed.json").exists()
    assert store.observe("p1", {"label":"Anything else?", "kind":"textarea", "value":"Second"})
    saved = json.loads((tmp_path / "observed.json").read_text(encoding="utf-8"))
    values = {field["value"] for field in saved["p1"]["fields"].values()}
    assert values == {"First", "Second"}
