from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from jobfilter.__main__ import build_parser
from jobfilter.autofill import (
    PROFILE_KEYS,
    AutofillError,
    application_source,
    export_autofill,
    init_profile,
    letter_text,
    load_profile,
    repair_mojibake,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')


def _profile(path: Path, **values: str) -> Path:
    data = {"first_name": "Max", "last_name": "Muster", "email": "max@example.org", "city": "Musterstadt"} | values
    path.write_text("\n".join(f'{key}: "{value}"' for key, value in data.items()), encoding="utf-8")
    return path


def _package(root: Path, application_id: str, status: str = "not_submitted", **extra: object) -> Path:
    folder = root / f"{application_id}_example-gmbh_analyst"
    (folder / "Attachements").mkdir(parents=True)
    _docx(folder / "Lebenslauf_Muster_Max_06.docx", ["Max Muster", "Berufserfahrung", "Analyst"])
    _docx(folder / "Anschreiben_Max_Muster_example-gmbh.docx", [
        "Max Muster", "13.9.1829", "Bewerbung als Analyst", "", "Sehr geehrte Damen und Herren,", "",
        "erster Absatz.", "", "Mit freundlichen Grüßen", "", "Max Muster", "Telefon: 1 | max@example.org",
    ])
    (folder / "Attachements" / "Zeugnis.pdf").write_bytes(b"%PDF zeugnis")
    (folder / "letter_brief.json").write_text(json.dumps({"ad_text": "PrÃ¤sentation der Analysen"}), encoding="utf-8")
    manifest = {
        "manifest_version": 2, "application_id": application_id, "title": "Analyst (m/w/d)", "employer": "Example GmbH",
        "location": "Ulm", "queue_rank": 3, "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/1",
        "external_url": "https://jobs.example.com/apply/1", "application_url": "https://jobs.example.com/apply/1",
        "selected_cv": "Lebenslauf_Muster_Max_06.docx", "cover_letter": "Anschreiben_Max_Muster_example-gmbh.docx",
        "attachments": ["Zeugnis.pdf", "Fehlt.pdf"], "submission_status": status,
    } | extra
    (folder / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _fake_converter(pairs):
    for source, target in pairs:
        target.write_bytes(b"%PDF from " + source.name.encode())
    return "fake"


def _extension(tmp_path: Path) -> Path:
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text("{}", encoding="utf-8")
    return extension


def test_profile_template_loads_only_when_required_fields_are_set(tmp_path):
    path = tmp_path / "bewerberprofil.yaml"
    assert init_profile(path) is True
    assert init_profile(path) is False  # never overwrites
    with pytest.raises(AutofillError, match="first_name, last_name, email"):
        load_profile(path)
    profile = load_profile(_profile(tmp_path / "p.yaml", salary=""))
    assert tuple(profile) == PROFILE_KEYS
    assert profile["first_name"] == "Max" and profile["salary"] == "" and profile["country"] == ""
    assert profile["highest_education"] == "" and profile["current_employment"] == ""
    (tmp_path / "bad.yaml").write_text('first_name: "A"\nlast_name: "B"\nemail: "c@d.e"\nshoe_size: "44"\n', encoding="utf-8")
    with pytest.raises(AutofillError, match="shoe_size"):
        load_profile(tmp_path / "bad.yaml")
    with pytest.raises(AutofillError, match="not found"):
        load_profile(tmp_path / "missing.yaml")


def test_background_top_up_can_skip_the_slow_workbook_backfill(tmp_path, monkeypatch):
    out = tmp_path / "out"
    _package(out, "aaaaaaaaaaaa")
    called = []
    monkeypatch.setattr("jobfilter.autofill.backfill_full_descriptions", lambda *args: called.append(args) or {})
    export_autofill(
        out, _profile(tmp_path / "p.yaml"), _extension(tmp_path), converter=_fake_converter,
        backfill_descriptions=False,
    )
    assert called == []


def test_letter_text_runs_from_salutation_to_signature(tmp_path):
    folder = _package(tmp_path, "aaaaaaaaaaaa")
    text = letter_text(folder / "Anschreiben_Max_Muster_example-gmbh.docx")
    assert text == "Sehr geehrte Damen und Herren,\n\nerster Absatz.\n\nMit freundlichen Grüßen\n\nMax Muster"


def test_mojibake_is_repaired_and_clean_text_is_untouched():
    broken = "Präsentation für Größe".encode("utf-8").decode("latin-1")
    assert broken != "Präsentation für Größe"
    assert repair_mojibake(broken) == "Präsentation für Größe"
    assert repair_mojibake("Größe, Café, 20 – 30 %") == "Größe, Café, 20 – 30 %"


@pytest.mark.parametrize(("url", "expected"), [
    ("https://www.arbeitsagentur.de/jobsuche/jobdetail/1", "Bundesagentur für Arbeit"),
    ("https://www.xing.com/jobs/1", "XING"),
    ("https://linkedin.com/jobs/view/1", "LinkedIn"),
    ("https://de.indeed.com/viewjob?jk=1", "Indeed"),
    ("https://www.stepstone.de/stellenangebote/1", "StepStone"),
    ("https://jobs.example.org/apply", "Karriereseite des Unternehmens"),
])
def test_application_source_is_derived_from_the_package_url(url, expected):
    assert application_source([url]) == expected


def test_export_writes_pdfs_and_extension_data_for_open_packages_only(tmp_path):
    out = tmp_path / "applications"
    ready = _package(out, "aaaaaaaaaaaa")
    _package(out, "bbbbbbbbbbbb", status="skipped")
    _package(out, "cccccccccccc", status="submitted")
    _package(out, "dddddddddddd", status="needs_hook")
    _package(out, "eeeeeeeeeeee", manifest_version=1, submission_mode="manual_or_authorized_integration")
    extension = _extension(tmp_path)
    stale = extension / "data" / "files" / "old" / "x.pdf"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")

    report = export_autofill(out, _profile(tmp_path / "p.yaml"), extension, converter=_fake_converter)

    assert report["packages"] == ["aaaaaaaaaaaa", "dddddddddddd"] and report["converted"] == 3 and report["engine"] == "fake"
    assert (ready / "Lebenslauf_Max_Muster.pdf").read_bytes().startswith(b"%PDF from Lebenslauf")
    assert (ready / "Anschreiben_Max_Muster.pdf").exists()
    assert not stale.exists()  # the data folder never keeps files of packages that left the queue
    data = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))
    assert data["profile"]["email"] == "max@example.org"
    package, pending = data["packages"]
    assert package["hosts"] == ["jobs.example.com", "arbeitsagentur.de"]
    assert package["cv"]["name"] == "Lebenslauf_Max_Muster.pdf"
    assert package["cv"]["size"] == (ready / "Lebenslauf_Max_Muster.pdf").stat().st_size
    assert (extension / package["cv"]["path"]).exists() and (extension / package["letter"]["path"]).exists()
    assert [item["name"] for item in package["attachments"]] == ["Zeugnis.pdf"]  # missing files are left out
    assert package["letter_text"].startswith("Sehr geehrte") and "Analyst" in package["cv_text"]
    assert package["job_excerpt"] == "Präsentation der Analysen"
    assert package["job_description"] == "Präsentation der Analysen" and package["job_description_source"] == "excerpt"
    assert package["source"] == "Bundesagentur für Arbeit"
    assert pending["letter_pending"] is True and "letter" not in pending and pending["cv"]["name"] == "Lebenslauf_Max_Muster.pdf"

    # Second run: PDFs are current, nothing is converted again unless asked.
    again = export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter)
    assert again["converted"] == 0
    forced = export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter, rebuild_pdf=True)
    assert forced["converted"] == 3
    (ready / "Attachements" / "Abschluss.pdf").write_bytes(b"%PDF abschluss")
    manifest = json.loads((ready / "application.json").read_text(encoding="utf-8"))
    manifest["attachments"] = ["Abschluss.pdf", "Zeugnis.pdf"]
    (ready / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter, attachment_order=["Zeugnis.pdf", "Abschluss.pdf"])
    ordered = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"][0]["attachments"]
    assert [item["name"] for item in ordered] == ["Zeugnis.pdf", "Abschluss.pdf"]
    named = export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter, only_ids=["cccccccccccc"])
    assert named["packages"] == []


def test_export_without_pdf_leaves_unconverted_packages_out_and_needs_the_extension(tmp_path):
    out = tmp_path / "applications"
    _package(out, "aaaaaaaaaaaa")
    extension = _extension(tmp_path)
    report = export_autofill(out, _profile(tmp_path / "p.yaml"), extension, make_pdf=False)
    assert report["packages"] == [] and report["missing_pdf"] == ["aaaaaaaaaaaa"]
    with pytest.raises(AutofillError, match="Extension folder not found"):
        export_autofill(out, tmp_path / "p.yaml", tmp_path / "nowhere", make_pdf=False)


def test_export_builds_combined_pdfs_only_for_the_current_package(tmp_path):
    from pypdf import PdfReader, PdfWriter

    def valid(path: Path, pages: int = 1) -> None:
        writer = PdfWriter()
        for _ in range(pages): writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream: writer.write(stream)

    out = tmp_path / "applications"
    folder = _package(out, "aaaaaaaaaaaa")
    _package(out, "bbbbbbbbbbbb")
    valid(folder / "Attachements" / "Zeugnis.pdf")
    def converter(pairs):
        for source, target in pairs: valid(target)
        return "fake"
    extension = _extension(tmp_path)
    export_autofill(out, _profile(tmp_path / "p.yaml"), extension, converter=converter)
    packages = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"]
    assert all(package["combined"] is None and package["letter_cv_combined"] is None for package in packages)

    export_autofill(
        out, tmp_path / "p.yaml", extension, converter=converter, current_id="aaaaaaaaaaaa",
    )
    packages = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"]
    package = next(item for item in packages if item["id"] == "aaaaaaaaaaaa")
    follower = next(item for item in packages if item["id"] == "bbbbbbbbbbbb")
    assert package["combined"]["name"].startswith("Bewerbungsunterlagen_")
    assert len(PdfReader(extension / package["combined"]["path"]).pages) == 3  # letter, CV, certificate
    assert package["letter_cv_combined"]["name"].startswith("Anschreiben_und_Lebenslauf_")
    assert len(PdfReader(extension / package["letter_cv_combined"]["path"]).pages) == 2
    assert package["attachments_combined"] == package["attachments"][0]
    assert len(PdfReader(extension / package["attachments_combined"]["path"]).pages) == 1
    assert follower["combined"] is None and follower["letter_cv_combined"] is None


def test_residence_document_is_separate_from_general_and_combined_attachments(tmp_path):
    from pypdf import PdfReader, PdfWriter

    def valid(path: Path, pages: int = 1) -> None:
        writer = PdfWriter()
        for _ in range(pages): writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream: writer.write(stream)

    out = tmp_path / "applications"
    _package(out, "aaaaaaaaaaaa")
    shared = tmp_path / "Job App Docs" / "Attachements"
    shared.mkdir(parents=True)
    general = shared / "Anhang_Max_Muster.pdf"
    restricted = shared / "Passport, Visum, Aufenthaltstitel.pdf"
    valid(general)
    valid(restricted, pages=2)
    def converter(pairs):
        for source, target in pairs: valid(target)
        return "fake"
    extension = _extension(tmp_path)
    export_autofill(
        out, _profile(tmp_path / "p.yaml"), extension, converter=converter,
        attachment_names=[general.name], attachment_order=[general.name],
        residence_attachment_names=[restricted.name], attachments_dir=shared,
        current_id="aaaaaaaaaaaa",
    )
    package = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"][0]
    assert [item["name"] for item in package["attachments"]] == [general.name]
    assert [item["name"] for item in package["residence_documents"]] == [restricted.name]
    assert len(PdfReader(extension / package["combined"]["path"]).pages) == 3  # letter, CV, general bundle
    assert package["attachments_combined"] == package["attachments"][0]
    assert len(PdfReader(extension / package["attachments_combined"]["path"]).pages) == 1
    assert (extension / package["residence_documents"][0]["path"]).is_file()


def test_export_autofill_parser_options():
    args = build_parser().parse_args(["export-autofill", "--only", "a,b", "--no-pdf", "--pdf-engine", "word"])
    assert (args.command, args.only, args.no_pdf, args.rebuild_pdf, args.pdf_engine, args.init_profile) == (
        "export-autofill", "a,b", True, False, "word", False,
    )


def test_export_updates_files_in_place_and_survives_locked_files(tmp_path, monkeypatch):
    import jobfilter.autofill as autofill

    out = tmp_path / "applications"
    ready = _package(out, "aaaaaaaaaaaa")
    extension = _extension(tmp_path)
    export_autofill(out, _profile(tmp_path / "p.yaml"), extension, converter=_fake_converter)

    copies, sleeps = [], []
    real_copy = autofill.shutil.copy2
    monkeypatch.setattr(autofill.time, "sleep", sleeps.append)

    def counting_copy(source, target):
        copies.append(Path(target).name)
        return real_copy(source, target)

    monkeypatch.setattr(autofill.shutil, "copy2", counting_copy)
    export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter)
    assert copies == []  # unchanged files are not touched again

    # A rebuilt PDF is copied again; a file held by another program for a moment is retried.
    (ready / "Lebenslauf_Max_Muster.pdf").write_bytes(b"%PDF new version")
    held = {"times": 1}

    def held_once(source, target):
        if held["times"]:
            held["times"] -= 1
            raise PermissionError(5, "Access is denied")
        return counting_copy(source, target)

    monkeypatch.setattr(autofill.shutil, "copy2", held_once)
    export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter)
    published = extension / "data" / "files" / "aaaaaaaaaaaa" / "Lebenslauf_Max_Muster.pdf"
    assert published.read_bytes() == b"%PDF new version" and copies == ["Lebenslauf_Max_Muster.pdf"] and len(sleeps) == 1

    # A stale folder that Windows will not let go of no longer stops the export (the old rmtree did).
    stale = extension / "data" / "files" / "gone" / "x.pdf"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    real_rmdir = Path.rmdir

    def locked_rmdir(self):
        if self.name == "gone":
            raise PermissionError(5, "Access is denied")
        return real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", locked_rmdir)
    report = export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter)
    assert report["packages"] == ["aaaaaaaaaaaa"] and report["stale_left"] == [str(stale.parent)]
    assert not stale.exists() and json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"]


def test_certificates_come_from_the_one_original_folder(tmp_path):
    out = tmp_path / "applications"
    ready = _package(out, "aaaaaaaaaaaa")
    shared = tmp_path / "Job App Docs" / "Attachements"
    shared.mkdir(parents=True)
    (shared / "Zeugnis.pdf").write_bytes(b"%PDF original")
    extension = _extension(tmp_path)
    export_autofill(out, _profile(tmp_path / "p.yaml"), extension, converter=_fake_converter, attachments_dir=shared)
    package = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"][0]
    (attachment,) = package["attachments"]
    assert attachment["disk_path"] == str(shared / "Zeugnis.pdf")
    assert (extension / attachment["path"]).read_bytes() == b"%PDF original"
    # A package of an older version keeps working from its own copy when the original is gone.
    (shared / "Zeugnis.pdf").unlink()
    export_autofill(out, tmp_path / "p.yaml", extension, converter=_fake_converter, attachments_dir=shared)
    package = json.loads((extension / "data" / "data.json").read_text(encoding="utf-8"))["packages"][0]
    assert package["attachments"][0]["disk_path"] == str(ready / "Attachements" / "Zeugnis.pdf")
