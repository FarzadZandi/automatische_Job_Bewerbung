"""Keep the CV "Projekte" section in step with the applicant's public GitHub repositories.

The repository list comes from the free GitHub REST API (public data only, no key,
60 requests per hour per IP). A sync costs one request; repeats within the cache
period cost none, and an unchanged list is answered with HTTP 304. No model or paid
API is involved. The HTML profile page is not read: github.com disallows robots there.

German CV lines are never generated. They live in a curated catalog
(``Job App Docs/github_projekte.yaml``); a repository without a verified line is
never placed. New repositories are appended to the catalog as FEHLT stubs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests
import yaml

from .scoring import normalize

API_URL = "https://api.github.com/users/{user}/repos"
MISSING = "[FEHLT: Bewerber muss dies liefern]"
MAX_CV_LINE_CHARS = 200  # two lines in the CV template, so placing a project never adds a page
USER_AGENT = "jobfilter-github-sync"


@dataclass(frozen=True)
class Project:
    repo: str
    title: str
    cv_line: str
    url: str = ""
    families: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    use: bool = False
    language: str = ""
    topics: tuple[str, ...] = ()
    pushed_at: str = ""
    on_github: bool = True

    @property
    def placeable(self) -> bool:
        return self.use and self.on_github and bool(self.cv_line) and MISSING not in self.cv_line


@dataclass
class SyncReport:
    user: str
    fetched: bool = False
    not_modified: bool = False
    repos: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: str = ""

    def lines(self) -> list[str]:
        if self.error:
            head = f"GitHub sync for {self.user} failed: {self.error}. Using the cached list."
        elif self.not_modified:
            head = f"GitHub: {len(self.repos)} public repositories, unchanged since the last sync."
        else:
            head = f"GitHub: {len(self.repos)} public repositories synced."
        lines = [head]
        if self.new:
            lines.append(
                "New repositories added to the catalog without a CV line (not placed until you write one): "
                + ", ".join(self.new)
            )
        if self.missing:
            lines.append("In the catalog but no longer public on GitHub (not placed): " + ", ".join(self.missing))
        return lines


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def cache_age(path: str | Path, *, now: datetime | None = None) -> timedelta | None:
    cache = _read_cache(Path(path))
    try:
        fetched = datetime.fromisoformat(str(cache["fetched_at"]))
    except (KeyError, ValueError):
        return None
    return (now or datetime.now(timezone.utc)) - fetched


def _catalog_entries(path: Path) -> dict[str, Mapping[str, Any]]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    projects = raw.get("projects") if isinstance(raw, Mapping) else None
    if projects is None:
        return {}
    if not isinstance(projects, Mapping):
        raise ValueError(f"{path}: 'projects' must be a mapping of repository names")
    return {str(name): (entry or {}) for name, entry in projects.items()}


def _stub(repo: Mapping[str, Any]) -> str:
    name = str(repo["name"])
    description = " ".join(str(repo.get("description") or "").split())[:160]
    language = str(repo.get("language") or "")
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        f"  {name}:\n"
        f"    # Neu seit Sync {today}. GitHub-Beschreibung: {description or 'keine'}. Sprache: {language or 'unbekannt'}.\n"
        f"    title: {json.dumps(name)}\n"
        f"    cv_line: {json.dumps(MISSING)}\n"
        "    families: []\n"
        "    keywords: []\n"
        "    use: false\n"
    )


def sync_github(
    user: str,
    *,
    cache_path: str | Path,
    catalog_path: str | Path,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> SyncReport:
    """Refresh the cached public repository list and append stubs for new repositories.

    Network or API errors never raise: the report carries the error and the
    existing cache stays in use.
    """
    report = SyncReport(user=user)
    cache_file, catalog_file = Path(cache_path), Path(catalog_path)
    cache = _read_cache(cache_file)
    http = session or requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if cache.get("etag") and cache.get("user") == user:
        headers["If-None-Match"] = str(cache["etag"])
    repos: list[dict[str, Any]] = []
    url: str | None = API_URL.format(user=user) + "?type=owner&sort=pushed&per_page=100"
    etag = ""
    try:
        first = True
        while url:
            response = http.get(url, headers=headers if first else {k: v for k, v in headers.items() if k != "If-None-Match"}, timeout=timeout)
            if first and response.status_code == 304:
                report.not_modified = True
                repos = list(cache.get("repos") or [])
                break
            if response.status_code != 200:
                try:
                    detail = str(response.json().get("message") or "")
                except (ValueError, AttributeError):
                    detail = ""
                raise RuntimeError(f"HTTP {response.status_code}: {(detail or response.text)[:200].rstrip('.')}")
            if first:
                etag = response.headers.get("ETag", "")
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("unexpected response from the GitHub API")
            repos.extend(payload)
            url = response.links.get("next", {}).get("url")
            first = False
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        report.error = str(exc)
        report.repos = [str(repo.get("name")) for repo in cache.get("repos") or []]
        return report

    public = [
        {
            "name": str(repo.get("name") or ""),
            "html_url": str(repo.get("html_url") or ""),
            "description": str(repo.get("description") or ""),
            "language": str(repo.get("language") or ""),
            "topics": list(repo.get("topics") or []),
            "pushed_at": str(repo.get("pushed_at") or ""),
            "archived": bool(repo.get("archived")),
        }
        for repo in repos
        if repo.get("name") and not repo.get("private") and not repo.get("fork")
    ]
    report.fetched = not report.not_modified
    report.repos = [repo["name"] for repo in public]
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "user": user,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "etag": etag or cache.get("etag", ""),
        "repos": public,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    catalog = _catalog_entries(catalog_file)
    new = [repo for repo in public if repo["name"] not in catalog]
    if new:
        if not catalog_file.exists():
            catalog_file.parent.mkdir(parents=True, exist_ok=True)
            catalog_file.write_text("projects:\n", encoding="utf-8")
        text = catalog_file.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        catalog_file.write_text(text + "".join(_stub(repo) for repo in new), encoding="utf-8")
        report.new = [repo["name"] for repo in new]
    report.missing = sorted(set(catalog) - set(report.repos))
    return report


def load_projects(catalog_path: str | Path, cache_path: str | Path | None = None) -> list[Project]:
    """Merge the curated catalog with the cached GitHub metadata, in catalog order."""
    catalog = _catalog_entries(Path(catalog_path))
    cache = _read_cache(Path(cache_path)) if cache_path else {}
    cached = {str(repo.get("name")): repo for repo in cache.get("repos") or []}
    has_cache = bool(cache.get("repos"))
    projects = []
    for name, entry in catalog.items():
        meta = cached.get(name, {})
        projects.append(Project(
            repo=name,
            title=str(entry.get("title") or name).strip(),
            cv_line=" ".join(str(entry.get("cv_line") or "").split()),
            url=str(meta.get("html_url") or entry.get("url") or ""),
            families=tuple(str(value).upper() for value in entry.get("families") or ()),
            keywords=tuple(dict.fromkeys(
                normalize(str(value)) for value in entry.get("keywords") or () if str(value).strip()
            )),
            use=bool(entry.get("use", False)),
            language=str(meta.get("language") or entry.get("language") or ""),
            topics=tuple(normalize(str(value)) for value in meta.get("topics") or ()),
            pushed_at=str(meta.get("pushed_at") or ""),
            on_github=(name in cached) if has_cache else True,
        ))
    return projects


def project_problems(project: Project) -> list[str]:
    """Mechanical checks for one CV line that may be placed (empty list when it passes)."""
    from .paragraph_bank import BANNED_PHRASES, FORBIDDEN_SKILLS, _dash_problems

    if not project.use:
        return []
    label = project.repo
    if not project.cv_line or MISSING in project.cv_line:
        return [f"{label}: use is true but the CV line is missing"]
    problems = []
    if len(project.cv_line) > MAX_CV_LINE_CHARS:
        problems.append(f"{label}: CV line has {len(project.cv_line)} characters, maximum {MAX_CV_LINE_CHARS}")
    if not project.cv_line.startswith(project.title + ":"):
        problems.append(f"{label}: CV line must start with the title followed by a colon")
    lowered = project.cv_line.casefold()
    problems.extend(f"{label}: banned phrase '{phrase}'" for phrase in BANNED_PHRASES if phrase in lowered)
    for pattern in FORBIDDEN_SKILLS:
        match = re.search(pattern, lowered)
        if match:
            problems.append(f"{label}: '{match.group(0)}' is Grundkenntnisse only")
    problems.extend(f"{label}: {problem}" for problem in _dash_problems(project.cv_line))
    return problems


def catalog_problems(projects: Iterable[Project]) -> list[str]:
    return [problem for project in projects for problem in project_problems(project)]


def _term_in(term: str, text: str) -> bool:
    """Short alphanumeric terms must be whole tokens; longer terms must start a word.

    Suffixes are allowed ("filial" finds "filialen"), but a term never matches inside
    another word, so "werbung" does not find "bewerbung" and "klima" does not find
    "betriebsklima". Compounds that matter belong in the keyword list themselves.
    """
    if len(term) <= 3 and re.fullmatch(r"[a-z0-9]+", term):
        return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None
    return re.search(rf"(?<!\w){re.escape(term)}", text) is not None


def _family(value: str) -> str:
    match = re.search(r"\bB(0[1-9]|10)\b", value or "", re.IGNORECASE)
    return f"B{match.group(1)}" if match else ""


def score_project(project: Project, *, title: str, body: str, primary: str, secondary: str) -> tuple[float, list[str]]:
    """Relevance of one project to one job; returns the score and the reasons."""
    score, reasons = 0.0, []
    if primary and primary in project.families:
        score += 4.0 if project.families[0] == primary else 3.0
        reasons.append(f"family {primary}")
    if secondary and secondary in project.families:
        score += 1.5
        reasons.append(f"family {secondary}")
    terms = tuple(dict.fromkeys(term for term in (normalize(t) for t in project.keywords + project.topics) if term))
    title_hits = [term for term in terms if _term_in(term, title)]
    body_hits = [term for term in terms if term not in title_hits and _term_in(term, body)]
    score += 2.0 * len(title_hits) + min(len(body_hits), 5)
    if title_hits or body_hits:
        reasons.append("keywords " + ", ".join((title_hits + body_hits)[:6]))
    if project.language.casefold() == "python" and _term_in("python", body):
        score += 0.5
    return score, reasons


def select_projects(
    projects: Sequence[Project],
    *,
    slots: int,
    job_title: str,
    job_text: str,
    primary_family: str,
    secondary_family: str = "",
    pinned_text: str = "",
) -> list[tuple[Project, str]]:
    """Pick projects for the CV slots: projects named in the letter first, then by relevance."""
    if slots <= 0:
        return []
    candidates = [project for project in projects if project.placeable]
    title, body = normalize(job_title), normalize(job_text)
    primary, secondary = _family(primary_family), _family(secondary_family)
    pinned_lower = pinned_text.casefold()
    chosen: list[tuple[Project, str]] = []
    for project in candidates:
        if project.title and project.title.casefold() in pinned_lower:
            chosen.append((project, "named in the cover letter"))
    ranked = []
    for order, project in enumerate(candidates):
        if any(project is picked for picked, _ in chosen):
            continue
        score, reasons = score_project(project, title=title, body=body, primary=primary, secondary=secondary)
        ranked.append((-score, order, project, "; ".join(reasons) or "catalog order"))
    ranked.sort(key=lambda item: (item[0], item[1]))
    chosen.extend((project, reason) for _, _, project, reason in ranked)
    return chosen[:slots]
