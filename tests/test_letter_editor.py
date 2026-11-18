from __future__ import annotations

import json
import html
import zipfile
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

from jobfilter.letter_editor import edit_letter, read_letter, save_package
from tests.test_hooks import _bank


INTRO = "mit Interesse bewerbe ich mich auf die ausgeschriebene Stelle im Bereich Datenanalyse und Berichterstattung bei Ihrem Unternehmen."
COMPANY = "Die ausgeschriebenen Aufgaben verbinden Analyse und verständliche Berichterstattung. Meine belegte Erfahrung in der Aufbereitung und Auswertung von Daten kann ich dafür einsetzen und Ergebnisse strukturiert für Fachbereiche darstellen. Die konkrete Zusammenarbeit mit Ihrem Team möchte ich im Gespräch genauer kennenlernen."


def _docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{html.escape(value)}</w:t></w:r></w:p>" for value in paragraphs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')


def _letter(path: Path) -> list[str]:
    paragraphs = [
        "Bewerbung als Analyst", "Sehr geehrte Damen und Herren,", INTRO,
        "Fester Beleg eins bleibt exakt erhalten.", "Fester Beleg zwei bleibt exakt erhalten.",
        COMPANY, "Derzeit befinde ich mich in der Abschlussphase meines Studiums.", "Mit freundlichen Grüßen", "Max Muster",
    ]
    _docx(path, paragraphs)
    return paragraphs


def test_reads_and_edits_only_hook_paragraphs(tmp_path):
    path = tmp_path / "letter.docx"
    original = _letter(path)
    before = read_letter(path)
    result = edit_letter(path, "neue konkrete Einleitung für diese Stelle.", "Neuer Absatz für das Unternehmen und die Aufgaben.")
    assert before["intro"] == INTRO and before["company_paragraph"] == COMPANY
    assert result["intro"].startswith("neue konkrete") and result["company_paragraph"].startswith("Neuer Absatz")
    assert result["fixed_paragraphs"] == [value for value in original if value not in {INTRO, COMPANY}]


def _package(tmp_path: Path):
    docs = tmp_path / "Job App Docs"
    docs.mkdir()
    _bank(docs / "anschreiben_bausteine.yaml")
    (docs / "bewerberprofil.yaml").write_text('first_name: "Max"\nlast_name: "Muster"\nemail: "m@example.org"\n', encoding="utf-8")
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text("{}", encoding="utf-8")
    folder = tmp_path / "out" / "applications" / "abc_example"
    folder.mkdir(parents=True)
    _letter(folder / "letter.docx")
    _docx(folder / "cv.docx", ["Python SQL Power BI", "Über fünf Jahre Erfahrung in angewandter Datenanalyse."])
    manifest = {
        "manifest_version": 2, "application_id": "abc", "title": "Analyst", "employer": "Example GmbH",
        "location": "Berlin", "url": "https://jobs.example/a", "application_url": "https://jobs.example/a",
        "selected_cv": "cv.docx", "cover_letter": "letter.docx", "submission_status": "opened",
        "primary_cv_family": "B06", "attachments": [],
    }
    (folder / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "letter_brief.json").write_text(json.dumps({"family": "B06", "ad_text": "Analyse und Berichte"}), encoding="utf-8")
    (folder / "hook.json").write_text(json.dumps({"intro": INTRO, "company_paragraph": COMPANY}), encoding="utf-8")
    config = SimpleNamespace(
        application_output_dir=tmp_path / "out" / "applications", applicant_profile_path=docs / "bewerberprofil.yaml",
        autofill_extension_dir=extension, application_attachment_allowlist=(), job_documents_dir=docs,
        application_archive_dir=tmp_path / "Bewerbungen",
    )
    return config, folder


def test_save_returns_warnings_keeps_master_and_rebuilds_pdf(tmp_path):
    config, folder = _package(tmp_path)
    fixed = read_letter(folder / "letter.docx")["fixed_paragraphs"]
    calls = []
    def converter(pairs):
        calls.extend(pairs)
        for _, target in pairs:
            target.write_bytes(b"%PDF fake")
        return "fake"
    result = save_package(config, "abc", "ich bin hoch motiviert – für diese Stelle.", COMPANY, converter=converter)
    assert result["reopen"] is True and calls
    assert any("banned phrase" in warning for warning in result["warnings"])
    assert any("dash" in warning for warning in result["warnings"])
    assert read_letter(folder / "letter.docx")["fixed_paragraphs"] == fixed
    data = json.loads((config.autofill_extension_dir / "data" / "data.json").read_text(encoding="utf-8"))
    assert "hoch motiviert" in data["packages"][0]["letter_text"]
    tracker = load_workbook(config.application_archive_dir / "Bewerbungs_Tracker.xlsx")["Bewerbungen"]
    headers = [cell.value for cell in tracker[1]]
    row = {name: tracker.cell(2, index + 1).value for index, name in enumerate(headers)}
    assert "hoch motiviert" in row["Anschreiben_Output"] and row["Entscheidung"] == "Paket erstellt"
