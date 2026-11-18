from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

from jobfilter.fetch import Fetcher

from .base import Adapter, JobAd, JobStub


def imported_csv_source_url(
    path: str | Path,
    *,
    source: str = "indeed",
    default_employment_type: str = "Vollzeit",
) -> str:
    absolute = Path(path).expanduser().resolve()
    query = urlencode({"path": str(absolute), "default_employment_type": default_employment_type})
    return f"job-import://{source}?{query}"


def _first(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def _employment_type(value: str, default: str) -> str:
    normalized = value.strip().casefold().replace("_", "").replace("-", "")
    return {
        "fulltime": "Vollzeit",
        "vollzeit": "Vollzeit",
        "parttime": "Teilzeit",
        "teilzeit": "Teilzeit",
        "internship": "Praktikum",
        "praktikum": "Praktikum",
        "workingstudent": "Werkstudent",
        "werkstudent": "Werkstudent",
    }.get(normalized, value.strip() or default)


class ImportedCSVAdapter(Adapter):
    """Normalize an authorized job-search CSV export into the main pipeline."""

    host_patterns: list[str] = []
    requires_position_delay = False

    def __init__(self, source_url: str):
        parts = urlsplit(source_url)
        if parts.scheme != "job-import":
            raise ValueError(f"Not a job import source: {source_url}")
        query = parse_qs(parts.query)
        self.source = parts.netloc.strip().casefold() or "import"
        path_value = query.get("path", [""])[0]
        if not path_value:
            raise ValueError(f"Job import source has no path: {source_url}")
        self.path = Path(path_value).expanduser()
        self.default_employment_type = query.get("default_employment_type", ["Vollzeit"])[0]
        self.name = f"import:{self.source}"
        self.pages_walked = 0
        self.last_total_results: int | None = None
        self._ads: dict[str, JobAd] = {}

    def parse_detail(self, html_or_json: str, url: str) -> JobAd:
        raise NotImplementedError("Imported rows already include normalized job details")

    def load_detail(self, stub: JobStub, fetch: Fetcher) -> JobAd:
        try:
            return self._ads[stub.url]
        except KeyError as exc:
            raise ValueError(f"Imported detail was not cached for {stub.url}") from exc

    def iter_listing(self, url: str, fetch: Fetcher, max_pages: int | None) -> Iterator[JobStub]:
        if not self.path.exists():
            raise FileNotFoundError(f"Imported job CSV does not exist: {self.path}")
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.pages_walked = 1
        self.last_total_results = len(rows)
        for row in rows:
            title = _first(row, "title", "job_title", "position")
            employer = _first(row, "employer", "company", "company_name")
            location = _first(row, "location", "job_location")
            full_text = _first(row, "full_text", "description", "job_description", "snippet")
            posted = _first(row, "posted", "date_posted", "posted_date")
            employment = _employment_type(
                _first(row, "employment_type", "job_type", "type"),
                self.default_employment_type,
            )
            job_url = _first(row, "url", "job_url", "apply_url", "link")
            if not job_url:
                digest = hashlib.sha1(f"{title}\n{employer}\n{location}".encode("utf-8")).hexdigest()
                job_url = f"imported-job://{self.source}/{digest}"
            if not title:
                continue
            ad = JobAd(
                url=job_url,
                title=title,
                employer=employer,
                location=location,
                posted_date=posted,
                full_text=full_text,
                employment_type=employment,
                raw_meta={"import_source": self.source, "import_path": str(self.path)},
            )
            self._ads[job_url] = ad
            yield JobStub(job_url, title, employer, location, posted)
