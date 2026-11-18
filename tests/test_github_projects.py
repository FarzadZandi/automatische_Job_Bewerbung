import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import requests
from lxml import etree

from jobfilter.__main__ import main
from jobfilter.applications import (
    ApplicationJob,
    cv_project_slot_count,
    place_projects_in_cv,
    prepare_application_packages,
)
from jobfilter.github_projects import (
    MAX_CV_LINE_CHARS,
    MISSING,
    Project,
    catalog_problems,
    load_projects,
    project_problems,
    select_projects,
    sync_github,
)

ROOT = Path(__file__).parents[1]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class FakeResponse:
    def __init__(self, status, payload=None, etag="", links=None):
        self.status_code = status
        self._payload = payload
        self.headers = {"ETag": etag} if etag else {}
        self.links = links or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


REPOS = [
    {"name": "SalesForecast", "html_url": "https://github.com/u/SalesForecast", "description": "Sales",
     "language": "Python", "topics": ["forecasting"], "pushed_at": "2026-09-09T00:00:00Z", "private": False, "fork": False},
    {"name": "NewThing", "html_url": "https://github.com/u/NewThing", "description": "Something new",
     "language": "R", "topics": [], "pushed_at": "2026-11-01T00:00:00Z", "private": False, "fork": False},
    {"name": "SecretRepo", "private": True, "fork": False},
    {"name": "ForkedRepo", "private": False, "fork": True},
]


def _catalog(path, *, use_sales=True):
    path.write_text(
        "projects:\n"
        "  SalesForecast:\n"
        "    title: \"SalesForecast\"\n"
        "    cv_line: \"SalesForecast: Absatzprognose für 100 Filialen (Python).\"\n"
        "    families: [B05, B06]\n"
        "    keywords: [absatz, forecast]\n"
        f"    use: {'true' if use_sales else 'false'}\n"
        "  Gone:\n"
        "    title: \"Gone\"\n"
        "    cv_line: \"Gone: Ein entferntes Projekt (Python).\"\n"
        "    families: [B06]\n"
        "    use: true\n",
        encoding="utf-8",
    )


def test_sync_caches_public_repos_appends_stubs_and_uses_etag(tmp_path):
    catalog, cache = tmp_path / "catalog.yaml", tmp_path / "cache" / "repos.json"
    _catalog(catalog)
    session = FakeSession([FakeResponse(200, REPOS, etag='"abc"')])
    report = sync_github("u", cache_path=cache, catalog_path=catalog, session=session)
    assert report.error == "" and report.fetched
    assert report.repos == ["SalesForecast", "NewThing"]  # private and forked repos are ignored
    assert report.new == ["NewThing"]
    assert report.missing == ["Gone"]
    assert "Authorization" not in session.calls[0][1]  # keyless public API
    text = catalog.read_text(encoding="utf-8")
    assert "  NewThing:" in text and MISSING in text and text.startswith("projects:\n  SalesForecast:")
    projects = {project.repo: project for project in load_projects(catalog, cache)}
    assert projects["SalesForecast"].placeable
    assert not projects["NewThing"].placeable  # FEHLT stub is never placed
    assert not projects["Gone"].placeable  # no longer public on GitHub

    second = FakeSession([FakeResponse(304)])
    again = sync_github("u", cache_path=cache, catalog_path=catalog, session=second)
    assert again.not_modified and again.new == []
    assert second.calls[0][1]["If-None-Match"] == '"abc"'
    assert catalog.read_text(encoding="utf-8").count("  NewThing:") == 1


def test_sync_errors_keep_the_cache(tmp_path):
    catalog, cache = tmp_path / "catalog.yaml", tmp_path / "repos.json"
    _catalog(catalog)
    sync_github("u", cache_path=cache, catalog_path=catalog, session=FakeSession([FakeResponse(200, REPOS[:1])]))
    before = cache.read_text(encoding="utf-8")
    failed = sync_github("u", cache_path=cache, catalog_path=catalog,
                         session=FakeSession([requests.ConnectionError("offline")]))
    assert "offline" in failed.error and failed.repos == ["SalesForecast"]
    limited = sync_github("u", cache_path=cache, catalog_path=catalog,
                          session=FakeSession([FakeResponse(403, {"message": "rate limit"})]))
    assert "HTTP 403" in limited.error
    assert cache.read_text(encoding="utf-8") == before


def _project(title, families=(), keywords=(), use=True, line=None):
    return Project(repo=title, title=title, cv_line=line or f"{title}: Beschreibung (Python).",
                   families=tuple(families), keywords=tuple(keywords), use=use)


def test_selection_pins_letter_projects_then_ranks_by_family_and_keywords():
    projects = [
        _project("Weather Forecast", ["B05", "B06"], ["prognose"]),
        _project("SalesForecast", ["B05"], ["absatz", "filial"]),
        _project("Startup Patterns", ["B03"], ["startup"]),
        _project("Disabled", ["B05"], ["absatz"], use=False),
        _project("Stub", ["B05"], ["absatz"], line=MISSING),
    ]
    chosen = select_projects(projects, slots=2, job_title="Demand Planner Absatz",
                             job_text="Absatzplanung für 300 Filialen", primary_family="B05 Prognose und Planung")
    assert [project.title for project, _ in chosen] == ["SalesForecast", "Weather Forecast"]
    pinned = select_projects(projects, slots=2, job_title="Demand Planner Absatz", job_text="Absatz",
                             primary_family="B05", pinned_text="Im Projekt Startup Patterns habe ich ...")
    assert [project.title for project, _ in pinned] == ["Startup Patterns", "SalesForecast"]
    assert pinned[0][1] == "named in the cover letter"
    assert select_projects(projects, slots=0, job_title="x", job_text="x", primary_family="B05") == []


def test_keywords_match_at_word_start_only():
    from jobfilter.github_projects import _term_in

    assert _term_in("filial", "planung fuer 300 filialen")
    assert not _term_in("werbung", "wir freuen uns auf ihre bewerbung")
    assert not _term_in("klima", "ein gutes betriebsklima")
    assert _term_in("a/b", "erfahrung mit a/b-tests")
    assert _term_in("gis", "kenntnisse in gis") and not _term_in("gis", "logistik")


def _cv_with_projects(path, lines):
    paragraphs = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/></w:pPr><w:r><w:t>{line}</w:t></w:r></w:p>' for line in lines
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Berufserfahrung</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="ListParagraph"/></w:pPr><w:r><w:t>Erfahrung bleibt.</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Projekte</w:t></w:r></w:p>'
        f'{paragraphs}'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Ausgewählte Publikation</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Publikation bleibt.</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def _texts(path):
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return ["".join(p.xpath(".//w:t/text()", namespaces={"w": W})) for p in root.iter(f"{{{W}}}p")]


def test_placement_fills_existing_slots_only(tmp_path):
    cv = tmp_path / "cv.docx"
    _cv_with_projects(cv, ["Weather Forecast: alte Zeile (Python).", "Facebook Ad Bidding A/B Test: alt (Python)."])
    assert cv_project_slot_count(cv) == 2
    shown = place_projects_in_cv(cv, [_project("SalesForecast"), _project("Weather Forecast", line="Weather Forecast: neue Zeile (Python, scikit-learn).")])
    texts = _texts(cv)
    assert shown == ["Weather Forecast", "SalesForecast"]  # the matching slot keeps its position
    assert texts[3] == "Weather Forecast: neue Zeile (Python, scikit-learn)."
    assert texts[4] == "SalesForecast: Beschreibung (Python)."
    assert "Erfahrung bleibt." in texts and "Publikation bleibt." in texts
    assert len(texts) == 7  # no paragraph added or removed


def test_placement_never_puts_a_longer_line_into_a_shorter_slot(tmp_path):
    cv = tmp_path / "cv.docx"
    _cv_with_projects(cv, ["Kurz: eine Zeile (Python).", "Lang: " + "x" * 150 + " (Python)."])
    long_project = _project("Long", line="Long: " + "y" * 150 + " (R).")
    short_project = _project("Short", line="Short: kurz (R).")
    assert place_projects_in_cv(cv, [long_project, short_project]) == ["Short", "Long"]
    texts = _texts(cv)
    assert texts[3] == "Short: kurz (R)." and texts[4].startswith("Long: yyy")

    one_line = tmp_path / "one_line.docx"
    _cv_with_projects(one_line, ["Kurz: eine Zeile (Python)."])
    assert place_projects_in_cv(one_line, [long_project]) == ["Kurz"]  # nothing fits: the slot stays
    assert _texts(one_line)[3] == "Kurz: eine Zeile (Python)."


def test_cv_lines_are_capped_so_they_stay_on_two_lines():
    fits = _project("Fits", line="Fits: " + "a" * (MAX_CV_LINE_CHARS - 12) + " (R).")
    too_long = _project("Long", line="Long: " + "a" * MAX_CV_LINE_CHARS + " (R).")
    assert len(fits.cv_line) <= MAX_CV_LINE_CHARS and project_problems(fits) == []
    assert any(f"maximum {MAX_CV_LINE_CHARS}" in problem for problem in project_problems(too_long))
    assert project_problems(_project("Off", line="Off: " + "a" * 400, use=False)) == []


def _job(**overrides):
    values = dict(
        queue_rank=1, source_sheet="PRIORITY", score=18, band="PRIORITY", title="Demand Planner (m/w/d)",
        employer="Example GmbH", location="Berlin", posted="2026-11-12", deadline="", board="test",
        url="https://example.com/job/1", external_url="", primary_cv_family="B05 Prognose und Planung",
        secondary_cv_family="", family_hits="absatzplanung", analysis_task_hits="", method_hits="",
        tool_hits="", language_flags="", full_text="Absatzplanung und Forecasting für den Handel.",
    )
    values.update(overrides)
    return ApplicationJob(**values)


def test_prepare_places_projects_in_the_copy_and_records_them(tmp_path):
    documents = tmp_path / "Job App Docs"
    (documents / "Attachements").mkdir(parents=True)
    with zipfile.ZipFile(documents / "Anschreiben_Max_Muster.docx", "w") as archive:
        archive.writestr("word/document.xml", "<x/>")
    bank = "\n".join(
        f"B{i:02d}:\n  - \"Nachweis eins B{i:02d} mit Weather Forecast.\"\n  - \"Nachweis zwei B{i:02d}.\"" for i in range(1, 11)
    )
    (documents / "anschreiben_bausteine.yaml").write_text(bank + "\n", encoding="utf-8")
    cv = documents / "Lebenslauf_Muster_Max_05.docx"
    _cv_with_projects(cv, ["Facebook Ad Bidding A/B Test: alt (Python).", "Weather Forecast: alt (Python)."])
    source_hash = hashlib.sha256(cv.read_bytes()).hexdigest()
    projects = [_project("Weather Forecast", ["B05"]), _project("SalesForecast", ["B05"], ["absatzplanung"]),
                _project("Startup Patterns", ["B03"], ["startup"])]
    result = prepare_application_packages(
        [_job()], documents_dir=documents, output_dir=tmp_path / "out", applicant_status="graduating",
        attachment_allowlist=[], github_projects=projects,
    )
    assert result[0]["github_projects"] == ["Weather Forecast", "SalesForecast"]
    package = Path(result[0]["package_dir"])
    texts = _texts(package / cv.name)
    assert "SalesForecast: Beschreibung (Python)." in texts
    assert not any(text.startswith("Facebook Ad Bidding") for text in texts)
    assert hashlib.sha256(cv.read_bytes()).hexdigest() == source_hash  # source CV untouched
    manifest = json.loads((package / "application.json").read_text(encoding="utf-8"))
    assert [entry["title"] for entry in manifest["cv_github_projects"]] == ["Weather Forecast", "SalesForecast"]
    assert manifest["cv_github_projects"][0]["reason"] == "named in the cover letter"
    brief = json.loads((package / "letter_brief.json").read_text(encoding="utf-8"))
    assert brief["cv_projects"] == ["Weather Forecast", "SalesForecast"]


def test_sync_command_requires_opt_in(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text('contact_email: "test@example.com"\n', encoding="utf-8")
    assert main(["--config", str(config), "sync-github"]) == 1
    assert "github_projects_enabled" in capsys.readouterr().out


def test_zero_max_age_never_contacts_github(tmp_path, monkeypatch, capsys):
    import jobfilter.__main__ as cli
    from jobfilter.config import Config

    catalog = tmp_path / "catalog.yaml"
    _catalog(catalog)
    config = tmp_path / "config.yaml"
    config.write_text(
        'contact_email: "test@example.com"\n'
        "github_projects_enabled: true\n"
        'github_user: "someone"\n'
        f'github_catalog_path: "{catalog.as_posix()}"\n'
        f'github_cache_path: "{(tmp_path / "cache.json").as_posix()}"\n'
        "github_sync_max_age_days: 0\n",
        encoding="utf-8",
    )

    def no_network(*args, **kwargs):
        raise AssertionError("GitHub must not be contacted")

    monkeypatch.setattr(cli, "sync_github", no_network)
    projects = cli._github_projects(Config.load(config))
    assert [project.repo for project in projects] == ["SalesForecast", "Gone"]  # no cache: catalog as written
    assert "available for CVs: 2 of 2" in capsys.readouterr().out


REAL_CATALOG = ROOT / "Job App Docs" / "github_projekte.yaml"


@pytest.mark.skipif(not REAL_CATALOG.exists(), reason="personal project catalog is not available")
def test_personal_catalog_lines_pass_checks():
    projects = load_projects(REAL_CATALOG)
    assert catalog_problems(projects) == []
    assert sum(project.placeable for project in projects) >= 2
