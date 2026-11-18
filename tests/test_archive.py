from __future__ import annotations

import json
import gc
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from jobfilter.__main__ import build_parser
from jobfilter.applications import set_application_status
from jobfilter.archive import HEADERS, TRACKER_NAME, ArchiveBusy, letter_document_text, sync_archive
from tests.test_autofill import _docx

LETTER = [
    "Max Muster", "Beispielweg 7", "12345 Musterstadt", "13.11.2026", "Example GmbH", "Ulm", "",
    "Bewerbung als Analyst (m/w/d)", "", "Sehr geehrte Damen und Herren,", "", "erster Absatz.", "",
    "Mit freundlichen Grüßen", "", "Max Muster", "Telefon: 1 | max@example.org",
]


def _package(root: Path, application_id: str, status: str, *, letter: bool = True, **extra: object) -> Path:
    folder = root / f"{application_id}_example"
    folder.mkdir(parents=True)
    _docx(folder / "cv.docx", ["Max Muster", "Berufserfahrung", "Analyst bei Beispiel AG"])
    if letter:
        _docx(folder / "letter.docx", LETTER)
    (folder / "letter_brief.json").write_text(json.dumps({"ad_text": "Wir suchen für das Team und die Analyse der Daten."}), encoding="utf-8")
    manifest = {
        "manifest_version": 2, "application_id": application_id, "title": f"Analyst {application_id[:2]}",
        "employer": "Example GmbH", "location": "Ulm", "score": 12.0, "band": "PRIORITY",
        "primary_cv_family": "B06 Data Analytics", "family_hits": "analyse", "language_flags": "GERMAN C1+",
        "application_url": "https://jobs.example.com/1", "selected_cv": "cv.docx",
        "cover_letter": "letter.docx" if letter else "", "submission_status": status,
    } | extra
    (folder / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _rows(path: Path) -> list[dict[str, object]]:
    workbook = load_workbook(path)
    try:
        sheet = workbook["Bewerbungen"]
        headers = [cell.value for cell in sheet[1]]
        return [dict(zip(headers, [cell.value for cell in row])) for row in sheet.iter_rows(min_row=2) if any(cell.value for cell in row)]
    finally:
        workbook.close()
        gc.collect()


def test_letter_text_starts_at_the_subject_and_ends_at_the_signature(tmp_path):
    _docx(tmp_path / "letter.docx", LETTER)
    assert letter_document_text(tmp_path / "letter.docx") == (
        "Bewerbung als Analyst (m/w/d)\n\nSehr geehrte Damen und Herren,\n\nerster Absatz.\n\n"
        "Mit freundlichen Grüßen\n\nMax Muster"
    )


def test_tracker_holds_one_row_per_package_with_cv_and_letter_text(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    _package(out, "aaaaaaaaaaaa", "not_submitted")
    _package(out, "bbbbbbbbbbbb", "needs_hook", letter=False)
    _package(out, "cccccccccccc", "skipped")
    _package(out, "dddddddddddd", "not_submitted", manifest_version=1, submission_mode="manual_or_authorized_integration")
    report = sync_archive(out, archive)
    assert (report["added"], report["rows"], report["documents_saved"]) == (1, 1, 0)
    (row,) = _rows(archive / TRACKER_NAME)
    assert tuple(row) == HEADERS
    assert (row["ID"], row["Unternehmen"], row["Entscheidung"], row["Ergebnis"], row["Quelle"]) == (
        "aaaaaaaaaaaa", "Example GmbH", "Paket erstellt", "Offen", "Pipeline",
    )
    assert (row["Typ"], row["JD-Sprache"], row["Lebenslauf"], row["Score"]) == ("Vollzeit", "DE", "B06", "12 Punkte (PRIORITY)")
    assert row["CV_Output"].startswith("B06 Data Analytics\n\nMax Muster") and "Beispiel AG" in row["CV_Output"]
    assert row["Anschreiben_Output"].startswith("Bewerbung als")
    assert row["Ordner"] == str(Path("out") / "applications" / "aaaaaaaaaaaa_example")
    workbook = load_workbook(archive / TRACKER_NAME)
    assert workbook.sheetnames == ["Bewerbungen", "Nicht beworben", "Legende"]
    assert workbook["Bewerbungen"].freeze_panes == "A2" and workbook["Bewerbungen"].auto_filter.ref.startswith("A1:Q")


def test_manual_package_keeps_source_and_original_link_in_tracker(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    original = "https://www.xing.com/jobs/berlin-data-analyst-123"
    _package(
        out, "aaaaaaaaaaaa", "not_submitted", board="manual",
        application_url="https://jobs.example.com/data-analyst", url=original,
    )
    sync_archive(out, archive)
    (row,) = _rows(archive / TRACKER_NAME)
    assert row["Quelle"] == "manual" and row["Link"] == original


def test_submission_keeps_the_sent_documents_and_user_columns_survive(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    folder = _package(out, "aaaaaaaaaaaa", "not_submitted")
    sync_archive(out, archive)
    path = archive / TRACKER_NAME
    workbook = load_workbook(path)
    sheet = workbook["Bewerbungen"]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(row=2, column=headers.index("Ergebnis") + 1, value="Interview")
    sheet.cell(row=2, column=headers.index("Notizen") + 1, value="Rückruf am Montag")
    workbook.save(path)
    workbook.close()
    del workbook
    gc.collect()

    (folder / "Lebenslauf_Max_Muster.pdf").write_bytes(b"%PDF cv")
    (folder / "Anschreiben_Max_Muster.pdf").write_bytes(b"%PDF letter")
    manifest = json.loads((folder / "application.json").read_text(encoding="utf-8"))
    manifest |= {"submission_status": "submitted", "status_updated_at": "2026-11-14T10:00:00+00:00"}
    (folder / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = sync_archive(out, archive)
    assert (report["added"], report["updated"], report["documents_saved"]) == (0, 1, 1)
    (row,) = _rows(path)
    assert (row["Entscheidung"], row["Datum"], row["Ergebnis"], row["Notizen"]) == ("Beworben", "2026-11-14", "Interview", "Rückruf am Montag")
    kept = archive / "Unterlagen" / "2026-11-14_Example-GmbH_aaaaaaaaaaaa"
    assert sorted(item.name for item in kept.iterdir()) == ["Anschreiben_Max_Muster.pdf", "Lebenslauf_Max_Muster.pdf"]
    assert row["Ordner"] == str(Path("Bewerbungen") / "Unterlagen" / kept.name)

    # After submission the record is frozen: a later change of the package never rewrites what was sent.
    _docx(folder / "letter.docx", [line.replace("erster Absatz.", "anderer Text.") for line in LETTER])
    sync_archive(out, archive)
    assert "erster Absatz." in _rows(path)[0]["Anschreiben_Output"]

    manifest["submission_status"] = "skipped"
    (folder / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    sync_archive(out, archive)
    assert _rows(path)[0]["Entscheidung"] == "Nicht beworben" and len(_rows(path)) == 1


def test_mistaken_submission_can_be_corrected_in_tracker(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    folder = _package(out, "aaaaaaaaaaaa", "submitted")
    manifest_path = folder / "application.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status_updated_at"] = "2026-11-14T10:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    sync_archive(out, archive)
    assert _rows(archive / TRACKER_NAME)[0]["Entscheidung"] == "Beworben"

    set_application_status(out, "aaaaaaaaaaaa", "not_submitted")
    sync_archive(out, archive)
    (row,) = _rows(archive / TRACKER_NAME)
    assert row["Entscheidung"] == "Paket erstellt"
    assert row["Datum"] == "2026-11-13"
    assert row["Ordner"].endswith("aaaaaaaaaaaa_example")


def test_existing_tracker_is_kept_and_its_rows_are_adopted_not_duplicated(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    old = Workbook()
    sheet = old.active
    sheet.title = "Bewerbungen"
    sheet.append(["Datum", "Unternehmen", "Position", "Typ", "JD-Sprache", "Score", "Lebenslauf", "Entscheidung", "Ergebnis", "Quelle", "Notizen", "CV_Output", "Anschreiben_Output"])
    sheet.append(["2026-06-28", "Lidl", "Praktikum", "Praktikum", "DE", "8/10", "21", "Beworben", "Abgelehnt (Formschreiben)", "E-Mail", "alt", None, None])
    sheet.append(["2026-11-01", "Example GmbH", "Analyst aa", "VZ", "DE", "8/10", "B06", "Paket erstellt", "Offen", "Chat 01", "von Hand", None, None])
    old.create_sheet("Legende").append(["Feld", "Zulaessige Werte und Bedeutung"])
    old.save(tmp_path / "old.xlsx")
    _package(out, "aaaaaaaaaaaa", "not_submitted")
    report = sync_archive(out, archive, import_from=tmp_path / "old.xlsx")
    rows = _rows(archive / TRACKER_NAME)
    assert report["added"] == 0 and len(rows) == 2
    assert rows[0]["Unternehmen"] == "Lidl" and rows[0]["Lebenslauf"] == "21" and rows[0]["ID"] is None
    assert (rows[1]["ID"], rows[1]["Typ"], rows[1]["Quelle"], rows[1]["Notizen"]) == ("aaaaaaaaaaaa", "VZ", "Chat 01", "von Hand")
    assert rows[1]["Anschreiben_Output"].startswith("Bewerbung als")
    legend = [row[0].value for row in load_workbook(archive / TRACKER_NAME)["Legende"].iter_rows()]
    assert "ID" in legend and "Ordner" in legend


def test_open_workbook_is_reported_as_busy(tmp_path, monkeypatch):
    out = tmp_path / "out" / "applications"
    _package(out, "aaaaaaaaaaaa", "not_submitted")

    def locked(self, filename):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Workbook, "save", locked)
    with pytest.raises(ArchiveBusy, match="open in Excel"):
        sync_archive(out, tmp_path / "Bewerbungen")


def test_tracker_backfills_and_cleans_only_package_rows_on_both_sheets_and_makes_backup(tmp_path):
    out, archive = tmp_path / "out" / "applications", tmp_path / "Bewerbungen"
    _package(out, "aaaaaaaaaaaa", "not_submitted")
    sync_archive(out, archive)
    path = archive / TRACKER_NAME
    workbook = load_workbook(path)
    main = workbook["Bewerbungen"]
    main_headers = [cell.value for cell in main[1]]
    letter_column = main_headers.index("Anschreiben_Output") + 1
    main.cell(2, letter_column, "Example GmbH\nUlm\n\n13.11.2026\n\n" + letter_document_text(out / "aaaaaaaaaaaa_example" / "letter.docx"))
    other = workbook["Nicht beworben"]
    other_headers = [cell.value for cell in other[1]]
    other.append([None] * len(other_headers))
    other.cell(2, other_headers.index("Unternehmen") + 1, "Example GmbH")
    other.cell(2, other_headers.index("Position") + 1, "Analyst aa")
    other.append([None] * len(other_headers))
    other.cell(3, other_headers.index("Unternehmen") + 1, "Manual GmbH")
    other.cell(3, other_headers.index("Position") + 1, "Manual row")
    other.cell(3, other_headers.index("Anschreiben_Output") + 1, "Do not change")
    workbook.save(path)
    workbook.close()
    del workbook
    gc.collect()

    report = sync_archive(out, archive)
    assert report["letter_backfill"] == {"filled": 1, "cleaned": 1, "left_empty": 0, "reasons": {}}
    reloaded = load_workbook(path)
    assert reloaded["Bewerbungen"].cell(2, letter_column).value.startswith("Bewerbung als")
    other = reloaded["Nicht beworben"]
    other_headers = [cell.value for cell in other[1]]
    assert other.cell(2, other_headers.index("Anschreiben_Output") + 1).value.startswith("Bewerbung als")
    assert other.cell(3, other_headers.index("Anschreiben_Output") + 1).value == "Do not change"
    assert len(list(archive.glob("Bewerbungs_Tracker_backup_*.xlsx"))) == 1


def test_archive_command_is_registered():
    args = build_parser().parse_args(["archive", "--import-tracker", "old.xlsx"])
    assert (args.command, args.import_tracker) == ("archive", Path("old.xlsx"))
