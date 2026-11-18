"""Read-only, sequential survey of employer application portals.

The survey never fills a field.  It may decline a cookie banner and follow one plain apply control
outside a form, then injects ``filler.js`` and calls ``describe()`` only.  Output is text-only and
bounded so it can be inspected later without retaining employer pages or personal data.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import yaml
from openpyxl import load_workbook


JOB_BOARDS = re.compile(
    r"(^|\.)(indeed|xing|linkedin|stepstone|arbeitsagentur|glassdoor|kununu|monster)\.", re.IGNORECASE,
)
NON_EMPLOYER_PORTALS = re.compile(
    r"(^|\.)(?:jobvector|finest-jobs|empfehlungsbund|radas|stellenanzeigen|stellenangebote|"
    r"stellenonline|yourfirm|jobware|joblift|jobrapido|kimeta|metajob|meinestadt|adzuna)\.",
    re.IGNORECASE,
)
APPLICATION_URL_HINT = re.compile(
    r"(?:^|[./_-])(?:bewerb\w*|apply|application|jobs?|careers?|karriere|stellenangebote?|"
    r"vacanc(?:y|ies)|positions?|recruit\w*)(?:[./_?&=\-]|$)", re.IGNORECASE,
)
NON_JOB_URL_HINT = re.compile(r"datenschutz|privacy|benefits?|diversity|culture|ansprechpartner", re.IGNORECASE)
ATS_HOSTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in (
        ("avature", r"(^|\.)avature\."),
        ("successfactors", r"(^|\.)successfactors\."),
        ("workday", r"(^|\.)(?:workday|myworkdayjobs)\."),
        ("personio", r"(^|\.)personio\."),
        ("softgarden", r"(^|\.)softgarden\."),
        ("greenhouse", r"(^|\.)(?:greenhouse|greenhouse\.io|boards\.greenhouse\.io)\."),
        ("lever", r"(^|\.)lever\.co$"),
        ("smartrecruiters", r"(^|\.)smartrecruiters\.com$"),
        ("join.com", r"(^|\.)join\.com$"),
        ("onlyfy", r"(^|\.)onlyfy\."),
        ("dvinci", r"(^|\.)dvinci\."),
        ("rexx", r"(^|\.)rexx\."),
        ("umantis", r"(^|\.)umantis\."),
        ("concludis", r"(^|\.)concludis\."),
        ("recruitee", r"(^|\.)recruitee\."),
        ("teamtailor", r"(^|\.)teamtailor\."),
        ("icims", r"(^|\.)icims\."),
        ("taleo", r"(^|\.)taleo\."),
        ("ashby", r"(^|\.)ashbyhq\."),
        ("helixjobs", r"(^|\.)helixjobs\."),
        ("bewerbermanagement", r"(^|\.)bewerbermanagement\."),
    )
)
KNOWN_ATS = {name for name, _ in ATS_HOSTS} | {"greenhouse", "lever", "ashby", "smartrecruiters"}
APPLY_TEXT = re.compile(r"\b(jetzt\s+bewerben|bewerben|bewerbung\s+starten|zur\s+bewerbung|apply(?:\s+now)?)\b", re.I)
COOKIE_DECLINE = re.compile(
    r"^(ablehnen|alle\s+ablehnen|nur\s+notwendige|nur\s+essenzielle|notwendige\s+cookies|"
    r"decline|reject(?:\s+all)?|only\s+necessary|essential\s+only)$", re.I,
)
MAX_JSONL_BYTES = 4_500_000
MAX_FIELDS = 750
LOGGER = logging.getLogger(__name__)
APPLIED_SURVEY_RULES = (
    ("Current location ✱", "city", "applied"),
    ("Select containing LinkedIn, Indeed, Stepstone and XING", "source", "applied"),
    ("Deutsch label containing Grundkenntnisse/fließend/verhandlungssicher/Muttersprache", "german_level", "applied"),
)
NEEDS_APPLICANT = (
    "Most Recent Employer*",
    "Most Recent Job Title*",
    "Will you now or in the future require immigration sponsorship for employment visa status?*",
    "Please indicate your gender identity",
    "What is your highest level of education?*",
    "In addition to fluency in German and English, do you speak any other languages fluently?*",
    "How often are you willing to travel for business purposes?*",
    "Current company",
    "Working Experience",
    "Relocation choice (Yes / ready to relocate / cannot relocate)",
    "How many years of experience do you have working with Workday? (if applicable)",
    "Do you have experience building automation workflows using n8n, UiPath, or similar tools?",
    "Programming and integration skill rating (1–5)",
    "Highest education, degree subject/title, marital status and vocational qualification fields",
)


@dataclass(frozen=True)
class Portal:
    host: str
    employer: str
    ats: str
    url: str
    score: float


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.").casefold()
    except ValueError:
        return ""


def is_job_board(host: str) -> bool:
    return bool(JOB_BOARDS.search(host.removeprefix("www.")))


def _embedded_application_urls(text: object) -> list[str]:
    urls = []
    for match in re.findall(r"https?://[^\s<>\"']+", str(text or ""), re.IGNORECASE):
        url = match.rstrip(".,);:]!?'")
        host = host_of(url)
        path = urlsplit(url).path.casefold()
        if not host or is_job_board(host) or NON_EMPLOYER_PORTALS.search(host):
            continue
        if path.endswith((".pdf", ".doc", ".docx")) or NON_JOB_URL_HINT.search(url):
            continue
        if APPLICATION_URL_HINT.search(url):
            urls.append(url)
    return list(dict.fromkeys(urls))


def _employer_tokens(employer: str) -> list[str]:
    generic = {"gmbh", "group", "holding", "deutschland", "germany", "company", "ag", "se", "co", "kg"}
    return [token for token in re.findall(r"[a-z0-9]+", employer.casefold()) if len(token) >= 3 and token not in generic]


def classify_ats(host: str, employer: str = "", hint: str = "") -> str:
    hint = hint.casefold().removeprefix("company:")
    if hint in KNOWN_ATS:
        return hint
    for name, pattern in ATS_HOSTS:
        if pattern.search(host):
            return name
    compact_host = re.sub(r"[^a-z0-9]", "", host.casefold())
    if any(token in compact_host for token in _employer_tokens(employer)):
        return "own site"
    if host.split(".", 1)[0] in {"career", "careers", "jobs", "job", "karriere", "stellen"}:
        return "own site"
    return "unknown"


def _company_hints(path: str | Path) -> dict[str, str]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    rows = raw.get("companies", []) if isinstance(raw, Mapping) else []
    return {
        str(row.get("name") or "").strip().casefold(): str(row.get("ats") or "").strip().casefold()
        for row in rows if isinstance(row, Mapping) and row.get("name") and row.get("ats")
    }


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def workbook_portals(paths: Iterable[str | Path], companies_path: str | Path) -> list[Portal]:
    """Highest-scoring distinct employer/application host from scored workbooks."""
    hints = _company_hints(companies_path)
    best: dict[str, Portal] = {}
    for path_value in paths:
        path = Path(path_value)
        if not path.is_file():
            continue
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                headers = [str(value or "").strip() for value in next(rows, ())]
                if not headers or "url" not in headers:
                    continue
                index = {name: position for position, name in enumerate(headers)}
                for row in rows:
                    def get(name: str) -> object:
                        position = index.get(name)
                        return row[position] if position is not None and position < len(row) else ""
                    employer = str(get("employer") or "").strip()
                    board = str(get("board") or "").strip().casefold()
                    hint = hints.get(employer.casefold()) or board
                    external = str(get("external_url") or "").strip()
                    original = str(get("url") or "").strip()
                    urls = []
                    if external and not is_job_board(host_of(external)):
                        urls.append(external)
                    if original and not is_job_board(host_of(original)):
                        urls.append(original)
                    urls.extend(_embedded_application_urls(get("full_text")))
                    for url in dict.fromkeys(urls):
                        host = host_of(url)
                        if (not host or is_job_board(host) or NON_EMPLOYER_PORTALS.search(host)
                                or urlsplit(url).scheme not in {"http", "https"}):
                            continue
                        portal = Portal(host, employer, classify_ats(host, employer, hint), url, _number(get("score")))
                        previous = best.get(host)
                        if previous is None or portal.score > previous.score:
                            best[host] = portal
        finally:
            workbook.close()
    return sorted(best.values(), key=lambda portal: (-portal.score, portal.host))


def choose_portals(candidates: Sequence[Portal], *, target: int = 55) -> list[Portal]:
    if not 50 <= target <= 60:
        raise ValueError("portal target must be between 50 and 60")
    groups: dict[str, list[Portal]] = defaultdict(list)
    for portal in candidates:
        groups[portal.ats].append(portal)
    selected: list[Portal] = []
    used: set[str] = set()
    ats_counts: Counter[str] = Counter()

    def add(portal: Portal) -> bool:
        if portal.host in used:
            return False
        if portal.ats in KNOWN_ATS and ats_counts[portal.ats] >= 6:
            return False
        selected.append(portal)
        used.add(portal.host)
        ats_counts[portal.ats] += 1
        return True

    for portal in groups.get("own site", [])[:10]:
        add(portal)
    ordered_groups = [name for name in sorted(groups) if name != "own site"] + ["own site"]
    position = 0
    while len(selected) < target:
        changed = False
        for name in ordered_groups:
            rows = groups[name]
            if position < len(rows):
                changed = add(rows[position]) or changed
                if len(selected) >= target:
                    break
        if not changed and all(position + 1 >= len(groups[name]) for name in ordered_groups):
            break
        position += 1
    if len(selected) < target:
        for portal in candidates:
            add(portal)
            if len(selected) >= target:
                break
    return sorted(selected, key=lambda portal: (-portal.score, portal.host))


def write_portals(path: str | Path, portals: Sequence[Portal]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["host", "employer", "ATS", "example job URL", "score"])
        for portal in portals:
            writer.writerow([portal.host, portal.employer, portal.ats, portal.url, f"{portal.score:g}"])
    return target


def read_portals(path: str | Path) -> list[Portal]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [
            Portal(
                str(row.get("host") or "").strip().casefold(), str(row.get("employer") or "").strip(),
                str(row.get("ATS") or "unknown").strip(), str(row.get("example job URL") or "").strip(),
                _number(row.get("score")),
            )
            for row in csv.DictReader(handle) if row.get("host") and row.get("example job URL")
        ]


def _visible_state(page) -> dict[str, int]:
    parts = page.frames
    visible = total = forms = 0
    for frame in parts:
        try:
            state = frame.evaluate("""() => {
              const controls = [...document.querySelectorAll('input,select,textarea')];
              const shown = controls.filter((el) => { const s=getComputedStyle(el), b=el.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && b.width>1 && b.height>1; });
              return {total: controls.length, visible: shown.length, forms: document.querySelectorAll('form').length};
            }""")
        except Exception:
            continue
        visible += int(state["visible"]); total += int(state["total"]); forms += int(state["forms"])
    return {"visible": visible, "total": total, "forms": forms}


def _click_cookie_decline(page) -> bool:
    for frame in page.frames:
        try:
            controls = frame.locator("button, [role=button], a")
            for index in range(min(controls.count(), 80)):
                control = controls.nth(index)
                text = " ".join((control.inner_text(timeout=500) or "").split())
                if control.is_visible() and COOKIE_DECLINE.fullmatch(text):
                    control.click(timeout=2000)
                    return True
        except Exception:
            continue
    return False


def _apply_control(page) -> tuple[object | None, str, str]:
    """Return (locator, href, text); a control inside a form is never eligible."""
    for frame in page.frames:
        try:
            controls = frame.locator("a[href], button, [role=button]")
            for index in range(min(controls.count(), 200)):
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                text = " ".join((control.inner_text(timeout=500) or control.get_attribute("aria-label") or "").split())
                if len(text) > 80 or not APPLY_TEXT.search(text):
                    continue
                if control.evaluate("el => !!el.closest('form')"):
                    continue
                if (control.get_attribute("type") or "").casefold() == "submit":
                    continue
                return control, str(control.get_attribute("href") or ""), text
        except Exception:
            continue
    return None, "", ""


def _wall_type(page, initial_host: str, *, email_only: bool = False) -> str:
    final = host_of(page.url)
    texts, password, captcha = [], False, False
    for frame in page.frames:
        try:
            texts.append((frame.locator("body").inner_text(timeout=1000) or "")[:12000])
            password = password or frame.locator('input[type="password"]').count() > 0
            captcha = captcha or frame.locator(
                'iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="turnstile"],.g-recaptcha,.h-captcha,[class*="captcha" i],[id*="captcha" i]'
            ).count() > 0
        except Exception:
            continue
    haystack = f"{page.url}\n{page.title()}\n{' '.join(texts)}".casefold()
    if captcha:
        return "captcha"
    if re.search(r"registrier|registration|create\s+(?:an\s+)?account|sign\s*up", haystack):
        return "register"
    if password or re.search(r"\blog\s*in\b|\bsign\s*in\b|anmelden", haystack):
        return "login"
    if email_only:
        return "email-only"
    if final and initial_host and final != initial_host and not final.endswith(f".{initial_host}") and not initial_host.endswith(f".{final}"):
        return "external redirect"
    return "none"


def _describe(page, filler_js: str) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    for frame in page.frames:
        try:
            frame.evaluate(filler_js)
            described = frame.evaluate("() => window.__jfFiller.describe()")
        except Exception:
            continue
        for field in described or []:
            options = [str(value)[:160] for value in field.get("options", [])[:15]]
            fields.append({
                "label": str(field.get("label") or "")[:300], "kind": str(field.get("kind") or ""),
                "key": field.get("key") or None, "required": bool(field.get("required")),
                "option_count": int(field.get("optionCount") or len(field.get("options", []))),
                "options": options if field.get("kind") == "select" else [],
            })
            if len(fields) >= MAX_FIELDS:
                return fields
    return fields


def _has_multistep_control(page) -> bool:
    """Inspect labels only; never activate a step control."""
    pattern = re.compile(r"^(next|continue|weiter|fortfahren|naechster schritt|nächster schritt)(?:\s*[>›»])?$", re.I)
    for frame in page.frames:
        try:
            controls = frame.locator("button, [role=button], a")
            for index in range(min(controls.count(), 200)):
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                label = " ".join((control.inner_text(timeout=500) or control.get_attribute("aria-label") or "").split())
                if pattern.fullmatch(label):
                    return True
        except Exception:
            continue
    return False


def survey_portal(
    browser, portal: Portal, *, filler_js: str, rules_js: str, timeout_seconds: float = 30.0,
) -> dict[str, object]:
    if is_job_board(portal.host) or is_job_board(host_of(portal.url)):
        target = urlsplit(portal.url)
        return {
            "host": portal.host, "employer": portal.employer, "ATS": portal.ats,
            "final_host": (target.hostname or "").removeprefix("www.").casefold(),
            "final_path": target.path or "/", "steps": ["blocked excluded job board"],
            "page_loads": 0, "wall_type": "error", "error": "excluded job board",
            "hidden_until_click": False, "multi_step": False, "frames": 0,
            "looks_like_application": False, "fields": [],
        }
    started = time.monotonic()
    page = browser.new_page()
    page.set_default_timeout(3000)
    steps = ["open job URL"]
    loads = 1
    email_only = hidden_until_click = multi_step = False
    try:
        page.goto(portal.url, wait_until="domcontentloaded", timeout=max(1000, int(timeout_seconds * 1000)))
        if _click_cookie_decline(page):
            steps.append("decline cookies")
        before = _visible_state(page)
        control, href, text = _apply_control(page)
        if control is not None and time.monotonic() - started < timeout_seconds:
            absolute = urljoin(page.url, href) if href else ""
            target_host = host_of(absolute)
            if absolute.casefold().startswith("mailto:"):
                email_only = True
                steps.append("email apply link observed")
            elif target_host and is_job_board(target_host):
                steps.append("apply target is excluded job board")
            elif href and urlsplit(absolute).scheme in {"http", "https", "file"}:
                page.goto(absolute, wait_until="domcontentloaded", timeout=max(1000, int((timeout_seconds - (time.monotonic() - started)) * 1000)))
                loads += 1
                steps.append(f"follow apply link: {text[:60]}")
            else:
                page_url_before_click = page.url
                control.click(timeout=max(500, int((timeout_seconds - (time.monotonic() - started)) * 1000)))
                steps.append(f"reveal apply form: {text[:60]}")
                page.wait_for_timeout(400)
                if page.url != page_url_before_click:
                    loads += 1
        after = _visible_state(page)
        hidden_until_click = len(steps) > 1 and before["visible"] == 0 and after["visible"] > 0
        try:
            page.evaluate(rules_js)
            recognition = bool(page.evaluate("() => window.JF.looksLikeApplication(location.href, document.title)"))
        except Exception:
            recognition = False
        fields = _describe(page, filler_js)
        multi_step = _has_multistep_control(page) or any(
            re.search(r"\b(next|continue|weiter|fortfahren|schritt\s+\d+)\b", str(value), re.I)
            for field in fields for value in [field["label"]]
        )
        final = urlsplit(page.url)
        wall = _wall_type(page, portal.host, email_only=email_only)
        return {
            "host": portal.host, "employer": portal.employer, "ATS": portal.ats,
            "final_host": (final.hostname or "").removeprefix("www.").casefold(), "final_path": final.path or "/",
            "steps": steps, "page_loads": loads, "wall_type": wall,
            "hidden_until_click": hidden_until_click, "multi_step": multi_step,
            "frames": len(page.frames), "looks_like_application": recognition, "fields": fields,
        }
    except Exception as error:
        final = urlsplit(page.url or portal.url)
        return {
            "host": portal.host, "employer": portal.employer, "ATS": portal.ats,
            "final_host": (final.hostname or "").removeprefix("www.").casefold(), "final_path": final.path or "/",
            "steps": steps, "page_loads": loads, "wall_type": "error", "error": str(error)[:500],
            "hidden_until_click": hidden_until_click, "multi_step": multi_step,
            "frames": len(page.frames), "looks_like_application": False, "fields": [],
        }
    finally:
        page.close()


def _completed_hosts(path: Path) -> set[str]:
    if not path.exists():
        return set()
    hosts = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("host"):
            hosts.add(str(record["host"]).casefold())
    return hosts


def run_survey(
    portals_path: str | Path, output_path: str | Path, extension_dir: str | Path, *,
    delay_min: float = 5.0, delay_max: float = 10.0, timeout_seconds: float = 30.0,
) -> dict[str, int]:
    if delay_min < 0 or delay_max < delay_min:
        raise ValueError("survey delays must satisfy 0 <= delay_min <= delay_max")
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ValueError("survey timeout must be between 0 and 30 seconds")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = _completed_hosts(output)
    portals = [portal for portal in read_portals(portals_path) if portal.host not in completed]
    if not portals:
        return {"written": 0, "skipped": len(completed), "errors": 0}
    filler_js = (Path(extension_dir) / "filler.js").read_text(encoding="utf-8")
    rules_js = (Path(extension_dir) / "rules.js").read_text(encoding="utf-8")
    local_browsers = Path(sys.prefix) / "playwright-browsers"
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and local_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is required for 'jobfilter survey'. Install the survey dependency in .venv.") from error
    written = errors = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for index, portal in enumerate(portals):
                if index:
                    time.sleep(random.uniform(delay_min, delay_max))
                LOGGER.info("Survey portal %s/%s: %s", index + 1, len(portals), portal.host)
                record = survey_portal(
                    browser, portal, filler_js=filler_js, rules_js=rules_js, timeout_seconds=timeout_seconds,
                )
                line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                if output.exists() and output.stat().st_size + len(line) > MAX_JSONL_BYTES:
                    raise RuntimeError("survey.jsonl reached its 4.5 MB safety cap")
                with output.open("ab") as handle:
                    handle.write(line)
                written += 1
                errors += record["wall_type"] == "error"
                LOGGER.info(
                    "Survey result %s: wall=%s, fields=%s, loads=%s",
                    portal.host, record["wall_type"], len(record["fields"]), record["page_loads"],
                )
        finally:
            browser.close()
    return {"written": written, "skipped": len(completed), "errors": errors}


def load_survey(path: str | Path) -> list[dict[str, object]]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines() if Path(path).exists() else []:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _reported_key(field: Mapping[str, object], upload_index: int) -> str:
    """Reflect deterministic upload routing when analysing an older read-only record."""
    key = str(field.get("key") or "")
    if key or field.get("kind") != "file":
        return key
    return "cv" if upload_index == 0 else "attachments"


def write_report(jsonl_path: str | Path, report_path: str | Path) -> Path:
    records = load_survey(jsonl_path)
    per_ats: dict[str, Counter[str]] = defaultdict(Counter)
    unknown: Counter[str] = Counter()
    walls: dict[str, Counter[str]] = defaultdict(Counter)
    uploads: Counter[str] = Counter()
    hidden, multi, misses = [], [], []
    for record in records:
        ats = str(record.get("ATS") or "unknown")
        fields = list(record.get("fields") or [])
        per_ats[ats]["portals"] += 1
        walls[ats][str(record.get("wall_type") or "none")] += 1
        upload_index = 0
        for field in fields:
            per_ats[ats]["fields"] += 1
            effective_key = _reported_key(field, upload_index)
            if field.get("kind") == "file":
                upload_index += 1
            if effective_key:
                per_ats[ats]["recognised"] += 1
            else:
                unknown[str(field.get("label") or "(empty label)").strip()] += 1
            if field.get("kind") == "file":
                uploads[str(field.get("label") or "(empty label)").strip()] += 1
        if record.get("hidden_until_click"):
            hidden.append(str(record.get("host")))
        if record.get("multi_step"):
            multi.append(str(record.get("host")))
        if fields and not record.get("looks_like_application"):
            misses.append(str(record.get("host")))
    walls_seen = sorted({wall for counter in walls.values() for wall in counter})
    lines = [
        "# Employer application portal survey", "", f"Prepared: {time.strftime('%Y-%m-%d')}", "",
        "## Executive Summary", "",
        f"The read-only survey completed {len(records)} distinct portal records. "
        f"{sum(counter['fields'] for counter in per_ats.values())} form fields were described without entering values.",
        "",
        "## Field recognition by ATS", "",
        "| ATS | Portals | Fields | Recognised | Share |", "|---|---:|---:|---:|---:|",
    ]
    for ats in sorted(per_ats):
        counter = per_ats[ats]
        share = counter["recognised"] / counter["fields"] if counter["fields"] else 0
        lines.append(f"| {ats} | {counter['portals']} | {counter['fields']} | {counter['recognised']} | {share:.1%} |")
    lines += [
        "", "The shares include the current deterministic routing for generic upload slots. "
        "Report-only mode cannot recover labels that the historical JSONL stored as `text`; "
        "the corrected extraction is covered by the two local Greenhouse fixtures.",
    ]
    lines += ["", "## Most frequent unrecognised labels", "", "| Label | Count |", "|---|---:|"]
    lines += ["| " + label.replace("|", "\\|") + f" | {count} |" for label, count in unknown.most_common(40)]
    lines += ["", "## Wall types by ATS", "", "| ATS | " + " | ".join(walls_seen) + " |", "|---|" + "---:|" * len(walls_seen)]
    for ats in sorted(walls):
        lines.append("| " + ats + " | " + " | ".join(str(walls[ats][wall]) for wall in walls_seen) + " |")
    lines += ["", "## Upload field labels", ""]
    lines += [f"- {label}: {count}" for label, count in uploads.most_common()] or ["- None observed."]
    lines += ["", "## Hidden and multi-step patterns", "",
              f"- Hidden until the allowed apply reveal: {len(hidden)} portal(s): {', '.join(hidden) or 'none'}.",
              f"- Multi-step indicators: {len(multi)} portal(s): {', '.join(multi) or 'none'}.",
              "", "## Recognition misses", "",
              f"A visible form was found while `looksLikeApplication()` was false on {len(misses)} portal(s): {', '.join(misses) or 'none'}.",
              "The exact surveyed employer hosts `commerzbank.de`, `dsv.com`, and `gpanrw.de` are now recognised without broadening generic `/karriere` matching. "
              "ATS host recognition was also extended for Ashby, HelixJobs, and Bewerbermanagement.",
              "", "## Deterministic rule proposals", "",
              "Rules are applied only after a surveyed label is reviewed against an existing profile key.", "",
              "| Surveyed label pattern | Existing profile key | Status |", "|---|---|---|",
              *["| " + label.replace("|", "\\|") + f" | `{key}` | {status} |" for label, key, status in APPLIED_SURVEY_RULES], "",
              "## Needs applicant input", "",
              "Unrecognised questions that require a new personal fact, choice, or legal declaration remain manual and are not added to filler rules.", "",
              *[f"- {label}" for label in NEEDS_APPLICANT],
              "", "## Scope", "",
              "The survey was read-only and sequential. It stored no field values, profile data, screenshots, or HTML. Error records include the observed failure only; bot detection was not bypassed."]
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def run_command(
    *, portals_path: str | Path, output_dir: str | Path, extension_dir: str | Path,
    workbooks: Sequence[str | Path] = (), companies_path: str | Path = "companies.yaml",
    select_only: bool = False, report_only: bool = False, limit: int = 55,
    delay_min: float = 5.0, delay_max: float = 10.0, timeout_seconds: float = 30.0,
) -> dict[str, object]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    portal_file = Path(portals_path)
    if workbooks:
        selected = choose_portals(workbook_portals(workbooks, companies_path), target=limit)
        write_portals(portal_file, selected)
    if select_only:
        return {"selected": len(read_portals(portal_file)), "portals": portal_file}
    jsonl = destination / "survey.jsonl"
    result = {"written": 0, "skipped": 0, "errors": 0}
    if not report_only:
        result = run_survey(
            portal_file, jsonl, extension_dir, delay_min=delay_min, delay_max=delay_max,
            timeout_seconds=timeout_seconds,
        )
    report = write_report(jsonl, destination / "SURVEY_REPORT.md")
    return result | {"report": report, "jsonl": jsonl, "portals": portal_file}
