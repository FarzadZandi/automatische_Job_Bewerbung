from __future__ import annotations

import json
from pathlib import Path

import yaml

from jobfilter.answerbank import (
    classify_question, ensure_bank, extract_cv_skill_names, load_bank, resolve_question, save_entry,
)
from jobfilter.formanswers import answer_fields


FLIX = [
    "Please tell us your earliest possible starting date and your current notice period.",
    "How many years of experience do you have working with Workday? (if applicable)",
    "Do you have experience building automation workflows using n8n, UiPath, or similar tools? If yes, please briefly describe.",
    "Do you have HR Tech domain (dealing with complex system landscapes)?",
    "How would you rate your (non-AI) programming and integration skills on a scale of 1-5? Please feel free to explain.",
]


def bank_fixture() -> dict[str, object]:
    return {
        "facts": {key: {"de": "", "en": ""} for key in (
            "notice_period", "start_date", "salary", "relocation", "travel_willingness", "driving_licence", "work_authorisation_text", "other_languages",
        )},
        "tools": [{
            "name": "Python", "aliases": ["Python"], "years": "", "level": "", "category": "automation",
            "example": {"de": "Ich automatisiere mit Python.", "en": "I build automation with Python."},
        }],
        "not_used": {
            "templates": {
                "de": "Ich habe {tool} nicht eingesetzt. Vergleichbar: {comparables}.",
                "en": "I have not used {tool}. Comparable: {comparables}.",
            },
            "tool_categories": {"UiPath": "automation", "n8n": "automation"},
        },
        "ratings": [{"area": "programming and integration", "value": 4, "scale": [1, 5]}],
        "saved": [], "learned": [], "ignore": [],
    }


def test_classifies_the_five_flix_questions_and_extracts_tools_and_scale():
    bank = bank_fixture()
    found = [classify_question(label, bank) for label in FLIX]
    assert [item["kind"] for item in found] == ["fact", "tool-years", "tool-experience", "tool-experience", "self-rating"]
    assert found[0]["fact_keys"] == ["notice_period", "start_date"]
    assert found[1]["tools"] == ["Workday"]
    assert found[2]["tools"] == ["n8n", "UiPath"]
    assert found[3]["tools"] == ["HR Tech"]
    assert found[4]["scale"] == [1.0, 5.0]


def test_unknown_tools_use_only_user_assigned_categories_and_comparables():
    bank = bank_fixture()
    field = {"label": FLIX[2], "kind": "textarea"}
    result = resolve_question(field, bank, {})
    assert result["source"] == "answer bank"
    assert result["value"] == "I have not used n8n, UiPath. Comparable: Python."
    del bank["not_used"]["tool_categories"]["UiPath"]
    assert resolve_question(field, bank, {})["missing"] is True


def test_missing_tool_years_never_calls_claude_and_asks_for_the_bank(tmp_path):
    data = tmp_path / "data.json"
    data.write_text(json.dumps({"profile": {}, "packages": [{"id": "abc", "employer": "Flix", "title": "Role"}]}), encoding="utf-8")
    bank_path = tmp_path / "answers.yaml"
    bank = bank_fixture()
    bank["tools"].append({"name": "Workday", "aliases": ["Workday"], "years": "", "level": "", "category": "", "example": {"de": "", "en": ""}})
    bank_path.write_text(yaml.safe_dump(bank, sort_keys=False), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Claude must not be called for missing years")

    result = answer_fields(
        data, "abc", [{"uid": "f0-years", "label": FLIX[1], "kind": "textarea"}],
        tmp_path / "cache.json", runner=forbidden, bank_path=bank_path,
    )
    assert result["answers"] == []
    assert result["needs_bank"][0]["kind"] == "tool-years"


def test_rating_conversion_requires_two_explicit_linear_scales():
    bank = bank_fixture()
    bank["ratings"] = [{"area": "programming and integration", "value": 8, "scale": [0, 10]}]
    explicit = resolve_question({"label": FLIX[4]}, bank, {})
    assert explicit["value"] == "4.2 out of 5." and explicit["source"] == "answer bank"
    implicit = resolve_question({"label": "How would you rate your programming and integration skills?"}, bank, {})
    assert implicit["missing"] is True
    bank["ratings"][0]["scale"] = []
    assert resolve_question({"label": FLIX[4]}, bank, {})["missing"] is True


def test_language_follows_the_question():
    bank = bank_fixture()
    bank["facts"]["notice_period"] = {"de": "Meine Kündigungsfrist beträgt vier Wochen.", "en": "My notice period is four weeks."}
    german = resolve_question({"label": "Wie lang ist Ihre Kündigungsfrist?"}, bank, {})
    english = resolve_question({"label": "What is your notice period?"}, bank, {})
    assert german["value"] == "" and german["suggestion"]["value"].startswith("Meine")
    assert english["value"] == "" and english["suggestion"]["value"].startswith("My")


def test_bank_facts_win_and_sport_thieme_dates_and_salary_are_formatted():
    bank = bank_fixture()
    bank["facts"]["start_date"] = {"de": "01.12.2026", "en": "01/12/2026"}
    bank["facts"]["salary"] = {"de": "52000", "en": "52000"}
    profile = {"start_date": "02.01.2027", "salary": "99999 EUR"}
    start = resolve_question({"label": "Wann könntest du bei uns starten? *", "kind": "text"}, bank, profile)
    salary = resolve_question({"label": "Was möchtest du für deinen Einsatz bei uns verdienen? (Angabe als Jahresbrutto in Vollzeit) *", "kind": "text"}, bank, profile)
    assert start["value"] == "01.12.2026" and start["source"] == "answer bank"
    assert salary["value"] == "52000" and salary["source"] == "answer bank"
    assert resolve_question({"label": "When can you start? DD/MM/YYYY", "inputType": "text"}, bank, profile)["value"] == "01/12/2026"
    assert resolve_question({"label": "Start date", "inputType": "date"}, bank, profile)["value"] == "2026-12-01"


def test_other_languages_and_unsafe_saved_answers(tmp_path):
    import pytest
    bank = bank_fixture()
    bank["facts"]["other_languages"] = {"de": "Persisch (Muttersprache)", "en": "Persian (native)"}
    result = resolve_question({"label": "In addition to German and English, do you speak any other languages?", "kind": "text"}, bank, {})
    assert result["value"] == "Persian (native)"
    bank["saved"] = [{"pattern": "Privacy", "answer": {"en": "Yes"}, "scope": "any employer"}]
    assert resolve_question({"label": "Privacy", "kind": "select"}, bank, {})["missing"] is True
    with pytest.raises(ValueError, match="never saved"):
        save_entry(tmp_path / "unused.yaml", {"label": "I confirm Privacy", "value": "Yes", "language": "en"})


def test_saved_scope_reuses_any_employer_but_not_another_employer_for_private_scope():
    bank = bank_fixture()
    bank["saved"] = [{
        "pattern": r"^Preferred work setting\?$", "answer": {"de": "", "en": "Hybrid."},
        "scope": "this employer only", "employer": "Flix",
    }]
    field = {"label": "Preferred work setting?"}
    assert resolve_question(field, bank, {}, employer="Flix")["value"] == "Hybrid."
    assert resolve_question(field, bank, {}, employer="Celonis")["missing"] is True
    bank["saved"][0]["scope"] = "any employer"
    assert resolve_question(field, bank, {}, employer="Celonis")["value"] == "Hybrid."


def test_cv_prefill_reads_only_the_explicit_software_line_and_leaves_details_empty(tmp_path):
    cv = """Kompetenzen
Business Intelligence: Workday is mentioned in prose.
Software: Python | SQL | Power BI | Excel inkl. VBA
Sprachen
Deutsch B2
"""
    assert extract_cv_skill_names(cv) == ["Python", "SQL", "Power BI", "Excel"]
    path = tmp_path / "antworten.yaml"
    bank = ensure_bank(path, {}, [cv])
    assert [item["name"] for item in bank["tools"]] == ["Python", "SQL", "Power BI", "Excel"]
    assert all(item["years"] == item["level"] == "" and item["example"] == {"de": "", "en": ""} for item in bank["tools"])


def test_save_entry_keeps_language_and_employer_scope(tmp_path):
    path = tmp_path / "antworten.yaml"
    save_entry(path, {"label": "Why this role?", "value": "Approved text.", "language": "en", "scope": "this employer only", "employer": "Example"})
    item = load_bank(path)["saved"][0]
    assert item["answer"]["en"] == "Approved text." and item["scope"] == "this employer only" and item["employer"] == "Example"


def test_ignore_survives_reload_and_hides_logged_question(tmp_path):
    from jobfilter.answerbank import unresolved_from_log
    path = tmp_path / "antworten.yaml"
    save_entry(path, {"kind": "ignore", "label": "Weitere Informationen"})
    log = tmp_path / "form_log.jsonl"
    log.write_text(json.dumps({"id": "p1", "fields": [{"label": "Weitere Informationen", "kind": "textarea", "status": "unknown"}]}) + "\n", encoding="utf-8")
    bank = load_bank(path)
    assert bank["ignore"] == ["Weitere Informationen"]
    assert unresolved_from_log(log, bank, {}) == []
