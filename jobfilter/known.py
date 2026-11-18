"""Jobs already dealt with, so a search never brings them back.

Sources: every row of the application tracker ("Bewerbungen" and "Nicht beworben", including rows
typed in by hand) and every application package, whatever its status. A job matches when the
employer and the job title agree, tolerant of gender tags such as (m/w/d), legal forms such as
GmbH, and small wording differences.
"""
from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)
GENDER_TAG = re.compile(
    r"[(\[]\s*(?:[mwfdxi]|div(?:ers)?|all\s+genders?|gn)(?:\s*[/|,*]\s*(?:[mwfdxi]|div(?:ers)?))*\s*[)\]]",
    re.IGNORECASE,
)
LEGAL = {
    "gmbh", "ag", "se", "kg", "co", "mbh", "ohg", "ug", "kgaa", "ev", "gbr", "plc", "ltd", "inc", "llc", "sarl",
    "sa", "bv", "nv", "holding", "group", "gruppe", "deutschland", "germany", "haftungsbeschraenkt", "und", "the",
}
# One of these alone does not identify an employer.
COMMON = {
    "consulting", "solutions", "services", "service", "digital", "software", "systems", "technologies", "technology",
    "management", "partner", "partners", "bank", "personal", "international", "energie", "energy", "logistik",
    "logistics", "media", "marketing", "beratung", "it", "data", "analytics", "stadt", "universitaet",
}
FILLER = {
    "und", "and", "the", "der", "die", "das", "fuer", "for", "im", "in", "mit", "of", "zur", "zum", "bei", "als", "an",
    "de", "en", "mwd", "wmd", "gn", "all", "genders", "gender", "vollzeit", "teilzeit", "full", "time", "part",
    "remote", "hybrid", "befristet", "unbefristet", "ab", "sofort", "job", "stelle",
}


def _words(text: object) -> list[str]:
    value = GENDER_TAG.sub(" ", str(text or "")).casefold()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        value = value.replace(umlaut, plain)
    return [word for word in re.split(r"[^a-z0-9]+", value) if len(word) > 1]


def employer_key(name: object) -> frozenset[str]:
    return frozenset(word for word in _words(name) if word not in LEGAL)


def title_key(title: object) -> frozenset[str]:
    return frozenset(word for word in _words(title) if word not in FILLER)


def same_employer(first: frozenset[str], second: frozenset[str]) -> bool:
    if not first or not second:
        return False
    if first == second:
        return True
    small, large = sorted((first, second), key=len)
    if not small <= large:
        return False
    return len(small) >= 2 or not small <= COMMON


def same_title(first: frozenset[str], second: frozenset[str]) -> bool:
    if not first or not second:
        return False
    if first == second:
        return True
    small, large = sorted((first, second), key=len)
    if len(small) >= 2 and small <= large:
        return True
    return len(first & second) / len(first | second) >= 0.75


@dataclass
class KnownPositions:
    entries: list[tuple[frozenset[str], frozenset[str], str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, tracker: str | Path | None, output_dir: str | Path | None) -> "KnownPositions":
        known = cls()
        if tracker and Path(tracker).is_file():
            known._read_tracker(Path(tracker))
        if output_dir and Path(output_dir).is_dir():
            for path in Path(output_dir).glob("*/application.json"):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    continue
                known.add(
                    manifest.get("employer"), manifest.get("title"),
                    f"package exists ({manifest.get('submission_status') or 'open'})", manifest.get("application_id"),
                )
        return known

    def _read_tracker(self, tracker: Path) -> None:
        from openpyxl import load_workbook  # only when a tracker exists

        try:
            workbook = load_workbook(tracker, read_only=True, data_only=True)
        except (OSError, zipfile.BadZipFile, KeyError, ValueError) as error:
            LOGGER.warning("Could not read the tracker %s: %s", tracker, error)
            return
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                header = [str(cell or "").strip() for cell in (next(rows, None) or ())]
                if "Unternehmen" not in header or "Position" not in header:
                    continue
                employer, title = header.index("Unternehmen"), header.index("Position")
                ident = header.index("ID") if "ID" in header else None
                reason = "rejected earlier (tracker)" if sheet.title.casefold().startswith("nicht") else "in the tracker"
                for row in rows:
                    cells = list(row) + [None] * len(header)
                    self.add(cells[employer], cells[title], reason, cells[ident] if ident is not None else "")
        finally:
            workbook.close()

    def add(self, employer: object, title: object, reason: str, application_id: object = "") -> None:
        employer_words, title_words = employer_key(employer), title_key(title)
        if employer_words and title_words:
            self.entries.append((employer_words, title_words, reason, str(application_id or "").strip().lower()))

    def match(self, title: object, employer: object, *, except_id: str = "") -> str:
        """Why this job is already dealt with, or "" when it is new."""
        employer_words, title_words = employer_key(employer), title_key(title)
        skip_id = except_id.strip().lower()
        for known_employer, known_title, reason, application_id in self.entries:
            if skip_id and application_id == skip_id:
                continue
            if same_employer(employer_words, known_employer) and same_title(title_words, known_title):
                return reason
        return ""
