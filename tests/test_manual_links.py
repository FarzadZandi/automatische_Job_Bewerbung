from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobfilter.adapters.base import JobAd, JobStub
from jobfilter.config import Config
from jobfilter.manual_links import (
    LinkEntry,
    accept_match,
    board_title_city,
    complete_saved_draft,
    complete_pending,
    extract_generic_job,
    load_pending,
    load_drafts,
    parse_link_line,
    process_links,
    retry_failed_links,
    save_drafts,
    save_pending,
    title_employer_hint,
    title_match_ratio,
)
from jobfilter.store import SeenStore, canonicalize_url


ROOT = Path(__file__).resolve().parents[1]
XING_MARKDOWN = "[Job: Forschungsreferent (m/w/d) | XING Jobs](https://www.xing.com/jobs/berlin-for%C2%ADschungs%C2%ADre%C2%ADfe%C2%ADrent-123456789?ijt=alert)"
XING_TITLE_URL = "Data Analyst | https://www.xing.com/jobs/muenchen-data-analyst-987654321?rru=email&refId=abc"
XING_MAINZ = "[Job: For\u00adschungs\u00adre\u00adfe\u00adrent*in (m/w/d) | XING Jobs](https://www.xing.com/jobs/mainz-for-schungs-re-fe-rent-159101726)"
XING_ULM = "[Job: Consultant for International Research Collaborations and Strategic Partnerships (m/f/d) | XING Jobs](https://www.xing.com/jobs/ulm-referent-internationale-forschungskooperationen-strategische-partnerschaften-158989369)"
XING_MAINZ_BARE = "https://www.xing.com/jobs/mainz-for-schungs-re-fe-rent-159101726"


def _config(tmp_path: Path) -> Config:
    return Config(
        contact_email="test@example.org", keywords_path=ROOT / "tests" / "fixtures" / "keywords_example_analytics.json",
        database_path=tmp_path / "seen.sqlite", output_dir=tmp_path / "out",
        application_archive_dir=tmp_path / "archive", application_output_dir=tmp_path / "applications",
        companies_path=tmp_path / "companies.yaml",
    )


def _fake_board_search(stub: JobStub, calls: list[tuple[str, str]]):
    def search(title, city, fetcher):
        calls.append((title, city))
        if not accept_match(title, city, stub):
            return None
        return JobAd(
            url=stub.url, title=stub.title, employer=stub.employer, location=stub.location,
            posted_date="2026-11-14", full_text="Analyse von Daten, Forschung und strategischen Partnerschaften. " * 4,
            employment_type="Vollzeit",
        )
    return search


def test_three_input_formats_and_xing_alert_fixtures():
    bare = parse_link_line("https://careers.example.com/job/1?utm_source=mail&trk=7")
    titled = parse_link_line(XING_TITLE_URL)
    markdown = parse_link_line(XING_MARKDOWN)
    assert bare == LinkEntry("https://careers.example.com/job/1?utm_source=mail&trk=7", "https://careers.example.com/job/1", "")
    assert titled and titled.title == "Data Analyst" and "?" not in titled.url
    assert markdown and markdown.title == "Forschungsreferent (m/w/d)"
    assert board_title_city(markdown) == (["Forschungsreferent (m/w/d)", "forschungsreferent"], "berlin")
    slug_only = LinkEntry("", "https://www.xing.com/jobs/berlin-for%C2%ADschungs%C2%ADre%C2%ADfe%C2%ADrent-123")
    assert board_title_city(slug_only) == (["forschungsreferent"], "berlin")
    assert parse_link_line("# comment") is None and parse_link_line("  ") is None


def test_tracking_parameters_are_removed_case_insensitively():
    cleaned = canonicalize_url("https://example.com/job/1?b=2&ijt=x&rru=y&utm_medium=z&trk=4&refId=5&a=1")
    assert cleaned == "https://example.com/job/1?a=1&b=2"


def test_title_and_city_match_requires_eighty_percent_and_same_city():
    good = JobStub("u", "Senior Data Analyst", "Example", "München")
    bad_title = JobStub("u", "Data Engineer", "Example", "München")
    bad_city = JobStub("u", "Senior Data Analyst", "Example", "Berlin")
    assert title_match_ratio("Senior Data Analyst", good.title) == 1
    for suffix in ("*in", ":in", "_in", "/in"):
        assert title_match_ratio(f"For\u200bschungsreferent{suffix} (all genders)", "Forschungsreferent (w/m/d)") == 1
    assert title_match_ratio("For\u200cschungs\u200dre\ufeffferent*in (m/f/d)", "Forschungsreferent (m/w/d)") == 1
    assert accept_match("Senior Data Analyst", "muenchen", good)
    assert not accept_match("Senior Data Analyst", "muenchen", bad_title)
    assert not accept_match("Senior Data Analyst", "muenchen", bad_city)
    assert title_employer_hint("Data Analyst bei Example GmbH") == ("Data Analyst", "Example GmbH")
    assert title_employer_hint("Data Analyst") == ("Data Analyst", "")


def test_job_board_is_never_fetched_and_done_bookkeeping_is_resume_safe(tmp_path):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "links_done.txt", tmp_path / "pending.json"
    links.write_text(XING_MARKDOWN + "\n", encoding="utf-8")

    class NoFetch:
        def get(self, *args, **kwargs):
            raise AssertionError("job-board URL was fetched")

    results = process_links(config, links, done, pending, fetcher=NoFetch(), board_search=lambda title, city, fetcher: None)
    assert results[0]["result"] == "needs ad text"
    assert links.read_text(encoding="utf-8") == ""
    assert "needs ad text" in done.read_text(encoding="utf-8")
    pending_row = load_pending(pending)[0]
    assert pending_row["title"] == "Forschungsreferent (m/w/d)"
    assert pending_row["candidates"] == ["Forschungsreferent (m/w/d)", "forschungsreferent"]
    assert process_links(config, links, done, pending, fetcher=NoFetch()) == []


def test_mainz_soft_hyphen_alert_matches_arbeitsagentur_candidate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(XING_MAINZ + "\n", encoding="utf-8")
    calls = []
    stub = JobStub("https://arbeitsagentur.example/mainz", "Forschungsreferent*in (m/w/d)", "Institut", "Mainz")
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda *args, **kwargs: (Path("a"), Path("b")))
    result = process_links(config, links, done, pending, fetcher=object(), board_search=_fake_board_search(stub, calls))
    assert result[0]["result"].startswith("scored ")
    assert calls == [("Forschungsreferent*in (m/w/d)", "mainz")]
    assert load_pending(pending) == []


def test_ulm_english_alert_matches_through_german_slug_candidate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(XING_ULM + "\n", encoding="utf-8")
    calls = []
    stub = JobStub(
        "https://arbeitsagentur.example/ulm",
        "Referent Internationale Forschungskooperationen & Strategische Partnerschaften (m/w/d)",
        "Universität", "Ulm",
    )
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda *args, **kwargs: (Path("a"), Path("b")))
    result = process_links(config, links, done, pending, fetcher=object(), board_search=_fake_board_search(stub, calls))
    assert result[0]["result"].startswith("scored ")
    assert calls == [
        ("Consultant for International Research Collaborations and Strategic Partnerships (m/f/d)", "ulm"),
        ("referent internationale forschungskooperationen strategische partnerschaften", "ulm"),
    ]


def test_bare_xing_syllable_slug_matches_joined_candidate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(XING_MAINZ_BARE + "\n", encoding="utf-8")
    calls = []
    stub = JobStub("https://arbeitsagentur.example/mainz", "Forschungsreferent*in (m/w/d)", "Institut", "Mainz")
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda *args, **kwargs: (Path("a"), Path("b")))
    result = process_links(config, links, done, pending, fetcher=object(), board_search=_fake_board_search(stub, calls))
    assert result[0]["result"].startswith("scored ")
    assert calls == [("for schungs re fe rent", "mainz"), ("forschungsreferent", "mainz")]


def test_no_board_candidate_matches_creates_one_pending_row_without_xing_fetch(tmp_path):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(XING_ULM + "\n", encoding="utf-8")
    calls = []

    class NoFetch:
        def get(self, url, *args, **kwargs):
            assert "xing.com" not in url
            raise AssertionError("no network call was expected")

    result = process_links(
        config, links, done, pending, fetcher=NoFetch(),
        board_search=lambda title, city, fetcher: calls.append((title, city)) or None,
    )
    assert result[0]["result"] == "needs ad text"
    rows = load_pending(pending)
    assert len(rows) == 1
    assert rows[0]["title"].startswith("Consultant for International")
    assert rows[0]["candidates"] == [title for title, _ in calls]
    assert rows[0]["reason"] == "not found on Arbeitsagentur or the employer feed"
    assert len(calls) == 2


def test_candidate_fetch_errors_become_one_pending_item_and_one_info_line(tmp_path, caplog):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(XING_MAINZ_BARE + "\n", encoding="utf-8")

    def broken(title, city, fetcher):
        raise RuntimeError("rendered search unavailable")

    with caplog.at_level("INFO", logger="jobfilter.manual_links"):
        result = process_links(config, links, done, pending, fetcher=object(), board_search=broken)
    assert result[0]["result"] == "needs ad text"
    rows = load_pending(pending)
    assert len(rows) == 1
    assert rows[0]["title"] == "Forschungsreferent"
    assert rows[0]["reason"] == "not found on Arbeitsagentur or the employer feed"
    assert sum("adapter or fetch error" in record.message for record in caplog.records) == 1
    assert "error:" not in done.read_text(encoding="utf-8")


def test_retry_errors_moves_only_failed_lines_back_to_links(tmp_path):
    links, done = tmp_path / "links.txt", tmp_path / "links_done.txt"
    existing = "https://example.com/existing"
    failed = "[Job: Analyst | XING Jobs](https://www.xing.com/jobs/mainz-analyst-1)"
    links.write_text(existing + "\n", encoding="utf-8")
    done.write_text(
        "2026-11-15\tscored REVIEW\thttps://example.com/ok\n"
        f"2026-11-15\terror: invalid line\t{failed}\n",
        encoding="utf-8",
    )
    assert retry_failed_links(links, done) == 1
    assert links.read_text(encoding="utf-8").splitlines() == [existing, failed]
    assert done.read_text(encoding="utf-8").splitlines() == [
        "2026-11-15\tscored REVIEW\thttps://example.com/ok",
    ]


def test_named_employer_alert_checks_company_feed_after_arbeitsagentur(tmp_path, monkeypatch):
    config = _config(tmp_path)
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(
        "Data Analyst bei Example GmbH | https://www.xing.com/jobs/berlin-data-analyst-123\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda *args, **kwargs: (Path("a"), Path("b")))

    def company(title, city, employer, fetcher, path):
        calls.append((title, city, employer, path))
        return JobAd(
            url="https://jobs.example/123", title=title, employer=employer, location=city,
            posted_date="2026-11-14", full_text="Analyse von Daten und Prozessen mit Python und SQL. " * 4,
            employment_type="Vollzeit",
        )

    results = process_links(
        config, links, done, pending, fetcher=object(),
        board_search=lambda title, city, fetcher: None, company_search=company,
    )
    assert results[0]["result"].startswith("scored ")
    assert calls == [("Data Analyst", "berlin", "Example GmbH", config.companies_path)]


def test_duplicate_seen_url_skips_before_fetch(tmp_path):
    config = _config(tmp_path)
    clean = "https://careers.example.com/job/1"
    SeenStore(config.database_path).mark_seen(clean, "Analyst", "Example", "manual")
    links, done, pending = tmp_path / "links.txt", tmp_path / "done.txt", tmp_path / "pending.json"
    links.write_text(clean + "?utm_source=alert&ijt=1\n", encoding="utf-8")

    class NoFetch:
        def get(self, *args, **kwargs):
            raise AssertionError("duplicate URL was fetched")

    result = process_links(config, links, done, pending, fetcher=NoFetch())
    assert result[0]["result"] == "duplicate seen URL"


def test_generic_employer_page_extraction_prefers_jobposting_data():
    page = """
      <html><head><meta property="og:site_name" content="Fallback GmbH"></head><body><main><h1>Fallback title</h1></main>
      <script type="application/ld+json">{"@type":"JobPosting","title":"Data Analyst","hiringOrganization":{"name":"Example GmbH"},"jobLocation":{"address":{"addressLocality":"Berlin"}},"employmentType":"FULL_TIME","description":"<p>Analyse von Daten und Prozessen mit SQL. Zusammenarbeit mit den Fachbereichen.</p>"}</script>
      </body></html>
    """
    ad = extract_generic_job(page, "https://jobs.example/1")
    assert (ad.title, ad.employer, ad.location, ad.employment_type) == ("Data Analyst", "Example GmbH", "Berlin", "FULL_TIME")
    assert "Analyse von Daten" in ad.full_text


def test_pasted_ad_text_scores_manual_and_uses_typed_employer(tmp_path, monkeypatch):
    config = _config(tmp_path)
    pending = tmp_path / "pending.json"
    save_pending(pending, [{
        "id": "abc", "title": "Data Analyst", "city": "Berlin",
        "url": "https://www.xing.com/jobs/berlin-data-analyst-1", "reason": "no match", "date": "2026-11-14",
    }])
    written = []
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda items, *args, **kwargs: written.extend(items) or (Path("a"), Path("b")))
    text = "Aufgaben\nAnalyse von Daten und Prozessen mit Python und SQL. " * 4
    result = complete_pending(config, pending, "abc", text, "Example GmbH")
    assert result["band"] and result["result"].startswith("scored ")
    assert written[0].board == "manual" and written[0].ad.url.startswith("https://www.xing.com/")
    assert written[0].result.band != "DROP" and load_pending(pending) == []
    assert SeenStore(config.database_path).is_seen(written[0].ad.url, "Data Analyst", "Example GmbH")


def test_paste_flow_reads_employer_line_and_requires_one_when_absent(tmp_path, monkeypatch):
    config = _config(tmp_path)
    pending = tmp_path / "pending.json"
    row = {"id": "abc", "title": "Analyst", "city": "Berlin", "url": "https://x.example/1"}
    save_pending(pending, [row])
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda *args, **kwargs: (Path("a"), Path("b")))
    text = "Unternehmen: Beispiel GmbH\nAufgaben: Daten analysieren, Prozesse bewerten und Berichte mit SQL erstellen. " * 3
    assert complete_pending(config, pending, "abc", text)["employer"] == "Beispiel GmbH"
    save_pending(pending, [row])
    with pytest.raises(ValueError, match="Employer is missing"):
        complete_pending(config, pending, "abc", "Aufgaben: Daten analysieren und Prozesse bewerten. " * 4)


def test_xing_copy_uses_title_and_employer_lines_and_keeps_part_time(tmp_path, monkeypatch):
    config = _config(tmp_path)
    pending = tmp_path / "pending.json"
    save_pending(pending, [{
        "id": "mainz", "title": "for schungs re fe rent", "city": "mainz",
        "url": "https://www.xing.com/jobs/mainz-for-schungs-re-fe-rent-159101726",
        "candidates": ["for schungs re fe rent", "forschungsreferent"],
        "reason": "no 80% title and city match",
    }])
    written = []
    monkeypatch.setattr("jobfilter.manual_links.write_outputs", lambda items, *args, **kwargs: written.extend(items) or (Path("a"), Path("b")))
    text = (
        "For\u00adschungs\u00adre\u00adfe\u00adrent*in (m/w/d)\n"
        "Jo\u00adhan\u00adnes Gu\u00adten\u00adberg-Uni\u00adver\u00adsi\u00adtät Mainz\n\n"
        "Part-timeEmployment type: Part-time\nMainz\nIhre Aufgaben:\n"
        "Sie beraten Wissenschaftlerinnen und Wissenschaftler bei Forschungsförderprogrammen, "
        "Anträgen, Finanzplanung und dem Projektmanagement drittmittelgeförderter Projekte. " * 2
    )
    drafts = tmp_path / "manual_drafts.json"
    save_drafts(drafts, {"mainz": {"text": text, "employer": ""}})
    result = complete_saved_draft(config, pending, drafts, "mainz")
    assert result["employer"] == "Johannes Gutenberg-Universität Mainz"
    assert result["title"] == "Forschungsreferent*in (m/w/d)"
    assert written[0].ad.employment_type == "Teilzeit" and load_pending(pending) == []
    assert load_drafts(drafts) == {}
    results = json.loads((pending.parent / "manual_results.json").read_text(encoding="utf-8"))
    assert results[0]["employment_format"] == "PART-TIME / TEILZEIT"
    assert results[0]["band"] == written[0].result.band

