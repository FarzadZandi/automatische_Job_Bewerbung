"""Applicant language levels and Tier 2 skills, read from the private profile and answer bank.

Prompts contain placeholders such as {DE_LEVEL}; apply() fills them. Config.load() calls configure();
nothing personal is hardcoded in the package.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

HIGH_LEVELS = {"c1", "c2", "native", "muttersprache", "muttersprachlich", "muttersprachler"}
STATE: dict[str, Any] = {"de": "", "en": "", "statement": ""}
# Shared list object: modules that imported it see updates made by configure().
FORBIDDEN_SKILLS: list[str] = []


def _read(path: str | Path | None) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if path else {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, Mapping) else {}


def configure(profile: Mapping[str, Any] | str | Path | None = None, bank: Mapping[str, Any] | str | Path | None = None) -> None:
    profile = profile if isinstance(profile, Mapping) else _read(profile)
    bank = bank if isinstance(bank, Mapping) else _read(bank)
    STATE["de"] = str(profile.get("german_level") or "").strip()
    STATE["en"] = str(profile.get("english_level") or "").strip()
    STATE["statement"] = " ".join(str(profile.get("language_statement_de") or "").split())
    skills = [str(item).strip() for item in (bank.get("tier2_skills") or []) if str(item).strip()]
    FORBIDDEN_SKILLS[:] = [rf"\b{re.escape(skill.casefold())}\b" for skill in skills]


def german_level() -> str:
    return STATE["de"]


def german_capped() -> bool:
    """True when the applicant's German is below C1: claims above the stated level are blocked."""
    return STATE["de"].casefold() not in HIGH_LEVELS


def _de() -> str:
    return STATE["de"] or "dem im Profil angegebenen Niveau"


def _levels() -> str:
    parts = []
    if STATE["de"]:
        parts.append(f"Deutsch {STATE['de']}")
    if STATE["en"]:
        parts.append(f"Englisch {STATE['en']}")
    return ", ".join(parts) or "keine Sprachniveaus angegeben"


def letter_language_rule() -> str:
    if not german_capped():
        return ("Sprache: Erwähne Sprachkenntnisse nur, wenn die Anzeige sie verlangt, und nur so, wie sie in den "
                f"Fakten stehen ({_levels()}).")
    if STATE["statement"]:
        return ("Sprache: Nur wenn die Anzeige Deutsch auf C1, verhandlungssicher oder fließend verlangt, schreibe am "
                f'Ende genau diesen Satz: "{STATE["statement"]}" Sonst erwähne keine Sprachkenntnisse. '
                f"Behaupte nie mehr als {_de()} für Deutsch.")
    return f"Sprache: Erwähne keine Sprachkenntnisse. Behaupte nie mehr als {_de()} für Deutsch."


def apply(text: str) -> str:
    style = "mit einfachen korrekten Sätzen" + (f" auf {STATE['de']}-Niveau" if german_capped() and STATE["de"] else "")
    cap = (f"Behaupte für Deutsch nie mehr als {_de()}." if german_capped()
           else "Nenne Sprachniveaus nur so, wie sie in den Fakten stehen.")
    return (text.replace("{LETTER_LANGUAGE_RULE}", letter_language_rule())
                .replace("{DE_STYLE}", style)
                .replace("{DE_CAP}", cap)
                .replace("{LEVELS}", _levels())
                .replace("{DE_LEVEL}", _de()))
