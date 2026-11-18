from __future__ import annotations

from pathlib import Path
import os
import shutil
import platform
import sys
from datetime import datetime, timezone

import yaml

from .pdfopt import optimised_path


EXPLICIT_CONFIG_KEYS = (
    "answer_bank_path", "applicant_file_stem", "applicant_profile_path", "application_archive_dir",
    "autofill_extension_dir", "claude_model", "letter_prefetch", "letter_writer", "openai_model",
    "home_city", "home_coordinates", "company_ats_location_terms",
)


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def replication_checks(config, config_path: str | Path) -> list[tuple[str, bool, str]]:
    path = Path(config_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        raw = {}
    missing_keys = [key for key in EXPLICIT_CONFIG_KEYS if key not in raw]
    checks: list[tuple[str, bool, str]] = [
        ("config keys", not missing_keys, "complete" if not missing_keys else "missing: " + ", ".join(missing_keys)),
        ("Claude model", "haiku" in str(config.claude_model).casefold(), str(config.claude_model)),
    ]

    attachment_dir = config.job_documents_dir / "Attachements"
    oversized: list[str] = []
    for name in config.application_attachment_allowlist:
        source = attachment_dir / name
        optimized = optimised_path(source)
        candidate = optimized if optimized.is_file() and source.is_file() and optimized.stat().st_mtime >= source.stat().st_mtime else source
        if candidate.is_file() and candidate.stat().st_size > 5 * 1024 * 1024:
            oversized.append(f"{name} ({candidate.stat().st_size / 1024 / 1024:.1f} MB)")
    checks.append(("attachment upload size", not oversized, "within 5 MB" if not oversized else ", ".join(oversized)))

    extension_data = config.autofill_extension_dir / "data"
    extension_bytes = directory_size(extension_data)
    checks.append((
        "extension data size", extension_bytes <= 15 * 1024 * 1024,
        f"{extension_bytes / 1024 / 1024:.1f} MB (limit 15 MB)",
    ))

    referenced = {
        "applicant profile": config.applicant_profile_path,
        "answer bank": config.answer_bank_path,
        "paragraph bank": config.job_documents_dir / "anschreiben_bausteine.yaml",
    }
    if config.github_projects_enabled:
        referenced["GitHub catalog"] = config.github_catalog_path
    for name in (*config.application_attachment_allowlist, *config.application_residence_attachment_names):
        referenced[f"attachment {name}"] = attachment_dir / name
    if config.applicant_status in {"enrolled", "graduating"}:
        for name in config.enrolment_attachment_names:
            referenced[f"enrolment attachment {name}"] = attachment_dir / name
    missing_files = [f"{name}: {file}" for name, file in referenced.items() if not Path(file).is_file()]
    checks.append((
        "configured Job App Docs files", not missing_files,
        "present" if not missing_files else "; ".join(missing_files),
    ))
    return checks


def doctor_checks(config, config_path: str | Path) -> list[tuple[str, bool, str]]:
    """All CLI/app setup checks with the concrete fix included in every missing detail."""
    checks = replication_checks(config, config_path)
    docs = config.job_documents_dir
    checks.append(("documents folder", docs.is_dir(), str(docs) if docs.is_dir() else f"create or configure {docs}"))
    cvs = list(docs.glob("*Lebenslauf*.docx")) if docs.is_dir() else []
    checks.append(("CV families", bool(cvs), f"{len(cvs)} DOCX file(s)" if cvs else f"add Lebenslauf DOCX files to {docs}"))
    templates = list(docs.glob("*Anschreiben*.docx")) if docs.is_dir() else []
    checks.append(("cover-letter template", bool(templates), f"{len(templates)} DOCX file(s)" if templates else f"add an Anschreiben DOCX to {docs}"))
    try:
        profile = yaml.safe_load(config.applicant_profile_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        profile = {}
    required = ("first_name", "last_name", "email", "german_level", "english_level", "start_date")
    missing = [key for key in required if not str(profile.get(key) or "").strip()]
    checks.append(("profile", not missing, "complete" if not missing else "fill: " + ", ".join(missing)))
    word = shutil.which("WINWORD.EXE") or next((str(path) for path in (
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft Office/root/Office16/WINWORD.EXE",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Office/root/Office16/WINWORD.EXE",
    ) if path.is_file()), "")
    from .osutil import is_windows, soffice_path
    office = soffice_path()
    if word and is_windows():
        checks.append(("PDF engine", True, f"Microsoft Word: {word}"))
    else:
        checks.append(("PDF engine", bool(office), f"LibreOffice: {office}" if office else (
            "install Microsoft Word or LibreOffice" if is_windows() else "install LibreOffice (https://www.libreoffice.org)")))
    from .claude_writer import claude_status
    claude = claude_status()
    claude_ok = config.letter_writer != "claude" or bool(claude.get("plan"))
    checks.append(("Claude plan", claude_ok, "not required" if config.letter_writer != "claude" else (
        "ready" if claude_ok else str(claude.get("login") or "run the shown Claude login command"))))
    data = config.autofill_extension_dir / "data" / "data.json"
    checks.append(("extension export", data.is_file(), str(data) if data.is_file() else "click Prepare or Update PDFs and form data"))
    return checks


def status_report(config, config_path: str | Path) -> dict:
    """Machine-readable setup state for the setup Claude (read through the folder connection)."""
    from .osutil import browser_path, claude_candidates, soffice_path
    checks = doctor_checks(config, config_path)
    problems = [f"{name}: {detail}" for name, ok, detail in checks if not ok]
    browser = browser_path(config.browser)
    docs = config.job_documents_dir
    families: list[str] = []
    keywords_error = ""
    try:
        from .scoring import family_codes, load_keywords
        families = list(family_codes(load_keywords(config.keywords_path)))
    except (OSError, ValueError, KeyError) as error:
        keywords_error = str(error)[:200]
    cv_files = {code: bool(docs.is_dir() and list(docs.glob(f"*Lebenslauf*_{code[1:]}.docx"))) for code in families}
    bank: dict[str, str] = {}
    bank_path = docs / "anschreiben_bausteine.yaml"
    if families and bank_path.exists():
        from .paragraph_bank import check_paragraph_bank
        report = check_paragraph_bank(bank_path, families=tuple(families))
        for code in families:
            bank[code] = "ok" if code in report.complete else ("missing" if code in report.missing else "problem")
    try:
        profile = yaml.safe_load(config.applicant_profile_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        profile = {}
    attachments = docs / "Attachements"
    candidates = claude_candidates()
    return {
        "os": platform.system(), "os_version": platform.version(), "python_version": platform.python_version(),
        "python_found": True, "python_path": sys.executable,
        "venv_ok": sys.prefix != getattr(sys, "base_prefix", sys.prefix), "deps_ok": True,
        "browser": "edge" if browser and "edge" in browser.casefold() else ("chrome" if browser else "none"),
        "browser_path": browser or "",
        "pdf_engine": next((detail.split(":")[0].lower().replace("microsoft ", "") for name, ok, detail in checks
                            if name == "PDF engine" and ok), "none"),
        "claude_cli": {"found": bool(candidates), "path": candidates[0] if candidates else ""},
        "claude_signed_in": next((ok for name, ok, _ in checks if name == "Claude plan"), False),
        "profile_missing_fields": [key for key in ("first_name", "last_name", "email", "german_level", "english_level", "start_date")
                                   if not str(profile.get(key) or "").strip()],
        "answers_ok": config.answer_bank_path.is_file(),
        "keywords": {"ok": bool(families), "families": len(families), "error": keywords_error},
        "cv_files": cv_files, "bank": bank,
        "letter_template_ok": bool(docs.is_dir() and list(docs.glob("*Anschreiben*.docx"))),
        "extension_folder_ok": config.autofill_extension_dir.is_dir(),
        "attachments_count": len([p for p in attachments.iterdir() if p.is_file()]) if attachments.is_dir() else 0,
        "archive_dir_ok": Path(config.jd_archive_dir).parent.is_dir(),
        "ready": not problems, "problems": problems,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
