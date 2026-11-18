"""Validation for the cover-letter proof-paragraph bank (anschreiben_bausteine*.yaml).

The checker never judges whether a claim is true; that is what the NACHWEIS file
and the applicant's review are for. It enforces the mechanical rules: structure,
length, banned phrases, skills that may only appear as Grundkenntnisse, and
dash usage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

MISSING_MARKER = "[FEHLT"
FAMILIES = tuple(f"B{index:02d}" for index in range(1, 11))
MIN_WORDS = 60
MAX_WORDS = 90
BANNED_PHRASES = (
    "hoch motiviert", "hochmotiviert", "leidenschaftlich", "perfekt passend",
    "freue mich sehr", "dynamischer teamplayer", "ich brenne für",
    "highly motivated", "passionate about", "excited to apply",
)
# Grundkenntnisse only: never presented as applied experience in a letter.
from .applicant import FORBIDDEN_SKILLS  # filled from the answer bank's tier2_skills
GERMAN_LEVEL_CLAIM = re.compile(
    r"\bdeutsch\w*\b[^.]{0,60}\b(?:c1|c2|fließend|fliessend|verhandlungssicher|muttersprach\w*)\b",
    re.IGNORECASE,
)
EN_DASH_RANGE = re.compile(r"[\d%)]\s*–\s*\d")


@dataclass
class BankReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    word_counts: dict[str, tuple[int, ...]] = field(default_factory=dict)
    complete: list[str] = field(default_factory=list)


def word_count(text: str) -> int:
    return sum(1 for token in text.split() if re.search(r"\w", token))


def _dash_problems(text: str) -> list[str]:
    problems = []
    if "—" in text:
        problems.append("contains an em dash")
    for match in re.finditer("–", text):
        window = text[max(0, match.start() - 3):match.end() + 3]
        if not EN_DASH_RANGE.search(window):
            problems.append("en dash outside a number or date range")
            break
    if re.search(r"\s-\s", text):
        problems.append("spaced hyphen used as a dash")
    return problems


def check_paragraph(family: str, index: int, text: str, *, forbidden_skills: tuple[str, ...] = FORBIDDEN_SKILLS, german_level: str = "") -> list[str]:
    """Return rule violations for one paragraph (empty list when it passes)."""
    label = f"{family} paragraph {index}"
    lowered = text.casefold()
    problems: list[str] = []
    words = word_count(text)
    if not MIN_WORDS <= words <= MAX_WORDS:
        problems.append(f"{label}: {words} words, expected {MIN_WORDS} to {MAX_WORDS}")
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            problems.append(f"{label}: banned phrase '{phrase}'")
    for pattern in forbidden_skills:
        match = re.search(pattern, lowered)
        if match:
            problems.append(f"{label}: '{match.group(0)}' is Grundkenntnisse only and cannot be a proof")
    from . import applicant
    level = german_level or applicant.german_level()
    if level.casefold() not in applicant.HIGH_LEVELS and GERMAN_LEVEL_CLAIM.search(text):
        problems.append(f"{label}: claims a German level above {level or 'the profile level'}")
    problems.extend(f"{label}: {problem}" for problem in _dash_problems(text))
    return problems


def check_paragraph_bank(path: str | Path, *, families: tuple[str, ...] | None = None, tier2_skills: tuple[str, ...] = (), german_level: str = "") -> BankReport:
    report = BankReport()
    bank_path = Path(path)
    if not bank_path.exists():
        report.errors.append(f"File does not exist: {bank_path}")
        return report
    try:
        raw = yaml.safe_load(bank_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        report.errors.append(f"Invalid YAML: {exc}")
        return report
    if not isinstance(raw, Mapping):
        report.errors.append("The bank must be a mapping of CV family codes (B01, B02, ...)")
        return report
    expected = families or FAMILIES
    unknown = sorted(set(map(str, raw)) - set(expected))
    if unknown:
        report.errors.append("Unknown keys: " + ", ".join(unknown))
    seen: dict[str, str] = {}
    forbidden = tuple(rf"\b{re.escape(skill)}\b" for skill in tier2_skills) if tier2_skills else FORBIDDEN_SKILLS
    for family in expected:
        paragraphs = raw.get(family)
        if not isinstance(paragraphs, list) or len(paragraphs) != 2:
            report.errors.append(f"{family}: needs exactly two paragraphs")
            continue
        texts = [" ".join(str(value or "").split()) for value in paragraphs]
        if any(MISSING_MARKER in text for text in texts):
            report.missing.append(family)
            continue
        if texts[0] == texts[1]:
            report.errors.append(f"{family}: both paragraphs are identical")
        family_errors: list[str] = []
        for index, text in enumerate(texts, 1):
            family_errors.extend(check_paragraph(family, index, text, forbidden_skills=forbidden, german_level=german_level))
            if text in seen:
                report.warnings.append(f"{family} paragraph {index} repeats {seen[text]}")
            else:
                seen[text] = f"{family} paragraph {index}"
        report.word_counts[family] = tuple(word_count(text) for text in texts)
        report.errors.extend(family_errors)
        if not family_errors:
            report.complete.append(family)
    return report


def format_bank_report(report: BankReport, path: str | Path) -> str:
    lines = [f"Paragraph bank: {path}"]
    for family in sorted(set(report.missing) | set(report.word_counts)):
        if family in report.missing:
            lines.append(f"  {family}  FEHLT (not usable until filled)")
        elif family in report.word_counts:
            counts = " / ".join(str(count) for count in report.word_counts[family])
            state = "OK" if family in report.complete else "ERROR"
            lines.append(f"  {family}  {state}  words {counts}")
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    for warning in report.warnings:
        lines.append(f"WARNING: {warning}")
    lines.append(
        f"Summary: {len(report.complete)} usable, {len(report.missing)} FEHLT, "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    )
    return "\n".join(lines)
