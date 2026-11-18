"""Manual job-link intake without scraping job boards or inventing missing facts."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup

from .adapters import adapter_for_url
from .adapters.arbeitsagentur import ArbeitsagenturAdapter
from .adapters.base import JobAd, JobStub
from .adapters.company_ats import CompanyATSAdapter, load_company_sources
from .export import ScoredJob, write_outputs
from .fetch import Fetcher
from .known import KnownPositions, employer_key, same_employer, title_key
from .scoring import ScoreResult, load_keywords, score_text
from .store import SeenStore, canonicalize_url


JOB_BOARD = re.compile(r"(^|\.)(xing|linkedin|indeed|stepstone|glassdoor|kununu|monster)\.", re.I)
MARKDOWN_LINK = re.compile(r"^\[([^]]+)]\((https?://[^)]+)\)\s*$", re.I)
TITLE_URL = re.compile(r"^(.*?)\s+\|\s+(https?://\S+)\s*$", re.I)
EMPLOYER_LINE = re.compile(r"(?im)^\s*(?:arbeitgeber|unternehmen|company|employer)\s*:\s*(.+?)\s*$")
INVISIBLE_TITLE = re.compile("[\u00ad\u200b\u200c\u200d\ufeff]")
GENDER_SUFFIX = re.compile(
    r"(?i)(?<=\w)[*:_/]in\b|\s*(?:\(|\[)\s*(?:(?:[mwfd]\s*/\s*){2}[mwfd]|all\s+genders?)\s*(?:\)|\])"
)
PENDING_LIMIT = 500
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkEntry:
    raw: str
    url: str
    title: str = ""


def _clean_title(value: str) -> str:
    title = INVISIBLE_TITLE.sub("", value)
    title = re.sub(r"^\s*Job\s*:\s*", "", title, flags=re.I).strip()
    title = re.sub(r"\s*\|\s*(?:XING|LinkedIn|Indeed|StepStone)\s+Jobs?\s*$", "", title, flags=re.I)
    return " ".join(title.split())


def parse_link_line(line: str) -> LinkEntry | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    match = MARKDOWN_LINK.fullmatch(raw)
    if match:
        return LinkEntry(raw, canonicalize_url(match.group(2)), _clean_title(match.group(1)))
    match = TITLE_URL.fullmatch(raw)
    if match:
        return LinkEntry(raw, canonicalize_url(match.group(2)), _clean_title(match.group(1)))
    if urlsplit(raw).scheme in {"http", "https"} and urlsplit(raw).hostname:
        return LinkEntry(raw, canonicalize_url(raw), "")
    raise ValueError("expected a URL, 'Title | URL', or a Markdown job link")


def is_job_board(url: str) -> bool:
    return bool(JOB_BOARD.search((urlsplit(url).hostname or "").removeprefix("www.")))


def _plain_slug(value: str) -> str:
    return INVISIBLE_TITLE.sub("", unquote(value))


def board_title_city(entry: LinkEntry) -> tuple[list[str], str]:
    """Recover up to three ordered title candidates and XING's city slug."""
    host = (urlsplit(entry.url).hostname or "").casefold()
    path = [part for part in urlsplit(entry.url).path.split("/") if part]
    slug = _plain_slug(path[-1] if path else "")
    city = ""
    slug_title = ""
    if "xing." in host and slug:
        parts = [part for part in slug.split("-") if part]
        if parts and re.fullmatch(r"\d+", parts[-1]):
            parts.pop()
        if parts:
            city = parts.pop(0)
        slug_title = " ".join(parts)
    elif slug:
        slug_title = re.sub(r"[-_]+", " ", re.sub(r"[-_]?\d+$", "", slug))
    link_title = _clean_title(entry.title)
    slug_title = " ".join(slug_title.split())
    proposed = [link_title, slug_title]
    slug_tokens = slug_title.split()
    if slug_tokens and sum(len(token) <= 4 for token in slug_tokens) * 2 >= len(slug_tokens):
        proposed.append("".join(slug_tokens))
    candidates: list[str] = []
    seen: set[str] = set()
    for title in proposed:
        key = title.casefold()
        if title and key not in seen:
            candidates.append(title)
            seen.add(key)
    return candidates[:3], city


def title_employer_hint(value: str) -> tuple[str, str]:
    """Use an employer only when the pasted alert text states it explicitly."""
    match = re.fullmatch(r"(.+?)\s+(?:bei|at)\s+(.+)", value.strip(), flags=re.I)
    if not match:
        return value, ""
    return " ".join(match.group(1).split()), " ".join(match.group(2).split())


def _readable_pending_title(entry: LinkEntry, candidates: Sequence[str]) -> str:
    pasted = _clean_title(entry.title)
    joined = next((value for value in reversed(candidates) if value and " " not in value), "")
    if pasted:
        if joined and len(pasted.split()) >= 3:
            return joined[:1].upper() + joined[1:]
        return pasted
    title = candidates[0] if candidates else "Job title missing"
    if len(title.split()) >= 3 and candidates and " " not in candidates[-1]:
        title = candidates[-1]
    return title[:1].upper() + title[1:]


def title_match_ratio(wanted: str, candidate: str) -> float:
    wanted = GENDER_SUFFIX.sub(" ", INVISIBLE_TITLE.sub("", wanted))
    candidate = GENDER_SUFFIX.sub(" ", INVISIBLE_TITLE.sub("", candidate))
    reference = title_key(wanted)
    if not reference:
        return 0.0
    return len(reference & title_key(candidate)) / len(reference)


def _norm_place(value: str) -> str:
    return " ".join(value.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss").split())


def city_matches(wanted: str, candidate: str) -> bool:
    wanted_key = _norm_place(wanted)
    if not wanted_key:
        return False
    return any(wanted_key == _norm_place(part.strip()) for part in re.split(r"[;,/]", candidate) if part.strip())


def accept_match(wanted_title: str, wanted_city: str, candidate: JobStub) -> bool:
    return title_match_ratio(wanted_title, candidate.title) >= 0.8 and city_matches(wanted_city, candidate.location)


def _jsonld_job(soup: BeautifulSoup) -> Mapping[str, object]:
    def walk(value: object) -> Mapping[str, object] | None:
        if isinstance(value, Mapping):
            kind = value.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(item).casefold() == "jobposting" for item in kinds):
                return value
            for nested in value.values():
                found = walk(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = walk(nested)
                if found:
                    return found
        return None

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            found = walk(json.loads(script.string or script.get_text()))
        except (TypeError, ValueError):
            continue
        if found:
            return found
    return {}


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.select_one(f'meta[property="{name}"], meta[name="{name}"]')
        if node and node.get("content"):
            return str(node.get("content")).strip()
    return ""


def _organization(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or "").strip()
    return str(value or "").strip()


def _location(value: object) -> str:
    rows = value if isinstance(value, list) else [value]
    names = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        address = row.get("address") or {}
        name = str(address.get("addressLocality") or row.get("name") or "").strip() if isinstance(address, Mapping) else ""
        if name and name not in names:
            names.append(name)
    return "; ".join(names)


def extract_generic_job(page: str, url: str, *, fallback_title: str = "") -> JobAd:
    soup = BeautifulSoup(page, "lxml")
    data = _jsonld_job(soup)
    heading = soup.find("h1")
    title = str(data.get("title") or (heading.get_text(" ", strip=True) if heading else "") or _meta(soup, "og:title") or fallback_title).strip()
    employer = _organization(data.get("hiringOrganization")) or _meta(soup, "og:site_name")
    description = data.get("description")
    if description:
        text = BeautifulSoup(html.unescape(str(description)), "lxml").get_text("\n", strip=True)
    else:
        main = soup.find("main") or soup.find("article") or soup.body
        text = main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True)
    if not title or len(text) < 40:
        raise ValueError("the employer page contains no usable job title or text")
    employment = data.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(item) for item in employment)
    return JobAd(
        url=url, title=title, employer=employer, location=_location(data.get("jobLocation")),
        posted_date=str(data.get("datePosted") or ""), deadline=str(data.get("validThrough") or ""),
        full_text=text, employment_type=str(employment or "Vollzeit"), raw_meta={"manual_original_url": url},
    )


def employer_from_text(text: str, title_candidates: Sequence[str] = ()) -> str:
    match = EMPLOYER_LINE.search(text)
    if match:
        return " ".join(INVISIBLE_TITLE.sub("", match.group(1)).split())
    lines = [" ".join(INVISIBLE_TITLE.sub("", line).split()) for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    pasted_title = _clean_title(lines[0])
    candidates = [_clean_title(value) for value in title_candidates if _clean_title(value)]
    if candidates and max(title_match_ratio(candidate, pasted_title) for candidate in candidates) >= 0.8:
        # XING's copied advertisement begins with title, then employer. The value still comes directly
        # from the user's pasted text; without a matching title line the employer stays manual.
        return lines[1]
    return ""


def title_from_text(text: str, candidates: Sequence[str], fallback: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if lines:
        pasted = _clean_title(lines[0])
        cleaned = [_clean_title(value) for value in candidates if _clean_title(value)]
        if cleaned and max(title_match_ratio(candidate, pasted) for candidate in cleaned) >= 0.8:
            return pasted
    return _clean_title(fallback) or "Manual job"


def _manual_result(result: ScoreResult) -> ScoreResult:
    if result.band != "DROP":
        return result
    return replace(result, band="REVIEW", reason="manual_link_requires_review", drop_stage="")


def _score(ad: JobAd, config) -> ScoredJob:
    result = score_text(
        ad.title, ad.full_text, load_keywords(config.keywords_path), config.thresholds,
        employment_type=ad.employment_type, employer=ad.employer,
    )
    return ScoredJob(ad, "manual", _manual_result(result))


def _known_url_reason(url: str, tracker: Path, packages: Path) -> str:
    target = canonicalize_url(url)
    if tracker.is_file():
        from openpyxl import load_workbook
        workbook = load_workbook(tracker, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                headers = [str(value or "").strip() for value in next(rows, ())]
                indexes = [headers.index(name) for name in ("Link", "URL", "url") if name in headers]
                for row in rows:
                    if any(index < len(row) and row[index] and canonicalize_url(str(row[index])) == target for index in indexes):
                        return "duplicate tracker URL"
        finally:
            workbook.close()
    if packages.is_dir():
        for path in packages.glob("*/application.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            urls = [manifest.get(key) for key in ("url", "external_url", "application_url")]
            if any(value and canonicalize_url(str(value)) == target for value in urls):
                return "duplicate package URL"
    return ""


def _pending_id(url: str) -> str:
    return hashlib.sha1(canonicalize_url(url).encode("utf-8")).hexdigest()[:12]


def load_pending(path: str | Path) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [dict(row) for row in value if isinstance(row, Mapping)][:PENDING_LIMIT]
    for row in rows:
        candidates = [str(item) for item in row.get("candidates", [])]
        row["title"] = _readable_pending_title(
            LinkEntry("", str(row.get("url") or ""), str(row.get("title") or "")), candidates,
        )
        if row.get("reason") == "no 80% title and city match":
            row["reason"] = "not found on Arbeitsagentur or the employer feed"
    return rows


def save_pending(path: str | Path, rows: Sequence[Mapping[str, object]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(list(rows)[-PENDING_LIMIT:], ensure_ascii=False, indent=2), encoding="utf-8")


def load_drafts(path: str | Path) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): {"text": str(item.get("text") or ""), "employer": str(item.get("employer") or "")}
        for key, item in value.items() if isinstance(item, Mapping)
    }


def save_drafts(path: str | Path, drafts: Mapping[str, Mapping[str, str]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def load_manual_results(path: str | Path) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def save_manual_result(path: str | Path, item: ScoredJob) -> None:
    target = Path(path)
    rows = load_manual_results(target)
    row = {
        "title": item.ad.title, "employer": item.ad.employer, "url": item.ad.url,
        "location": item.ad.location, "band": item.result.band, "score": item.result.score,
        "employment_format": item.result.employment_format,
        "primary_cv_family": item.result.primary_cv_family,
        "date": date.today().isoformat(),
    }
    key = canonicalize_url(item.ad.url)
    rows = [existing for existing in rows if canonicalize_url(str(existing.get("url") or "")) != key]
    rows.append(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(rows[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def remove_manual_result(path: str | Path, url: str) -> dict[str, object]:
    """Remove one visible manual-result receipt, atomically, and return the removed row."""
    target = Path(path)
    rows = load_manual_results(target)
    key = canonicalize_url(url)
    matches = [row for row in rows if canonicalize_url(str(row.get("url") or "")) == key]
    if not matches:
        raise FileNotFoundError("Manual job result not found")
    kept = [row for row in rows if canonicalize_url(str(row.get("url") or "")) != key]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return matches[-1]


def complete_saved_draft(config, pending_path: str | Path, drafts_path: str | Path, pending_id: str) -> dict[str, str]:
    """Score one disk-backed draft, removing it only after the pending row was completed."""
    drafts = load_drafts(drafts_path)
    draft = drafts.get(pending_id)
    if draft is None:
        raise FileNotFoundError(f"saved manual text not found: {pending_id}")
    result = complete_pending(config, pending_path, pending_id, draft["text"], draft["employer"])
    drafts.pop(pending_id, None)
    save_drafts(drafts_path, drafts)
    return result


def _add_pending(
    path: Path, entry: LinkEntry, title: str, city: str, reason: str, employer: str = "",
    candidates: Sequence[str] = (),
) -> dict[str, object]:
    rows = load_pending(path)
    existing = next((row for row in rows if canonicalize_url(row.get("url", "")) == entry.url), None)
    if existing:
        return existing
    row = {
        "id": _pending_id(entry.url), "title": title, "city": city, "url": entry.url,
        "employer": employer, "candidates": list(candidates),
        "reason": reason, "date": date.today().isoformat(),
    }
    rows.append(row)
    save_pending(path, rows)
    return row


def search_arbeitsagentur(title: str, city: str, fetcher: Fetcher) -> JobAd | None:
    if not title or not city:
        return None
    search = f"https://www.arbeitsagentur.de/jobsuche/suche?angebotsart=1&was={quote(title)}&wo={quote(city)}"
    adapter = ArbeitsagenturAdapter()
    for stub in adapter.iter_listing(search, fetcher, 2):
        if accept_match(title, city, stub):
            return adapter.load_detail(stub, fetcher)
    return None


def search_company_feeds(title: str, city: str, employer: str, fetcher: Fetcher, companies_path: Path) -> JobAd | None:
    if not employer or not companies_path.is_file():
        return None
    wanted = employer_key(employer)
    for source in load_company_sources(companies_path, config.company_ats_location_terms):
        adapter = CompanyATSAdapter(source)
        if not same_employer(wanted, employer_key(adapter.company)):
            continue
        for stub in adapter.iter_listing(source, fetcher, None):
            if accept_match(title, city, stub):
                return adapter.load_detail(stub, fetcher)
    return None


def _manual_ad(matched: JobAd, entry: LinkEntry) -> JobAd:
    external = str(matched.raw_meta.get("external_final_url") or matched.raw_meta.get("external_url") or "")
    return replace(
        matched, url=entry.url,
        raw_meta=matched.raw_meta | {"external_url": external, "manual_original_url": entry.url},
    )


def process_links(
    config, links_path: str | Path, done_path: str | Path, pending_path: str | Path, *,
    fetcher: Fetcher | None = None,
    board_search: Callable[[str, str, Fetcher], JobAd | None] = search_arbeitsagentur,
    company_search: Callable[[str, str, str, Fetcher, Path], JobAd | None] = search_company_feeds,
) -> list[dict[str, str]]:
    links, done, pending_file = Path(links_path), Path(done_path), Path(pending_path)
    if not links.exists():
        return []
    raw_lines = links.read_text(encoding="utf-8-sig").splitlines()
    active = [line for line in raw_lines if line.strip() and not line.lstrip().startswith("#")]
    if not active:
        return []
    fetcher = fetcher or Fetcher(config)
    store = SeenStore(config.database_path)
    known = KnownPositions.load(config.application_archive_dir / "Bewerbungs_Tracker.xlsx", config.application_output_dir)
    pending_urls = {canonicalize_url(row.get("url", "")) for row in load_pending(pending_file)}
    scored: list[ScoredJob] = []
    accepted: list[tuple[JobAd, ScoredJob]] = []
    results: list[dict[str, str]] = []
    for line in active:
        try:
            entry = parse_link_line(line)
            assert entry is not None
        except Exception as error:
            results.append({"line": line, "result": f"error: {str(error)[:240]}"})
            continue
        try:
            reason = _known_url_reason(entry.url, config.application_archive_dir / "Bewerbungs_Tracker.xlsx", config.application_output_dir)
            if reason or store.is_seen(entry.url) or entry.url in pending_urls:
                result = reason or ("duplicate pending" if entry.url in pending_urls else "duplicate seen URL")
                results.append({"line": line, "result": result})
                continue
            if is_job_board(entry.url):
                raw_candidates, city = board_title_city(entry)
                employer_hint = ""
                candidates: list[str] = []
                for raw_candidate in raw_candidates:
                    candidate, hint = title_employer_hint(raw_candidate)
                    if hint and not employer_hint:
                        employer_hint = hint
                    if candidate and candidate.casefold() not in {value.casefold() for value in candidates}:
                        candidates.append(candidate)
                matched = None
                search_errors: list[Exception] = []
                for candidate in candidates[:3]:
                    try:
                        matched = board_search(candidate, city, fetcher)
                    except Exception as error:
                        search_errors.append(error)
                        matched = None
                    if matched is None and employer_hint:
                        try:
                            matched = company_search(candidate, city, employer_hint, fetcher, config.companies_path)
                        except Exception as error:
                            search_errors.append(error)
                            matched = None
                    if matched is not None:
                        break
                if search_errors:
                    LOGGER.info(
                        "Manual link search had %s adapter or fetch error(s); treating them as no match: %s",
                        len(search_errors), search_errors[0],
                    )
                if matched is None:
                    row = _add_pending(
                        pending_file, entry, _readable_pending_title(entry, candidates), city,
                        "not found on Arbeitsagentur or the employer feed",
                        employer_hint, candidates,
                    )
                    pending_urls.add(entry.url)
                    results.append({"line": line, "result": "needs ad text", "id": str(row["id"])})
                    continue
                ad = _manual_ad(matched, entry)
            else:
                try:
                    adapter = adapter_for_url(entry.url)
                except ValueError:
                    page = fetcher.get(entry.url, cache_detail=True)
                    ad = extract_generic_job(page.text, entry.url, fallback_title=entry.title)
                else:
                    ad = adapter.load_detail(JobStub(entry.url, entry.title), fetcher)
                    ad = _manual_ad(ad, entry)
            if not ad.employer:
                row = _add_pending(pending_file, entry, ad.title or entry.title, ad.location, "employer missing")
                pending_urls.add(entry.url)
                results.append({"line": line, "result": "needs ad text", "id": row["id"]})
                continue
            known_reason = known.match(ad.title, ad.employer)
            if known_reason or store.is_seen(entry.url, ad.title, ad.employer):
                results.append({"line": line, "result": known_reason or "duplicate seen job"})
                continue
            item = _score(ad, config)
            scored.append(item)
            accepted.append((ad, item))
            results.append({"line": line, "result": f"scored {item.result.band}", "band": item.result.band})
        except Exception as error:
            LOGGER.info("Manual link could not be read automatically; sending it to the paste queue: %s", error)
            candidates, city = board_title_city(entry) if is_job_board(entry.url) else ([entry.title], "")
            row = _add_pending(
                pending_file, entry, _readable_pending_title(entry, candidates), city,
                "could not read automatically", candidates=candidates,
            )
            pending_urls.add(entry.url)
            results.append({"line": line, "result": "needs ad text", "id": str(row["id"])})
    if scored:
        write_outputs(
            scored, config.output_dir, keywords=load_keywords(config.keywords_path),
            home_coordinates=config.home_coordinates,
        )
        for ad, item in accepted:
            store.mark_seen(ad.url, ad.title, ad.employer, "manual")
            save_manual_result(pending_file.with_name("manual_results.json"), item)
    done.parent.mkdir(parents=True, exist_ok=True)
    with done.open("a", encoding="utf-8") as handle:
        for row in results:
            handle.write(f"{date.today().isoformat()}\t{row['result']}\t{row['line']}\n")
    comments = [line for line in raw_lines if not line.strip() or line.lstrip().startswith("#")]
    links.write_text("\n".join(comments).rstrip() + ("\n" if comments else ""), encoding="utf-8")
    return results


def retry_failed_links(links_path: str | Path, done_path: str | Path) -> int:
    """Move prior input-line errors back to the intake file and keep all other history."""
    links, done = Path(links_path), Path(done_path)
    if not done.exists():
        return 0
    retry, keep = [], []
    for line in done.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[1].startswith("error:"):
            retry.append(parts[2])
        else:
            keep.append(line)
    if not retry:
        return 0
    existing = set(links.read_text(encoding="utf-8-sig").splitlines()) if links.exists() else set()
    moved = [line for line in retry if line not in existing]
    if moved:
        links.parent.mkdir(parents=True, exist_ok=True)
        with links.open("a", encoding="utf-8") as handle:
            for line in moved:
                handle.write(line + "\n")
    done.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return len(moved)


def append_links(path: str | Path, text: str) -> int:
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return 0
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")
    return len(lines)


def complete_pending(config, pending_path: str | Path, pending_id: str, text: str, employer: str = "") -> dict[str, str]:
    rows = load_pending(pending_path)
    row = next((item for item in rows if item.get("id") == pending_id), None)
    if row is None:
        raise FileNotFoundError("pending manual link not found")
    clean_text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(clean_text) < 80:
        raise ValueError("Paste the complete advertisement text.")
    candidates = [str(row.get("title") or ""), *(str(item) for item in row.get("candidates", []))]
    company = employer_from_text(clean_text, candidates) or " ".join(employer.split()) or str(row.get("employer") or "").strip()
    if not company:
        raise ValueError("Employer is missing. Add a line 'Unternehmen: ...' or type it in the employer field.")
    known = KnownPositions.load(config.application_archive_dir / "Bewerbungs_Tracker.xlsx", config.application_output_dir)
    reason = known.match(row.get("title"), company)
    seen = SeenStore(config.database_path)
    if not reason and seen.is_seen(str(row["url"]), str(row.get("title") or ""), company):
        reason = "duplicate seen job"
    if reason:
        rows.remove(row); save_pending(pending_path, rows)
        return {"result": reason, "band": ""}
    ad = JobAd(
        url=str(row["url"]), title=title_from_text(clean_text, candidates, str(row.get("title") or "")), employer=company,
        location=str(row.get("city") or ""), posted_date=date.today().isoformat(), full_text=clean_text,
        employment_type="Teilzeit" if re.search(r"(?i)\b(?:teilzeit|part[ -]?time)\b", clean_text) else "Vollzeit",
        raw_meta={"manual_original_url": str(row["url"])},
    )
    item = _score(ad, config)
    write_outputs(
        [item], config.output_dir, keywords=load_keywords(config.keywords_path),
        home_coordinates=config.home_coordinates,
    )
    seen.mark_seen(ad.url, ad.title, ad.employer, "manual")
    save_manual_result(Path(pending_path).with_name("manual_results.json"), item)
    rows.remove(row)
    save_pending(pending_path, rows)
    return {"result": f"scored {item.result.band}", "band": item.result.band, "title": ad.title, "employer": company}

