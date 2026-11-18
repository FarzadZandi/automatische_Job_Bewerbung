"""Local autofill hand-off: PDF export, applicant profile and the data file of the browser extension.

Nothing here calls a network service or a model. The browser extension in ``extension/`` reads
the files this module writes and fills application forms locally; it never submits a form.
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import yaml
from lxml import etree

from .applications import backfill_full_descriptions, is_legacy_manifest

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Statuses whose packages are still to be sent.
OPEN_STATUSES = frozenset({"needs_hook", "not_submitted", "approved", "opened"})

PROFILE_TEMPLATE = """\
# Applicant profile for the form filler. Personal data: this file stays local (Job App Docs is gitignored).
# Leave a value empty ("") when you do not want it filled; the extension then leaves the field for you.
salutation: ""          # Herr / Frau, exactly as forms offer it
academic_title: ""
first_name: ""
last_name: ""
email: ""
phone: ""
street: ""
house_number: ""
postal_code: ""
city: ""
country: "Deutschland"
birth_date: ""          # TT.MM.JJJJ
birth_place: ""
nationality: ""
linkedin: ""
xing: ""
github: ""
website: ""
german_level: ""         # GER level only, e.g. B2
english_level: ""        # GER level only, e.g. C1
disability: ""           # only an explicit user answer, e.g. No / Nein
highest_education: ""    # completed degree, subject, institution, grade and graduation year
current_employment: ""   # current position and employment period
start_date: ""          # earliest start, e.g. 01/12/2026 or "nach Absprache"
salary: ""              # Gehaltsvorstellung, e.g. "55.000 EUR brutto pro Jahr"
notice_period: ""
"""

PROFILE_KEYS = tuple(
    line.split(":", 1)[0] for line in PROFILE_TEMPLATE.splitlines() if line and not line.startswith("#")
)
REQUIRED_PROFILE_KEYS = ("first_name", "last_name", "email")


class AutofillError(RuntimeError):
    pass


def load_profile(path: str | Path) -> dict[str, str]:
    profile_path = Path(path)
    if not profile_path.exists():
        raise AutofillError(
            f"Applicant profile not found: {profile_path}. Run 'jobfilter export-autofill --init-profile' "
            "and fill it in."
        )
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AutofillError(f"{profile_path} must contain key: value pairs")
    unknown = sorted(set(raw) - set(PROFILE_KEYS))
    if unknown:
        raise AutofillError(f"Unknown profile keys in {profile_path}: {', '.join(unknown)}")
    profile = {key: str(raw.get(key) or "").strip() for key in PROFILE_KEYS}
    missing = [key for key in REQUIRED_PROFILE_KEYS if not profile[key]]
    if missing:
        raise AutofillError(f"Profile {profile_path} needs: {', '.join(missing)}")
    return profile


def init_profile(path: str | Path) -> bool:
    """Write the empty profile template. Never overwrites an existing profile."""
    profile_path = Path(path)
    if profile_path.exists():
        return False
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(PROFILE_TEMPLATE, encoding="utf-8")
    return True


def application_source(urls: Sequence[str]) -> str:
    """Employer-facing source name derived only from the package's original URLs."""
    joined = " ".join(str(url).casefold() for url in urls)
    for needle, label in (
        ("arbeitsagentur", "Bundesagentur für Arbeit"), ("xing", "XING"), ("linkedin", "LinkedIn"),
        ("indeed", "Indeed"), ("stepstone", "StepStone"),
    ):
        if needle in joined:
            return label
    # Jobs read from an employer's own career site or its applicant-tracking system.
    return "Karriereseite des Unternehmens" if joined.strip() else ""


def docx_paragraphs(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t"))
        paragraphs.append(" ".join(text.replace("\u00a0", " ").split()))
    return paragraphs


def letter_text(path: str | Path) -> str:
    """Cover letter from the salutation to the signature, for a form's free-text field."""
    paragraphs = docx_paragraphs(path)
    start = next((i for i, text in enumerate(paragraphs) if text.startswith(("Sehr geehrte", "Guten Tag", "Dear"))), 0)
    end = len(paragraphs)
    for index in range(start, len(paragraphs)):
        if paragraphs[index].startswith(("Mit freundlichen Grüßen", "Kind regards", "Best regards")):
            signature = next((j for j in range(index + 1, len(paragraphs)) if paragraphs[j]), index)
            end = signature + 1
            break
    blocks = [text for text in paragraphs[start:end] if text]
    return "\n\n".join(blocks)


def cv_text(path: str | Path) -> str:
    return "\n".join(text for text in docx_paragraphs(path) if text)


_MOJIBAKE = re.compile("[\u00c2-\u00f4][\u0080-\u00bf]{1,3}")


def repair_mojibake(text: str) -> str:
    """Undo UTF-8 text that an older fetch decoded as ISO-8859-1 (\"PrÃ¤sentation\")."""
    def fix(match: re.Match[str]) -> str:
        try:
            return match.group().encode("latin-1").decode("utf-8")
        except UnicodeError:
            return match.group()

    for _ in range(3):
        repaired = _MOJIBAKE.sub(fix, text)
        if repaired == text:
            break
        text = repaired
    return text


def _ps_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _convert_with_word(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Export with Word. Word sometimes drops the connection after an export (seen on OneDrive
    folders), so every document gets a second try with a fresh Word, and a failed Close is ignored:
    the PDF is already written by then."""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "function New-Word { $w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; return $w }",
        "$pairs = @(",
    ]
    # The leading comma keeps each pair an array of its own, also when there is only one pair.
    lines += [f"  ,@({_ps_quote(source.resolve())}, {_ps_quote(target.resolve())})" for source, target in pairs]
    lines += [
        ")",
        "$word = New-Word",
        "$failed = 0",
        "foreach ($pair in $pairs) {",
        "  $done = $false",
        "  $last = ''",
        "  for ($attempt = 0; $attempt -lt 2 -and -not $done; $attempt++) {",
        "    try {",
        "      $doc = $word.Documents.Open($pair[0], $false, $true)",
        "      $doc.ExportAsFixedFormat($pair[1], 17)",
        "      $done = $true",
        "      try { $doc.Close($false) } catch { }",
        "    } catch {",
        "      $last = $_.Exception.Message",
        "      try { $word.Quit() } catch { }",
        "      $word = New-Word",
        "    }",
        "  }",
        "  if (-not $done) { [Console]::Error.WriteLine('FAILED ' + $pair[0] + ': ' + $last); $failed++ }",
        "}",
        "try { $word.Quit() } catch { }",
        "exit $failed",
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as handle:
        handle.write("\n".join(lines))
        script = handle.name
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            check=True, capture_output=True, timeout=120 + 60 * len(pairs),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def _soffice() -> str | None:
    """LibreOffice on PATH or in its default Windows or Mac install location."""
    from .osutil import soffice_path
    found = soffice_path()
    if found:
        return found
    candidate = r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"
    return candidate if Path(candidate).exists() else None


def _convert_with_libreoffice(pairs: Sequence[tuple[Path, Path]]) -> None:
    binary = _soffice()
    if binary is None:
        raise AutofillError("Neither Microsoft Word nor LibreOffice is available for PDF export")
    for source, target in pairs:
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(
                [binary, "--headless", "--convert-to", "pdf", "--outdir", scratch, str(source)],
                check=True, capture_output=True, timeout=180,
            )
            produced = Path(scratch) / (source.stem + ".pdf")
            if not produced.exists():
                raise AutofillError(f"LibreOffice produced no PDF for {source}")
            shutil.move(str(produced), str(target))


def convert_to_pdf(pairs: Sequence[tuple[Path, Path]], *, engine: str = "auto") -> str:
    """Convert (docx, pdf) pairs. Word keeps the page breaks the CVs were checked with."""
    if not pairs:
        return "none"
    if engine not in {"auto", "word", "libreoffice"}:
        raise AutofillError(f"Unknown PDF engine: {engine}")
    word_error = ""
    if engine in {"auto", "word"} and os.name == "nt":
        try:
            _convert_with_word(pairs)
            missing = [target for _, target in pairs if not target.exists()]
            if not missing:
                return "word"
            word_error = f"Word wrote no file for {missing[0].name}"
        except subprocess.CalledProcessError as error:
            output = (error.stderr or b"") + b"\n" + (error.stdout or b"")
            word_error = " ".join(output.decode("utf-8", errors="replace").split())[-700:] or str(error)
        except (OSError, subprocess.SubprocessError) as error:
            word_error = str(error)
        if engine == "word":
            raise AutofillError(f"Word PDF export failed: {word_error}")
    elif engine == "word":
        raise AutofillError("Word PDF export needs Windows")
    try:
        _convert_with_libreoffice(pairs)
    except AutofillError as error:
        if word_error:
            raise AutofillError(f"Word PDF export failed ({word_error}) and LibreOffice is not installed.") from error
        raise
    return "libreoffice"


def _needs_build(source: Path, target: Path) -> bool:
    return not target.exists() or target.stat().st_mtime < source.stat().st_mtime


def _hosts(urls: Iterable[str]) -> list[str]:
    hosts = []
    for url in urls:
        host = urlsplit(url).hostname or ""
        host = host.removeprefix("www.")
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _retry(action: Callable[[], object], attempts: int = 6) -> object:
    """Windows: OneDrive, the virus scanner or the browser can hold a file for a moment."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5 * (attempt + 1))
    return None


def _copy_if_changed(source: Path, target: Path) -> None:
    """Copy only when the target is missing or different, so unchanged files are never touched."""
    if target.is_file() and filecmp.cmp(source, target, shallow=False):
        return
    target.parent.mkdir(parents=True, exist_ok=True)

    def copy() -> None:
        if target.exists():
            os.chmod(target, stat.S_IWRITE)  # a read-only copy cannot be replaced on Windows
        shutil.copy2(source, target)

    _retry(copy)


def _remove_stale(files_dir: Path, keep: set[Path]) -> list[str]:
    """Delete files that no open package uses any more. A file that stays locked is left for the next export."""
    left: list[str] = []
    for path in sorted(files_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_file() and path not in keep:
                os.chmod(path, stat.S_IWRITE)
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            left.append(str(path))
    return left


def _combine_pdfs(sources: Sequence[Path], target: Path) -> bool:
    """Build one temporary upload PDF; return False only for malformed legacy test/input PDFs."""
    if not sources:
        return False
    newest = max(source.stat().st_mtime for source in sources)
    if target.is_file() and target.stat().st_mtime >= newest:
        return True
    try:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for source in sources:
            for page in PdfReader(str(source)).pages:
                writer.add_page(page)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp.pdf")
        with temporary.open("wb") as stream:
            writer.write(stream)
        temporary.replace(target)
        return True
    except Exception:
        return False


def export_autofill(
    output_dir: str | Path,
    profile_path: str | Path,
    extension_dir: str | Path,
    *,
    only_ids: Iterable[str] = (),
    make_pdf: bool = True,
    rebuild_pdf: bool = False,
    attachment_order: Sequence[str] = (),
    attachment_names: Sequence[str] | None = None,
    residence_attachment_names: Sequence[str] = (),
    pdf_engine: str = "auto",
    converter: Callable[[Sequence[tuple[Path, Path]]], str] | None = None,
    attachments_dir: str | Path | None = None,
    current_id: str = "",
    window_limit: int = 6,
    applicant_file_stem: str = "",
    workbook_path: str | Path | None = None,
    database_path: str | Path | None = None,
    answer_bank_path: str | Path | None = None,
    backfill_descriptions: bool = True,
) -> dict[str, object]:
    """Create the PDFs of every open package and write the extension's data folder.

    General attachments come from ``attachments_dir`` (the one original folder); a package folder
    of an older version may still hold its own copies. Restricted residence documents are published
    separately and are never included in general or combined application PDFs.
    """
    profile = load_profile(profile_path)
    if answer_bank_path:
        from .answerbank import load_bank
        facts = load_bank(answer_bank_path).get("facts", {})
        # The user's explicit bank wording wins. Values stay plain because filler.js formats
        # dates and numeric salary fields from their actual HTML attributes and labels.
        for key in ("start_date", "salary", "relocation", "travel_willingness", "driving_licence", "work_authorisation_text", "other_languages"):
            wording = facts.get(key, {}) if isinstance(facts, dict) else {}
            value = str((wording or {}).get("de") or (wording or {}).get("en") or "").strip()
            if value:
                profile[key] = value
    destination = Path(output_dir)
    backfill = backfill_full_descriptions(destination, workbook_path, database_path) if backfill_descriptions \
        else {"full": 0, "excerpt": 0, "unchanged": 0}
    extension = Path(extension_dir)
    if not (extension / "manifest.json").exists():
        raise AutofillError(f"Extension folder not found: {extension}")
    order = [str(name) for name in attachment_order]
    configured_names = None if attachment_names is None else [str(name) for name in attachment_names]
    residence_names = [str(name) for name in residence_attachment_names]
    shared_attachments = Path(attachments_dir) if attachments_dir else None
    missing_residence = [
        name for name in residence_names
        if shared_attachments is None or not (shared_attachments / name).is_file()
    ]
    if missing_residence:
        raise AutofillError("Restricted residence attachment(s) not found: " + ", ".join(missing_residence))
    only = {str(value).strip().lower() for value in only_ids if str(value).strip()}
    person = (applicant_file_stem.strip() or f"{profile['first_name']}_{profile['last_name']}").replace(" ", "_")
    cv_name = f"Lebenslauf_{person}.pdf"
    letter_name = f"Anschreiben_{person}.pdf"

    selected: list[tuple[Path, dict[str, object]]] = []
    for manifest_path in sorted(destination.glob("*/application.json"), key=lambda item: item.parent.name.casefold()):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        application_id = str(manifest.get("application_id", "")).lower()
        if only and application_id not in only:
            continue
        if is_legacy_manifest(manifest) or manifest.get("submission_status") not in OPEN_STATUSES:
            continue
        folder = manifest_path.parent
        if not (folder / str(manifest.get("selected_cv", ""))).is_file():
            continue
        selected.append((folder, manifest))

    sheet_order = {name: index for index, name in enumerate(("PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW"))}
    def score(item):
        folder, manifest = item
        try: points = float(manifest.get("score") or 0)
        except (TypeError, ValueError): points = 0
        return (
            0 if str(manifest.get("application_id") or "").casefold() == current_id.casefold() else 1,
            0 if str(manifest.get("board") or "").casefold() == "manual" else 1,
            sheet_order.get(str(manifest.get("source_sheet") or ""), 99), -points, folder.name.casefold(),
        )
    selected.sort(key=score)
    if not only and window_limit > 0:
        selected = selected[:window_limit]
    pairs: list[tuple[Path, Path]] = []
    for folder, manifest in selected:
        sources = [(manifest["selected_cv"], cv_name)]
        if manifest.get("submission_status") != "needs_hook" and manifest.get("cover_letter") and (folder / str(manifest["cover_letter"])).is_file():
            sources.append((manifest["cover_letter"], letter_name))
        for source_name, target_name in sources:
            source, target = folder / str(source_name), folder / target_name
            if rebuild_pdf or _needs_build(source, target):
                pairs.append((source, target))
    engine = "skipped"
    conversion_started = time.perf_counter()
    if make_pdf:
        engine = (converter or (lambda items: convert_to_pdf(items, engine=pdf_engine)))(pairs)
    conversion_seconds = time.perf_counter() - conversion_started
    try:
        from .metrics import append_jsonl
        append_jsonl(destination.parent / "timings.jsonl", {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"), "task": "export",
            "step": "pdf-conversion", "seconds": round(conversion_seconds, 3), "items": len(pairs),
            "seconds_per_document": round(conversion_seconds / len(pairs), 3) if pairs else 0,
        })
    except OSError:
        pass

    data_dir = extension / "data"
    files_dir = data_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    # Files are updated in place, never by deleting the folder: on Windows a folder that OneDrive,
    # the virus scanner or the browser holds for a moment cannot be deleted.
    published: set[Path] = set()

    def publish(source: Path, relative: str, upload_name: str) -> dict[str, str]:
        target = data_dir / relative
        if target not in published:
            _copy_if_changed(source, target)
            published.add(target)
        return {"path": f"data/{relative}", "name": upload_name, "disk_path": str(source), "size": source.stat().st_size}

    packages = []
    missing_pdf = []
    for folder, manifest in selected:
        application_id = str(manifest["application_id"])
        cv_pdf, letter_pdf = folder / cv_name, folder / letter_name
        letter_pending = manifest.get("submission_status") == "needs_hook" or not bool(manifest.get("cover_letter")) or not (folder / str(manifest.get("cover_letter") or "")).is_file()
        if not cv_pdf.exists() or (not letter_pending and not letter_pdf.exists()):
            missing_pdf.append(application_id)
            continue
        urls = [str(manifest.get(key) or "") for key in ("application_url", "external_url", "url")]
        urls = [url for index, url in enumerate(urls) if url and url not in urls[:index]]
        attachments = []
        names = list(configured_names) if configured_names is not None else [str(name) for name in manifest.get("attachments", [])]
        # Forms often cap the number of extra files: the configured order decides which go first.
        names.sort(key=lambda name: order.index(name) if name in order else len(order))
        for name in names:
            source = folder / "Attachements" / str(name)
            if attachments_dir and (Path(attachments_dir) / str(name)).is_file():
                source = Path(attachments_dir) / str(name)
            if source.is_file():
                from .pdfopt import upload_pdf
                attachments.append(publish(upload_pdf(source), f"files/_attachments/{name}", str(name)))
        residence_documents = [
            publish(shared_attachments / name, f"files/_restricted/{name}", str(name))
            for name in residence_names
        ] if shared_attachments else []
        combined_name = f"Bewerbungsunterlagen_{person}.pdf"
        combined_target = files_dir / application_id / combined_name
        combined_sources = [letter_pdf, cv_pdf, *[Path(str(item["disk_path"])) for item in attachments]]
        combined = None
        is_current = bool(current_id) and application_id.casefold() == current_id.casefold()
        if is_current and not letter_pending and _combine_pdfs(combined_sources, combined_target):
            published.add(combined_target)
            combined = {
                "path": f"data/files/{application_id}/{combined_name}", "name": combined_name,
                "disk_path": str(combined_target), "size": combined_target.stat().st_size,
            }
        letter_cv_combined = None
        letter_cv_name = f"Anschreiben_und_Lebenslauf_{person}.pdf"
        letter_cv_target = files_dir / application_id / letter_cv_name
        if is_current and not letter_pending and _combine_pdfs([letter_pdf, cv_pdf], letter_cv_target):
            published.add(letter_cv_target)
            letter_cv_combined = {
                "path": f"data/files/{application_id}/{letter_cv_name}", "name": letter_cv_name,
                "disk_path": str(letter_cv_target), "size": letter_cv_target.stat().st_size,
            }
        attachments_combined = None
        if len(attachments) == 1:
            attachments_combined = attachments[0]
        elif attachments:
            extras_name = f"Anlagen_{person}.pdf"
            extras_target = files_dir / application_id / extras_name
            if _combine_pdfs([Path(str(item["disk_path"])) for item in attachments], extras_target):
                published.add(extras_target)
                attachments_combined = {
                    "path": f"data/files/{application_id}/{extras_name}", "name": extras_name,
                    "disk_path": str(extras_target), "size": extras_target.stat().st_size,
                }
        brief_path = folder / "letter_brief.json"
        brief = json.loads(brief_path.read_text(encoding="utf-8")) if brief_path.exists() else {}
        package = {
            "id": application_id,
            "title": manifest.get("title", ""),
            "employer": manifest.get("employer", ""),
            "location": manifest.get("location", ""),
            "status": manifest.get("submission_status", ""),
            "confirmed_at": manifest.get("confirmed_at", ""),
            "queue_rank": manifest.get("queue_rank", 0),
            "urls": urls,
            "hosts": _hosts(urls),
            "folder": str(folder),
            "cv": publish(cv_pdf, f"files/{application_id}/{cv_name}", cv_name),
            "letter_pending": letter_pending,
            "attachments": attachments,
            "residence_documents": residence_documents,
            "combined": combined,
            "letter_cv_combined": letter_cv_combined,
            "attachments_combined": attachments_combined,
            "letter_text": "" if letter_pending else letter_text(folder / str(manifest["cover_letter"])),
            "cv_text": cv_text(folder / str(manifest["selected_cv"])),
            "job_excerpt": repair_mojibake(str(brief.get("ad_text") or ""))[:2500],
            "job_description": repair_mojibake(str(brief.get("ad_text_full") or brief.get("ad_text") or ""))[:15000],
            "job_description_source": str(brief.get("ad_text_full_source") or "excerpt"),
            "source": application_source(urls),
        }
        if not letter_pending:
            package["letter"] = publish(letter_pdf, f"files/{application_id}/{letter_name}", letter_name)
        packages.append(package)
    packages.sort(key=lambda item: (int(item["queue_rank"] or 0), str(item["id"])))
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "packages": packages,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    from .safeio import atomic_write_text
    atomic_write_text(data_dir / "data.json", text)
    stale_left = _remove_stale(files_dir, published)
    return {
        "stale_left": stale_left,
        "packages": [item["id"] for item in packages],
        "converted": len(pairs) if make_pdf else 0,
        "engine": engine,
        "conversion_seconds": round(conversion_seconds, 3),
        "seconds_per_document": round(conversion_seconds / len(pairs), 3) if pairs else 0,
        "missing_pdf": missing_pdf,
        "data_file": data_dir / "data.json",
        "description_backfill": backfill,
    }
