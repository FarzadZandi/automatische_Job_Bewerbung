import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from lxml import etree
from openpyxl import Workbook

from jobfilter.applications import (
    APPLICATION_SHEET_ORDER,
    FULL_TIME_VALUE,
    MISSING_EVIDENCE,
    ApplicationJob,
    _queue_rows,
    _date_value,
    _lowercase_first,
    backfill_full_descriptions,
    brief_excerpt,
    expire_applications,
    format_skip_summary,
    full_ad_text,
    is_legacy_manifest,
    load_application_jobs,
    next_application,
    open_application_handoff,
    prepare_application_packages,
    set_application_status,
)


ROOT = Path(__file__).parents[1]


def _configured_real_template() -> Path:
    from jobfilter.autofill import load_profile
    from jobfilter.config import Config

    config = Config.load(ROOT / "config.yaml", validate=False)
    stem = config.applicant_file_stem.strip()
    if not stem and config.applicant_profile_path.is_file():
        profile = load_profile(config.applicant_profile_path)
        stem = f"{profile.get('first_name', '')}_{profile.get('last_name', '')}".strip("_")
    return config.job_documents_dir / f"Anschreiben_{stem.replace(' ', '_')}.docx"


HEADERS = [
    "score", "band", "employment_format", "primary_cv_family", "secondary_cv_family",
    "title", "employer", "location", "posted", "deadline", "board", "url", "external_url",
    "family_hits", "analysis_task_hits", "method_hits", "tool_hits", "language_flags", "full_text",
]


def _row(sheet, index, *, employment=FULL_TIME_VALUE, score=None, posted="2026-11-12", deadline=""):
    return [
        20 - index if score is None else score,
        "TOP" if sheet == "INDUSTRY-PhD" else sheet.upper(),
        employment, "B02 Strategy und Business Development", "",
        f"Job {sheet} {index}", "Example GmbH", "Berlin", posted, deadline, "test",
        f"https://example.com/{sheet}/{index}", f"https://careers.example.com/{sheet}/{index}",
        "strategy", "KPI reporting", "forecasting", "Power BI", "",
        "Full-time strategy analytics and business development.",
    ]


def _workbook(path):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for index, sheet_name in enumerate(APPLICATION_SHEET_ORDER):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(HEADERS)
        sheet.append(_row(sheet_name, index))
        if sheet_name == "PRIORITY":
            sheet.append(_row(sheet_name, 99, employment="WERKSTUDENT / WORKING STUDENT"))
    workbook.create_sheet("Academische").append(HEADERS)
    workbook.create_sheet("DROPPED").append(HEADERS)
    workbook.save(path)


def _template(path):
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr><w:tc>
        <w:p><w:r><w:t>Max Muster</w:t></w:r></w:p>
        <w:p><w:r><w:t>01.01.2020</w:t></w:r></w:p>
      </w:tc></w:tr>
      <w:tr><w:tc>
        <w:p><w:r><w:t>Staiger GmbH</w:t></w:r></w:p>
        <w:p><w:r><w:t>Old address</w:t></w:r></w:p>
        <w:p><w:r><w:t>Stale line</w:t></w:r></w:p>
      </w:tc></w:tr>
    </w:tbl>
    <w:p><w:r><w:t>Bewerbung als Werkstudent Business Development</w:t></w:r></w:p>
    <w:p><w:r><w:t>Sehr geehrtes Recruiting-Team,</w:t></w:r></w:p>
    <w:p><w:r><w:t>Dies ist eine ausreichend lange ursprüngliche Einleitung für das Anschreiben.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Unveränderte Berufserfahrung eins.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Unveränderte Berufserfahrung zwei.</w:t></w:r></w:p>
    <w:p><w:r><w:t>An Staiger interessiert mich besonders die Position und das Unternehmen.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Aktuell bin ich im Studium und stimme den Starttermin gerne ab.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Mit freundlichen Grüßen</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document.encode("utf-8"))


def _paragraph_bank(path, *, missing_b06=False):
    rows = []
    for index in range(1, 11):
        family = f"B{index:02d}"
        if family == "B02":
            values = ["Verifizierter Nachweis eins.", "Verifizierter Nachweis zwei."]
        elif family == "B06" and not missing_b06:
            values = ["Daten-Nachweis eins.", "Daten-Nachweis zwei."]
        else:
            values = [MISSING_EVIDENCE, MISSING_EVIDENCE]
        rows.extend([f"{family}:", f"  - {json.dumps(values[0], ensure_ascii=False)}", f"  - {json.dumps(values[1], ensure_ascii=False)}"])
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _job(**overrides):
    values = dict(
        queue_rank=1, source_sheet="PRIORITY", score=18, band="PRIORITY",
        title="Strategy Analyst", employer="Example GmbH", location="Berlin",
        posted="2026-11-12", deadline="", board="test",
        url="https://example.com/job/1", external_url="https://careers.example.com/job/1",
        primary_cv_family="B02 Strategy und Business Development", secondary_cv_family="",
        family_hits="strategy", analysis_task_hits="KPI reporting", method_hits="forecasting",
        tool_hits="Power BI", language_flags="", full_text="Full-time corporate strategy analytics.",
    )
    values.update(overrides)
    return ApplicationJob(**values)


def _documents(tmp_path):
    documents = tmp_path / "Job App Docs"
    attachments = documents / "Attachements"
    attachments.mkdir(parents=True)
    _template(documents / "Anschreiben_Max_Muster.docx")
    _paragraph_bank(documents / "anschreiben_bausteine.yaml")
    (documents / "Lebenslauf_Muster_Max_02.docx").write_bytes(b"cv")
    (documents / "Lebenslauf_Muster_Max_06.docx").write_bytes(b"cv")
    (attachments / "Certificate.pdf").write_bytes(b"pdf")
    (attachments / "immatbescheinigung.pdf").write_bytes(b"pdf")
    (attachments / "Private.pdf").write_bytes(b"private")
    return documents


def test_application_queue_uses_fresh_vollzeit_and_requested_order(tmp_path):
    path = tmp_path / "jobs_vollzeit.xlsx"
    _workbook(path)
    jobs = load_application_jobs(path, today=date(2026, 11, 13))
    assert [job.source_sheet for job in jobs] == list(APPLICATION_SHEET_ORDER)
    assert all(job.title != "Job PRIORITY 99" for job in jobs)
    assert jobs[0].external_url.startswith("https://careers.example.com/")
    assert load_application_jobs(path, limit=2, today=date(2026, 11, 13)) == jobs[:2]
    all_formats = load_application_jobs(path, today=date(2026, 11, 13), employment_format=None)
    assert len(all_formats) == len(jobs) + 1
    assert any(job.title == "Job PRIORITY 99" for job in all_formats)


def test_existing_package_loader_includes_part_time_jobs_other(tmp_path):
    from jobfilter.__main__ import _load_prepare_jobs

    primary = tmp_path / "jobs_vollzeit.xlsx"
    _workbook(primary)
    other = Workbook()
    sheet = other.active
    sheet.title = "PRIORITY"
    sheet.append(HEADERS)
    row = _row("PRIORITY", 200, employment="PART-TIME / TEILZEIT")
    row[11] = "https://example.com/manual-part-time/200"
    sheet.append(row)
    other.save(tmp_path / "jobs_other.xlsx")

    normal = _load_prepare_jobs(primary, max_age_days=21)
    rebuilding = _load_prepare_jobs(primary, max_age_days=3650, include_other_formats=True)
    assert all(job.url != row[11] for job in normal)
    assert any(job.url == row[11] for job in rebuilding)


def test_queue_sorts_score_then_deadline_and_skips_stale_rows(tmp_path):
    path = tmp_path / "jobs_vollzeit.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PRIORITY"
    sheet.append(HEADERS)
    sheet.append(_row("PRIORITY", 1, score=12, posted="2026-11-10", deadline="2026-12-01"))
    sheet.append(_row("PRIORITY", 2, score=20, posted="2026-11-10", deadline="2026-12-20"))
    sheet.append(_row("PRIORITY", 3, score=12, posted="2026-11-10", deadline="2026-11-20"))
    sheet.append(_row("PRIORITY", 4, score=99, posted="2026-10-01", deadline=""))
    sheet.append(_row("PRIORITY", 5, score=99, posted="2026-11-10", deadline="2026-11-01"))
    workbook.save(path)
    jobs = load_application_jobs(path, today=date(2026, 11, 13), max_age_days=21)
    assert [job.score for job in jobs] == [20, 12, 12]
    assert [job.deadline for job in jobs[1:]] == ["2026-11-20", "2026-12-01"]
    assert [job.queue_rank for job in jobs] == [1, 2, 3]


def test_package_waits_for_hook_then_builds_evidence_only_letter(tmp_path):
    documents = _documents(tmp_path)
    template = documents / "Anschreiben_Max_Muster.docx"
    original_hash = hashlib.sha256(template.read_bytes()).hexdigest()
    output = tmp_path / "applications"
    first = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output,
        applicant_status="enrolled", attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    package = Path(first[0]["package_dir"])
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    assert manifest["submission_status"] == "needs_hook"
    assert manifest["cover_letter"] == ""
    assert not list(package.glob("Anschreiben_*.docx"))
    brief = json.loads((package / "letter_brief.json").read_text(encoding="utf-8"))
    assert set(brief) == {
        "title", "employer", "location", "family", "applicant_status", "graduation_date",
        "external_url", "analysis_task_hits", "method_hits", "tool_hits", "ad_text_source", "ad_text",
            "ad_text_full", "ad_text_full_source", "cv_projects", "applicant_facts",
    }
    assert brief["ad_text_full"] == "Full-time corporate strategy analytics."
    assert brief["ad_text_full_source"] == "full"
    assert brief["cv_projects"] == []  # no GitHub catalog passed
    assert brief["external_url"] == "https://careers.example.com/job/1"
    assert brief["tool_hits"] == "Power BI"
    assert brief["family"] == "B02"
    assert not (package / "Attachements").exists()  # certificates are named, never copied into the package
    assert "Private.pdf" not in json.loads((package / "application.json").read_text(encoding="utf-8"))["attachments"]

    (package / "hook.json").write_text(json.dumps({
        "intro": "Die ausgeschriebene Rolle verbindet Strategie und Analyse.",
        "company_paragraph": "An Example GmbH interessiert mich die datenbasierte Strategiearbeit.",
    }, ensure_ascii=False), encoding="utf-8")
    second = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output,
        applicant_status="enrolled", attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    assert second[0]["status"] == "not_submitted"
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    cover = package / manifest["cover_letter"]
    with zipfile.ZipFile(cover) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    texts = root.xpath(".//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    text = " ".join(texts)
    assert "Bewerbung als Strategy Analyst" in text
    assert "Sehr geehrte Damen und Herren," in text
    assert "die ausgeschriebene Rolle" in text
    assert "Verifizierter Nachweis eins" in text
    assert "Example GmbH Berlin" in text
    assert "Staiger" not in text and "Old address" not in text and "Stale line" not in text
    assert date.today().strftime("%d.%m.%Y") in text
    assert manifest["cover_letter_token_usage"] == 0
    assert hashlib.sha256(template.read_bytes()).hexdigest() == original_hash

    assert next_application(output) is None
    set_application_status(output, manifest["application_id"], "approved")
    assert next_application(output)["title"] == "Strategy Analyst"


def test_missing_family_evidence_skips_job_and_continues(tmp_path):
    documents = _documents(tmp_path)
    _paragraph_bank(documents / "anschreiben_bausteine.yaml", missing_b06=True)
    skipped = []
    result = prepare_application_packages(
        [
            _job(primary_cv_family="B06 Data Analytics", url="https://example.com/job/b06"),
            _job(primary_cv_family="", url="https://example.com/job/none"),
            _job(url="https://example.com/job/b02"),
        ],
        documents_dir=documents, output_dir=tmp_path / "applications",
        applicant_status="enrolled", attachment_allowlist=[], skipped=skipped,
    )
    assert [row["url"] for row in result] == ["https://example.com/job/b02"]
    assert [(entry["family"], entry["reason"]) for entry in skipped] == [
        ("B06", "missing_evidence_paragraphs"), ("none", "no_cv_family"),
    ]
    summary = format_skip_summary(skipped)
    assert any(line.startswith("B06: 1 job(s) skipped") and "FEHLT" in line for line in summary)


def test_graduated_package_excludes_enrolment_certificate(tmp_path):
    documents = _documents(tmp_path)
    output = tmp_path / "applications"
    result = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output, applicant_status="graduated",
        graduation_date="08. November",
        attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    package = Path(result[0]["package_dir"])
    named = json.loads((package / "application.json").read_text(encoding="utf-8"))["attachments"]
    assert named == ["Certificate.pdf"] and not (package / "Attachements").exists()
    (package / "hook.json").write_text(json.dumps({
        "intro": "Die Position verbindet Strategie und Analyse.",
        "company_paragraph": "An Example GmbH interessiert mich die strategische Ausrichtung.",
    }, ensure_ascii=False), encoding="utf-8")
    prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output, applicant_status="graduated",
        graduation_date="08. November",
        attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(package / manifest["cover_letter"]) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    text = " ".join(root.xpath(
        ".//w:t/text()",
        namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
    ))
    assert "am 8. November abgeschlossen" in text
    assert "immatrikuliert" not in text


def test_graduating_status_uses_final_phase_wording_and_keeps_enrolment_certificate(tmp_path):
    documents = _documents(tmp_path)
    output = tmp_path / "applications"
    first = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    package = Path(first[0]["package_dir"])
    assert "immatbescheinigung.pdf" in json.loads((package / "application.json").read_text(encoding="utf-8"))["attachments"]
    (package / "hook.json").write_text(json.dumps({
        "intro": "Die Position verbindet Strategie und Analyse.",
        "company_paragraph": "An Example GmbH interessiert mich die strategische Ausrichtung.",
    }, ensure_ascii=False), encoding="utf-8")
    prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=["Certificate.pdf", "immatbescheinigung.pdf"],
    )
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(package / manifest["cover_letter"]) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    text = " ".join(root.xpath(
        ".//w:t/text()",
        namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"},
    ))
    assert "in der Abschlussphase meines Studiums" in text
    assert "immatrikuliert" not in text


def test_applicant_status_must_be_confirmed(tmp_path):
    documents = _documents(tmp_path)
    with pytest.raises(ValueError, match="PROFILE"):
        prepare_application_packages(
            [_job()], documents_dir=documents, output_dir=tmp_path / "applications",
            applicant_status="needs_confirmation", attachment_allowlist=[],
        )


def test_expiration_and_open_transition(tmp_path, monkeypatch):
    output = tmp_path / "applications"
    package = output / "job"
    package.mkdir(parents=True)
    manifest = {
        "manifest_version": 2, "application_id": "abc", "queue_rank": 1, "source_sheet": "PRIORITY",
        "score": 10, "title": "Fresh", "employer": "Example", "posted": "2026-11-12",
        "deadline": "2026-12-01", "url": "https://example.com",
        "external_url": "https://careers.example.com", "application_url": "https://careers.example.com",
        "submission_mode": "manual_extension_handoff",
        "selected_cv": "cv.docx", "cover_letter": "letter.docx", "submission_status": "approved",
    }
    (package / "letter.docx").write_bytes(b"docx")
    (package / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    opened = []
    monkeypatch.setattr("jobfilter.applications.webbrowser.open", lambda url, new=0: opened.append(url))
    monkeypatch.setattr("jobfilter.applications._show_manual_action_notification", lambda: opened.append("notice"))
    result = open_application_handoff(output)
    assert result["submission_status"] == "opened"
    assert opened == ["https://careers.example.com", "notice"]
    saved = json.loads((package / "application.json").read_text(encoding="utf-8"))
    assert saved["submission_status"] == "opened"

    saved.update({"submission_status": "approved", "posted": "2026-10-01", "deadline": ""})
    (package / "application.json").write_text(json.dumps(saved), encoding="utf-8")
    assert expire_applications(output, today=date(2026, 11, 13), max_age_days=21) == ["abc"]
    assert next_application(output) is None


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_real_generated_letter_renders_to_one_page_with_libreoffice(tmp_path):
    from jobfilter.config import Config

    config = Config.load(ROOT / "config.yaml", validate=False)
    documents = config.job_documents_dir
    template = _configured_real_template()
    if not template.is_file():
        pytest.skip("configured personal template is not available")
    output = tmp_path / "rendered_letter.docx"
    from jobfilter.applications import load_paragraph_bank, tailor_cover_letter

    proof = load_paragraph_bank(documents / "anschreiben_bausteine.yaml")["B02"]
    tailor_cover_letter(
        template, output, _job(),
        proof_paragraphs=proof,
        hook={
            "intro": "Die Position verbindet Marktanalyse und Strategie.",
            "company_paragraph": "An Example GmbH interessiert mich die strategische Ausrichtung.",
        },
        applicant_status="enrolled",
        generation_date=date(2026, 11, 13),
    )
    environment = os.environ | {"TEMP": str(tmp_path), "TMP": str(tmp_path)}
    completed = subprocess.run(
        [shutil.which("soffice"), "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(output)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    pdf = output.with_suffix(".pdf")
    assert pdf.exists()
    assert len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes())) == 1


@pytest.mark.parametrize(("value", "expected"), [
    ("18. Oktober 2026", date(2026, 10, 18)),
    ("18. Okt. 2026", date(2026, 10, 18)),
    ("3. März 2027", date(2027, 3, 3)),
    ("bis zum 18. Oktober 2026", date(2026, 10, 18)),
    ("18.10.2026", date(2026, 10, 18)),
    ("8.11.26", date(2026, 11, 8)),
    ("18/10/2026", date(2026, 10, 18)),
    ("2026-10-18", date(2026, 10, 18)),
    ("2026-10-18T08:00:00Z", date(2026, 10, 18)),
    ("", None),
    ("sofort", None),
])
def test_german_and_numeric_dates_are_parsed(value, expected):
    assert _date_value(value) == expected


def test_german_month_deadline_expires_and_sorts(tmp_path):
    path = tmp_path / "jobs_vollzeit.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PRIORITY"
    sheet.append(HEADERS)
    sheet.append(_row("PRIORITY", 1, score=15, posted="2026-11-10", deadline="18. Oktober 2026"))
    sheet.append(_row("PRIORITY", 2, score=15, posted="2026-11-10", deadline="30. November 2026"))
    sheet.append(_row("PRIORITY", 3, score=15, posted="2026-11-10", deadline="20. November 2026"))
    workbook.save(path)
    jobs = load_application_jobs(path, today=date(2026, 11, 13))
    assert [job.deadline for job in jobs] == ["20. November 2026", "30. November 2026"]

    output = tmp_path / "applications"
    package = output / "old"
    package.mkdir(parents=True)
    (package / "application.json").write_text(json.dumps({
        "application_id": "old", "submission_status": "not_submitted",
        "posted": "2026-11-10", "deadline": "18. Oktober 2026",
    }), encoding="utf-8")
    assert expire_applications(output, today=date(2026, 11, 13)) == ["old"]


@pytest.mark.parametrize(("value", "expected"), [
    ("Ihre Ausschreibung verbindet Strategie und Analyse.", "Ihre Ausschreibung verbindet Strategie und Analyse."),
    ("Ihnen biete ich fünf Jahre Erfahrung.", "Ihnen biete ich fünf Jahre Erfahrung."),
    ("Mit großem Interesse habe ich gelesen.", "mit großem Interesse habe ich gelesen."),
    ("Die Position verbindet Analyse und Strategie.", "die Position verbindet Analyse und Strategie."),
    ("Ich bewerbe mich als Analyst.", "ich bewerbe mich als Analyst."),
    ("Datenanalyse ist mein Schwerpunkt.", "Datenanalyse ist mein Schwerpunkt."),
    ("Marktanalyse und Strategie verbinden sich hier.", "Marktanalyse und Strategie verbinden sich hier."),
])
def test_lowercase_first_only_changes_function_words(value, expected):
    assert _lowercase_first(value) == expected


def _status_package(output, *, status="needs_hook", cover_letter="", legacy=False, name="pkg"):
    package = output / name
    package.mkdir(parents=True, exist_ok=True)
    manifest = {
        "application_id": name, "queue_rank": 1, "source_sheet": "PRIORITY", "score": 10,
        "title": "Analyst", "employer": "Example", "posted": "2026-11-12", "deadline": "",
        "url": "https://example.com/1", "selected_cv": "cv.docx",
        "cover_letter": cover_letter, "submission_status": status,
    }
    if legacy:
        manifest["submission_mode"] = "manual_or_authorized_integration"
    else:
        manifest.update({
            "manifest_version": 2, "application_url": "https://example.com/1",
            "submission_mode": "manual_extension_handoff",
        })
    (package / "application.json").write_text(json.dumps(manifest), encoding="utf-8")
    if cover_letter:
        (package / cover_letter).write_bytes(b"docx")
    return package


def test_status_transitions_are_enforced(tmp_path):
    output = tmp_path / "applications"
    _status_package(output, name="hook")
    with pytest.raises(ValueError, match="needs_hook to approved"):
        set_application_status(output, "hook", "approved")

    package = _status_package(output, name="ready", status="not_submitted", cover_letter="letter.docx")
    (package / "letter.docx").unlink()
    with pytest.raises(ValueError, match="cover letter file is missing"):
        set_application_status(output, "ready", "approved")
    (package / "letter.docx").write_bytes(b"docx")
    assert set_application_status(output, "ready", "approved")["submission_status"] == "approved"
    assert set_application_status(output, "ready", "submitted")["submission_status"] == "submitted"
    corrected = set_application_status(output, "ready", "not_submitted")
    assert corrected["submission_status"] == "not_submitted"
    assert corrected["submission_correction_from"] == "submitted"
    assert "submission_correction_previous_updated_at" in corrected
    with pytest.raises(ValueError, match="set automatically"):
        set_application_status(output, "ready", "needs_hook")
    assert set_application_status(output, "ready", "expired")["submission_status"] == "expired"
    with pytest.raises(ValueError, match="from expired to approved"):
        set_application_status(output, "ready", "approved")
    assert set_application_status(output, "hook", "skipped")["submission_status"] == "skipped"


def test_legacy_packages_can_only_be_expired_or_skipped(tmp_path):
    output = tmp_path / "applications"
    _status_package(output, name="legacy", status="not_submitted", cover_letter="letter.docx", legacy=True)
    manifest = json.loads((output / "legacy" / "application.json").read_text(encoding="utf-8"))
    assert is_legacy_manifest(manifest)
    with pytest.raises(ValueError, match="old letter builder"):
        set_application_status(output, "legacy", "approved")
    assert set_application_status(output, "legacy", "expired")["submission_status"] == "expired"
    assert _queue_rows(output)[0]["legacy"] == "yes"


def test_force_never_rebuilds_opened_or_submitted_packages(tmp_path):
    documents = _documents(tmp_path)
    output = tmp_path / "applications"
    first = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output,
        applicant_status="enrolled", attachment_allowlist=[],
    )
    manifest_path = Path(first[0]["package_dir"]) / "application.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["submission_status"] = "submitted"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    again = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output,
        applicant_status="enrolled", attachment_allowlist=[], force=True,
    )
    assert again == []
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["submission_status"] == "submitted"


def test_brief_excerpt_keeps_tasks_and_profile_and_drops_boilerplate():
    text = (
        "Wir sind ein traditionsreiches Familienunternehmen mit 5.000 Mitarbeitenden. " * 20
        + "Ihre Aufgaben: Aufbau von KPI-Dashboards in Power BI und Analyse von Absatzdaten. "
        + "Ihr Profil: Abgeschlossenes Studium, SQL und Python. "
        + "Wir bieten: Obstkorb, Jobrad und flexible Arbeitszeiten."
    )
    excerpt, source = brief_excerpt(text)
    assert source == "sections"
    assert excerpt.startswith("Ihre Aufgaben")
    assert "Ihr Profil" in excerpt and "SQL und Python" in excerpt
    assert "Familienunternehmen" not in excerpt
    assert "Obstkorb" not in excerpt

    plain, plain_source = brief_excerpt("Kurzer Text ohne Überschriften. " * 200)
    assert plain_source == "start"
    assert len(plain) <= 2500

    english, _ = brief_excerpt("About us. " * 50 + "Your responsibilities include forecasting. Requirements: Python. What we offer: lunch.")
    assert english.startswith("Your responsibilities") and "lunch" not in english


def test_full_ad_text_repairs_whitespace_mojibake_and_caps_length():
    broken = "Präsentation für Teams".encode("utf-8").decode("latin-1")
    assert full_ad_text(f"  {broken}\n\n  Datenanalyse  ") == "Präsentation für Teams Datenanalyse"
    assert len(full_ad_text("x" * 20000)) == 15000


def test_full_description_backfill_uses_workbook_then_marks_excerpt(tmp_path):
    output = tmp_path / "applications"
    full, excerpt = output / "aaaaaaaaaaaa_example", output / "bbbbbbbbbbbb_example"
    for application_id, folder, url in (
        ("aaaaaaaaaaaa", full, "https://jobs.example/one"),
        ("bbbbbbbbbbbb", excerpt, "https://jobs.example/two"),
    ):
        folder.mkdir(parents=True)
        (folder / "application.json").write_text(json.dumps({
            "manifest_version": 2, "application_id": application_id, "title": "Analyst",
            "employer": "Example", "application_url": url, "submission_status": "not_submitted",
        }), encoding="utf-8")
        (folder / "letter_brief.json").write_text(json.dumps({"ad_text": "Kurzer Auszug"}), encoding="utf-8")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["url", "title", "employer", "full_text"])
    sheet.append(["https://jobs.example/one", "", "", "Vollständige Anzeige mit Aufgaben und Profil"])
    workbook_path = tmp_path / "jobs.xlsx"
    workbook.save(workbook_path)

    report = backfill_full_descriptions(output, workbook_path)
    assert report == {"full": 1, "excerpt": 1, "unchanged": 0}
    full_brief = json.loads((full / "letter_brief.json").read_text(encoding="utf-8"))
    excerpt_brief = json.loads((excerpt / "letter_brief.json").read_text(encoding="utf-8"))
    assert full_brief["ad_text_full"] == "Vollständige Anzeige mit Aufgaben und Profil"
    assert full_brief["ad_text_full_source"] == "full"
    assert excerpt_brief == {
        "ad_text": "Kurzer Auszug", "ad_text_full": "Kurzer Auszug", "ad_text_full_source": "excerpt",
    }


REAL_TEMPLATE = _configured_real_template()


@pytest.mark.skipif(not REAL_TEMPLATE.exists(), reason="personal template is not available")
def test_real_template_date_and_recipient_are_replaced_exactly_once(tmp_path):
    from jobfilter.applications import tailor_cover_letter

    output = tmp_path / "letter.docx"
    tailor_cover_letter(
        REAL_TEMPLATE, output, _job(employer="Beispiel GmbH & Co. KG", location="Stuttgart"),
        proof_paragraphs=("Nachweis eins für den Test.", "Nachweis zwei für den Test."),
        hook={
            "intro": "Ihre Ausschreibung verbindet Marktanalyse und Strategie.",
            "company_paragraph": "An der Beispiel GmbH interessiert mich die Ausrichtung.",
        },
        applicant_status="graduating",
        generation_date=date(2026, 11, 13),
    )
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    text = " ".join(root.xpath(".//w:t/text()", namespaces=namespaces))
    template_date = "12.09.2026"
    assert template_date not in text
    assert text.count("13.9.1829") == 1
    for stale in ("Staiger", "Erligheim", "Johannes-Bieg"):
        assert stale not in text
    assert "Ihre Ausschreibung verbindet" in text
    date_nodes = root.xpath(".//w:date/@w:fullDate", namespaces=namespaces)
    assert all(value.startswith("2026-11-13") for value in date_nodes)
    cell = root.xpath("./w:body/w:tbl[1]/w:tr[2]/w:tc[1]", namespaces=namespaces)[0]
    paragraphs = cell.xpath("./w:p", namespaces=namespaces)
    assert ["".join(p.xpath(".//w:t/text()", namespaces=namespaces)) for p in paragraphs] == [
        "Beispiel GmbH & Co. KG", "Stuttgart",
    ]
    assert paragraphs[0].xpath(".//w:r/w:rPr/w:b", namespaces=namespaces)
    assert not paragraphs[1].xpath(".//w:r/w:rPr/w:b", namespaces=namespaces)


def _hook(package, intro):
    (package / "hook.json").write_text(json.dumps({
        "intro": intro,
        "company_paragraph": "An Example GmbH interessiert mich die datenbasierte Strategiearbeit.",
    }, ensure_ascii=False), encoding="utf-8")


def _letter_text(package):
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(package / manifest["cover_letter"]) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return " ".join(root.xpath(".//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))


def test_existing_only_renders_hooked_packages_and_creates_none(tmp_path):
    documents = _documents(tmp_path)
    output = tmp_path / "applications"
    jobs = [_job(url=f"https://example.com/job/{n}", title=f"Strategy Analyst {n}") for n in (1, 2, 3)]
    first = prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output,
        applicant_status="graduating", attachment_allowlist=[], limit=1,
    )
    package = Path(first[0]["package_dir"])
    _hook(package, "Die ausgeschriebene Rolle verbindet Strategie und Analyse.")
    rendered = prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output,
        applicant_status="graduating", attachment_allowlist=[], existing_only=True,
    )
    assert [entry["application_id"] for entry in rendered] == [first[0]["application_id"]]
    assert rendered[0]["status"] == "not_submitted"
    assert len(list(output.glob("*/application.json"))) == 1  # no package for jobs 2 and 3


def test_only_rebuilds_named_packages_and_reports_the_rest(tmp_path):
    documents = _documents(tmp_path)
    output = tmp_path / "applications"
    jobs = [_job(url=f"https://example.com/job/{n}", title=f"Strategy Analyst {n}") for n in (1, 2)]
    built = prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output,
        applicant_status="graduating", attachment_allowlist=[],
    )
    first, second = (Path(entry["package_dir"]) for entry in built)
    first_id, second_id = (entry["application_id"] for entry in built)
    _hook(first, "Die ausgeschriebene Rolle verbindet Strategie und Analyse.")
    _hook(second, "Die zweite Rolle verbindet Strategie und Analyse.")
    prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output,
        applicant_status="graduating", attachment_allowlist=[], existing_only=True,
    )
    second_letter = second / json.loads((second / "application.json").read_text(encoding="utf-8"))["cover_letter"]
    second_bytes = second_letter.read_bytes()

    _hook(first, "Eine neue Einleitung für die erste Rolle.")
    skipped = []
    assert prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=[], only_ids=[first_id], skipped=skipped,
    ) == []
    assert [(entry["application_id"], entry["reason"]) for entry in skipped] == [(first_id, "needs_force")]

    rebuilt = prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=[], only_ids=[first_id.upper(), "deadbeef0000"], force=True, skipped=(skipped := []),
    )
    assert [entry["application_id"] for entry in rebuilt] == [first_id]
    assert "eine neue Einleitung" in _letter_text(first)
    assert second_letter.read_bytes() == second_bytes  # the other package is untouched
    assert [(entry["application_id"], entry["reason"]) for entry in skipped] == [("deadbeef0000", "not_in_workbook")]

    set_application_status(output, second_id, "skipped")
    forced = prepare_application_packages(
        jobs, documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=[], existing_only=True, force=True,
    )
    assert [entry["application_id"] for entry in forced] == [first_id]
    statuses = {
        json.loads(path.read_text(encoding="utf-8"))["application_id"]: json.loads(path.read_text(encoding="utf-8"))["submission_status"]
        for path in output.glob("*/application.json")
    }
    assert statuses[second_id] == "skipped"  # retired packages are not brought back by --force


def test_prepare_parser_accepts_only_and_existing_only():
    from jobfilter.__main__ import build_parser

    args = build_parser().parse_args(["prepare-applications", "--only", "abc123,def456", "--existing-only"])
    assert args.only == "abc123,def456" and args.existing_only
    manual = build_parser().parse_args(["prepare-applications", "--job-url", "https://jobs.example/manual"])
    assert manual.job_url == "https://jobs.example/manual"


def test_queue_order_of_tied_packages_does_not_depend_on_folder_listing(tmp_path, monkeypatch):
    import pathlib

    output = tmp_path / "applications"
    for name in ("cccc", "aaaa", "bbbb"):
        _status_package(output, name=name, status="not_submitted", cover_letter="letter.docx")
    original_glob = pathlib.Path.glob
    monkeypatch.setattr(pathlib.Path, "glob", lambda self, pattern: reversed(sorted(original_glob(self, pattern))))
    assert [row["application_id"] for row in _queue_rows(output)] == ["aaaa", "bbbb", "cccc"]


def test_jobs_already_handled_get_no_new_package(tmp_path):
    from jobfilter.known import KnownPositions

    documents, output = _documents(tmp_path), tmp_path / "applications"
    known = KnownPositions()
    known.add("Example GmbH", "Strategy Analyst", "in the tracker")
    skipped = []
    result = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=output, applicant_status="graduating",
        attachment_allowlist=["Certificate.pdf"], skipped=skipped, known=known,
    )
    assert result == [] and skipped[0]["reason"] == "already_handled"
