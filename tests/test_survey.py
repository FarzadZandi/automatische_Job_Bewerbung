from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from jobfilter.__main__ import build_parser
from jobfilter.survey import (
    Portal,
    choose_portals,
    classify_ats,
    workbook_portals,
    run_survey,
    survey_portal,
    write_portals,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / "tests" / "fixtures" / "forms"


@pytest.fixture
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Exception as error:
            pytest.skip(f"Chromium is not available: {error}")
        yield instance
        instance.close()


def _scripts():
    return (
        (ROOT / "extension" / "filler.js").read_text(encoding="utf-8"),
        (ROOT / "extension" / "rules.js").read_text(encoding="utf-8"),
    )


def test_read_only_survey_declines_cookie_reveals_and_describes(browser):
    filler, rules = _scripts()
    portal = Portal("fixture.example", "Fixture", "own site", (FORMS / "survey_hidden.html").as_uri(), 12)
    record = survey_portal(browser, portal, filler_js=filler, rules_js=rules, timeout_seconds=10)
    assert record["wall_type"] == "none" and record["hidden_until_click"] is True
    assert record["multi_step"] is True
    assert record["page_loads"] == 1 and record["frames"] == 1
    assert record["steps"] == ["open job URL", "decline cookies", "reveal apply form: Jetzt bewerben"]
    by_label = {field["label"]: field for field in record["fields"]}
    assert by_label["Vorname *"]["key"] == "first_name"
    assert by_label["Deutschkenntnisse"]["option_count"] == 2
    assert by_label["Warum möchten Sie bei uns arbeiten?"]["key"] is None
    assert all("value" not in field for field in record["fields"])


def test_survey_follows_only_one_apply_link_and_stops_at_login(browser):
    filler, rules = _scripts()
    portal = Portal("fixture.example", "Fixture", "unknown", (FORMS / "survey_link.html").as_uri(), 5)
    record = survey_portal(browser, portal, filler_js=filler, rules_js=rules, timeout_seconds=10)
    assert record["wall_type"] == "login" and record["page_loads"] == 2
    assert len([step for step in record["steps"] if step.startswith("follow apply")]) == 1
    assert all(field["key"] != "password" for field in record["fields"])


@pytest.mark.parametrize(
    ("fixture", "labels"),
    [
        ("survey_greenhouse_classic.html", {"First Name", "Last Name", "Email Address"}),
        ("survey_greenhouse_custom.html", {"Most Recent Employer*", "Most Recent Job Title*", "Portfolio URL"}),
    ],
)
def test_offline_greenhouse_survey_recovers_real_labels(browser, fixture, labels):
    filler, rules = _scripts()
    portal = Portal("boards.greenhouse.io", "Fixture", "greenhouse", (FORMS / fixture).as_uri(), 1)
    record = survey_portal(browser, portal, filler_js=filler, rules_js=rules, timeout_seconds=10)
    found = {field["label"] for field in record["fields"]}
    assert labels <= found
    assert "text" not in found and "Ignored lower-priority label" not in found


def test_survey_never_loads_an_excluded_job_board():
    class BrowserThatMustNotOpen:
        def new_page(self):
            raise AssertionError("excluded portal was opened")

    portal = Portal("arbeitsagentur.de", "Excluded", "unknown", "https://arbeitsagentur.de/jobs/1?q=secret", 99)
    record = survey_portal(BrowserThatMustNotOpen(), portal, filler_js="", rules_js="")
    assert record["page_loads"] == 0 and record["wall_type"] == "error"
    assert record["final_path"] == "/jobs/1" and "q=" not in json.dumps(record)


def test_resume_skips_completed_hosts_and_report_uses_no_values(tmp_path):
    portals = tmp_path / "portals.csv"
    write_portals(portals, [Portal("fixture.example", "Fixture", "own site", (FORMS / "survey_hidden.html").as_uri(), 1)])
    output = tmp_path / "survey.jsonl"
    output.write_text(json.dumps({"host": "fixture.example", "ATS": "own site", "wall_type": "none", "fields": []}) + "\n", encoding="utf-8")
    result = run_survey(portals, output, ROOT / "extension", delay_min=0, delay_max=0, timeout_seconds=5)
    assert result == {"written": 0, "skipped": 1, "errors": 0}
    report = write_report(output, tmp_path / "SURVEY_REPORT.md")
    assert report.is_file() and "1 distinct portal" in report.read_text(encoding="utf-8")


def test_report_counts_the_current_generic_upload_routing(tmp_path):
    output = tmp_path / "survey.jsonl"
    output.write_text(json.dumps({
        "host": "fixture.example", "ATS": "greenhouse", "wall_type": "none",
        "fields": [
            {"label": "Maximal 50 MB", "kind": "file", "key": None},
            {"label": "Attach", "kind": "file", "key": None},
            {"label": "Unknown question", "kind": "text", "key": None},
        ],
    }) + "\n", encoding="utf-8")
    report = write_report(output, tmp_path / "SURVEY_REPORT.md").read_text(encoding="utf-8")
    assert "| greenhouse | 1 | 3 | 2 | 66.7% |" in report
    assert "| Unknown question | 1 |" in report
    assert "| Maximal 50 MB |" not in report


def test_ats_classification_and_balanced_selection():
    assert classify_ats("jobs.smartrecruiters.com") == "smartrecruiters"
    assert classify_ats("jobs.ashbyhq.com") == "ashby"
    assert classify_ats("helixjobs.com") == "helixjobs"
    assert classify_ats("bewerbermanagement.net") == "bewerbermanagement"
    assert classify_ats("careers.example.de", "Example GmbH") == "own site"
    assert classify_ats("forms.vendor.invalid", "Example GmbH") == "unknown"
    candidates = []
    for index in range(15):
        candidates.append(Portal(f"careers{index}.employer{index}.de", f"Employer {index}", "own site", f"https://careers{index}.employer{index}.de/job", 100-index))
    for ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "personio", "unknown"):
        for index in range(10):
            candidates.append(Portal(f"{ats}{index}.example", f"{ats} {index}", ats, f"https://{ats}{index}.example/job", 80-index))
    chosen = choose_portals(candidates, target=55)
    counts = {}
    for portal in chosen:
        counts[portal.ats] = counts.get(portal.ats, 0) + 1
    assert len(chosen) == 55 and counts["own site"] >= 10
    assert all(counts.get(ats, 0) <= 6 for ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "personio"))


def test_portal_candidates_include_application_urls_from_scored_text(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["score", "employer", "board", "url", "external_url", "full_text"])
    sheet.append([
        18, "Diehl", "arbeitsagentur", "https://arbeitsagentur.de/jobs/1", "",
        "Details: https://career.diehl.com/Vacancies/19042/Application/New/31 and privacy: https://diehl.com/karriere/privacy",
    ])
    path = tmp_path / "scored.xlsx"
    workbook.save(path)
    candidates = workbook_portals([path], tmp_path / "missing.yaml")
    assert [(row.host, row.ats) for row in candidates] == [("career.diehl.com", "own site")]


def test_cli_has_survey_command_and_portal_csv_headers(tmp_path):
    args = build_parser().parse_args(["survey", "--select-only", "--limit", "50"])
    assert args.command == "survey" and args.limit == 50 and args.select_only is True
    path = write_portals(tmp_path / "portals.csv", [Portal("jobs.example", "Example", "own site", "https://jobs.example/1", 9)])
    with path.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == ["host", "employer", "ATS", "example job URL", "score"]
