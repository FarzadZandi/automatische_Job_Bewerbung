"""Job description archive (Stellenarchiv) for later CV adaptation.

Applied and skipped jobs keep their full ad text in one Markdown file per job; dropped and expired jobs get an
index line only. Every write is atomic and locked; repeated calls with the same status add nothing.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from .safeio import atomic_write_text, file_lock

LOGGER = logging.getLogger(__name__)
COLUMNS = (
    "archived_at", "application_id", "status", "reason", "primary_family", "secondary_family", "family_scores",
    "score", "band", "employer", "title", "location", "url", "source", "language_flags", "posting_date", "file",
)
STATUSES = {"applied", "skipped", "dropped", "expired"}
FULL_TEXT_STATUSES = {"applied", "skipped"}


def _value(job: object, name: str, default: Any = "") -> Any:
    return job.get(name, default) if isinstance(job, Mapping) else getattr(job, name, default)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:60] or "unknown"


def _read_rows(index: Path) -> list[dict[str, str]]:
    if not index.exists():
        return []
    return list(csv.DictReader(io.StringIO(index.read_text(encoding="utf-8-sig"))))


def record_outcome(
    archive_dir: str | Path, job: object, status: str, *, reason: str = "", result: object | None = None,
    full_text: str = "", cv_file: str = "", letter_file: str = "",
) -> Path | None:
    """Archive one job decision. Returns the full-text file for applied/skipped, else None."""
    if status not in STATUSES:
        raise ValueError("status must be applied, skipped, dropped, or expired")
    root = Path(archive_dir)
    root.mkdir(parents=True, exist_ok=True)
    app_id = str(_value(job, "application_id") or _value(job, "id") or "")
    url = str(_value(job, "url") or "")
    identity = app_id or url
    if not identity:
        raise ValueError("a job needs an application_id or a url to be archived")
    now = datetime.now(timezone.utc)
    score_obj = result or job
    scores = _value(score_obj, "family_scores", {}) or {}
    row = {
        "archived_at": now.isoformat(timespec="seconds"), "application_id": app_id, "status": status, "reason": reason,
        "primary_family": _value(score_obj, "primary_cv_family"), "secondary_family": _value(score_obj, "secondary_cv_family"),
        "family_scores": json.dumps(scores, ensure_ascii=False) if isinstance(scores, Mapping) else str(scores),
        "score": _value(score_obj, "score"), "band": _value(score_obj, "band"),
        "employer": _value(job, "employer") or _value(job, "company"), "title": _value(job, "title"),
        "location": _value(job, "location"), "url": url, "source": _value(job, "source") or _value(job, "board"),
        "language_flags": " ".join(_value(score_obj, "language_flags", ()) or ()) if not isinstance(_value(score_obj, "language_flags", ""), str) else _value(score_obj, "language_flags", ""),
        "posting_date": _value(job, "posting_date") or _value(job, "posted"), "file": "",
    }
    index = root / "index.csv"
    output: Path | None = None
    with file_lock(root / ".index.guard") as owned:
        if not owned:
            raise OSError(f"Could not lock {index}")
        rows = _read_rows(index)
        mine = [r for r in rows if (r.get("application_id") or r.get("url")) == identity]
        if status in FULL_TEXT_STATUSES:
            known = next((r["file"] for r in mine if r.get("file")), "")
            if known:
                output = root / known
            else:
                short = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
                output = root / f"{now:%Y}" / f"{now:%Y-%m-%d}_{_slug(str(row['employer']))}_{_slug(str(row['title']))}_{short}.md"
            row["file"] = str(output.relative_to(root)).replace("\\", "/")
            text = full_text or str(_value(job, "full_text") or _value(job, "description") or "")
            note = ""
            if not text:
                text = str(_value(job, "excerpt") or _value(job, "ad_text") or "")
                note = "\n\n> nur Auszug"
            front = {**row, "cv_file": cv_file, "letter_file": letter_file}
            atomic_write_text(output, "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False)
                              + "---\n\n## Stellenanzeige\n\n" + text.strip() + note + "\n")
        if any(r.get("status") == status for r in mine):
            return output
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        if not rows and not index.exists():
            writer.writeheader()
        writer.writerow(row)
        if index.exists():
            with index.open("a", encoding="utf-8", newline="") as handle:
                handle.write(stream.getvalue())
        else:
            atomic_write_text(index, stream.getvalue(), encoding="utf-8-sig")
    return output


def archive_package(archive_dir: str | Path, package_dir: str | Path, status: str, *, reason: str = "") -> Path | None:
    """Archive a prepared application package (application.json + letter_brief.json). Never raises."""
    try:
        folder = Path(package_dir)
        manifest = json.loads((folder / "application.json").read_text(encoding="utf-8-sig"))
        brief_path = folder / "letter_brief.json"
        brief = json.loads(brief_path.read_text(encoding="utf-8-sig")) if brief_path.exists() else {}
        full = str(brief.get("ad_text_full") or "") if brief.get("ad_text_full_source") == "full" else ""
        job = {**manifest, "excerpt": brief.get("ad_text_full") or brief.get("ad_text") or ""}
        job.setdefault("url", manifest.get("application_url") or manifest.get("external_url") or "")
        return record_outcome(
            archive_dir, job, status, reason=reason, full_text=full,
            cv_file=str(manifest.get("selected_cv") or ""), letter_file=str(manifest.get("cover_letter") or ""),
        )
    except Exception as error:  # noqa: BLE001 - archiving must never break the application flow
        LOGGER.warning("Stellenarchiv: %s not archived: %s", package_dir, error)
        return None


def archive_drop(archive_dir: str | Path, ad: object, result: object, *, reason: str = "") -> None:
    """Index line for a job a gate removed. Never raises."""
    try:
        record_outcome(archive_dir, ad, "dropped", reason=reason, result=result)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("Stellenarchiv: dropped job not indexed: %s", error)
