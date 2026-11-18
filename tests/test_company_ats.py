import json

from jobfilter.adapters import CompanyATSAdapter, adapter_for_url
from jobfilter.adapters.company_ats import (
    CompanySpec,
    _employment_type,
    _location_relevant,
    company_source_url,
    load_company_sources,
)
from jobfilter.fetch import FetchResult


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FetchResult(url, json.dumps(self.payload), 200, "application/json")


def test_loads_enabled_company_sources(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "companies:\n"
        "  - {name: Example, ats: greenhouse, token: example}\n"
        "  - {name: Disabled, ats: lever, token: disabled, active: false}\n",
        encoding="utf-8",
    )
    sources = load_company_sources(path)
    assert len(sources) == 1
    assert sources[0].startswith("company-ats://greenhouse/example?")
    adapter = adapter_for_url(sources[0])
    assert isinstance(adapter, CompanyATSAdapter)
    assert adapter.company == "Example"


def test_greenhouse_jobs_are_normalized_and_location_filtered():
    source = company_source_url(CompanySpec("Example GmbH", "greenhouse", "example"), ("germany", "berlin"))
    adapter = CompanyATSAdapter(source)
    fetcher = FakeFetcher({
        "jobs": [
            {
                "title": "Strategy Analyst",
                "location": {"name": "Berlin, Germany"},
                "content": "<p>Full-time analytics and corporate strategy.</p>",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                "updated_at": "2026-11-12T08:30:00Z",
            },
            {
                "title": "US Analyst",
                "location": {"name": "New York, USA"},
                "content": "<p>Analytics</p>",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/2",
            },
        ]
    })
    stubs = list(adapter.iter_listing(source, fetcher, None))
    assert len(stubs) == 1
    assert stubs[0].title == "Strategy Analyst"
    ad = adapter.load_detail(stubs[0], fetcher)
    assert ad.employer == "Example GmbH"
    assert ad.employment_type == "Vollzeit"
    assert "corporate strategy" in ad.full_text
    assert adapter.last_total_results == 2


def test_employment_type_uses_title_and_structured_fields_not_ad_body():
    assert _employment_type("Data Analyst", "") == "Vollzeit"
    assert _employment_type("Data Analyst", "Part-time") == "Part-time"
    # An ad body mentioning collaboration with interns must not change the type.
    assert _employment_type("Data Analyst") == "Vollzeit"
    terms = ("germany", "berlin")
    assert _location_relevant("Berlin, Germany, de", terms)
    assert _location_relevant("Remote, DE", terms)
    assert not _location_relevant("Delhi, India", terms)


def test_smartrecruiters_filters_country_and_fetches_german_detail_description():
    source = company_source_url(CompanySpec("Example GmbH", "smartrecruiters", "Example"), ("germany", "berlin"))
    adapter = CompanyATSAdapter(source)

    class RoutingFetcher:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url.endswith("/postings/1"):
                payload = {
                    "postingUrl": "https://jobs.smartrecruiters.com/Example/1-analyst",
                    "typeOfEmployment": {"label": "Full-time"},
                    "jobAd": {"sections": {
                        "jobDescription": {"title": "Aufgaben", "text": "<p>Strategie und Datenanalyse.</p>"},
                        "qualifications": {"title": "Profil", "text": "<p>Power BI und KPI reporting.</p>"},
                    }},
                }
            else:
                payload = {
                    "totalFound": 1,
                    "content": [{
                        "id": "1", "name": "Strategy Analyst", "releasedDate": "2026-11-12",
                        "location": {"city": "Berlin", "country": "Germany", "countryCode": "de"},
                        "department": {"label": "Strategy"},
                    }],
                }
            return FetchResult(url, json.dumps(payload), 200, "application/json")

    fetcher = RoutingFetcher()
    stubs = list(adapter.iter_listing(source, fetcher, None))
    assert len(stubs) == 1
    ad = adapter.load_detail(stubs[0], fetcher)
    assert "Strategie und Datenanalyse" in ad.full_text
    assert "Power BI" in ad.full_text
    assert ad.url.startswith("https://jobs.smartrecruiters.com/")
    assert "country=de" in fetcher.calls[0]
    assert fetcher.calls[1].endswith("/postings/1")



def test_smartrecruiters_listing_does_not_fetch_details_until_load_detail():
    source = company_source_url(CompanySpec("Example GmbH", "smartrecruiters", "Example"), ("germany", "berlin"))
    adapter = CompanyATSAdapter(source)

    class CountingFetcher:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if "/postings/" in url:
                payload = {"postingUrl": f"https://jobs.smartrecruiters.com/Example/{url.rsplit('/', 1)[1]}-x",
                           "jobAd": {"sections": {"jobDescription": {"title": "Aufgaben", "text": "<p>Analyse</p>"}}}}
            else:
                payload = {"totalFound": 3, "content": [
                    {"id": str(i), "name": f"Analyst {i}", "location": {"city": "Berlin", "countryCode": "de"}}
                    for i in range(3)
                ]}
            return FetchResult(url, json.dumps(payload), 200, "application/json")

    fetcher = CountingFetcher()
    stubs = list(adapter.iter_listing(source, fetcher, None))
    assert len(stubs) == 3
    assert len(fetcher.calls) == 1  # listing only; already-seen jobs never cost a detail request
    ad = adapter.load_detail(stubs[1], fetcher)
    assert len(fetcher.calls) == 2 and fetcher.calls[1].endswith("/postings/1")
    assert "Analyse" in ad.full_text
    assert ad.url == "https://jobs.smartrecruiters.com/Example/1-x"
    assert "detail_pending" not in ad.raw_meta
    adapter.load_detail(stubs[1], fetcher)
    assert len(fetcher.calls) == 2  # cached after the first load
    assert CompanyATSAdapter.requires_position_delay is False
