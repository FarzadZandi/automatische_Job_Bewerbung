from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import unicodedata
import webbrowser
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import yaml
from lxml import etree
from openpyxl import load_workbook

from .github_projects import Project, select_projects
from .store import canonicalize_url
from .known import KnownPositions
from .safeio import atomic_write_json


APPLICATION_SHEET_ORDER = ("PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW")
FULL_TIME_VALUE = "FULL-TIME / VOLLZEIT"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": WORD_NS}
MISSING_EVIDENCE = "[FEHLT: Bewerber muss dies liefern]"
APPLICATION_STATUSES = (
    "needs_hook", "not_submitted", "approved", "opened", "submitted",
    "skipped", "failed", "expired",
)
MANIFEST_VERSION = 2
# Target status -> statuses it may be reached from. "needs_hook" is set only by
# prepare-applications, never by hand.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "approved": frozenset({"not_submitted"}),
    "opened": frozenset({"approved"}),
    "submitted": frozenset({"approved", "opened"}),
    "failed": frozenset({"approved", "opened"}),
    # "submitted" is accepted only as an explicit correction of a mistaken
    # confirmation. The app asks for confirmation before using this path.
    "not_submitted": frozenset({"needs_hook", "opened", "failed", "skipped", "submitted"}),
    "skipped": frozenset({"needs_hook", "not_submitted", "approved", "opened", "failed"}),
    "expired": frozenset({"needs_hook", "not_submitted", "approved", "opened", "failed"}),
    "needs_hook": frozenset(),
}
LEGACY_ALLOWED_STATUSES = frozenset({"expired", "skipped"})
PROTECTED_STATUSES = frozenset({"opened", "submitted"})


def is_legacy_manifest(manifest: Mapping[str, object]) -> bool:
    """Packages built before the evidence-only letter builder must not be sent."""
    if int(_number(manifest.get("manifest_version"))) >= MANIFEST_VERSION:
        return False
    return (
        "application_url" not in manifest
        or _text(manifest.get("submission_mode")) == "manual_or_authorized_integration"
    )


@dataclass(frozen=True)
class ApplicationJob:
    queue_rank: int
    source_sheet: str
    score: float
    band: str
    title: str
    employer: str
    location: str
    posted: str
    deadline: str
    board: str
    url: str
    external_url: str
    primary_cv_family: str
    secondary_cv_family: str
    family_hits: str
    analysis_task_hits: str
    method_hits: str
    tool_hits: str
    language_flags: str
    full_text: str

    @property
    def application_url(self) -> str:
        return self.external_url or self.url


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


GERMAN_MONTHS = {
    "januar": 1, "jan": 1, "jaenner": 1, "februar": 2, "feb": 2,
    "marz": 3, "maerz": 3, "mar": 3, "maer": 3, "mrz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6,
    "juli": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11,
    "dezember": 12, "dez": 12,
}
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)")
_WORD_DATE = re.compile(r"(?<!\d)(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß]+)\.?\s+(\d{4})(?!\d)")


def _date_value(value: object) -> date | None:
    """Parse ISO, DD.MM.YYYY, DD.MM.YY, DD/MM/YYYY and "18. Oktober 2026" style dates."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    match = _ISO_DATE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    match = _NUMERIC_DATE.search(text)
    if match:
        year = int(match.group(3))
        if len(match.group(3)) == 2:
            year += 2000
        try:
            return date(year, int(match.group(2)), int(match.group(1)))
        except ValueError:
            pass
    for match in _WORD_DATE.finditer(text):
        month_name = (
            unicodedata.normalize("NFKD", match.group(2).replace("ä", "ae").replace("Ä", "Ae"))
            .encode("ascii", "ignore").decode("ascii").casefold()
        )
        if month_name in GERMAN_MONTHS:
            try:
                return date(int(match.group(3)), GERMAN_MONTHS[month_name], int(match.group(1)))
            except ValueError:
                return None
    return None


def _is_expired(posted: object, deadline: object, *, today: date, max_age_days: int) -> bool:
    posted_date = _date_value(posted)
    deadline_date = _date_value(deadline)
    return bool(
        (deadline_date is not None and deadline_date < today)
        or (posted_date is not None and posted_date < today - timedelta(days=max_age_days))
    )


def load_application_jobs(
    workbook_path: str | Path,
    *,
    limit: int | None = None,
    max_age_days: int = 21,
    today: date | None = None,
    employment_format: str | None = FULL_TIME_VALUE,
) -> list[ApplicationJob]:
    """Read fresh candidates, then rank by sheet, score, and deadline."""
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Application workbook does not exist: {path}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if max_age_days < 1:
        raise ValueError("max_age_days must be positive")
    today = today or date.today()
    workbook = load_workbook(path, read_only=True, data_only=True)
    jobs: list[ApplicationJob] = []
    seen: set[str] = set()
    try:
        for sheet_name in APPLICATION_SHEET_ORDER:
            if sheet_name not in workbook.sheetnames:
                continue
            sheet = workbook[sheet_name]
            header_values = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
            headers = [_text(value) for value in header_values]
            for values in sheet.iter_rows(min_row=2, values_only=True):
                record = {header: value for header, value in zip(headers, values) if header}
                if employment_format is not None and _text(record.get("employment_format")) != employment_format:
                    continue
                url = _text(record.get("url"))
                title = _text(record.get("title"))
                if not url or not title or _is_expired(
                    record.get("posted"), record.get("deadline"),
                    today=today, max_age_days=max_age_days,
                ):
                    continue
                key = canonicalize_url(url)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(ApplicationJob(
                    queue_rank=0,
                    source_sheet=sheet_name,
                    score=_number(record.get("score")),
                    band=_text(record.get("band")),
                    title=title,
                    employer=_text(record.get("employer")),
                    location=_text(record.get("location")),
                    posted=_text(record.get("posted")),
                    deadline=_text(record.get("deadline")),
                    board=_text(record.get("board")),
                    url=url,
                    external_url=_text(record.get("external_url")),
                    primary_cv_family=_text(record.get("primary_cv_family")),
                    secondary_cv_family=_text(record.get("secondary_cv_family")),
                    family_hits=_text(record.get("family_hits")),
                    analysis_task_hits=_text(record.get("analysis_task_hits")),
                    method_hits=_text(record.get("method_hits")),
                    tool_hits=_text(record.get("tool_hits")),
                    language_flags=_text(record.get("language_flags")),
                    full_text=_text(record.get("full_text")),
                ))
    finally:
        workbook.close()
    sheet_rank = {name: index for index, name in enumerate(APPLICATION_SHEET_ORDER)}
    jobs.sort(key=lambda job: (
        sheet_rank[job.source_sheet], -job.score,
        _date_value(job.deadline) or date.max,
        -(_date_value(job.posted) or date.min).toordinal(),
    ))
    ranked = [replace(job, queue_rank=index) for index, job in enumerate(jobs, 1)]
    return ranked[:limit] if limit is not None else ranked


def _family_code(job: ApplicationJob) -> str:
    for value in (job.primary_cv_family, job.secondary_cv_family):
        match = re.search(r"\bB(0[1-9]|1[0-9]|20)\b", value, re.IGNORECASE)
        if match:
            return f"B{match.group(1)}"
    raise ValueError(f"No the configured CV families CV family is assigned to {job.title!r}")


def _slug(value: str, *, maximum: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    return (normalized or "job")[:maximum].rstrip("-")


def _clean_german(value: str) -> str:
    return " ".join(value.replace(" — ", ", ").replace(" – ", ", ").split())


def _lowercase_first(value: str) -> str:
    value = _clean_german(value)
    match = re.match(r"(?P<prefix>[^A-Za-zÄÖÜäöüß]*)(?P<word>[A-Za-zÄÖÜäöüß]+)", value)
    if not match:
        return value
    function_words = {
        "der", "die", "das", "den", "dem", "ein", "eine", "bei", "mit", "für", "in", "im",
        "als", "seit", "durch", "nach", "auf", "an", "wenn", "da", "weil", "während",
        "mein", "meine", "ich",
    }
    word = match.group("word")
    if word.casefold() in function_words:
        index = match.start("word")
        return value[:index] + word[0].lower() + value[index + 1:]
    return value


def load_paragraph_bank(path: str | Path, families: tuple[str, ...] | None = None) -> dict[str, tuple[str, str]]:
    bank_path = Path(path)
    if not bank_path.exists():
        raise FileNotFoundError(f"Cover-letter paragraph bank does not exist: {bank_path}")
    raw = yaml.safe_load(bank_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError("Cover-letter paragraph bank must be a mapping")
    result: dict[str, tuple[str, str]] = {}
    expected = families or tuple(sorted(str(key) for key in raw if re.fullmatch(r"B\d{2}", str(key))))
    for family in expected:
        paragraphs = raw.get(family)
        if not isinstance(paragraphs, list) or len(paragraphs) != 2:
            raise ValueError(f"{family} must contain exactly two evidence paragraphs")
        result[family] = (_clean_german(str(paragraphs[0])), _clean_german(str(paragraphs[1])))
    return result


def _verified_proof(bank: Mapping[str, tuple[str, str]], family: str) -> tuple[str, str]:
    paragraphs = bank[family]
    if any(not paragraph or MISSING_EVIDENCE in paragraph for paragraph in paragraphs):
        raise ValueError(f"{family} cannot be prepared: {MISSING_EVIDENCE}")
    return paragraphs


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))


def _set_paragraph_text(paragraph: etree._Element, text: str) -> None:
    runs = paragraph.xpath(".//w:r", namespaces=NS)
    first_run = runs[0] if runs else etree.SubElement(paragraph, f"{{{WORD_NS}}}r")
    for child in list(first_run):
        if child.tag != f"{{{WORD_NS}}}rPr":
            first_run.remove(child)
    for extra_run in runs[1:]:
        parent = extra_run.getparent()
        if parent is not None:
            parent.remove(extra_run)
    for index, line in enumerate(text.split("\n")):
        if index:
            etree.SubElement(first_run, f"{{{WORD_NS}}}br")
        node = etree.SubElement(first_run, f"{{{WORD_NS}}}t")
        if line.startswith(" ") or line.endswith(" "):
            node.set(f"{{{XML_NS}}}space", "preserve")
        node.text = line


def _set_paragraph_bold(paragraph: etree._Element, bold: bool) -> None:
    runs = paragraph.xpath(".//w:r", namespaces=NS)
    if not runs:
        return
    run = runs[0]
    rpr = run.find("w:rPr", namespaces=NS)
    if bold:
        if rpr is None:
            rpr = etree.Element(f"{{{WORD_NS}}}rPr")
            run.insert(0, rpr)
        if rpr.find("w:b", namespaces=NS) is None:
            etree.SubElement(rpr, f"{{{WORD_NS}}}b")
    elif rpr is not None:
        for tag in ("b", "bCs"):
            for node in rpr.findall(f"w:{tag}", namespaces=NS):
                rpr.remove(node)


def _find_paragraph(paragraphs: Iterable[etree._Element], predicate, label: str) -> etree._Element:
    for paragraph in paragraphs:
        if predicate(_paragraph_text(paragraph).strip()):
            return paragraph
    raise ValueError(f"Cover-letter template is missing the {label} slot")


def _set_recipient_and_date(body: etree._Element, company: str, location: str, generation_date: date) -> None:
    recipient_cells = body.xpath("./w:tbl[1]/w:tr[2]/w:tc[1]", namespaces=NS)
    if not recipient_cells:
        raise ValueError("Cover-letter template is missing the recipient table cell")
    cell = recipient_cells[0]
    paragraphs = cell.xpath("./w:p", namespaces=NS)
    if not paragraphs:
        paragraphs = [etree.SubElement(cell, f"{{{WORD_NS}}}p")]
    _set_paragraph_text(paragraphs[0], company)
    _set_paragraph_bold(paragraphs[0], True)
    if location:
        if len(paragraphs) < 2:
            paragraphs.append(etree.SubElement(cell, f"{{{WORD_NS}}}p"))
        _set_paragraph_text(paragraphs[1], location)
        _set_paragraph_bold(paragraphs[1], False)
        keep = 2
    else:
        keep = 1
    for paragraph in paragraphs[keep:]:
        cell.remove(paragraph)

    sender_cells = body.xpath("./w:tbl[1]/w:tr[1]/w:tc", namespaces=NS)
    date_paragraph = None
    for sender_cell in sender_cells:
        for paragraph in sender_cell.xpath(".//w:p", namespaces=NS):
            if re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", _paragraph_text(paragraph)):
                date_paragraph = paragraph
                break
        if date_paragraph is not None:
            break
    if date_paragraph is None:
        raise ValueError("Cover-letter template is missing the date slot")
    _set_paragraph_text(date_paragraph, generation_date.strftime("%d.%m.%Y"))
    for date_node in date_paragraph.xpath(".//w:date", namespaces=NS):
        date_node.set(f"{{{WORD_NS}}}fullDate", generation_date.isoformat() + "T00:00:00Z")


def available_from(start_date: object, today: date) -> str:
    """The profile's earliest start date as TT.MM.JJJJ when it is a real future date, else ''."""
    text = str(start_date or "").strip()
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    try:
        if match:
            when = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        elif iso:
            when = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        else:
            return ""
    except ValueError:
        return ""
    return when.strftime("%d.%m.%Y") if when > today else ""


def tailor_cover_letter(
    template_path: str | Path,
    output_path: str | Path,
    job: ApplicationJob,
    *,
    proof_paragraphs: tuple[str, str],
    hook: Mapping[str, object],
    applicant_status: str,
    graduation_date: str = "",
    generation_date: date | None = None,
    start_date: str = "",
) -> Path:
    """Create a deterministic, evidence-only letter without calling a model API."""
    if applicant_status not in {"enrolled", "graduating", "graduated", "professional"}:
        raise ValueError(
            "Set applicant_status to enrolled, graduating, graduated, or professional before preparing applications"
        )
    intro = _text(hook.get("intro"))
    company_paragraph = _text(hook.get("company_paragraph"))
    if not intro or not company_paragraph or MISSING_EVIDENCE in intro + company_paragraph:
        raise ValueError("hook.json needs non-empty intro and company_paragraph values")
    template = Path(template_path)
    output = Path(output_path)
    if not template.exists():
        raise FileNotFoundError(f"Cover-letter template does not exist: {template}")
    graduation_date_text = "" if applicant_status == "professional" else re.sub(r"^0(?=\d)", "", _text(graduation_date))
    closing = (
        "Aktuell bin ich immatrikuliert. Einen möglichen Starttermin stimme ich gerne mit Ihnen "
        "ab. Ich freue mich, Ihnen in einem persönlichen Gespräch mehr über meine Erfahrung und "
        "meinen Beitrag zu dieser Position zu berichten."
        if applicant_status == "enrolled" else
        "Derzeit befinde ich mich in der Abschlussphase meines Studiums. Einen möglichen Starttermin "
        "stimme ich gerne mit Ihnen ab. Ich freue mich, Ihnen in einem persönlichen Gespräch mehr "
        "über meine Erfahrung und meinen Beitrag zu dieser Position zu berichten."
        if applicant_status == "graduating" else
        (
            f"Mein Studium habe ich am {graduation_date_text} abgeschlossen. Einen möglichen Starttermin "
            "stimme ich gerne mit Ihnen ab. Ich freue mich, Ihnen in einem persönlichen Gespräch mehr "
            "über meine Erfahrung und meinen Beitrag zu dieser Position zu berichten."
            if graduation_date_text else
            "Einen möglichen Starttermin stimme ich gerne mit Ihnen ab. Ich freue mich, Ihnen in einem "
            "persönlichen Gespräch mehr über meine Erfahrung und meinen Beitrag zu dieser Position zu berichten."
        )
    )
    available = available_from(start_date, generation_date or date.today())
    if available:
        closing = closing.replace(
            "Einen möglichen Starttermin stimme ich gerne mit Ihnen ab.", f"Ich stehe Ihnen ab dem {available} zur Verfügung.",
        )
    replacements = {
        "subject": _clean_german(f"Bewerbung als {job.title}"),
        "greeting": "Sehr geehrte Damen und Herren,",
        "intro": _lowercase_first(intro),
        "proof_1": _clean_german(proof_paragraphs[0]),
        "proof_2": _clean_german(proof_paragraphs[1]),
        "company": _clean_german(company_paragraph),
        "closing": closing,
    }
    with zipfile.ZipFile(template, "r") as source:
        root = etree.fromstring(source.read("word/document.xml"))
        body = root.find("w:body", namespaces=NS)
        if body is None:
            raise ValueError("Cover-letter template has no Word document body")
        paragraphs = body.findall("w:p", namespaces=NS)
        slots = {
            "subject": _find_paragraph(paragraphs, lambda value: "Bewerbung als" in value, "subject"),
            "greeting": _find_paragraph(paragraphs, lambda value: value.startswith("Sehr geehrt"), "greeting"),
            "company": _find_paragraph(
                paragraphs,
                lambda value: value.startswith("An ") and "interessiert mich besonders" in value,
                "company paragraph",
            ),
            "closing": _find_paragraph(
                paragraphs,
                lambda value: value.startswith("Aktuell bin ich") or value.startswith("Einen möglichen Starttermin"),
                "closing",
            ),
        }
        greeting_index = paragraphs.index(slots["greeting"])
        company_index = paragraphs.index(slots["company"])
        content_slots = [
            paragraph for paragraph in paragraphs[greeting_index + 1:company_index]
            if _paragraph_text(paragraph).strip()
        ]
        if len(content_slots) < 3:
            raise ValueError("Cover-letter template needs introduction and two evidence paragraphs")
        slots["intro"], slots["proof_1"], slots["proof_2"] = content_slots[:3]
        for name, paragraph in slots.items():
            _set_paragraph_text(paragraph, replacements[name])
        _set_recipient_and_date(body, job.employer, job.location, generation_date or date.today())
        patched_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as destination:
            for info in source.infolist():
                data = patched_xml if info.filename == "word/document.xml" else source.read(info.filename)
                destination.writestr(info, data)
    with zipfile.ZipFile(output, "r") as check:
        if check.testzip() is not None:
            raise ValueError(f"Generated cover letter is not a valid DOCX: {output}")
    return output


def render_existing_letter(
    output_dir: str | Path,
    documents_dir: str | Path,
    application_id: str,
    *,
    applicant_status: str,
    graduation_date: str = "",
    start_date: str = "",
    applicant_file_stem: str = "",
) -> Path:
    """Render the letter of one already prepared package and make it queue-ready.

    This is the small on-demand counterpart of ``prepare_application_packages``:
    it never selects another job, rewrites a CV, or touches another package.
    """
    destination = Path(output_dir)
    match = next((path for path in destination.glob("*/application.json")
                  if json.loads(path.read_text(encoding="utf-8-sig")).get("application_id") == application_id), None)
    if match is None:
        raise FileNotFoundError(f"Application ID not found: {application_id}")
    manifest = json.loads(match.read_text(encoding="utf-8-sig"))
    folder = match.parent
    hook = _load_hook(folder / "hook.json")
    if hook is None:
        raise ValueError(f"No checked hook exists for {application_id}")
    documents = Path(documents_dir)
    templates = sorted(documents.glob("Anschreiben*.docx"), key=lambda path: (len(path.stem), path.name.casefold()))
    template = templates[0] if templates else documents / "Anschreiben_Master.docx"
    bank = load_paragraph_bank(documents / "anschreiben_bausteine.yaml")
    family = str(manifest.get("primary_cv_family") or "")[:3]
    proof = _verified_proof(bank, family)
    stem = applicant_file_stem.strip() or template.stem.removeprefix("Anschreiben_") or "Bewerber"
    cover_name = f"Anschreiben_{stem}_{_slug(str(manifest.get('employer') or ''), maximum=36)}.docx"
    values = {item.name: manifest.get(item.name, "") for item in fields(ApplicationJob)}
    values["queue_rank"] = int(_number(values.get("queue_rank")))
    values["score"] = _number(values.get("score"))
    job = ApplicationJob(**values)
    rendered = tailor_cover_letter(
        template, folder / cover_name, job, proof_paragraphs=proof, hook=hook,
        applicant_status=applicant_status, graduation_date=graduation_date, start_date=start_date,
    )
    manifest["cover_letter"] = cover_name
    manifest["submission_status"] = "not_submitted"
    manifest["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(match, manifest)
    _queue_rows(destination)
    return rendered


def _application_id(job: ApplicationJob) -> str:
    return hashlib.sha1(canonicalize_url(job.url).encode("utf-8")).hexdigest()[:12]


def _manifest_sort_key(manifest: Mapping[str, object]) -> tuple[object, ...]:
    sheet_rank = {name: index for index, name in enumerate(APPLICATION_SHEET_ORDER)}
    return (
        sheet_rank.get(_text(manifest.get("source_sheet")), len(sheet_rank)),
        -_number(manifest.get("score")),
        _date_value(manifest.get("deadline")) or date.max,
        -(_date_value(manifest.get("posted")) or date.min).toordinal(),
    )


def _queue_rows(output_dir: str | Path) -> list[dict[str, object]]:
    destination = Path(output_dir)
    manifests: list[tuple[Path, dict[str, object]]] = []
    for manifest_path in destination.glob("*/application.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifests.append((manifest_path, manifest))
    # Folder name breaks ties, so the order never depends on how the file system lists folders.
    manifests.sort(key=lambda item: (_manifest_sort_key(item[1]), item[0].parent.name.casefold()))
    rows: list[dict[str, object]] = []
    for queue_rank, (manifest_path, manifest) in enumerate(manifests, 1):
        if int(_number(manifest.get("queue_rank"))) != queue_rank:
            manifest["queue_rank"] = queue_rank
            atomic_write_json(manifest_path, manifest)
        package_dir = manifest_path.parent.resolve()
        external_url = _text(manifest.get("external_url"))
        url = _text(manifest.get("url"))
        rows.append({
            "application_id": _text(manifest.get("application_id")),
            "queue_rank": queue_rank,
            "source_sheet": _text(manifest.get("source_sheet")),
            "score": _number(manifest.get("score")),
            "title": _text(manifest.get("title")),
            "employer": _text(manifest.get("employer")),
            "posted": _text(manifest.get("posted")),
            "deadline": _text(manifest.get("deadline")),
            "url": url,
            "external_url": external_url,
            "application_url": external_url or url,
            "selected_cv": _text(manifest.get("selected_cv")),
            "cover_letter": _text(manifest.get("cover_letter")),
            "attachments_dir": str(package_dir / "Attachements") if (package_dir / "Attachements").is_dir() else "",
            "package_dir": str(package_dir),
            "submission_status": _text(manifest.get("submission_status")) or "not_submitted",
            "legacy": "yes" if is_legacy_manifest(manifest) else "",
        })
    return rows


def next_application(output_dir: str | Path) -> dict[str, object] | None:
    return next(
        (
            row for row in _queue_rows(output_dir)
            if row["submission_status"] == "approved" and not row["legacy"]
        ),
        None,
    )


def _show_manual_action_notification() -> None:
    try:
        from .osutil import alert
        alert(
            "Manual application check required",
            "Use the jobfilter extension, then manually complete every CAPTCHA and any field it could not fill. "
            "Review everything before you submit.",
        )
    except OSError:
        return


def open_application_handoff(output_dir: str | Path) -> dict[str, object] | None:
    """Open one approved form/package, notify the user, and record it as opened."""
    row = next_application(output_dir)
    if row is None:
        return None
    webbrowser.open(str(row["application_url"]), new=2)
    from .osutil import open_path
    open_path(str(row["package_dir"]))
    _show_manual_action_notification()
    set_application_status(output_dir, str(row["application_id"]), "opened")
    return row | {"submission_status": "opened"}


def set_application_status(output_dir: str | Path, application_id: str, status: str) -> dict[str, object]:
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(APPLICATION_STATUSES)}")
    destination = Path(output_dir)
    matches: list[Path] = []
    for manifest_path in destination.glob("*/application.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _text(manifest.get("application_id")) == application_id:
            matches.append(manifest_path)
    if not matches:
        raise FileNotFoundError(f"Application ID not found: {application_id}")
    if len(matches) > 1:
        raise ValueError(f"Duplicate application ID found: {application_id}")
    path = matches[0]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    current = _text(manifest.get("submission_status")) or "not_submitted"
    if current == status:
        return manifest
    if is_legacy_manifest(manifest) and status not in LEGACY_ALLOWED_STATUSES:
        raise ValueError(
            f"{application_id} was built by the old letter builder; it can only be marked "
            "expired or skipped. Prepare a fresh package instead."
        )
    allowed_from = STATUS_TRANSITIONS.get(status, frozenset())
    if current not in allowed_from:
        raise ValueError(
            f"Cannot change {application_id} from {current} to {status}; allowed from: "
            + (", ".join(sorted(allowed_from)) or "nothing (set automatically)")
        )
    if status in {"approved", "not_submitted"}:
        cover_letter = _text(manifest.get("cover_letter"))
        if not cover_letter or not (path.parent / cover_letter).is_file():
            raise ValueError(
                f"Cannot mark {application_id} {status}: the cover letter file is missing. "
                "Write hook.json and rerun prepare-applications first."
            )
    changed_at = datetime.now(timezone.utc).isoformat()
    if current == "submitted" and status == "not_submitted":
        manifest["submission_correction_at"] = changed_at
        manifest["submission_correction_from"] = "submitted"
        manifest["submission_correction_previous_updated_at"] = _text(manifest.get("status_updated_at"))
    elif status == "submitted":
        manifest.pop("submission_correction_at", None)
        manifest.pop("submission_correction_from", None)
        manifest.pop("submission_correction_previous_updated_at", None)
    manifest["submission_status"] = status
    manifest["status_updated_at"] = changed_at
    if status in {"skipped", "not_submitted"}:
        manifest.pop("confirmed_at", None)
    atomic_write_json(path, manifest)
    _queue_rows(destination)
    return manifest


def expire_applications(
    output_dir: str | Path,
    *,
    max_age_days: int = 21,
    today: date | None = None,
    archive_dir: str | Path | None = None,
) -> list[str]:
    """Mark existing unsubmitted manifests expired when deadline or age is stale."""
    destination = Path(output_dir)
    today = today or date.today()
    expired: list[str] = []
    for path in destination.glob("*/application.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if _text(manifest.get("submission_status")) in {"submitted", "skipped", "failed", "expired"}:
            continue
        if not _is_expired(manifest.get("posted"), manifest.get("deadline"), today=today, max_age_days=max_age_days):
            continue
        manifest["submission_status"] = "expired"
        manifest["status_updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, manifest)
        expired.append(_text(manifest.get("application_id")))
        if archive_dir is not None:
            from .jdarchive import archive_package
            archive_package(archive_dir, path.parent, "expired", reason="expired unopened")
    _queue_rows(destination)
    return expired


BRIEF_MAX_CHARS = 2500
FULL_AD_MAX_CHARS = 15000
_TASK_HEADINGS = re.compile(
    r"(?:\b(?:ihre|deine|ihr|dein)\s+(?:aufgaben(?:gebiet|bereich)?|t[aä]tigkeiten|rolle)\b"
    r"|\bdas\s+erwartet\s+(?:sie|dich)\b"
    r"|\bwas\s+(?:sie|dich)\s+erwartet\b"
    r"|\b(?:aufgaben|t[aä]tigkeiten|aufgabengebiet|aufgabenbereich)\s*:"
    r"|\byour\s+(?:tasks|role|responsibilities|mission|impact)\b"
    r"|\bwhat\s+you(?:['’]ll|\s+will)\s+do\b"
    r"|\bresponsibilities\s*:)",
    re.IGNORECASE,
)
_PROFILE_HEADINGS = re.compile(
    r"(?:\b(?:ihr|dein)\s+profil\b"
    r"|\bdas\s+bringen\s+(?:sie|du)\s+mit\b"
    r"|\bwas\s+(?:sie|du)\s+mitbringen?\b|\bwas\s+du\s+mitbringst\b"
    r"|\b(?:ihre|deine)\s+qualifikation(?:en)?\b"
    r"|\banforderungen\b|\bqualifikationen\s*:"
    r"|\byour\s+(?:profile|qualifications|background)\b"
    r"|\bwhat\s+you\s+bring\b|\brequirements\b)",
    re.IGNORECASE,
)
_OFFER_HEADINGS = re.compile(
    r"(?:\bwir\s+bieten\b|\bdas\s+bieten\s+wir\b|\bunser\s+angebot\b|\bihre\s+vorteile\b"
    r"|\bdeine\s+vorteile\b|\bwas\s+wir\s+(?:ihnen|dir)\s+bieten\b|\bbenefits\b"
    r"|\bwhat\s+we\s+offer\b|\bhaben\s+wir\s+ihr\s+interesse\b)",
    re.IGNORECASE,
)


def brief_excerpt(full_text: str, *, max_chars: int = BRIEF_MAX_CHARS) -> tuple[str, str]:
    """Return the task and profile sections of an ad, not the employer boilerplate.

    The workbook stores ad text on one line, so headings are found as phrases.
    Returns (excerpt, source) where source is "sections" or "start".
    """
    text = " ".join(str(full_text or "").split())
    if not text:
        return "", "empty"
    task = _TASK_HEADINGS.search(text)
    profile = _PROFILE_HEADINGS.search(text, task.end() if task else 0)
    start_match = task or profile
    if start_match is None:
        return text[:max_chars].rstrip(), "start"
    start = start_match.start()
    offer = _OFFER_HEADINGS.search(text, (profile or start_match).end())
    end = offer.start() if offer else len(text)
    return text[start:min(end, start + max_chars)].rstrip(), "sections"


def full_ad_text(value: object, *, max_chars: int = FULL_AD_MAX_CHARS) -> str:
    """One compact, repaired copy of an ad for form-question chat context."""
    text = " ".join(str(value or "").split())
    mojibake = re.compile("[\u00c2-\u00f4][\u0080-\u00bf]{1,3}")
    for _ in range(3):
        repaired = mojibake.sub(
            lambda match: _repair_utf8_fragment(match.group()), text,
        )
        if repaired == text:
            break
        text = repaired
    return text[:max_chars].rstrip()


def _repair_utf8_fragment(value: str) -> str:
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def backfill_full_descriptions(
    output_dir: str | Path, workbook_path: str | Path | None, database_path: str | Path | None = None,
) -> dict[str, int]:
    """Backfill open package briefs from workbook full_text, falling back to the saved excerpt."""
    by_url: dict[str, str] = {}
    by_job: dict[tuple[str, str], str] = {}
    path = Path(workbook_path) if workbook_path else None
    if path and path.is_file():
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                values = sheet.iter_rows(values_only=True)
                headers = [str(value or "").strip() for value in next(values, ())]
                for row in values:
                    record = {name: value for name, value in zip(headers, row) if name}
                    text = full_ad_text(record.get("full_text"))
                    if not text:
                        continue
                    url = canonicalize_url(str(record.get("url") or ""))
                    if url:
                        by_url[url] = text
                    key = (str(record.get("title") or "").casefold().strip(), str(record.get("employer") or "").casefold().strip())
                    if all(key):
                        by_job[key] = text
        finally:
            workbook.close()
    database = Path(database_path) if database_path else None
    if database and database.is_file():
        with sqlite3.connect(database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ad_text_cache" in tables:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(ad_text_cache)")}
                url_column = "canonical_url" if "canonical_url" in columns else "url" if "url" in columns else ""
                if url_column and "full_text" in columns:
                    selected = [url_column, "full_text"] + [name for name in ("title", "employer") if name in columns]
                    for row in connection.execute(f"SELECT {', '.join(selected)} FROM ad_text_cache"):
                        record = dict(zip(selected, row))
                        text = full_ad_text(record.get("full_text"))
                        if not text:
                            continue
                        url = canonicalize_url(str(record.get(url_column) or ""))
                        if url and url not in by_url:
                            by_url[url] = text
                        key = (str(record.get("title") or "").casefold().strip(), str(record.get("employer") or "").casefold().strip())
                        if all(key) and key not in by_job:
                            by_job[key] = text
    counts = {"full": 0, "excerpt": 0, "unchanged": 0}
    open_statuses = {"needs_hook", "not_submitted", "approved", "opened", "failed"}
    for manifest_path in Path(output_dir).glob("*/application.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if str(manifest.get("submission_status") or "") not in open_statuses:
            continue
        brief_path = manifest_path.parent / "letter_brief.json"
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            brief = {}
        if brief.get("ad_text_full") and brief.get("ad_text_full_source") == "full":
            counts["unchanged"] += 1
            continue
        text = ""
        for name in ("url", "external_url", "application_url"):
            url = canonicalize_url(str(manifest.get(name) or ""))
            if url and url in by_url:
                text = by_url[url]
                break
        if not text:
            key = (str(manifest.get("title") or "").casefold().strip(), str(manifest.get("employer") or "").casefold().strip())
            text = by_job.get(key, "")
        source = "full" if text else "excerpt"
        brief["ad_text_full"] = text or full_ad_text(brief.get("ad_text"))
        brief["ad_text_full_source"] = source
        atomic_write_json(brief_path, brief)
        counts[source] += 1
    return counts


SKIP_REASON_TEXT = {
    "missing_evidence_paragraphs": "proof paragraphs still marked FEHLT in anschreiben_bausteine.yaml",
    "no_cv_family": "no the configured CV families CV family assigned by scoring",
    "missing_cv_file": "Lebenslauf file for this family is missing",
    "no_existing_package": "no package exists yet for this ID",
    "protected_status": "already opened or submitted, never rebuilt",
    "retired_status": "expired or skipped; name it with --only to rebuild it",
    "needs_force": "letter already built; add --force to rebuild it",
    "not_in_workbook": "ID not found among the jobs in the workbook",
    "already_handled": "already in the tracker (applied or rejected) or packaged before",
}


def format_skip_summary(skipped: Iterable[Mapping[str, str]]) -> list[str]:
    """One line per family and reason, most frequent first."""
    counts = Counter((entry["family"], entry["reason"]) for entry in skipped)
    return [
        f"{family}: {count} job(s) skipped, {SKIP_REASON_TEXT.get(reason, reason)}"
        for (family, reason), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _load_hook(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid hook JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"hook.json must contain an object: {path}")
    return raw


def _project_slots(body: etree._Element) -> list[etree._Element]:
    """Paragraphs of the CV's "Projekte" section, up to the next heading."""
    paragraphs = body.findall("w:p", namespaces=NS)
    for index, paragraph in enumerate(paragraphs):
        style = paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
        if style and style[0].startswith("Heading") and _paragraph_text(paragraph).strip().casefold() == "projekte":
            slots = []
            for following in paragraphs[index + 1:]:
                following_style = following.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS)
                if following_style and following_style[0].startswith("Heading"):
                    break
                if _paragraph_text(following).strip():
                    slots.append(following)
            return slots
    return []


def cv_project_slot_count(cv_path: str | Path) -> int:
    with zipfile.ZipFile(cv_path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", namespaces=NS)
    return len(_project_slots(body)) if body is not None else 0


def _slot_title(paragraph: etree._Element) -> str:
    return _paragraph_text(paragraph).split(":", 1)[0].strip().casefold()


CV_LINE_CHARS = 100  # conservative characters per rendered line of a CV bullet


def _rendered_lines(text: str) -> int:
    return max(1, -(-len(text.strip()) // CV_LINE_CHARS))


def place_projects_in_cv(cv_path: str | Path, chosen: Sequence[Project]) -> list[str]:
    """Write the chosen project lines into the existing "Projekte" slots of a CV copy.

    The number of slots, their order on the page and their paragraph formatting stay
    as in the source CV. A project goes only into a slot whose current text needs at
    least as many lines, so the page count does not change; a project that fits no
    slot is left out. A project already shown in a slot keeps that slot. Returns the
    titles now shown.
    """
    path = Path(cv_path)
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    document = next(data for info, data in entries if info.filename == "word/document.xml")
    root = etree.fromstring(document)
    body = root.find("w:body", namespaces=NS)
    slots = _project_slots(body) if body is not None else []
    if not slots or not chosen:
        return [_paragraph_text(slot).split(":", 1)[0].strip() for slot in slots]
    remaining = list(chosen[:len(slots)])
    capacity = [_rendered_lines(_paragraph_text(slot)) for slot in slots]
    assignment: dict[int, Project] = {}
    for index, slot in enumerate(slots):
        for project in remaining:
            if project.title.casefold() == _slot_title(slot) and _rendered_lines(project.cv_line) <= capacity[index]:
                assignment[index] = project
                remaining.remove(project)
                break
    for index in range(len(slots)):
        if index in assignment:
            continue
        for project in remaining:
            if _rendered_lines(project.cv_line) <= capacity[index]:
                assignment[index] = project
                remaining.remove(project)
                break
    for index, project in assignment.items():
        _set_paragraph_text(slots[index], project.cv_line)
    patched = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w") as archive:
        for info, data in entries:
            archive.writestr(info, patched if info.filename == "word/document.xml" else data)
    return [_paragraph_text(slot).split(":", 1)[0].strip() for slot in slots]


def prepare_application_packages(
    jobs: Iterable[ApplicationJob],
    *,
    documents_dir: str | Path,
    output_dir: str | Path,
    applicant_status: str,
    graduation_date: str = "",
    attachment_allowlist: Iterable[str],
    start_date: str = "",
    enrolment_attachment_names: Iterable[str] = ("immatbescheinigung.pdf",),
    limit: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    skipped: list[dict[str, str]] | None = None,
    github_projects: Sequence[Project] = (),
    only_ids: Iterable[str] = (),
    existing_only: bool = False,
    known: KnownPositions | None = None,
    applicant_file_stem: str = "",
    applicant_facts: str = "",
) -> list[dict[str, object]]:
    """Build packages; jobs that cannot be prepared are skipped, not fatal.

    Pass a list as ``skipped`` to receive one entry per skipped job with the
    keys family, reason, title and employer (named IDs also carry application_id).

    ``only_ids`` rebuilds just those existing packages; ``existing_only`` never
    creates a package and renders the existing ones whose hook.json is now
    present. Neither mode creates packages for other jobs. Expired and skipped
    packages are only rebuilt when named in ``only_ids``.
    """
    documents = Path(documents_dir)
    destination = Path(output_dir)
    skip_log: list[dict[str, str]] = skipped if skipped is not None else []
    if applicant_status not in {"enrolled", "graduating", "graduated", "professional"}:
        raise ValueError(
            '[PROFILE: write "enrolled", "graduating", "graduated", or "professional" as applicant_status in config.yaml]'
        )
    template_candidates = sorted(documents.glob("Anschreiben*.docx"), key=lambda path: (len(path.stem), path.name.casefold()))
    template = template_candidates[0] if template_candidates else documents / "Anschreiben_Master.docx"
    stem = applicant_file_stem.strip() or template.stem.removeprefix("Anschreiben_") or "Bewerber"
    paragraph_bank_path = documents / "anschreiben_bausteine.yaml"
    attachments = documents / "Attachements"
    if not template.exists():
        raise FileNotFoundError(f"Cover-letter template does not exist: {template}")
    if not attachments.is_dir():
        raise FileNotFoundError(f"Attachments directory does not exist: {attachments}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    bank = load_paragraph_bank(paragraph_bank_path)
    allowed_names = tuple(dict.fromkeys(_text(name) for name in attachment_allowlist if _text(name)))
    missing_attachments = [name for name in allowed_names if not (attachments / name).is_file()]
    if missing_attachments:
        raise FileNotFoundError("Allowlisted attachments do not exist: " + ", ".join(missing_attachments))
    enrolment_names = {name.casefold() for name in enrolment_attachment_names}
    selected_attachments = [
        name for name in allowed_names
        if applicant_status in {"enrolled", "graduating"} or name.casefold() not in enrolment_names
    ]
    only = {_text(value).lower() for value in only_ids if _text(value)}
    existing_only = existing_only or bool(only)
    seen_ids: set[str] = set()
    results: list[dict[str, object]] = []
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        application_id = _application_id(job)
        if only and application_id not in only:
            continue
        seen_ids.add(application_id)
        folder = destination / f"{application_id}_{_slug(job.employer)}_{_slug(job.title)}"
        manifest_path = folder / "application.json"

        def skip(family_code: str, reason: str) -> None:
            entry = {
                "family": family_code, "reason": reason,
                "title": job.title, "employer": job.employer,
            }
            if only:
                entry["application_id"] = application_id
            skip_log.append(entry)

        if existing_only and not manifest_path.exists():
            if only:
                skip("none", "no_existing_package")
            continue
        if known is not None and not manifest_path.exists() and known.match(job.title, job.employer, except_id=application_id):
            skip("none", "already_handled")
            continue
        try:
            family = _family_code(job)
        except ValueError:
            skip("none", "no_cv_family")
            continue
        try:
            proof = _verified_proof(bank, family)
        except ValueError:
            skip(family, "missing_evidence_paragraphs")
            continue
        suffix = family.removeprefix('B')
        cv = next(iter(sorted(documents.glob(f"*_{suffix}.docx"))), documents / f"Lebenslauf_{stem}_{suffix}.docx")
        if not cv.exists():
            skip(family, "missing_cv_file")
            continue
        cover_name = f"Anschreiben_{stem}_{_slug(job.employer, maximum=36)}.docx"
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            existing_status = _text(existing.get("submission_status"))
            if existing_status in PROTECTED_STATUSES:
                if only:
                    skip(family, "protected_status")
                continue
            if existing_status in {"expired", "skipped"} and application_id not in only:
                continue  # never bring retired packages back unless named explicitly
            if not force and not (existing_status == "needs_hook" and (folder / "hook.json").exists()):
                if only:
                    skip(family, "needs_force")
                continue
        hook = _load_hook(folder / "hook.json") if folder.exists() else None
        status = "planned_needs_hook" if dry_run and hook is None else "planned" if dry_run else "needs_hook"
        chosen_projects: list[tuple[Project, str]] = []
        project_slots = 0
        if github_projects:
            try:
                project_slots = cv_project_slot_count(cv)
            except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError):
                project_slots = 0  # unreadable CV: keep its projects as they are
        if project_slots:
            chosen_projects = select_projects(
                github_projects,
                slots=project_slots,
                job_title=job.title,
                job_text=" ".join((
                    job.title, job.family_hits, job.analysis_task_hits, job.method_hits,
                    job.tool_hits, brief_excerpt(job.full_text)[0],  # tasks and profile, not boilerplate
                )),
                primary_family=job.primary_cv_family,
                secondary_family=job.secondary_cv_family,
                pinned_text=" ".join(proof),
            )
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cv, folder / cv.name)
            if chosen_projects:
                shown = {
                    title.casefold()
                    for title in place_projects_in_cv(folder / cv.name, [project for project, _ in chosen_projects])
                }
                chosen_projects = [(project, reason) for project, reason in chosen_projects if project.title.casefold() in shown]
        project_record = [
            {"title": project.title, "repo": project.repo, "url": project.url, "reason": reason}
            for project, reason in chosen_projects
        ]
        if not dry_run:
            # Certificates are not copied into the package: the manifest names them and everything that
            # sends them reads the one original folder (Job App Docs/Attachements).
            ad_excerpt, excerpt_source = brief_excerpt(job.full_text)
            brief = {
                "title": job.title,
                "employer": job.employer,
                "location": job.location,
                "family": family,
                "applicant_status": applicant_status,
                "graduation_date": graduation_date,
                "external_url": job.external_url,
                "analysis_task_hits": job.analysis_task_hits,
                "method_hits": job.method_hits,
                "tool_hits": job.tool_hits,
                "cv_projects": [entry["title"] for entry in project_record],
                "ad_text_source": excerpt_source,
                "ad_text": ad_excerpt,
                "ad_text_full": full_ad_text(job.full_text),
                "ad_text_full_source": "full" if full_ad_text(job.full_text) else "excerpt",
                "applicant_facts": applicant_facts,
            }
            atomic_write_json(folder / "letter_brief.json", brief)
            if hook is not None:
                tailor_cover_letter(
                    template, folder / cover_name, job,
                    proof_paragraphs=proof, hook=hook, applicant_status=applicant_status,
                    graduation_date=graduation_date, start_date=start_date,
                )
                status = "not_submitted"
            manifest = asdict(job) | {
                "manifest_version": MANIFEST_VERSION,
                "application_id": application_id,
                "application_url": job.application_url,
                "applicant_status": applicant_status,
                "graduation_date": graduation_date,
                "selected_cv": cv.name,
                "cv_github_projects": project_record,
                "cover_letter": cover_name if hook is not None else "",
                "attachments": selected_attachments,
                "submission_status": status,
                "submission_mode": "manual_extension_handoff",
                "autofill_handoff": "jobfilter_extension",
                "cover_letter_token_usage": 0,
            }
            atomic_write_json(manifest_path, manifest)
        results.append({
            "queue_rank": job.queue_rank,
            "source_sheet": job.source_sheet,
            "score": job.score,
            "title": job.title,
            "employer": job.employer,
            "url": job.url,
            "external_url": job.external_url,
            "application_url": job.application_url,
            "application_id": application_id,
            "selected_cv": cv.name,
            "github_projects": [entry["title"] for entry in project_record],
            "cover_letter": cover_name if hook is not None else "",
            "attachments_dir": str(attachments),
            "package_dir": str(folder),
            "status": status,
        })
        if limit is not None and len(results) >= limit:
            break
    for missing_id in sorted(only - seen_ids):
        skip_log.append({
            "family": "none", "reason": "not_in_workbook",
            "title": missing_id, "employer": "", "application_id": missing_id,
        })
    if not dry_run:
        _queue_rows(destination)
    return results
