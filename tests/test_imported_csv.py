from jobfilter.adapters import ImportedCSVAdapter, adapter_for_url
from jobfilter.adapters.imported_csv import imported_csv_source_url


def test_authorized_indeed_csv_is_normalized(tmp_path):
    path = tmp_path / "indeed.csv"
    path.write_text(
        "title,company,location,description,url,date_posted,job_type\n"
        "Strategy Analyst,Example GmbH,Berlin,Corporate strategy and analytics,"
        "https://example.com/job/1,2026-11-12,fulltime\n",
        encoding="utf-8",
    )
    source = imported_csv_source_url(path, source="indeed")
    adapter = adapter_for_url(source)
    assert isinstance(adapter, ImportedCSVAdapter)
    stubs = list(adapter.iter_listing(source, None, None))
    assert len(stubs) == 1
    ad = adapter.load_detail(stubs[0], None)
    assert adapter.name == "import:indeed"
    assert ad.employer == "Example GmbH"
    assert ad.employment_type == "Vollzeit"
    assert ad.full_text == "Corporate strategy and analytics"

