from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobfilter.__main__ import build_parser
from jobfilter.hooks import check_hook, extract_json, import_hooks

PROOF_1 = "Bei Example Pharma habe ich ETL-Strecken in SQL Server aufgebaut und rund 6 % Marktanteil gewonnen."
INTRO = ("zwischen Fachbereich und IT zu übersetzen und Anforderungen so zu ordnen, dass sie umsetzbar werden: "
         "Für diese Aufgabe bewerbe ich mich als Business Analyst.")
PARAGRAPH = ("In meinen Analyseprojekten habe ich Anforderungen aus Fachbereichen aufgenommen, hinterfragt, priorisiert "
             "und in Reportinglösungen überführt. Die Steuerung von Anforderungen zwischen Teams kenne ich aus sieben "
             "Beratungsmandaten, in denen ich Ergebnisse vor Entscheidungsträgern präsentiert habe. Deutsch spreche ich "
             "aktiv auf B2-Niveau, Englisch auf C1-Niveau. So kann ich Ihre Fachbereiche und die IT von der ersten "
             "Anforderung bis zur fertigen Lösung zuverlässig verbinden.")


def _bank(path: Path) -> Path:
    lines = []
    for index in range(1, 11):
        lines += [f"B{index:02d}:", f'  - "{PROOF_1}"', '  - "Am Example Research Center habe ich 2.500 Profile ausgewertet und sieben Beratungsmandate geführt."']
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _package(root: Path, application_id: str, status: str = "needs_hook") -> Path:
    folder = root / f"{application_id}_example"
    folder.mkdir(parents=True)
    (folder / "application.json").write_text(json.dumps({
        "application_id": application_id, "title": "Business Analyst (m/w/d)", "employer": "Example GmbH",
        "primary_cv_family": "B06 Data Analytics", "language_flags": "GERMAN C1+", "submission_status": status,
    }), encoding="utf-8")
    (folder / "letter_brief.json").write_text(json.dumps({
        "family": "B06", "ad_text": "Deine Aufgaben: Anforderungen priorisieren. FÃ¼r 3 Teams.",
        "applicant_facts": "Deutsch B2-Niveau, Englisch C1-Niveau",
    }), encoding="utf-8")
    return folder


def test_text_around_the_json_is_dropped():
    reply = (
        "I can't directly type into the form. However, here is what you asked for {see below}:\n"
        '```json\n{"hooks":[{"id":"a","intro":"x","company_paragraph":"y"}]}\n```\n'
        "Once you provide more details, I can refine these. Soll ich noch eine Variante schreiben?"
    )
    assert extract_json(reply)["hooks"][0]["id"] == "a"
    with pytest.raises(ValueError, match="no JSON"):
        extract_json("Gerne helfe ich dir weiter.")
    with pytest.raises(ValueError, match="hooks"):
        extract_json('Hier: {"answers": []}')


def test_hook_rules_catch_invention_and_style_violations():
    facts = "Business Analyst Example GmbH sieben Beratungsmandate B2-Niveau C1-Niveau 6 % 2.500"
    assert check_hook({"intro": INTRO, "company_paragraph": PARAGRAPH}, facts) == []
    bad = {
        "intro": "ich bin hoch motiviert — und bewerbe mich. Zweiter Satz.",
        "company_paragraph": PARAGRAPH.replace("B2-Niveau", "C1-Niveau") + " Ich habe 40 % Kosten gespart und dbt eingesetzt.",
    }
    problems = " | ".join(check_hook(bad, facts))
    for needle in ("banned phrase", "em dash", "words, expected", "above B2", "not in the facts: 40", "Grundkenntnisse"):
        assert needle in problems
    assert check_hook({"intro": "", "company_paragraph": PARAGRAPH}, facts)[0] == "intro: empty"


def test_import_writes_only_checked_hooks_and_never_overwrites(tmp_path):
    out = tmp_path / "applications"
    good = _package(out, "aaaaaaaaaaaa")
    _package(out, "bbbbbbbbbbbb")
    done = _package(out, "cccccccccccc", status="not_submitted")
    bank = _bank(tmp_path / "bank.yaml")
    reply = "Hier sind die Texte:\n" + json.dumps({"hooks": [
        {"id": "AAAAAAAAAAAA", "intro": INTRO, "company_paragraph": PARAGRAPH},
        {"id": "bbbbbbbbbbbb", "intro": INTRO, "company_paragraph": PARAGRAPH + " Dabei habe ich 99 Dashboards gebaut."},
        {"id": "cccccccccccc", "intro": INTRO, "company_paragraph": PARAGRAPH},
        {"id": "zzzzzzzzzzzz", "intro": INTRO, "company_paragraph": PARAGRAPH},
    ]}, ensure_ascii=False) + "\nMöchtest du Anpassungen?"
    report = import_hooks(out, bank, reply)
    assert report["written"] == ["aaaaaaaaaaaa"]
    assert json.loads((good / "hook.json").read_text(encoding="utf-8")) == {"intro": INTRO, "company_paragraph": PARAGRAPH}
    assert "99" in report["rejected"]["bbbbbbbbbbbb"][-1]
    assert "not needs_hook" in report["rejected"]["cccccccccccc"][0] and not (done / "hook.json").exists()
    assert report["rejected"]["zzzzzzzzzzzz"] == ["no package with this id"]
    again = import_hooks(out, bank, reply)
    assert again["written"] == [] and "already exists" in again["rejected"]["aaaaaaaaaaaa"][0]




def test_hook_never_states_a_gap_and_never_repeats_the_proof():
    facts = "Business Analyst Example GmbH sieben Beratungsmandate B2-Niveau C1-Niveau 6 % 2.500"
    for gap in ("Im regulierten Bankenumfeld habe ich noch nicht gearbeitet.",
                "PyTorch gehört nicht zu meinen belegten Erfahrungen, das sage ich offen.",
                "Die Anzeige verlangt verhandlungssicheres Deutsch; dieses Niveau habe ich nicht."):
        problems = " | ".join(check_hook({"intro": INTRO, "company_paragraph": PARAGRAPH + " " + gap}, facts))
        assert "states a gap" in problems, gap
    proof = ("Bei Example Pharma habe ich ETL-Strecken in Microsoft SQL Server aufgebaut, die heterogene Vertriebs- "
             "und Marktdaten standardisieren.")
    repeated = PARAGRAPH + " Bei Example Pharma habe ich ETL-Strecken in Microsoft SQL Server aufgebaut, die heterogene Vertriebs- und Marktdaten standardisieren."
    assert "repeats a proof paragraph sentence" in " | ".join(check_hook({"intro": INTRO, "company_paragraph": repeated}, facts, proof))
    assert check_hook({"intro": INTRO, "company_paragraph": PARAGRAPH}, facts, proof) == []
