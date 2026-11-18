from pathlib import Path

import pytest
from openpyxl import load_workbook

from jobfilter.scoring import load_keywords, score_text


ROOT = Path(__file__).parents[1]
RUN_DIR = ROOT / "out" / "run"
KEYWORDS = load_keywords(ROOT / "tests/fixtures/keywords_example_analytics.json")
OLD_SHEETS = {"PRIORITY", "REVIEW", "DE Required", "Academische", "DROPPED"}


def _new_sheet(result) -> str:
    return {
        "PRIORITY": "PRIORITY",
        "REVIEW": "REVIEW",
        "DE REQUIRED": "DE Required",
        "ACADEMISCHE": "Academische",
        "TOP": "INDUSTRY-PhD",
        "CHECK": "INDUSTRY-PhD",
        "DROP": "DROPPED",
    }[result.band]


@pytest.mark.skipif(not RUN_DIR.exists(), reason="historical daily workbooks are not available")
def test_latest_three_daily_workbooks_only_have_explained_v28_moves():
    paths = sorted(RUN_DIR.glob("jobs_*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)[:3]
    assert len(paths) == 3
    unexplained = []
    for path in paths:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                if sheet.title not in OLD_SHEETS:
                    continue
                headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
                for values in sheet.iter_rows(min_row=2, values_only=True):
                    if not any(value not in (None, "") for value in values):
                        continue
                    record = dict(zip(headers, values))
                    result = score_text(
                        str(record.get("title") or ""),
                        str(record.get("full_text") or record.get("snippet") or ""),
                        KEYWORDS,
                        employment_type=str(record.get("raw_employment_type") or record.get("employment_format") or ""),
                        employer=str(record.get("employer") or ""),
                    )
                    destination = _new_sheet(result)
                    if destination == sheet.title:
                        continue
                    allowed = destination == "INDUSTRY-PhD" or (
                        destination == "DROPPED"
                        and result.reason in {
                            "thesis_trainee_title",
                            "excluded_senior_or_leadership_role",
                            "excluded_technical_support_role",
                        }
                    )
                    if not allowed:
                        unexplained.append(
                            (path.name, record.get("title"), sheet.title, destination, result.reason)
                        )
        finally:
            workbook.close()
    assert unexplained == []
