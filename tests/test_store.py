from jobfilter.store import SeenStore, canonicalize_url


def test_canonicalize_removes_tracking_and_fragment():
    url = "HTTPS://Example.COM/job/1/?utm_source=x&b=2&a=1#details"
    assert canonicalize_url(url) == "https://example.com/job/1?a=1&b=2"


def test_seen_by_url_or_title_employer_identity(tmp_path):
    store = SeenStore(tmp_path / "seen.sqlite")
    store.mark_seen("https://example.com/job/1", "Data Analyst", "Example GmbH", "test")
    assert store.is_seen("https://example.com/job/1?utm_source=daily")
    assert store.is_seen("https://example.com/job/new-url", " Data Analyst ", "example gmbh")
    assert not store.is_seen("https://example.com/job/2", "Other", "Example GmbH")


def test_maintenance_prunes_old_cached_text_except_packaged_urls(tmp_path):
    store = SeenStore(tmp_path / "seen.sqlite")
    with store.connect() as db:
        db.execute("CREATE TABLE ad_text_cache(canonical_url TEXT, fetched_at TEXT, full_text TEXT)")
        db.executemany(
            "INSERT INTO ad_text_cache VALUES (?, ?, ?)",
            [("https://example.com/remove", "2000-01-01T00:00:00+00:00", "old"),
             ("https://example.com/keep", "2000-01-01T00:00:00+00:00", "packaged")],
        )
    assert store.maintain({"https://example.com/keep?utm_source=alert"}) == 1
    with store.connect() as db:
        assert [row[0] for row in db.execute("SELECT canonical_url FROM ad_text_cache")] == ["https://example.com/keep"]

