from datetime import date

from openpyxl import load_workbook

from jobfilter.adapters.base import JobAd
from jobfilter.export import (
    DROPPED_COLUMNS,
    INDUSTRY_COLUMNS,
    KEPT_COLUMNS,
    ScoredJob,
    _read_records,
    _write_records_workbook,
    write_checkpoint_outputs,
    write_outputs,
)
from jobfilter.scoring import ScoreResult


def item(
    band, score, title, *, reason=None, stage="",
    primary="B06 Data Analytics und Business Intelligence",
    employment_format="WERKSTUDENT / WORKING STUDENT",
    location="Stuttgart",
    posted="2026-08-25",
    url=None,
    industry_trigger="",
    university_partner="",
    external_url="",
    staffing_agency=False,
):
    ad = JobAd(
        url=url or f"https://example.com/{title.lower().replace(' ', '-')}",
        title=title,
        employer="Example GmbH",
        location=location,
        posted_date=posted,
        deadline="2026-09-30",
        full_text="KPI reporting and data analysis using Power BI.",
        employment_type="Werkstudent",
        raw_meta={"external_url": external_url, "is_staffing_agency": staffing_agency},
    )
    result = ScoreResult(
        score=score,
        raw_score=score,
        band=band,
        reason=reason,
        drop_stage=stage,
        matched_term="python" if band == "DROP" else "",
        employment_format=employment_format,
        primary_cv_family=primary,
        matched_terms={
            "B06.strong": ("data analytics",),
            "analysis_task": ("kpi reporting",),
            "methods": (),
            "tools_strong": ("power bi",),
        },
        language_flags=("GERMAN C1+ / NATIVE-LEVEL",) if title == "Review Job" else (),
        eligibility_flags=("IMMATRIKULATION REQUIRED",),
        penalties=("GERMAN C1+ / NATIVE-LEVEL (-5)",) if title == "Review Job" else (),
        bonuses=("EXACT STUDENT FORMAT (+2)",),
        industry_trigger=industry_trigger,
        university_partner=university_partner,
    )
    return ScoredJob(ad, "arbeitsagentur", result)


def test_writes_only_required_workbooks(tmp_path):
    jobs = [
        item("REVIEW", 8, "Review Job"),
        item("PRIORITY", 18, "Priority Job"),
        item("DE REQUIRED", 16, "German Required Job", reason="german_c1_c2_or_native_required"),
        item("ACADEMISCHE", 0, "Academic Job", reason="excluded_doctoral_candidate_role"),
        item("DROP", 0, "Dropped Job", reason="no_target_family_or_analysis_match", stage="relevance_gate", primary=""),
    ]
    full_time_xlsx, other_xlsx = write_outputs(jobs, tmp_path, date(2026, 8, 26))
    assert full_time_xlsx.name == "jobs_vollzeit_2026-08-26.xlsx"
    assert other_xlsx.name == "jobs_other_2026-08-26.xlsx"
    assert full_time_xlsx.parent.name == "run"
    assert other_xlsx.parent.name == "run"
    assert (tmp_path / "jobs_vollzeit.xlsx").exists()
    assert (tmp_path / "jobs_other.xlsx").exists()
    workbook = load_workbook(other_xlsx)
    assert workbook.sheetnames == ["PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW", "Academische", "DROPPED"]
    assert [cell.value for cell in workbook["PRIORITY"][1]] == KEPT_COLUMNS
    assert [cell.value for cell in workbook["DROPPED"][1]] == DROPPED_COLUMNS
    assert [cell.value for cell in workbook["INDUSTRY-PhD"][1]] == INDUSTRY_COLUMNS
    assert workbook["PRIORITY"].freeze_panes == "A2"
    assert workbook["PRIORITY"].auto_filter.ref == "A1:AE2"
    kept_index = {cell.value: cell.column for cell in workbook["PRIORITY"][1]}
    dropped_index = {cell.value: cell.column for cell in workbook["DROPPED"][1]}
    assert workbook["PRIORITY"].cell(2, kept_index["url"]).hyperlink.target.endswith("priority-job")
    assert workbook["PRIORITY"].cell(2, kept_index["federal_state"]).value == "Baden-Württemberg"
    assert workbook["PRIORITY"].cell(2, kept_index["distance_from_home_km"]).value is None
    assert workbook["REVIEW"].cell(2, kept_index["language_flags"]).value == "GERMAN C1+ / NATIVE-LEVEL"
    assert workbook["DE Required"].cell(2, kept_index["title"]).value == "German Required Job"
    assert workbook["Academische"].cell(2, kept_index["title"]).value == "Academic Job"
    assert workbook["DROPPED"].cell(2, dropped_index["band"]).value == "DROP"
    assert workbook["DROPPED"].cell(2, dropped_index["date"]).value == "2026-08-25"
    assert workbook["PRIORITY"].cell(2, kept_index["rule_version"]).value == "2.9-2026-11-13-r1"
    assert workbook["PRIORITY"].cell(2, kept_index["fetched_date"]).value == "2026-08-26"
    assert workbook["PRIORITY"].cell(2, kept_index["raw_employment_type"]).value == "Werkstudent"
    assert "KPI reporting" in workbook["PRIORITY"].cell(2, kept_index["full_text"]).value
    assert all(
        workbook["PRIORITY"].column_dimensions[workbook["PRIORITY"].cell(1, kept_index[name]).column_letter].hidden
        for name in ("rule_version", "fetched_date", "raw_employment_type", "full_text")
    )
    assert not list(tmp_path.rglob("*.txt"))


def test_rows_are_sorted_descending(tmp_path):
    _, other_xlsx = write_outputs(
        [item("PRIORITY", 12, "Lower"), item("PRIORITY", 22, "Higher")],
        tmp_path,
        date(2026, 8, 26),
    )
    sheet = load_workbook(other_xlsx)["PRIORITY"]
    assert [sheet["A2"].value, sheet["A3"].value] == [22, 12]


def test_compaction_keeps_recent_non_drop_text_and_minimises_drop(tmp_path):
    path = tmp_path / "master.xlsx"
    _write_records_workbook([
        {"_sheet": "REVIEW", "title": "Recent", "employer": "A", "url": "https://a", "band": "REVIEW", "posted": date.today().isoformat(), "full_text": "keep me"},
        {"_sheet": "REVIEW", "title": "Old", "employer": "B", "url": "https://b", "band": "REVIEW", "posted": "2020-01-01", "full_text": "remove me"},
        {"_sheet": "DROPPED", "title": "Drop", "employer": "C", "url": "https://c", "band": "DROP", "posted": "2026-01-02", "full_text": "never store"},
    ], path)
    workbook = load_workbook(path, read_only=True)
    headers = [cell.value for cell in workbook["REVIEW"][1]]
    text_column = headers.index("full_text") + 1
    values = {row[headers.index("title")].value: row[text_column - 1].value for row in workbook["REVIEW"].iter_rows(min_row=2)}
    assert values == {"Recent": "keep me", "Old": None}
    assert [cell.value for cell in workbook["DROPPED"][1]] == DROPPED_COLUMNS
    assert list(workbook["DROPPED"].values)[1] == ("Drop", "C", "https://c", "DROP", "2026-01-02")


def test_old_home_distance_column_is_read_once_and_written_with_generic_name(tmp_path):
    from openpyxl import Workbook

    old = tmp_path / "old.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "REVIEW"
    sheet.append(["title", "employer", "location", "band", "distance_from_previous_home_km"])
    sheet.append(["Analyst", "Example", "Berlin", "REVIEW", 500.0])
    workbook.save(old)

    records = _read_records(old)
    assert records[0]["distance_from_home_km"] == 500.0
    assert "distance_from_previous_home_km" not in records[0]
    migrated = tmp_path / "migrated.xlsx"
    _write_records_workbook(records, migrated)
    headers = [cell.value for cell in load_workbook(migrated)["REVIEW"][1]]
    assert "distance_from_home_km" in headers and "distance_from_previous_home_km" not in headers


def test_external_url_and_staffing_flag_are_exported(tmp_path):
    full_path, _ = write_outputs(
        [item(
            "PRIORITY", 18, "Agency Analyst", employment_format="FULL-TIME / VOLLZEIT",
            external_url="https://careers.example.com/apply/1", staffing_agency=True,
        )],
        tmp_path,
        date(2026, 8, 26),
    )
    sheet = load_workbook(full_path)["PRIORITY"]
    columns = {cell.value: cell.column for cell in sheet[1]}
    assert sheet.cell(2, columns["is_staffing_agency"]).value is True
    external = sheet.cell(2, columns["external_url"])
    assert external.value == "https://careers.example.com/apply/1"
    assert external.hyperlink.target == external.value


def test_rows_are_sorted_by_posting_date_before_score(tmp_path):
    _, other_xlsx = write_outputs(
        [
            item("PRIORITY", 30, "Older", posted="2026-08-20"),
            item("PRIORITY", 12, "Newer", posted="25.08.2026"),
        ],
        tmp_path,
        date(2026, 8, 26),
    )
    sheet = load_workbook(other_xlsx)["PRIORITY"]
    assert [sheet["F2"].value, sheet["F3"].value] == ["Newer", "Older"]


def test_industry_phd_sheet_has_extra_columns_and_required_sort(tmp_path):
    _, other_xlsx = write_outputs(
        [
            item("CHECK", 20, "Check", industry_trigger="company_signals"),
            item("TOP", 9, "Top Lower", industry_trigger="strong_term", university_partner="tum"),
            item("TOP", 12, "Top Higher", industry_trigger="employer_whitelist"),
        ],
        tmp_path,
        date(2026, 8, 26),
    )
    sheet = load_workbook(other_xlsx)["INDUSTRY-PhD"]
    assert [sheet["F2"].value, sheet["F3"].value, sheet["F4"].value] == ["Top Higher", "Top Lower", "Check"]
    assert sheet.cell(3, INDUSTRY_COLUMNS.index("university_partner") + 1).value == "tum"


def test_full_time_and_other_jobs_are_written_to_separate_workbooks(tmp_path):
    full_time_xlsx, other_xlsx = write_outputs(
        [
            item("PRIORITY", 20, "Full-time Analyst", employment_format="FULL-TIME / VOLLZEIT"),
            item("PRIORITY", 18, "Working Student Analyst"),
        ],
        tmp_path,
        date(2026, 8, 26),
    )
    full_time = load_workbook(full_time_xlsx)
    other = load_workbook(other_xlsx)
    assert full_time["PRIORITY"]["F2"].value == "Full-time Analyst"
    assert full_time["PRIORITY"].max_row == 2
    assert other["PRIORITY"]["F2"].value == "Working Student Analyst"
    assert other["PRIORITY"].max_row == 2


def test_repeated_same_day_runs_do_not_overwrite_prior_outputs(tmp_path):
    first = write_outputs([item("PRIORITY", 18, "First Batch")], tmp_path, date(2026, 8, 26))
    second = write_outputs([item("PRIORITY", 18, "Second Batch")], tmp_path, date(2026, 8, 26))
    assert first[0].name == "jobs_vollzeit_2026-08-26.xlsx"
    assert second[0].name == "jobs_vollzeit_2026-08-26_2.xlsx"
    assert all(path.exists() for path in (*first, *second))


def test_checkpoint_outputs_are_stable_and_do_not_rebuild_masters(tmp_path):
    first = write_checkpoint_outputs([item("PRIORITY", 18, "First")], tmp_path, 42, date(2026, 8, 26))
    second = write_checkpoint_outputs([item("PRIORITY", 19, "Second")], tmp_path, 42, date(2026, 8, 26))
    assert first == second
    assert all(path.exists() for path in second)
    assert not (tmp_path / "jobs_vollzeit.xlsx").exists()
    assert not (tmp_path / "jobs_other.xlsx").exists()
    assert load_workbook(second[1])["PRIORITY"]["F2"].value == "Second"


def test_master_workbook_deduplicates_urls_and_keeps_latest_record(tmp_path):
    write_outputs(
        [item("REVIEW", 8, "Same Job", posted="2026-08-20")],
        tmp_path,
        date(2026, 8, 26),
    )
    write_outputs(
        [item("PRIORITY", 18, "Same Job", posted="2026-08-25")],
        tmp_path,
        date(2026, 8, 27),
    )
    master = load_workbook(tmp_path / "jobs_other.xlsx")
    assert master["REVIEW"].max_row == 1
    assert master["PRIORITY"].max_row == 2
    assert master["PRIORITY"]["F2"].value == "Same Job"
    headers = {cell.value: cell.column for cell in master["PRIORITY"][1]}
    assert master["PRIORITY"].cell(2, headers["posted"]).value == "2026-08-25"


def test_cross_master_duplicate_is_kept_only_in_latest_canonical_group(tmp_path):
    shared_url = "https://example.com/jobs/one?utm_source=old"
    write_outputs(
        [item("PRIORITY", 18, "Analyst", employment_format="FULL-TIME / VOLLZEIT", posted="2026-08-20", url=shared_url)],
        tmp_path,
        date(2026, 8, 26),
    )
    write_outputs(
        [item("PRIORITY", 20, "Working Student Analyst", posted="2026-08-25", url="https://example.com/jobs/one")],
        tmp_path,
        date(2026, 8, 27),
    )
    full_time = load_workbook(tmp_path / "jobs_vollzeit.xlsx")
    other = load_workbook(tmp_path / "jobs_other.xlsx")
    assert full_time["PRIORITY"].max_row == 1
    assert other["PRIORITY"].max_row == 2
    assert other["PRIORITY"]["F2"].value == "Working Student Analyst"


def test_duplicates_are_removed_before_daily_split(tmp_path):
    shared_url = "https://example.com/jobs/two"
    full_path, other_path = write_outputs(
        [
            item("PRIORITY", 18, "Old Full Time", employment_format="FULL-TIME / VOLLZEIT", posted="2026-08-20", url=shared_url),
            item("PRIORITY", 20, "New Student", posted="2026-08-25", url=shared_url),
        ],
        tmp_path,
        date(2026, 8, 26),
    )
    assert load_workbook(full_path)["PRIORITY"].max_row == 1
    assert load_workbook(other_path)["PRIORITY"]["F2"].value == "New Student"
