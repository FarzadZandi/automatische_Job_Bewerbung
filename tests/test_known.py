from __future__ import annotations

import json

from openpyxl import Workbook

from jobfilter.known import KnownPositions, employer_key, same_employer, same_title, title_key


def _tracker(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bewerbungen"
    sheet.append(["Datum", "Unternehmen", "Position", "ID"])
    sheet.append(["01.10.2026", "Finanz Informatik GmbH & Co. KG", "Business Analyst Firmenkunden (m/w/d)", "aaaaaaaaaaaa"])
    sheet.append(["02.10.2026", "DIS AG", "Data Analyst", None])
    rejected = workbook.create_sheet("Nicht beworben")
    rejected.append(["Datum", "Unternehmen", "Position", "Grund"])
    rejected.append(["03.10.2026", "Deutsche Bahn AG", "Werkstudent Controlling", "Teilzeit"])
    workbook.create_sheet("Legende").append(["Feld", "Bedeutung"])
    workbook.save(path)
    return path


def test_matching_tolerates_tags_legal_forms_and_shorter_titles():
    assert same_employer(employer_key("DIS AG"), employer_key("DIS AG Perm"))
    assert not same_employer(employer_key("Deutsche Bahn AG"), employer_key("Deutsche Bank AG"))
    assert not same_employer(employer_key("Consulting GmbH"), employer_key("Example Consulting GmbH"))
    assert same_title(title_key("Data Analyst (w/m/d)"), title_key("Data Analyst Controlling (m/w/d)"))
    assert not same_title(title_key("Data Analyst Marketing"), title_key("Data Analyst Finance"))
    assert title_key("Analyst (all genders) Vollzeit") == title_key("Analyst")


def test_tracker_rows_and_packages_are_known(tmp_path):
    out = tmp_path / "applications"
    folder = out / "bbbbbbbbbbbb_x"
    folder.mkdir(parents=True)
    (folder / "application.json").write_text(json.dumps({
        "application_id": "bbbbbbbbbbbb", "employer": "Example GmbH", "title": "Strategy Analyst (m/w/d)",
        "submission_status": "skipped",
    }), encoding="utf-8")
    known = KnownPositions.load(_tracker(tmp_path / "tracker.xlsx"), out)
    assert known.match("Business Analyst Firmenkunden (w/m/d)", "Finanz Informatik GmbH & Co. KG") == "in the tracker"
    assert known.match("Data Analyst (m/w/d) im Reporting", "DIS AG Perm") == "in the tracker"
    assert known.match("Werkstudent Controlling", "Deutsche Bahn") == "rejected earlier (tracker)"
    assert known.match("Strategy Analyst", "Example GmbH") == "package exists (skipped)"
    assert known.match("Data Scientist", "DIS AG") == ""
    # A package never blocks itself, through its own tracker row or manifest.
    assert known.match("Business Analyst Firmenkunden", "Finanz Informatik", except_id="AAAAAAAAAAAA") == ""
    assert KnownPositions.load(tmp_path / "missing.xlsx", tmp_path / "none").entries == []
    (tmp_path / "broken.xlsx").write_bytes(b"not a workbook")
    assert KnownPositions.load(tmp_path / "broken.xlsx", None).entries == []
