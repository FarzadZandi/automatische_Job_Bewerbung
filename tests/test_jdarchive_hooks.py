from __future__ import annotations

import csv
import json
from pathlib import Path

from jobfilter import applicant
from jobfilter.config import Config
from jobfilter.jdarchive import archive_drop, archive_package, record_outcome
from jobfilter.scoring import ScoreResult


def _package(folder: Path, *, full: bool = True) -> Path:
    folder.mkdir(parents=True)
    (folder / "application.json").write_text(json.dumps({
        "application_id": "pkg-1", "employer": "Beispiel GmbH", "title": "Datenanalyst (m/w/d)",
        "application_url": "https://example.org/job/1", "primary_cv_family": "B02", "score": 14,
        "selected_cv": "Lebenslauf_Muster_Max_02.docx", "cover_letter": "Anschreiben_Muster_Max_Beispiel.docx",
    }), encoding="utf-8")
    brief = {"ad_text": "Kurzfassung", "ad_text_full": "Volltext der Anzeige mit Aufgaben und Anforderungen.",
             "ad_text_full_source": "full" if full else "excerpt"}
    (folder / "letter_brief.json").write_text(json.dumps(brief), encoding="utf-8")
    return folder


def _rows(root: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((root / "index.csv").open(encoding="utf-8-sig")))


def test_package_skipped_then_applied_keeps_one_file(tmp_path):
    package = _package(tmp_path / "out" / "pkg")
    root = tmp_path / "archive"
    first = archive_package(root, package, "skipped", reason="title gate")
    second = archive_package(root, package, "applied")
    assert first == second and first.exists()
    text = first.read_text(encoding="utf-8")
    assert "status: applied" in text and "Volltext der Anzeige" in text and "nur Auszug" not in text
    assert [row["status"] for row in _rows(root)] == ["skipped", "applied"]
    assert _rows(root)[0]["primary_family"] == "B02"
    assert len(list(root.rglob("*.md"))) == 1


def test_excerpt_is_marked_and_expired_has_no_file(tmp_path):
    package = _package(tmp_path / "pkg", full=False)
    root = tmp_path / "archive"
    path = archive_package(root, package, "applied")
    assert "nur Auszug" in path.read_text(encoding="utf-8")
    other = _package(tmp_path / "pkg2")
    data = json.loads((other / "application.json").read_text(encoding="utf-8")); data["application_id"] = "pkg-2"
    (other / "application.json").write_text(json.dumps(data), encoding="utf-8")
    assert archive_package(root, other, "expired") is None
    assert [row["file"] for row in _rows(root) if row["status"] == "expired"] == [""]


def test_archive_never_raises_and_drop_is_index_only(tmp_path):
    assert archive_package(tmp_path / "a", tmp_path / "missing", "applied") is None
    result = ScoreResult(score=0, raw_score=0, band="DROP", reason="no_family_evidence", drop_stage="relevance")
    archive_drop(tmp_path / "a", {"url": "https://example.org/x", "title": "Koch", "employer": "Kantine"}, result,
                 reason="no_family_evidence")
    archive_drop(tmp_path / "a", {"url": "https://example.org/x", "title": "Koch", "employer": "Kantine"}, result,
                 reason="no_family_evidence")
    rows = _rows(tmp_path / "a")
    assert len(rows) == 1 and rows[0]["status"] == "dropped" and rows[0]["file"] == ""
    assert not list((tmp_path / "a").rglob("*.md"))


def test_jd_archive_dir_follows_the_config_folder(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("contact_email: test@example.org\n", encoding="utf-8")
    config = Config.load(config_path, validate=False)
    assert Path(config.jd_archive_dir) == (tmp_path / "Bewerbungen" / "Stellenarchiv").resolve()


def test_language_levels_come_from_the_profile():
    applicant.configure({"german_level": "C2", "english_level": "B2"}, {"tier2_skills": ["Tableau"]})
    assert not applicant.german_capped()
    assert "Englisch B2" in applicant.apply("{LEVELS}")
    assert applicant.FORBIDDEN_SKILLS == [r"\btableau\b"]
    applicant.configure({"german_level": "B1"}, {})
    assert applicant.german_capped() and "B1" in applicant.apply("{DE_CAP}")
    assert "{" not in applicant.apply("{LETTER_LANGUAGE_RULE}{DE_STYLE}{LEVELS}{DE_LEVEL}")
