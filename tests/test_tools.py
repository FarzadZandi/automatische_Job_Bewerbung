"""The offline setup tools run on the fictional example persona and their outputs work with the app."""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PERSONA = ROOT / "templates" / "example_persona"
TOOLS = ROOT / "tools"


def run(tool: str, *args: object) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / tool), *map(str, args)], capture_output=True, text=True, cwd=ROOT)


def test_cv_letter_template_and_app_fill(tmp_path):
    pytest.importorskip("docx")
    cv = tmp_path / "Lebenslauf_Muster_Max_01.docx"
    assert run("build_cv_docx.py", PERSONA / "cv.yaml", cv).returncode == 0
    text = cv.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Berufserfahrung" in text and "Grundkenntnisse: Power BI" in text and "|" not in text
    template = tmp_path / "Anschreiben_Muster_Max.docx"
    assert run("build_letter_template.py", PERSONA / "profile.yaml", template).returncode == 0
    from jobfilter.applications import ApplicationJob, tailor_cover_letter
    fields = {name: "" for name in ApplicationJob.__dataclass_fields__}
    fields.update(title="Datenanalyst (m/w/d)", employer="Beispiel AG", location="Stuttgart")
    job = ApplicationJob(**fields)
    out = tailor_cover_letter(template, tmp_path / "letter.docx", job, proof_paragraphs=("Nachweis eins.", "Nachweis zwei."),
                              hook={"intro": "Ihre Daten sollen sicher genutzt werden, deshalb bewerbe ich mich.",
                                    "company_paragraph": "An Beispiel AG interessiert mich besonders die Analyse."},
                              applicant_status="professional", start_date="01.01.2099")
    from docx import Document
    body = "\n".join(p.text for p in Document(out).paragraphs)
    assert "Bewerbung als Datenanalyst" in body and "Nachweis zwei." in body and "01.01.2099" in body
    cells = Document(out).tables[0]
    assert cells.cell(1, 0).paragraphs[0].text == "Beispiel AG"


def test_family_letters_and_bank(tmp_path):
    pytest.importorskip("docx")
    assert run("build_family_letters.py", ROOT / "templates" / "letter_baukasten.example.yaml", tmp_path).returncode == 0
    assert (tmp_path / "Anschreiben_Vorlage_B01.docx").exists()
    assert "[UNTERNEHMEN" in (tmp_path / "Anschreiben_Vorlage_B01.md").read_text(encoding="utf-8")
    result = run("validate_bank.py", PERSONA / "bank.yaml", "--keywords", PERSONA / "keywords.json", "--profile", PERSONA / "profile.yaml")
    assert result.returncode == 0, result.stdout + result.stderr


def test_analysis_tools(tmp_path):
    vacancies = PERSONA / "vacancies"
    cv = tmp_path / "cv.docx"
    run("build_cv_docx.py", PERSONA / "cv.yaml", cv)
    result = run("ats_check.py", vacancies, cv.with_suffix(".txt"), PERSONA / "ledger.json", tmp_path / "ats.md")
    assert result.returncode == 0, result.stderr
    report = (tmp_path / "ats.md").read_text(encoding="utf-8")
    assert "## B01" in report and "claude_family" not in report and "vacancy_id" not in report
    assert run("family_overlap.py", vacancies, tmp_path / "overlap").returncode == 0
    assert "Market support" in (tmp_path / "overlap.md").read_text(encoding="utf-8")
    assert run("corpus_stats.py", vacancies).returncode == 0
    code = run("topic_check.py", vacancies, tmp_path / "topics.md", "--kmin", "2", "--kmax", "3", "--seeds", "2").returncode
    assert code == (0 if importlib.util.find_spec("sklearn") else 2)


def test_score_folder_and_agreement(tmp_path):
    scores = tmp_path / "scores.csv"
    result = subprocess.run([sys.executable, "-m", "jobfilter", "--config", str(tmp_path / "none.yaml"), "score-folder",
                             str(PERSONA / "vacancies"), "--keywords", str(PERSONA / "keywords.json"), "--out", str(scores)],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    rows = list(csv.DictReader(scores.open(encoding="utf-8-sig")))
    assert len(rows) == 12
    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["a", "b"])
        writer.writerows([[r["primary_family"], r["primary_family"]] for r in rows])
    out = run("agreement.py", labels, "a", "b")
    assert out.returncode == 0 and "Accuracy: 100.0%" in out.stdout
