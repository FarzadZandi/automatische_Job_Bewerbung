from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from jobfilter.formanswers import (
    answer_fields, build_plain_prompt, build_prompt, build_suggest_prompt, check_answer, facts_text,
)


def _data(path: Path) -> Path:
    path.write_text(json.dumps({
        "profile": {"first_name": "Max", "last_name": "Muster", "email": "max@example.org", "city": "Ulm"},
        "packages": [{
            "id": "abc", "title": "Analyst", "employer": "Example GmbH", "source": "LinkedIn",
            "cv_text": "Fünf Jahre Analyse mit Python. Projekt 2024.", "letter_text": "Ich arbeite mit Python.",
            "job_description": "Vollständige Stellenbeschreibung mit Analyse und Zusammenarbeit.",
        }],
    }), encoding="utf-8")
    return path


def test_chat_facts_keep_full_cv_and_letter_without_unlisted_profile_data():
    text = facts_text({"first_name": "Max", "birth_date": "01.01.1990"}, {
        "title": "Analyst", "employer": "Example", "source": "XING", "cv_text": "x" * 4000, "letter_text": "y" * 3000,
    })
    assert "birth_date" not in text
    assert len(text.split("Lebenslauf:\n", 1)[1].split("\n\nAnschreiben", 1)[0]) == 4000
    assert len(text.split("\n\nAnschreiben:\n", 1)[1]) == 3000


def test_unverified_facts_and_unbounded_long_answers_are_rejected():
    value, problems = check_answer({}, "Ich habe 7 Jahre Erfahrung.", "Ich habe fünf Jahre Erfahrung.", "Erfahrung")
    assert value == "" and any("not in the facts" in problem for problem in problems)
    value, problems = check_answer({}, "Ich habe Erfahrung mit dbt und Snowflake.", "Python und SQL", "Python und SQL")
    assert value == "" and any("Tier-2" in problem for problem in problems)
    value, problems = check_answer(
        {}, "Ich arbeite mit Python, SQL und Power BI.", "Python, SQL und Power BI", "Python, SQL und Power BI",
    )
    assert value == "Ich arbeite mit Python, SQL und Power BI." and problems == []
    value, problems = check_answer({}, "Ich nutze PowerBI.", "Python und SQL", "Python und SQL")
    assert value == "" and any("product-like" in problem for problem in problems)
    value, problems = check_answer({}, "x" * 601, "", "")
    assert value == "" and any("600" in problem for problem in problems)


def test_invented_capitalised_names_are_rejected_against_the_full_vocabulary():
    facts = "Kurze Fakten mit Python."
    value, problems = check_answer(
        {"label": "Welche Werkzeuge nutzen Sie?"},
        "Ich habe mit Databricks und Kubernetes gearbeitet.", facts, "Python SQL Power BI",
    )
    assert value == "" and any("Databricks" in problem and "Kubernetes" in problem for problem in problems)
    value, problems = check_answer(
        {"label": "Beruflicher Hintergrund"}, "Ich habe bei Siemens gearbeitet.", facts, "Example GmbH",
    )
    assert value == "" and any("Siemens" in problem for problem in problems)
    value, problems = check_answer(
        {"label": "Welche Aufgabe interessiert Sie?"}, "Ich übernehme Datenmodellierung.", facts,
        "Job excerpt: Datenmodellierung und Analyse",
    )
    assert value == "Ich übernehme Datenmodellierung." and problems == []


def test_claude_checks_still_reject_unverified_years_and_rating_numbers():
    years = {"label": "How many years of experience do you have working with Workday?"}
    vocabulary = "Workday experience direct documented quantified facts year"
    value, problems = check_answer(
        years, "No quantified direct Workday experience is documented in the facts.",
        "Workday experience facts", vocabulary,
    )
    assert value and problems == []
    value, problems = check_answer(years, "I have 3 years of Workday experience.", "Workday experience facts", vocabulary)
    assert value == "" and any("3" in problem for problem in problems)

    rating = {"label": "How would you rate your programming skills on a scale of 1-5?"}
    vocabulary = "programming skills Python SQL evidence"
    value, problems = check_answer(
        rating, "I would rate my programming skills 4 out of 5 based on Python and SQL evidence.",
        "Python SQL evidence", vocabulary,
    )
    assert value == "" and any("4" in problem and "5" in problem for problem in problems)
    value, problems = check_answer(rating, "I would rate my programming skills 6 out of 5.", "", vocabulary)
    assert value == "" and any("6" in problem for problem in problems)


def test_years_and_ratings_have_no_claude_specific_prompt_exception():
    years_prompt = build_plain_prompt(
        {"label": "How many years of experience do you have working with Workday?"}, "Workday facts",
    )
    assert "keine quantifizierte" not in years_prompt
    rating_prompt = build_prompt(
        [{"uid": "rate", "label": "Rate your skills on a scale of 1-5", "kind": "textarea"}], "Python facts",
    )
    assert "subjektive Selbsteinschätzung" not in rating_prompt


def test_all_form_prompt_builders_use_the_bounded_full_description_in_fixed_order():
    description = "A" * 1600 + " END_BEYOND_1500 " + "B" * 7000 + " OUTSIDE_CAP"
    field = {"uid": "a", "label": "Why this role?", "kind": "textarea", "options": ["Yes", "No"]}
    prompts = [
        build_prompt([field], "Verified applicant facts", job_description=description),
        build_plain_prompt(field, "Verified applicant facts", job_description=description),
        build_suggest_prompt([field | {"kind": "select"}], "Verified applicant facts", job_description=description),
    ]
    for prompt in prompts:
        assert "END_BEYOND_1500" in prompt and "OUTSIDE_CAP" not in prompt
        facts_at = prompt.index("FAKTEN\n")
        description_at = prompt.index("STELLENBESCHREIBUNG\n")
        field_at = min(index for marker in ("OFFENE FELDER\n", "FELD\n", "FELDER\n") if (index := prompt.find(marker)) >= 0)
        assert facts_at < description_at < field_at


def test_one_fake_claude_request_is_cached_per_package_and_label(tmp_path, monkeypatch):
    import jobfilter.claude_writer as writer

    monkeypatch.setattr(writer, "find_claude", lambda home=None: "claude.exe")
    calls = []
    commands = []

    def fake_runner(command, **kwargs):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="")
        commands.append(command)
        calls.append(kwargs["input"])
        result = "Ich arbeite mit Python." if "--json-schema" not in command else json.dumps({"answers": [
            {"id": "f0-a", "value": "Ich arbeite mit Python."},
            {"id": "f0-b", "value": "Vielleicht"},
        ]})
        return SimpleNamespace(returncode=0, stdout=json.dumps({"is_error": False, "result": result}), stderr="")

    fields = [
        {"uid": "f0-a", "label": "Warum möchten Sie hier arbeiten?", "kind": "textarea", "options": [], "maxLength": 120},
        {"uid": "f0-b", "label": "Sind Sie umzugsbereit?", "kind": "select", "options": ["Ja", "Nein"]},
        {"uid": "f0-c", "label": "Salary expectation", "kind": "text", "options": []},
        {"uid": "f0-d", "label": "Deutschkenntnisse", "kind": "text", "options": []},
        {"uid": "f0-e", "label": "Führerschein", "kind": "radio", "options": ["Ja", "Nein"]},
    ]
    first = answer_fields(_data(tmp_path / "data.json"), "abc", fields, tmp_path / "cache.json", runner=fake_runner)
    second = answer_fields(tmp_path / "data.json", "abc", fields, tmp_path / "cache.json", runner=fake_runner)
    assert len(calls) == 1 and first["cached"] is False and second["cached"] is True
    assert first["answers"] == second["answers"] == [{
        "uid": "f0-a", "value": "Ich arbeite mit Python.", "label": "Warum möchten Sie hier arbeiten?", "source": "Claude",
    }]
    assert "f0-b" not in calls[0] and "Optionen:" not in calls[0]
    assert first["rejected"] == {}
    assert first["yours"] == ["Sind Sie umzugsbereit?", "Führerschein"]
    assert [item["label"] for item in first["needs_bank"]] == ["Salary expectation", "Deutschkenntnisse"]
    assert first["model"] == second["model"] == "haiku"
    assert commands[0][commands[0].index("--model") + 1] == "haiku"
    assert "--json-schema" not in commands[0] and "ohne JSON" in calls[0]
    assert "STELLENBESCHREIBUNG\nVollständige Stellenbeschreibung" in calls[0]

    forced = answer_fields(
        tmp_path / "data.json", "abc", fields, tmp_path / "cache.json", runner=fake_runner,
        user_instruction="Emphasise the product impact without inventing facts.", force=True,
    )
    assert len(calls) == 2 and forced["cached"] is False
    assert "ZUSÄTZLICHE ANWEISUNG DES BEWERBERS" in calls[1]
    assert "Emphasise the product impact without inventing facts." in calls[1]
    assert "f0-b" not in calls[1] and "Optionen:" not in calls[1]


def test_multiple_open_text_fields_keep_one_structured_batch(tmp_path, monkeypatch):
    import jobfilter.claude_writer as writer
    monkeypatch.setattr(writer, "find_claude", lambda home=None: "claude.exe")
    commands = []
    prompts = []
    def fake_runner(command, **kwargs):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="")
        commands.append(command)
        prompts.append(kwargs["input"])
        result = {"answers": [
            {"id": "a", "value": "Ich arbeite mit Python."},
            {"id": "b", "value": "Ich arbeite mit Python."},
        ]}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"is_error": False, "result": json.dumps(result)}), stderr="")
    fields = [
        {"uid": "a", "label": "Warum diese Stelle?", "kind": "textarea", "maxLength": 200},
        {"uid": "b", "label": "Was bringen Sie mit?", "kind": "textarea", "maxLength": 200},
    ]
    result = answer_fields(_data(tmp_path / "data.json"), "abc", fields, tmp_path / "cache.json", runner=fake_runner)
    assert [item["uid"] for item in result["answers"]] == ["a", "b"]
    assert len(commands) == 1 and "--json-schema" in commands[0]
    assert "STELLENBESCHREIBUNG\nVollständige Stellenbeschreibung" in prompts[0]


def test_plain_prompt_uses_the_field_limit_and_keeps_user_instruction_bounded():
    prompt = build_plain_prompt(
        {"label": "Why this role?", "maxLength": 280}, "Verified Python facts",
        "Focus on impact. " + "x" * 2000,
    )
    assert "Maximal 280 Zeichen" in prompt and "ohne JSON" in prompt
    instruction = prompt.split("ZUSÄTZLICHE ANWEISUNG DES BEWERBERS\n", 1)[1].split("\n\n", 1)[0]
    assert len(instruction) == 1200


def test_user_instruction_is_bounded_and_does_not_replace_safety_rules():
    prompt = build_prompt(
        [{"uid": "a", "label": "What interests you?", "kind": "textarea"}],
        "Verified Python facts", "Focus on research. " + "x" * 2000,
    )
    assert "Verwende ausschließlich die Fakten" in prompt
    assert "Focus on research." in prompt
    instruction = prompt.split("ZUSÄTZLICHE ANWEISUNG DES BEWERBERS\n", 1)[1].split("\n\n", 1)[0]
    assert len(instruction) == 1200


def test_suggest_mode_accepts_only_exact_options_and_is_never_cached(tmp_path, monkeypatch):
    import jobfilter.claude_writer as writer
    monkeypatch.setattr(writer, "find_claude", lambda home=None: "claude.exe")
    calls = []
    def fake_runner(command, **kwargs):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="")
        calls.append((command, kwargs["input"]))
        result = {"answers": [
            {"id": "level", "value": "C1", "reason": "The profile states English C1."},
            {"id": "travel", "value": "Maybe", "reason": "Not an offered option."},
        ]}
        return SimpleNamespace(returncode=0, stdout=json.dumps({"is_error": False, "result": json.dumps(result)}), stderr="")
    fields = [
        {"uid": "level", "label": "English level", "kind": "select", "options": ["B2", "C1"]},
        {"uid": "travel", "label": "Travel readiness", "kind": "radio", "options": ["Yes", "No"]},
        {"uid": "salary", "label": "Salary expectation", "kind": "select", "options": ["50,000", "60,000"]},
    ]
    result = answer_fields(_data(tmp_path / "data.json"), "abc", fields, tmp_path / "cache.json", runner=fake_runner, mode="suggest")
    assert result["answers"] == []
    assert result["suggestions"] == [{"uid": "level", "label": "English level", "value": "C1", "reason": "The profile states English C1."}]
    assert result["rejected"]["Travel readiness"] == ["suggestion is not an exact offered option"]
    assert result["yours"] == ["Salary expectation"] and not (tmp_path / "cache.json").exists()
    assert calls[0][0][calls[0][0].index("--effort") + 1] == "low"
    assert "STELLENBESCHREIBUNG\nVollständige Stellenbeschreibung" in calls[0][1]


def test_general_free_questions_go_to_claude_but_declarations_do_not(tmp_path):
    from jobfilter.formanswers import PERSONAL_FREE
    from jobfilter.answerbank import classify_question

    values = 'What does "The Best Team Wins" mean to you?'
    assert classify_question(values)["kind"] == "free" and not PERSONAL_FREE.search(values)
    for label in ("I confirm, that I have read the Privacy Notice", "Please indicate your gender identity",
                  "Will you now or in the future require immigration sponsorship?", "Most Recent Employer*",
                  "Deutschkenntnisse"):
        assert PERSONAL_FREE.search(label), label


def test_chat_answer_uses_job_and_cv_context_and_reports_warnings(tmp_path):
    import json as _json
    from types import SimpleNamespace
    from jobfilter.formanswers import chat_answer

    data = tmp_path / "data.json"
    data.write_text(_json.dumps({"profile": {"first_name": "Max"}, "packages": [{
        "id": "p1", "title": "Data Analyst", "employer": "ACME GmbH", "cv_text": "Python SQL Power BI bei Example Pharma.",
        "letter_text": "Sehr geehrte Damen und Herren", "job_excerpt": "Kurzer Auszug.",
        "job_description": "Teamarbeit ist uns wichtig. Vollständige Stellenbeschreibung.",
    }]}), encoding="utf-8")
    prompts = []

    def runner(command, **options):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(stdout=_json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="", returncode=0)
        prompts.append(options.get("input", ""))
        reply = "1. Für mich bedeutet das, dass ein Team mit Python und SQL bei Siemens gemeinsam mehr erreicht."
        return SimpleNamespace(stdout=_json.dumps({"result": reply, "usage": {"input_tokens": 100, "output_tokens": 30}}), stderr="", returncode=0)

    result = chat_answer(data, "p1", 'Schreibe 80 Wörter zu "The Best Team Wins".',
                         [{"role": "user", "text": "Hallo"}, {"role": "claude", "text": "Hallo!"}],
                         runner=runner, usage_path=tmp_path / "usage.jsonl")
    prompt = prompts[0]
    assert "Example Pharma" in prompt and "Vollständige Stellenbeschreibung." in prompt and "Bewerber: Hallo" in prompt
    assert prompt.index("BEWERBERFAKTEN") < prompt.index("STELLENBESCHREIBUNG") < prompt.index("BISHERIGER CHAT")
    assert "Siemens" in result["answers"][0]["answer"]
    assert any("Siemens" in warning for warning in result["answers"][0]["warnings"])
    usage = _json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8"))
    assert usage["purpose"] == "chat" and usage["fields"] == 1


def test_chat_splits_five_answers_and_checks_each_one(tmp_path):
    from jobfilter.formanswers import chat_answer

    data = tmp_path / "data.json"
    data.write_text(json.dumps({"profile": {"first_name": "Max"}, "packages": [{
        "id": "p1", "title": "Analyst", "employer": "Example", "cv_text": "Python SQL Power BI",
        "letter_text": "Python", "job_description": "Analyse mit Python und SQL",
    }]}), encoding="utf-8")

    def runner(command, **options):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="", returncode=0)
        reply = "\n".join(f"{number}. Ich arbeite mit Python und SQL." for number in range(1, 6))
        return SimpleNamespace(stdout=json.dumps({"result": reply, "usage": {"input_tokens": 50, "output_tokens": 70}}), stderr="", returncode=0)

    message = "\n".join(f"{number}. Frage {number}?" for number in range(1, 6))
    result = chat_answer(data, "p1", message, runner=runner, usage_path=tmp_path / "usage.jsonl")
    assert [item["number"] for item in result["answers"]] == [1, 2, 3, 4, 5]
    assert all(item["question"] == f"Frage {item['number']}?" for item in result["answers"])
    assert all(item["warnings"] == [] for item in result["answers"])
    assert json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8"))["fields"] == 5


def test_chat_keeps_a_120_word_star_answer_and_includes_answer_bank(tmp_path):
    from jobfilter.formanswers import chat_answer
    data = tmp_path / "data.json"
    data.write_text(json.dumps({"profile": {}, "packages": [{
        "id": "p1", "title": "International role", "employer": "Example", "cv_text": "Beispiel GmbH MENA Südostasien Export GMP Prozess",
        "letter_text": "Beispiel GmbH", "job_description": "Länderanforderungen analysieren",
    }]}), encoding="utf-8")
    bank = tmp_path / "answers.yaml"
    bank.write_text(json.dumps({"facts": {"salary": {"de": "52000", "en": "52000"}}, "tools": []}), encoding="utf-8")
    words = ["Ich", "analysierte", "bei", "Beispiel", "GmbH", "Länderanforderungen", "für", "MENA", "und", "Südostasien", "im", "Export", "GMP", "Prozess"]
    reply = "1. " + " ".join((words * 9)[:120])
    prompts = []
    def runner(command, **options):
        if command[1:3] == ["auth", "status"]:
            return SimpleNamespace(stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}), stderr="", returncode=0)
        prompts.append(options["input"])
        return SimpleNamespace(stdout=json.dumps({"result": reply, "usage": {"input_tokens": 50, "output_tokens": 100}}), stderr="", returncode=0)
    result = chat_answer(data, "p1", "Bitte gebe ein Beispiel für eine Situation, den Prozess und die wichtigen Punkte.", runner=runner, bank_path=bank)
    assert len(result["answers"][0]["answer"].split()) == 120
    assert not any("longer than 50 words" in warning for warning in result["answers"][0]["warnings"])
    assert "ANTWORTBANK" in prompts[0] and "52000" in prompts[0]


def test_extension_chat_is_relayed_through_the_app_without_network_calls():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    panel = (root / "extension" / "panel.js").read_text(encoding="utf-8")
    background = (root / "extension" / "background.js").read_text(encoding="utf-8")
    page = (root / "jobfilter" / "app_page.html").read_text(encoding="utf-8")
    assert "jf-panel-chat" in panel and "jf-panel-chat-answer" in panel
    assert "action: 'chat'" in background and "'chat-answer'" in background
    assert 'api("/api/chat"' in page
    assert "chat-all-fields" not in panel and "bestFieldForQuestion" in panel
    for source in (panel, background):
        assert "fetch(" not in source


def test_round20_german_short_prompts_optional_notes_and_gap_checks(tmp_path):
    facts = "Python SQL Analyse"
    prompt = build_prompt(
        [{"uid": "q", "label": "Why this role?", "kind": "textarea"}], facts,
        job_description="Analyse", answer_language="de",
    )
    plain = build_plain_prompt(
        {"uid": "q", "label": "Why this role?", "kind": "textarea"}, facts,
        answer_language="de",
    )
    assert "Antworte immer auf Deutsch" in prompt and "Höchstens 50 Wörter" in prompt
    assert "Antworte immer auf Deutsch" in plain and "höchstens 50 Wörtern" in plain

    value, problems = check_answer(
        {"label": "Warum interessiert Sie die Stelle?"},
        "Ich habe bisher keine Erfahrung mit Python.", facts, facts,
    )
    assert value == "" and any("gap" in problem for problem in problems)
    value, problems = check_answer(
        {"label": "Warum interessiert Sie die Stelle?"},
        "Ich kann für die Stelle umziehen.", facts + " umziehen", facts + " umziehen",
    )
    assert value == "" and any("sensitive" in problem for problem in problems)

    calls = []
    def runner(command, **options):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    result = answer_fields(
        _data(tmp_path / "data.json"), "abc",
        [{"uid":"note", "label":"Anmerkungen", "kind":"textarea"}],
        tmp_path / "cache.json", runner=runner, answer_language="de",
    )
    assert result["answers"] == [] and calls == []
