from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Mapping, Sequence

from lxml import etree


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()


def _document(path: Path) -> tuple[list[tuple[zipfile.ZipInfo, bytes]], etree._Element, list[etree._Element]]:
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    xml = next(data for info, data in entries if info.filename == "word/document.xml")
    root = etree.fromstring(xml)
    return entries, root, root.xpath("//w:body/w:p", namespaces=NS)


def _slots(paragraphs: Sequence[etree._Element]) -> tuple[int, int]:
    greeting = next((i for i, p in enumerate(paragraphs) if _text(p).startswith("Sehr geehrt")), -1)
    closing = next((i for i, p in enumerate(paragraphs) if _text(p).startswith(("Aktuell bin ich", "Derzeit befinde", "Meinen M.Sc", "Einen möglichen Starttermin"))), -1)
    if greeting < 0 or closing < 0:
        raise ValueError("The cover letter does not contain its expected greeting and closing.")
    content = [i for i in range(greeting + 1, closing) if _text(paragraphs[i])]
    if len(content) < 4:
        raise ValueError("The cover letter does not contain the editable hook paragraphs.")
    return content[0], content[-1]


def read_letter(path: str | Path) -> dict[str, object]:
    target = Path(path)
    _, _, paragraphs = _document(target)
    intro_index, company_index = _slots(paragraphs)
    values = [_text(paragraph) for paragraph in paragraphs if _text(paragraph)]
    fixed = [_text(paragraph) for index, paragraph in enumerate(paragraphs) if _text(paragraph) and index not in {intro_index, company_index}]
    return {
        "paragraphs": values,
        "intro": _text(paragraphs[intro_index]),
        "company_paragraph": _text(paragraphs[company_index]),
        "fixed_paragraphs": fixed,
        "mtime": target.stat().st_mtime,
    }


def edit_letter(path: str | Path, intro: str, company_paragraph: str) -> dict[str, object]:
    """Replace the two hook paragraphs in place; all other paragraph text remains untouched."""
    target = Path(path)
    entries, root, paragraphs = _document(target)
    intro_index, company_index = _slots(paragraphs)
    before = [_text(paragraph) for index, paragraph in enumerate(paragraphs) if index not in {intro_index, company_index}]
    from .applications import _set_paragraph_text
    _set_paragraph_text(paragraphs[intro_index], " ".join(intro.split()))
    _set_paragraph_text(paragraphs[company_index], " ".join(company_paragraph.split()))
    after = [_text(paragraph) for index, paragraph in enumerate(paragraphs) if index not in {intro_index, company_index}]
    if before != after:
        raise ValueError("A fixed master paragraph changed; the letter was not saved.")
    patched = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".docx", dir=target.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as destination:
            for info, data in entries:
                destination.writestr(info, patched if info.filename == "word/document.xml" else data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return read_letter(target)


def package_folder(output_dir: str | Path, application_id: str) -> tuple[Path, dict[str, object]]:
    for manifest_path in Path(output_dir).glob("*/application.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if str(manifest.get("application_id")) == application_id:
            return manifest_path.parent, manifest
    raise FileNotFoundError(f"Application ID not found: {application_id}")


def letter_warnings(folder: Path, manifest: Mapping[str, object], intro: str, company: str, bank_path: Path) -> list[str]:
    from .applications import load_paragraph_bank
    from .autofill import cv_text
    from .hooks import _facts, check_hook
    facts = _facts(folder, manifest, load_paragraph_bank(bank_path))
    cv_path = folder / str(manifest.get("selected_cv") or "")
    letter_path = folder / str(manifest.get("cover_letter") or "")
    master = " ".join(read_letter(letter_path)["fixed_paragraphs"]) if letter_path.is_file() else ""
    vocabulary = " ".join((
        facts["title"], facts["employer"], facts["ad"], facts["proof"], facts["applicant"],
        cv_text(cv_path) if cv_path.is_file() else "", master,
    ))
    return check_hook({"intro": intro, "company_paragraph": company}, vocabulary)


def _export(config, application_id: str, *, converter: Callable[[Sequence[tuple[Path, Path]]], str] | None = None) -> dict[str, object]:
    from .autofill import export_autofill
    return export_autofill(
        config.application_output_dir, config.applicant_profile_path, config.autofill_extension_dir,
        converter=converter,
        attachment_order=config.application_attachment_allowlist,
        attachment_names=config.application_attachment_allowlist,
        residence_attachment_names=getattr(config, "application_residence_attachment_names", ()),
        attachments_dir=config.job_documents_dir / "Attachements",
        workbook_path=getattr(config, "application_workbook", None),
        database_path=getattr(config, "database_path", None),
        answer_bank_path=getattr(config, "answer_bank_path", None),
    )


def save_package(config, application_id: str, intro: str, company: str, *, converter=None) -> dict[str, object]:
    folder, manifest = package_folder(config.application_output_dir, application_id)
    path = folder / str(manifest.get("cover_letter") or "")
    warnings = letter_warnings(folder, manifest, intro, company, config.job_documents_dir / "anschreiben_bausteine.yaml")
    letter = edit_letter(path, intro, company)
    hook_path = folder / "hook.json"
    hook = json.loads(hook_path.read_text(encoding="utf-8")) if hook_path.exists() else {}
    hook.update({"intro": " ".join(intro.split()), "company_paragraph": " ".join(company.split()), "source": "user"})
    hook_path.write_text(json.dumps(hook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = _export(config, application_id, converter=converter)
    tracker = None
    if getattr(config, "application_archive_dir", None):
        from .archive import sync_archive
        tracker = sync_archive(config.application_output_dir, config.application_archive_dir)
    return {
        **letter, "warnings": warnings, "export": report, "tracker": tracker,
        "reopen": manifest.get("submission_status") == "opened",
    }
