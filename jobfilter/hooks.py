"""Prompt, parsing, and safety checks shared by automated cover-letter writing."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .applications import load_paragraph_bank
from .autofill import repair_mojibake
from . import applicant
from .paragraph_bank import BANNED_PHRASES, FORBIDDEN_SKILLS, GERMAN_LEVEL_CLAIM, _dash_problems, word_count

INTRO_WORDS = (18, 45)
PARAGRAPH_WORDS = (55, 140)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
FULL_AD_CHARS = 8000
RULES = """\
Ziel: Die Personalabteilung soll nach dem Lesen denken, dass dieser Bewerber die Aufgaben der Stelle erledigen kann.

Regeln für jeden Text:
1. Nutze nur die Fakten aus den Belegen, den Fakten für alle Stellen und der Anzeige. Erfinde nichts: keine Arbeitgeber, Projekte, Zahlen, Prozentwerte, Tools, Kurse, Zertifikate oder Branchenerfahrung.
2. "intro" hat ein bis zwei Sätze (20 bis 40 Wörter). Er nennt eine konkrete Aufgabe oder ein Ziel aus der Anzeige und endet mit der Bewerbung auf die Stelle. Er folgt direkt auf "Sehr geehrte Damen und Herren," und beginnt deshalb klein.
3. "company_paragraph" hat 90 bis 130 Wörter. Er nimmt zwei oder drei konkrete Aufgaben aus der Anzeige und zeigt zu jeder eine passende belegte Erfahrung (Handlung, Methode, Ergebnis). Er sagt am Ende in einem Satz, was der Bewerber damit für dieses Unternehmen leisten wird.
4. Schreibe nur über das, was der Bewerber kann. Erwähne NIE fehlende Erfahrung, fehlende Tools, einen anderen Studienabschluss oder ein niedrigeres Sprachniveau. Sätze wie "habe ich nicht", "gehört nicht zu meinen Erfahrungen", "sage ich offen" oder "fehlt mir" sind verboten.
5. Wiederhole keinen Satz und keine Aussage aus den zwei Belegen der eigenen Stelle (Abschnitt BELEGE, Kennung der Stelle): diese stehen bereits im Anschreiben. Nutze für den Passungsabsatz vor allem WEITERE BELEGE und die Fakten für alle Stellen.
6. {LETTER_LANGUAGE_RULE}
7. Verboten: hoch motiviert, leidenschaftlich, perfekt passend, freue mich sehr, dynamischer Teamplayer, ich brenne für.
8. Keine Gedankenstriche als Satzzeichen (weder — noch –). Nutze Komma, Doppelpunkt oder einen neuen Satz.
9. Formelles Deutsch in der Sie-Form, wie in einem Geschäftsbrief: sachlich, konkret, einfache korrekte Sätze. Kein Du, keine Umgangssprache, auch wenn die Anzeige duzt. Keine Zahl, die nicht in den Fakten steht.

Antworte NUR mit JSON in einer Zeile, ohne Einleitung, ohne Erklärung, ohne Rückfragen:
{"hooks":[{"id":"<id>","intro":"<Einleitung>","company_paragraph":"<Absatz>"}]}"""


def _waiting(output_dir: Path) -> list[tuple[Path, dict[str, object]]]:
    found = []
    for manifest_path in sorted(output_dir.glob("*/application.json"), key=lambda item: item.parent.name.casefold()):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest.get("submission_status") == "needs_hook" and not (manifest_path.parent / "hook.json").exists():
            found.append((manifest_path.parent, manifest))
    return found


def _facts(folder: Path, manifest: Mapping[str, object], bank: Mapping[str, tuple[str, str]]) -> dict[str, str]:
    brief_path = folder / "letter_brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8-sig")) if brief_path.exists() else {}
    family = str(brief.get("family") or str(manifest.get("primary_cv_family", ""))[:3])
    proof = bank.get(family, ("", ""))
    return {
        "id": str(manifest["application_id"]),
        "title": str(manifest.get("title", "")),
        "employer": str(manifest.get("employer", "")),
        "language": str(manifest.get("language_flags", "")),
        "ad": repair_mojibake(str(brief.get("ad_text_full") or brief.get("ad_text") or ""))[:FULL_AD_CHARS],
        "proof": "\n".join(proof),
        "applicant": str(brief.get("applicant_facts") or ""),
    }


def all_evidence(bank: Mapping[str, tuple[str, str]]) -> str:
    """Every checked proof paragraph: all of it is the applicant's real, verified experience."""
    return "\n".join(text for pair in bank.values() for text in pair)


def extract_json(text: str) -> dict[str, object]:
    """Keep only the JSON object of a chat reply; greetings, notes and code fences around it are dropped."""
    cleaned = re.sub(r"```(?:json)?", "", text or "", flags=re.IGNORECASE)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("The model reply contains no JSON.")
    decoder = json.JSONDecoder()
    while start >= 0:
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            start = cleaned.find("{", start + 1)
            continue
        if isinstance(value, dict) and isinstance(value.get("hooks"), list):
            return value
        start = cleaned.find("{", start + 1)
    raise ValueError('The model reply has no readable {"hooks": [...]} object.')


# A letter never volunteers a gap: these phrasings announce missing experience, tools or level.
GAP_STATEMENT = re.compile(
    r"\bhabe ich (?:bisher |noch )?(?:nicht|keine?n?)\b|geh[öo]r(?:t|en) nicht zu meinen|sage ich offen|"
    r"fehl(?:t|en) mir|nicht (?:belegt|vorweisen|nachweisen)|keine (?:praktische |belegte )?erfahrung|"
    r"verf[üu]ge ich nicht|bringe ich (?:noch )?nicht mit|dieses niveau habe ich nicht|"
    r"(?:noch )?nicht (?:gearbeitet|eingesetzt|genutzt)|"
    r"\b(?:i have|i've|i do) (?:not|no)\b|\bno (?:direct |practical )?experience\b|"
    r"\b(?:have not|haven't) (?:used|worked|applied)\b|\bi lack\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?;])\s+", text or "") if part.strip()]


def _overlap(sentence: str, other: str) -> float:
    a = {word for word in re.findall(r"\w{4,}", sentence.casefold())}
    b = {word for word in re.findall(r"\w{4,}", other.casefold())}
    return len(a & b) / len(a) if len(a) >= 6 else 0.0


def check_hook(hook: Mapping[str, object], facts_text: str, proof_text: str = "") -> list[str]:
    """Rule violations of one hook (empty list when it may be used)."""
    problems: list[str] = []
    proof_sentences = _sentences(proof_text)
    allowed_numbers = set(NUMBER.findall(facts_text))
    for key, (low, high) in (("intro", INTRO_WORDS), ("company_paragraph", PARAGRAPH_WORDS)):
        text = " ".join(str(hook.get(key) or "").split())
        if not text:
            problems.append(f"{key}: empty")
            continue
        words = word_count(text)
        if not low <= words <= high:
            problems.append(f"{key}: {words} words, expected {low} to {high}")
        lowered = text.casefold()
        problems += [f"{key}: banned phrase '{phrase}'" for phrase in BANNED_PHRASES if phrase in lowered]
        for pattern in FORBIDDEN_SKILLS:
            match = re.search(pattern, lowered)
            if match:
                problems.append(f"{key}: '{match.group(0)}' is Grundkenntnisse only")
        # An English level in the same sentence ("Deutsch B2, Englisch C1") is not a German claim.
        if applicant.german_capped() and GERMAN_LEVEL_CLAIM.search(re.sub(r"\bEnglisch\w*[^.,;]{0,40}", "", text)):
            problems.append(f"{key}: claims a German level above {applicant.german_level() or 'the profile level'}")
        problems += [f"{key}: {problem}" for problem in _dash_problems(text)]
        invented = sorted(set(NUMBER.findall(text)) - allowed_numbers)
        if invented:
            problems.append(f"{key}: number(s) not in the facts: {', '.join(invented)}")
        if "[" in text or "<" in text:
            problems.append(f"{key}: contains a placeholder")
        gap = GAP_STATEMENT.search(text)
        if gap:
            problems.append(f"{key}: states a gap ('{gap.group(0)}'); write only what the applicant can do")
        for sentence in _sentences(text):
            if any(_overlap(sentence, proof) >= 0.6 for proof in proof_sentences):
                problems.append(f"{key}: repeats a proof paragraph sentence: '{sentence[:80]}'")
                break
    return problems


def import_hooks(
    output_dir: str | Path, bank_path: str | Path, reply: str, *, force: bool = False,
) -> dict[str, object]:
    """Write hook.json for every hook in the reply that passes the checks."""
    destination = Path(output_dir)
    bank = load_paragraph_bank(bank_path)
    payload = extract_json(reply)
    folders: dict[str, tuple[Path, dict[str, object]]] = {}
    for manifest_path in destination.glob("*/application.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        folders[str(manifest.get("application_id", "")).lower()] = (manifest_path.parent, manifest)
    written: list[str] = []
    rejected: dict[str, list[str]] = {}
    for item in payload["hooks"]:
        if not isinstance(item, dict):
            continue
        application_id = str(item.get("id", "")).strip().lower()
        if application_id not in folders:
            rejected[application_id or "?"] = ["no package with this id"]
            continue
        folder, manifest = folders[application_id]
        if manifest.get("submission_status") != "needs_hook" and not force:
            rejected[application_id] = [f"package is {manifest.get('submission_status')}, not needs_hook (use --force to replace)"]
            continue
        if (folder / "hook.json").exists() and not force:
            rejected[application_id] = ["hook.json already exists (use --force to replace)"]
            continue
        facts = _facts(folder, manifest, bank)
        facts_text = " ".join([facts["title"], facts["employer"], facts["ad"], all_evidence(bank), facts["applicant"]])
        hook = {key: " ".join(str(item.get(key) or "").split()) for key in ("intro", "company_paragraph")}
        problems = check_hook(hook, facts_text, facts["proof"])
        if problems:
            rejected[application_id] = problems
            continue
        (folder / "hook.json").write_text(json.dumps(hook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(application_id)
    return {"written": written, "rejected": rejected}
