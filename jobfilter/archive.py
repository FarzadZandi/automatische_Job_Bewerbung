"""Application archive: one Excel tracker with the CV and letter text of every application.

The layout follows the configured tracker (sheets "Bewerbungen", "Nicht beworben", "Legende"; the
same first thirteen columns), so earlier entries carry over unchanged. The pipeline adds and updates
its own rows by application ID and never edits the columns the user owns (Ergebnis, Quelle, Notizen).
When an application is marked submitted, the documents that were sent are copied next to the tracker.
"""
from __future__ import annotations

import json
import re
import shutil
from io import BytesIO
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from .applications import is_legacy_manifest
from .autofill import cv_text, docx_paragraphs, repair_mojibake
from .store import canonicalize_url

TRACKER_NAME = "Bewerbungs_Tracker.xlsx"
SHEET = "Bewerbungen"
HEADERS = (
    "Datum", "Unternehmen", "Position", "Typ", "JD-Sprache", "Score", "Lebenslauf", "Entscheidung",
    "Ergebnis", "Quelle", "Notizen", "CV_Output", "Anschreiben_Output", "ID", "Ort", "Link", "Ordner",
)
NOT_APPLIED_HEADERS = (
    "Datum", "Unternehmen", "Position", "JD-Sprache", "Score", "Grund", "Anschreiben_Output",
)
WIDTHS = {
    "Datum": 12, "Unternehmen": 28, "Position": 44, "Typ": 12, "JD-Sprache": 11, "Score": 20, "Lebenslauf": 11,
    "Entscheidung": 18, "Ergebnis": 28, "Quelle": 16, "Notizen": 60, "CV_Output": 40, "Anschreiben_Output": 40,
    "ID": 14, "Ort": 20, "Link": 30, "Ordner": 40,
}
DECISIONS = ("Paket erstellt", "Beworben", "Nicht beworben")
RESULTS = ("Offen", "Abgelehnt (Formschreiben)", "Abgelehnt, Grund genannt", "Interview", "Angebot")
LEGEND = (
    ("Feld", "Zulässige Werte und Bedeutung"),
    ("Datum", "JJJJ-MM-TT. Bei 'Paket erstellt' das Datum des Anschreibens, bei 'Beworben' der Tag der Einreichung."),
    ("Typ", "Vollzeit | Praktikum | WS | Trainee | Festanstellung | unklar"),
    ("Score", "Punkte und Band aus der Pipeline (z. B. '12 Punkte (PRIORITY)'). Ältere Zeilen: X/10 aus dem Fit-Read."),
    ("Lebenslauf", "configured CV families. Welche der zehn Fassungen verwendet wurde."),
    ("Entscheidung", "Paket erstellt | Beworben | Nicht beworben. Die Pipeline setzt 'Beworben' erst, wenn die Einreichung bestätigt ist."),
    ("Ergebnis", "Offen | Abgelehnt (Formschreiben) | Abgelehnt, Grund genannt | Interview | Angebot. Wird von Hand gepflegt."),
    ("Quelle", "Woher die Information stammt: Pipeline | E-Mail | Portal."),
    ("CV_Output", "Text des Lebenslaufs, der für diese Bewerbung angehängt wurde."),
    ("Anschreiben_Output", "Text des Anschreibens, das für diese Bewerbung angehängt wurde."),
    ("ID", "Kennung des Bewerbungspakets. Zeilen ohne ID wurden von Hand eingetragen."),
    ("Ordner", "Bei 'Beworben': Ordner mit den eingereichten PDFs. Sonst der Paketordner."),
    ("Regel", "Die Pipeline ändert nie die Spalten Ergebnis, Quelle und Notizen einer vorhandenen Zeile."),
    ("Regel", "Ein Eintrag wird nie gelöscht. Korrekturen kommen in die Spalte Notizen."),
)
EXTRA_LEGEND = LEGEND[8:13]
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
DATE_LINE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
SUBJECT_LINE = re.compile(r"^(?:Bewerbung\s+als\b|Application\s+for\b)", re.IGNORECASE)
GERMAN_WORDS = re.compile(r"\b(?:und|der|die|das|mit|für|wir|sie|ist|von)\b", re.IGNORECASE)
ENGLISH_WORDS = re.compile(r"\b(?:and|the|with|for|you|our|are|of|to|will)\b", re.IGNORECASE)


class ArchiveBusy(RuntimeError):
    """The tracker is open in Excel, so it cannot be written."""


def letter_document_text(path: str | Path) -> str:
    """The attached letter from its subject line through the signature."""
    paragraphs = docx_paragraphs(path)
    salutation = next((i for i, text in enumerate(paragraphs) if text.startswith(("Sehr geehrte", "Guten Tag", "Dear"))), 0)
    start = next((i for i, text in enumerate(paragraphs[:salutation + 1]) if SUBJECT_LINE.match(text)), salutation)
    end = len(paragraphs)
    for index in range(salutation, len(paragraphs)):
        if paragraphs[index].startswith(("Mit freundlichen Grüßen", "Kind regards", "Best regards")):
            end = next((j for j in range(index + 1, len(paragraphs)) if paragraphs[j]), index) + 1
            break
    text = "\n".join(paragraphs[start:end]).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _clean_letter_cell(text: object) -> str:
    """Remove an old address/date prefix while preserving the stored sent text."""
    value = str(text or "").strip()
    lines = value.splitlines()
    start = next((i for i, line in enumerate(lines) if SUBJECT_LINE.match(line.strip())), None)
    if start is None:
        return value
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines[start:]).strip())


def _language(text: str) -> str:
    german, english = len(GERMAN_WORDS.findall(text)), len(ENGLISH_WORDS.findall(text))
    if not german and not english:
        return ""
    return "DE" if german >= english else "EN"


def _style(sheet: Worksheet) -> None:
    headers = [cell.value for cell in sheet[1]]
    for index, name in enumerate(headers, 1):
        cell = sheet.cell(row=1, column=index)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        letter = get_column_letter(index)
        width = WIDTHS.get(str(name))
        if width:
            sheet.column_dimensions[letter].width = width
        elif (sheet.column_dimensions[letter].width or 0) > 80:
            sheet.column_dimensions[letter].width = 60
    sheet.freeze_panes = "A2"
    if sheet.max_row >= 1 and headers:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(sheet.max_row, 2)}"
    # Long texts stay on one line, so every application is one compact row.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)
        sheet.row_dimensions[row[0].row].height = 15


def _validations(sheet: Worksheet) -> None:
    headers = [cell.value for cell in sheet[1]]
    sheet.data_validations.dataValidation = []
    for name, values in (("Entscheidung", DECISIONS), ("Ergebnis", RESULTS)):
        if name not in headers:
            continue
        letter = get_column_letter(headers.index(name) + 1)
        # Suggestions only: values typed by hand stay allowed.
        rule = DataValidation(type="list", formula1='"' + ",".join(values) + '"', allow_blank=True, showErrorMessage=False)
        rule.add(f"{letter}2:{letter}2000")
        sheet.add_data_validation(rule)


def _ensure_headers(sheet: Worksheet, wanted: tuple[str, ...]) -> dict[str, int]:
    headers = [str(cell.value) if cell.value is not None else "" for cell in sheet[1]] if sheet.max_row else []
    headers = headers if any(headers) else []
    for name in wanted:
        if name not in headers:
            headers.append(name)
            sheet.cell(row=1, column=len(headers), value=name)
    return {name: index for index, name in enumerate(headers, 1) if name}


def open_archive(path: str | Path, *, import_from: str | Path | None = None) -> Workbook:
    """Load the tracker, or create it (optionally from an existing tracker workbook)."""
    archive_path = Path(path)
    if archive_path.exists():
        # Load from memory so Windows does not retain a ZIP handle on the live
        # tracker and block the later atomic replacement.
        workbook = load_workbook(BytesIO(archive_path.read_bytes()))
    elif import_from is not None:
        workbook = load_workbook(BytesIO(Path(import_from).read_bytes()))
    else:
        workbook = Workbook()
        workbook.active.title = SHEET
    if SHEET not in workbook.sheetnames:
        workbook.create_sheet(SHEET, 0)
    _ensure_headers(workbook[SHEET], HEADERS)
    if "Nicht beworben" not in workbook.sheetnames:
        _ensure_headers(workbook.create_sheet("Nicht beworben"), NOT_APPLIED_HEADERS)
    if "Legende" not in workbook.sheetnames:
        legend = workbook.create_sheet("Legende")
        for row in LEGEND:
            legend.append(row)
        legend.column_dimensions["A"].width, legend.column_dimensions["B"].width = 24, 110
    else:
        legend = workbook["Legende"]
        known = {str(row[0].value) for row in legend.iter_rows(min_row=1) if row[0].value}
        for row in EXTRA_LEGEND:
            if row[0] not in known:
                legend.append(row)
    return workbook


def _save(workbook: Workbook, path: Path) -> None:
    for name in (SHEET, "Nicht beworben"):
        _style(workbook[name])
    _validations(workbook[SHEET])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.stem}_backup_{datetime.now().date().isoformat()}{path.suffix}")
        if not backup.exists():
            shutil.copy2(path, backup)
    temporary = path.with_name(path.name + ".building.tmp")
    try:
        try:
            workbook.save(temporary)
        except PermissionError as error:
            raise ArchiveBusy(f"{path.name} is open in Excel. Close it, then run the step again.") from error
        # openpyxl keeps the source ZIP handle alive after load_workbook on Windows;
        # release our own handle before the atomic replacement.
        workbook.close()
        from .safeio import atomic_write_bytes
        if not atomic_write_bytes(path, temporary.read_bytes()):
            # The complete workbook remains queued in memory and will be retried by the
            # next tracker write. The status change itself must not be lost or blocked.
            return
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß]+", "-", value).strip("-")
    return cleaned[:40] or "Bewerbung"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _local_date(stamp: object) -> str:
    try:
        return datetime.fromisoformat(str(stamp)).astimezone().date().isoformat()
    except ValueError:
        return ""


def _letter_date(letter: Path) -> str:
    """The date printed in the letter (TT.MM.JJJJ), else the file date."""
    try:
        for text in docx_paragraphs(letter)[:12]:
            if DATE_LINE.match(text):
                day, month, year = text.split(".")
                return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        pass
    return datetime.fromtimestamp(letter.stat().st_mtime).date().isoformat()


def _read_text(reader, path: Path) -> str:
    """Text of a Word file; an unreadable file leaves the cell as it is instead of stopping the sync."""
    try:
        return reader(path) if path.is_file() else ""
    except (zipfile.BadZipFile, KeyError, OSError, ValueError):
        return ""


def _copy_sent(folder: Path, manifest: Mapping[str, object], target: Path) -> None:
    """Keep the documents that were sent: the PDFs, or the Word files when no PDF exists."""
    target.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(folder.glob("*.pdf"))
    sources = pdfs or [folder / str(manifest.get(key, "")) for key in ("selected_cv", "cover_letter")]
    for source in sources:
        if source.is_file() and not (target / source.name).exists():
            shutil.copy2(source, target / source.name)


def sync_archive(output_dir: str | Path, archive_dir: str | Path, *, import_from: str | Path | None = None) -> dict[str, object]:
    """Add or update the tracker rows of all packages that have a cover letter."""
    packages = Path(output_dir)
    archive = Path(archive_dir)
    path = archive / TRACKER_NAME
    workbook = open_archive(path, import_from=import_from)
    sheet = workbook[SHEET]
    columns = _ensure_headers(sheet, HEADERS)
    not_applied = workbook["Nicht beworben"]
    _ensure_headers(not_applied, NOT_APPLIED_HEADERS)

    package_letters: list[dict[str, object]] = []
    for manifest_path in sorted(packages.glob("*/application.json"), key=lambda item: item.parent.name.casefold()):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if is_legacy_manifest(manifest):
            continue
        letter_path = manifest_path.parent / str(manifest.get("cover_letter") or "")
        package_letters.append({
            "id": str(manifest.get("application_id") or "").casefold(),
            "job": (str(manifest.get("employer") or "").casefold(), str(manifest.get("title") or "").casefold()),
            "letter": letter_path if letter_path.is_file() else None,
        })

    by_package_id = {str(item["id"]): item for item in package_letters if item["id"]}
    by_package_job = {item["job"]: item for item in package_letters if all(item["job"])}
    backfill = {"filled": 0, "cleaned": 0, "left_empty": 0, "reasons": {}}

    def backfill_sheet(target: Worksheet) -> None:
        target_columns = _ensure_headers(target, NOT_APPLIED_HEADERS if target.title == "Nicht beworben" else HEADERS)
        for number in range(2, target.max_row + 1):
            def cell_text(name: str) -> str:
                column = target_columns.get(name)
                return str(target.cell(number, column).value or "").strip() if column else ""

            package = by_package_id.get(cell_text("ID").casefold())
            if package is None:
                package = by_package_job.get((cell_text("Unternehmen").casefold(), cell_text("Position").casefold()))
            if package is None:
                # Manual tracker rows are never changed, but empty letter cells are accounted for.
                letter_column = target_columns["Anschreiben_Output"]
                if any(target.cell(number, column).value not in (None, "") for column in range(1, target.max_column + 1)) \
                        and target.cell(number, letter_column).value in (None, ""):
                    backfill["left_empty"] += 1
                    reasons = backfill["reasons"]
                    reasons["no matching package"] = reasons.get("no matching package", 0) + 1
                continue
            letter_path = package["letter"]
            letter_cell = target.cell(number, target_columns["Anschreiben_Output"])
            if not letter_path:
                if letter_cell.value in (None, ""):
                    backfill["left_empty"] += 1
                    reasons = backfill["reasons"]
                    reasons["package has no letter DOCX"] = reasons.get("package has no letter DOCX", 0) + 1
                continue
            if letter_cell.value in (None, ""):
                body = _read_text(letter_document_text, Path(letter_path))
                if body:
                    letter_cell.value = body[:32000]
                    backfill["filled"] += 1
                else:
                    backfill["left_empty"] += 1
                    reasons = backfill["reasons"]
                    reasons["letter DOCX could not be read"] = reasons.get("letter DOCX could not be read", 0) + 1
            else:
                cleaned = _clean_letter_cell(letter_cell.value)
                if cleaned != str(letter_cell.value).strip():
                    letter_cell.value = cleaned[:32000]
                    backfill["cleaned"] += 1

    backfill_sheet(sheet)
    backfill_sheet(not_applied)

    def value(row: int, name: str) -> str:
        cell = sheet.cell(row=row, column=columns[name]).value
        return "" if cell is None else str(cell).strip()

    by_id: dict[str, int] = {}
    by_job: dict[tuple[str, str], int] = {}
    for row in range(2, sheet.max_row + 1):
        if value(row, "ID"):
            by_id[value(row, "ID").lower()] = row
        elif value(row, "Unternehmen"):
            by_job[(value(row, "Unternehmen").casefold(), value(row, "Position").casefold())] = row

    added = updated = saved = 0
    for manifest_path in sorted(packages.glob("*/application.json"), key=lambda item: item.parent.name.casefold()):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if is_legacy_manifest(manifest):
            continue
        folder = manifest_path.parent
        application_id = str(manifest.get("application_id", "")).lower()
        status = str(manifest.get("submission_status") or "")
        letter = folder / str(manifest.get("cover_letter") or "")
        has_letter = bool(manifest.get("cover_letter")) and letter.is_file()
        row = by_id.get(application_id)
        if row is None:
            row = by_job.get((str(manifest.get("employer", "")).casefold(), str(manifest.get("title", "")).casefold()))
        if status in {"skipped", "expired"}:
            decision = "Nicht beworben"
            if row is None:
                continue  # never offered for sending: nothing to record
        elif status == "submitted":
            decision = "Beworben"
        elif has_letter and status in {"not_submitted", "approved", "opened", "failed"}:
            decision = "Paket erstellt"
        else:
            continue

        def put(name: str, content: object, *, only_if_empty: bool = False) -> None:
            cell = sheet.cell(row=row, column=columns[name])
            if only_if_empty and cell.value not in (None, ""):
                return
            cell.value = content

        is_new = row is None
        if is_new:
            row = sheet.max_row + 1 if sheet.max_row > 1 or sheet.cell(row=1, column=1).value else 2
            row = max(row, 2)
            added += 1
        corrected_submission = bool(manifest.get("submission_correction_at")) and status != "submitted"
        frozen = (not is_new) and value(row, "Entscheidung").startswith("Beworben") and not corrected_submission
        put("ID", application_id)
        put("Unternehmen", manifest.get("employer", ""), only_if_empty=True)
        put("Position", manifest.get("title", ""), only_if_empty=True)
        put("Ort", manifest.get("location", ""), only_if_empty=True)
        put("Typ", "Vollzeit", only_if_empty=True)
        put("Lebenslauf", str(manifest.get("primary_cv_family", ""))[:3], only_if_empty=True)
        put("Score", f"{float(manifest.get('score') or 0):g} Punkte ({manifest.get('band', '')})", only_if_empty=True)
        put("Ergebnis", "Offen", only_if_empty=True)
        source = "manual" if str(manifest.get("board") or "").casefold() == "manual" else "Pipeline"
        put("Quelle", source, only_if_empty=True)
        brief_path = folder / "letter_brief.json"
        ad_text = ""
        if brief_path.exists():
            ad_text = repair_mojibake(str(json.loads(brief_path.read_text(encoding="utf-8-sig")).get("ad_text") or ""))
        put("JD-Sprache", _language(ad_text), only_if_empty=True)
        notes = [f"Treffer: {manifest.get('family_hits')}" if manifest.get("family_hits") else "",
                 f"Sprachhinweis: {manifest.get('language_flags')}" if manifest.get("language_flags") else ""]
        put("Notizen", ". ".join(note for note in notes if note), only_if_empty=True)
        if source == "manual":
            link = str(manifest.get("url") or manifest.get("application_url") or "")
        else:
            link = str(manifest.get("application_url") or manifest.get("url") or "")
        if link and not frozen:
            cell = sheet.cell(row=row, column=columns["Link"], value=link)
            cell.hyperlink = link
        if not frozen:
            if has_letter:
                family = str(manifest.get("primary_cv_family", ""))
                cv_path = folder / str(manifest.get("selected_cv", ""))
                cv_body = _read_text(cv_text, cv_path)
                if cv_body:
                    put("CV_Output", f"{family}\n\n{cv_body}"[:32000])
                letter_body = _read_text(letter_document_text, letter)
                if letter_body:
                    put("Anschreiben_Output", letter_body[:32000])
            date = _local_date(manifest.get("status_updated_at")) if decision == "Beworben" else ""
            if not date and has_letter:
                date = _letter_date(letter)
            if date:
                put("Datum", date)
            shown = folder
            if decision == "Beworben":
                shown = archive / "Unterlagen" / f"{date or 'ohne-Datum'}_{_slug(str(manifest.get('employer', '')))}_{application_id}"
                _copy_sent(folder, manifest, shown)
                saved += 1
            cell = sheet.cell(row=row, column=columns["Ordner"], value=_relative(shown, archive.parent))
            cell.hyperlink = _relative(shown, archive).replace("\\", "/") if shown.is_relative_to(archive) else None
        if not is_new and value(row, "Entscheidung") != decision:
            updated += 1
        put("Entscheidung", decision)
        by_id[application_id] = row
    _save(workbook, path)
    return {
        "path": path, "added": added, "updated": updated, "documents_saved": saved,
        "rows": sheet.max_row - 1, "letter_backfill": backfill,
    }


def record_manual_not_applied(archive_dir: str | Path, item: Mapping[str, object]) -> dict[str, object]:
    """Ensure a removed manual link remains in the tracker as not applied, even without a package."""
    archive = Path(archive_dir)
    path = archive / TRACKER_NAME
    workbook = open_archive(path)
    sheet = workbook[SHEET]
    columns = _ensure_headers(sheet, HEADERS)
    wanted_id = str(item.get("application_id") or "").casefold()
    wanted_url = canonicalize_url(str(item.get("url") or ""))
    row = None
    for number in range(2, sheet.max_row + 1):
        existing_id = str(sheet.cell(number, columns["ID"]).value or "").casefold()
        existing_url = canonicalize_url(str(sheet.cell(number, columns["Link"]).value or ""))
        if (wanted_id and existing_id == wanted_id) or (wanted_url and existing_url == wanted_url):
            row = number
            break
    if row is None:
        row = max(2, sheet.max_row + 1)
    current = str(sheet.cell(row, columns["Entscheidung"]).value or "")
    if current.startswith("Beworben"):
        _save(workbook, path)
        return {"path": path, "decision": "Beworben", "row": row}

    def put(name: str, value: object, *, only_empty: bool = False) -> None:
        cell = sheet.cell(row, columns[name])
        if only_empty and cell.value not in (None, ""):
            return
        cell.value = value

    put("Datum", str(item.get("date") or datetime.now().date().isoformat()), only_empty=True)
    put("Unternehmen", str(item.get("employer") or ""), only_empty=True)
    put("Position", str(item.get("title") or ""), only_empty=True)
    put("Ort", str(item.get("location") or ""), only_empty=True)
    employment = str(item.get("employment_format") or "")
    put("Typ", "Teilzeit" if "TEILZEIT" in employment.upper() or "PART-TIME" in employment.upper() else "Vollzeit", only_empty=True)
    score = item.get("score")
    band = str(item.get("band") or "")
    if score not in (None, ""):
        put("Score", f"{float(score):g} Punkte ({band})", only_empty=True)
    put("Lebenslauf", str(item.get("primary_cv_family") or "")[:3], only_empty=True)
    put("Entscheidung", "Nicht beworben")
    put("Ergebnis", "Offen", only_empty=True)
    put("Quelle", "manual", only_empty=True)
    put("Notizen", "Manuell aus der Liste entfernt", only_empty=True)
    put("ID", str(item.get("application_id") or ""), only_empty=True)
    link = str(item.get("url") or "")
    if link:
        cell = sheet.cell(row, columns["Link"], value=link)
        cell.hyperlink = link
    _save(workbook, path)
    return {"path": path, "decision": "Nicht beworben", "row": row}
