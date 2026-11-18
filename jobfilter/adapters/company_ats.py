from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import yaml
from bs4 import BeautifulSoup

from jobfilter.fetch import Fetcher

from .base import Adapter, JobAd, JobStub


SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
@dataclass(frozen=True)
class CompanySpec:
    name: str
    ats: str
    token: str
    active: bool = True


def company_source_url(spec: CompanySpec, location_terms: Iterable[str] = ()) -> str:
    query = {"name": spec.name}
    terms = tuple(str(term).strip() for term in location_terms if str(term).strip())
    if terms:
        query["locations"] = "|".join(terms)
    return f"company-ats://{spec.ats}/{quote(spec.token, safe='')}?{urlencode(query)}"


def load_company_sources(path: str | Path, location_terms: Iterable[str] = ()) -> list[str]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Company source file does not exist: {source_path}")
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    rows = raw.get("companies", []) if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        raise ValueError("Company source file must contain a 'companies' list")
    sources: list[str] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise ValueError(f"Company entry {index} must be a mapping")
        active = row.get("active", True)
        if isinstance(active, str):
            active = active.strip().casefold() in {"1", "true", "yes", "y"}
        if not active:
            continue
        spec = CompanySpec(
            name=str(row.get("name") or row.get("company_name") or "").strip(),
            ats=str(row.get("ats") or row.get("ats_type") or "").strip().casefold(),
            token=str(row.get("token") or row.get("board_token") or "").strip(),
        )
        if not spec.name or not spec.token:
            raise ValueError(f"Company entry {index} needs name and token")
        if spec.ats not in SUPPORTED_ATS:
            raise ValueError(
                f"Company entry {index} has unsupported ATS {spec.ats!r}; "
                f"supported: {', '.join(sorted(SUPPORTED_ATS))}"
            )
        sources.append(company_source_url(spec, location_terms))
    return sources


def _plain_text(value: object) -> str:
    return BeautifulSoup(str(value or ""), "lxml").get_text("\n", strip=True)


def _date_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    return text[:10]


def _structured_label(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("label") or value.get("id") or "").strip()
    return str(value or "").strip()


def _employment_type(title: str, structured: object = "") -> str:
    structured_text = _structured_label(structured)
    if structured_text:
        return structured_text
    normalized = title.casefold()
    if any(term in normalized for term in ("werkstudent", "working student")):
        return "Werkstudent"
    if any(term in normalized for term in ("praktikum", "internship", " intern ")):
        return "Praktikum"
    if any(term in normalized for term in ("teilzeit", "part-time", "part time")):
        return "Teilzeit"
    return "Vollzeit"


def _location_relevant(location: str, location_terms: Iterable[str]) -> bool:
    normalized = location.casefold()
    ascii_variants = (
        normalized.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    )
    return bool(re.search(r"(?:^|[,;/\s])de(?:$|[,;/\s])", normalized)) or any(
        term.casefold() in normalized or term.casefold() in ascii_variants for term in location_terms
    )


class CompanyATSAdapter(Adapter):
    """Read public company job-board APIs and normalize them into JobAds."""

    host_patterns: list[str] = []
    requires_position_delay = False

    def __init__(self, source_url: str):
        parts = urlsplit(source_url)
        if parts.scheme != "company-ats":
            raise ValueError(f"Not a company ATS source: {source_url}")
        self.ats = parts.netloc.casefold()
        self.token = parts.path.strip("/")
        self.company = parse_qs(parts.query).get("name", [self.token])[0].strip()
        self.location_terms = tuple(
            term for term in parse_qs(parts.query).get("locations", ["germany|deutschland"])[0].split("|") if term
        )
        if self.ats not in SUPPORTED_ATS or not self.token:
            raise ValueError(f"Invalid company ATS source: {source_url}")
        self.name = f"company:{self.ats}"
        self.pages_walked = 0
        self.last_total_results: int | None = None
        self._ads: dict[str, JobAd] = {}

    def parse_detail(self, html_or_json: str, url: str) -> JobAd:
        raise NotImplementedError("Company ATS feeds already include normalized job details")

    def load_detail(self, stub: JobStub, fetch: Fetcher) -> JobAd:
        try:
            ad = self._ads[stub.url]
        except KeyError as exc:
            raise ValueError(f"Company ATS detail was not cached for {stub.url}") from exc
        if ad.raw_meta.get("detail_pending"):
            # Fetched here, not during listing, so only unseen jobs cost a request.
            ad = self._smartrecruiters_detail(ad, fetch)
            self._ads[stub.url] = ad
        return ad

    def _store(self, ads: Iterable[JobAd]) -> Iterator[JobStub]:
        for ad in ads:
            if not ad.url or not ad.title or not _location_relevant(ad.location, self.location_terms):
                continue
            self._ads[ad.url] = ad
            yield JobStub(ad.url, ad.title, ad.employer, ad.location, ad.posted_date)

    @staticmethod
    def _json(fetch: Fetcher, url: str) -> Any:
        return json.loads(fetch.get(url, expect_json=True).text)

    def _greenhouse(self, fetch: Fetcher) -> list[JobAd]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{quote(self.token, safe='')}/jobs?content=true"
        data = self._json(fetch, url)
        self.pages_walked += 1
        rows = data.get("jobs", []) if isinstance(data, Mapping) else []
        self.last_total_results = len(rows)
        ads = []
        for row in rows:
            text = _plain_text(row.get("content"))
            location = str((row.get("location") or {}).get("name") or "").strip()
            ads.append(JobAd(
                url=str(row.get("absolute_url") or "").strip(),
                title=str(row.get("title") or "").strip(),
                employer=self.company,
                location=location,
                posted_date=_date_text(row.get("updated_at")),
                full_text=text,
                employment_type=_employment_type(str(row.get("title") or "")),
                raw_meta={"ats": self.ats, "board_token": self.token},
            ))
        return ads

    def _lever(self, fetch: Fetcher) -> list[JobAd]:
        url = f"https://api.lever.co/v0/postings/{quote(self.token, safe='')}?mode=json"
        rows = self._json(fetch, url)
        self.pages_walked += 1
        if not isinstance(rows, list):
            raise ValueError(f"Lever returned a non-list payload for {self.company}")
        self.last_total_results = len(rows)
        ads = []
        for row in rows:
            categories = row.get("categories") or {}
            location = categories.get("location") or ""
            if isinstance(location, list):
                location = ", ".join(str(value) for value in location if value)
            sections: list[str] = []
            opening = row.get("descriptionPlain") or _plain_text(row.get("description"))
            if opening:
                sections.append(str(opening))
            for section in row.get("lists") or []:
                if section.get("text"):
                    sections.append(str(section["text"]))
                if section.get("content"):
                    sections.append(_plain_text(section["content"]))
            additional = row.get("additionalPlain") or _plain_text(row.get("additional"))
            if additional:
                sections.append(str(additional))
            text = "\n\n".join(value.strip() for value in sections if value and value.strip())
            title = str(row.get("text") or "").strip()
            ads.append(JobAd(
                url=str(row.get("hostedUrl") or row.get("applyUrl") or "").strip(),
                title=title,
                employer=self.company,
                location=str(location).strip(),
                posted_date=_date_text(row.get("createdAt")),
                full_text=text,
                employment_type=_employment_type(title, categories.get("commitment")),
                raw_meta={"ats": self.ats, "board_token": self.token},
            ))
        return ads

    def _ashby(self, fetch: Fetcher) -> list[JobAd]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(self.token, safe='')}"
        data = self._json(fetch, url)
        self.pages_walked += 1
        rows = data.get("jobs", []) if isinstance(data, Mapping) else []
        self.last_total_results = len(rows)
        ads = []
        for row in rows:
            locations = [str(row.get("location") or "").strip()]
            locations.extend(
                str(item.get("location") or "").strip()
                for item in (row.get("secondaryLocations") or [])
                if item.get("location")
            )
            location = ", ".join(dict.fromkeys(value for value in locations if value))
            text = _plain_text(row.get("descriptionHtml")) or str(row.get("descriptionPlain") or "").strip()
            title = str(row.get("title") or "").strip()
            ads.append(JobAd(
                url=str(row.get("jobUrl") or row.get("applyUrl") or "").strip(),
                title=title,
                employer=self.company,
                location=location,
                posted_date=_date_text(row.get("publishedAt")),
                full_text=text,
                employment_type=_employment_type(title, row.get("employmentType")),
                raw_meta={"ats": self.ats, "board_token": self.token},
            ))
        return ads

    def _smartrecruiters(self, fetch: Fetcher) -> list[JobAd]:
        offset, limit = 0, 100
        ads: list[JobAd] = []
        total = 0
        while True:
            url = (
                "https://api.smartrecruiters.com/v1/companies/"
                f"{quote(self.token, safe='')}/postings?limit={limit}&offset={offset}&country=de"
            )
            data = self._json(fetch, url)
            self.pages_walked += 1
            rows = data.get("content", []) if isinstance(data, Mapping) else []
            total = int(data.get("totalFound") or len(rows)) if isinstance(data, Mapping) else len(rows)
            for row in rows:
                location_data = row.get("location") or {}
                location = ", ".join(
                    str(location_data.get(key) or "").strip()
                    for key in ("city", "region", "country", "countryCode")
                    if location_data.get(key)
                )
                if not _location_relevant(location, self.location_terms):
                    continue
                posting_id = str(row.get("id") or "").strip()
                department = _structured_label(row.get("department"))
                title = str(row.get("name") or "").strip()
                # The public posting page; the API "ref" field is an API URL, not a page.
                job_url = str(row.get("postingUrl") or row.get("applyUrl") or "").strip()
                if not job_url and posting_id:
                    job_url = (
                        f"https://jobs.smartrecruiters.com/{quote(self.token, safe='')}/"
                        f"{quote(posting_id, safe='')}"
                    )
                ads.append(JobAd(
                    url=job_url,
                    title=title,
                    employer=self.company,
                    location=location,
                    posted_date=_date_text(row.get("releasedDate")),
                    full_text=f"Department: {department}" if department else "",
                    employment_type=_employment_type(title, row.get("typeOfEmployment")),
                    raw_meta={
                        "ats": self.ats,
                        "board_token": self.token,
                        "posting_id": posting_id,
                        "detail_pending": bool(posting_id),
                    },
                ))
            offset += limit
            if not rows or offset >= total:
                break
        self.last_total_results = total
        return ads

    def _smartrecruiters_detail(self, ad: JobAd, fetch: Fetcher) -> JobAd:
        """Fetch one SmartRecruiters posting and build the scoring text from its sections."""
        posting_id = str(ad.raw_meta.get("posting_id") or "")
        detail_url = (
            "https://api.smartrecruiters.com/v1/companies/"
            f"{quote(self.token, safe='')}/postings/{quote(posting_id, safe='')}"
        )
        detail = self._json(fetch, detail_url)
        if not isinstance(detail, Mapping):
            raise ValueError(f"SmartRecruiters returned an unexpected detail payload for {ad.url}")
        self.pages_walked += 1
        text_parts = [ad.full_text] if ad.full_text else []
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        if isinstance(sections, Mapping):
            for section in sections.values():
                if not isinstance(section, Mapping):
                    continue
                section_title = _plain_text(section.get("title"))
                section_text = _plain_text(section.get("text"))
                if section_title:
                    text_parts.append(section_title)
                if section_text:
                    text_parts.append(section_text)
        posting_url = str(detail.get("postingUrl") or "").strip()
        structured_type = detail.get("typeOfEmployment")
        return replace(
            ad,
            url=posting_url if posting_url.startswith("https://") else ad.url,
            full_text="\n\n".join(part for part in text_parts if part),
            employment_type=(
                _employment_type(ad.title, structured_type) if structured_type else ad.employment_type
            ),
            raw_meta={
                key: value for key, value in ad.raw_meta.items() if key != "detail_pending"
            } | {"detail_loaded": True},
        )

    def iter_listing(self, url: str, fetch: Fetcher, max_pages: int | None) -> Iterator[JobStub]:
        loaders = {
            "greenhouse": self._greenhouse,
            "lever": self._lever,
            "ashby": self._ashby,
            "smartrecruiters": self._smartrecruiters,
        }
        yield from self._store(loaders[self.ats](fetch))
