"""Deterministic, user-owned answers for recurring application questions.

The bank is local and is never sent to a model.  Matching deliberately prefers an
empty result over guessing a fact, tool category, amount of experience, or rating.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

import yaml


FACT_PATTERNS = {
    "notice_period": re.compile(r"notice period|k[uü]ndigungsfrist", re.I),
    "start_date": re.compile(
        r"start date|earliest.*start|available.*start|eintritt|verf[uü]gbar|starttermin|wunschtermin|startdatum|"
        r"\bstarten\b|when.{0,20}(?:can|could|would).{0,20}(?:you)?.{0,20}start|"
        r"wann.{0,30}(?:k[oö]nnten|k[oö]nnen|kannst).{0,30}(?:sie|du)?.{0,20}(?:beginnen|starten|anfangen)", re.I),
    "salary": re.compile(r"salary|compensation|gehalt|verg[uü]tung|verdienen|jahresbrutto|verg[uü]tungsvorstellung|gesamtverg[uü]tung", re.I),
    "relocation": re.compile(r"relocat|umzug|umzugsbereit", re.I),
    "travel_willingness": re.compile(r"travel|reisebereitschaft", re.I),
    "driving_licence": re.compile(r"driving licen[cs]e|f[uü]hrerschein", re.I),
    "work_authorisation_text": re.compile(
        r"work authori[sz]ation|work permit|arbeitserlaubnis|arbeitsberechtigung|authori[sz]ed to work|"
        r"legally (?:allowed|entitled|permitted) to work|right to work|eligible to work", re.I),
    "current_employer": re.compile(
        r"most recent employer|current (?:employer|company)|aktuelle[rn]? arbeitgeber|derzeitige[rn]? arbeitgeber|"
        r"letzte[rn]? arbeitgeber", re.I),
    "current_title": re.compile(
        r"most recent (?:job )?title|current (?:job )?(?:title|position|role)|aktuelle (?:position|berufsbezeichnung)|"
        r"derzeitige (?:position|berufsbezeichnung)|letzte (?:position|berufsbezeichnung)", re.I),
    "other_languages": re.compile(
        r"other languages|additional languages|weitere sprachen|anderen? sprachen|au[sß]er.{0,40}(?:deutsch|englisch).{0,40}sprachen", re.I),
}
YEARS = re.compile(r"how many years|years? of experience|wie viele.{0,30}jahr|erfahrung in jahren", re.I)
EXPERIENCE = re.compile(r"do you have.{0,80}experience|experience (?:working|building|using|with|in)|erfahrung.{0,60}(?:mit|in)", re.I)
RATING = re.compile(r"(?:rate|rating|bewert|einsch[aä]tz).{0,120}(?:scale|skala)", re.I)
SCALE = re.compile(r"(?:scale|skala)[^0-9]{0,24}(-?\d+(?:[.,]\d+)?)\s*(?:-|–|—|to|bis)\s*(-?\d+(?:[.,]\d+)?)", re.I)
EMPLOYER_FREE = re.compile(
    r"why.{0,80}(?:role|position|company)|what.{0,80}excites|motivat|warum.{0,80}(?:stelle|position|unternehmen)|"
    r"weshalb.{0,80}(?:stelle|unternehmen)|what interests you|warum interessieren|warum.{0,80}arbeiten|what.{0,80}bring|was bringen",
    re.I,
)
FORBIDDEN_REUSE = re.compile(
    r"i confirm|ich best[aä]tige|privacy|datenschutz|consent|einwillig|declaration|erkl[aä]rung|"
    r"staatsb[uü]rgerschaft|citizenship", re.I,
)
IGNORE_SUGGESTIONS = re.compile(
    r"^\s*(?:weitere informationen|website|other website|link 1|titel|notes?|anmerkungen?)\s*[:*.-]*\s*$", re.I,
)
GERMAN = re.compile(r"\b(?:wie|warum|welche|haben sie|erfahrung|jahre|stelle|unternehmen|kenntnisse|k[uü]ndigung|eintritt|anmerkung(?:en)?|bemerkung(?:en)?|sonstiges|nachricht|ihre?|sie|und|oder|bitte)\b|[äöüß]", re.I)
STOP_TOOLS = {
    "do", "you", "have", "experience", "working", "building", "using", "with", "similar", "tools", "tool",
    "if", "applicable", "please", "briefly", "describe", "how", "many", "years", "domain", "dealing", "complex",
    "system", "landscapes", "and", "or", "mit", "oder", "erfahrung", "kenntnisse", "in", "the", "your",
}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def question_language(label: object) -> str:
    return "de" if GERMAN.search(str(label or "")) else "en"


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _scale(label: object) -> tuple[float, float] | None:
    match = SCALE.search(str(label or ""))
    if not match:
        return None
    low, high = _number(match.group(1)), _number(match.group(2))
    return (low, high) if low is not None and high is not None and low != high else None


def empty_bank() -> dict[str, object]:
    return {
        "facts": {key: {"de": "", "en": ""} for key in FACT_PATTERNS},
        "tools": [],
        "not_used": {
            "templates": {
                "de": "Ich habe {tool} bisher nicht eingesetzt. Vergleichbare, von mir verwendete Werkzeuge: {comparables}.",
                "en": "I have not used {tool}. Comparable tools I have used: {comparables}.",
            },
            "tool_categories": {},
        },
        "ratings": [],
        "saved": [],
        "learned": [],
        "pending": [],
        "ignore": [],
    }


def _normalize_bank(raw: object) -> dict[str, object]:
    bank = empty_bank()
    if not isinstance(raw, dict):
        return bank
    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    for key in FACT_PATTERNS:
        value = facts.get(key, {})
        if isinstance(value, str):
            bank["facts"][key] = {"de": value, "en": value}
        elif isinstance(value, dict):
            bank["facts"][key] = {"de": str(value.get("de") or ""), "en": str(value.get("en") or "")}
    bank["tools"] = [dict(item) for item in raw.get("tools", []) if isinstance(item, dict)]
    bank["ratings"] = [dict(item) for item in raw.get("ratings", []) if isinstance(item, dict)]
    bank["saved"] = [dict(item) for item in raw.get("saved", []) if isinstance(item, dict)]
    bank["learned"] = [dict(item) for item in raw.get("learned", []) or [] if isinstance(item, dict)]
    bank["pending"] = [dict(item) for item in raw.get("pending", []) or [] if isinstance(item, dict)]
    bank["ignore"] = [str(item) for item in raw.get("ignore", []) or [] if str(item).strip()]
    not_used = raw.get("not_used") if isinstance(raw.get("not_used"), dict) else {}
    templates = not_used.get("templates") if isinstance(not_used.get("templates"), dict) else {}
    bank["not_used"] = {
        "templates": {
            "de": str(templates.get("de") or bank["not_used"]["templates"]["de"]),
            "en": str(templates.get("en") or bank["not_used"]["templates"]["en"]),
        },
        "tool_categories": dict(not_used.get("tool_categories") or {}),
    }
    return bank


def load_bank(path: str | Path) -> dict[str, object]:
    try:
        return _normalize_bank(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    except (OSError, ValueError, yaml.YAMLError):
        return empty_bank()


def write_bank(path: str | Path, bank: Mapping[str, object]) -> None:
    _write_bank(Path(path), bank)


def _write_bank(path: Path, bank: Mapping[str, object]) -> None:
    from .safeio import atomic_write_text
    atomic_write_text(path, yaml.safe_dump(dict(bank), allow_unicode=True, sort_keys=False))


def extract_cv_skill_names(cv_text: object) -> list[str]:
    """Extract only the explicit Software line inside the CV skills section."""
    text = str(cv_text or "")
    section = re.search(r"(?ims)^\s*(?:Kompetenzen|Skills)\s*$([\s\S]*?)(?=^\s*(?:Sprachen|Languages|Berufserfahrung|Experience)\s*$)", text)
    if not section:
        return []
    software = re.search(r"(?im)^\s*(?:Software|Tools)\s*:\s*(.+)$", section.group(1))
    if not software:
        return []
    names = []
    for value in re.split(r"\s*[|;,]\s*", software.group(1)):
        name = re.sub(r"\s+(?:inkl\.|including).*$", "", value, flags=re.I).strip(" .")
        if name and _norm(name) not in {_norm(item) for item in names}:
            names.append(name[:80])
    return names


def ensure_bank(path: str | Path, profile: Mapping[str, object], cv_texts: Sequence[object] = ()) -> dict[str, object]:
    bank_path = Path(path)
    existed = bank_path.exists()
    bank = load_bank(bank_path)
    changed = not existed
    known = {_norm(item.get("name")): item for item in bank["tools"] if item.get("name")}
    for cv_text in cv_texts:
        for name in extract_cv_skill_names(cv_text):
            if _norm(name) not in known:
                item = {"name": name, "aliases": [name], "years": "", "level": "", "category": "", "example": {"de": "", "en": ""}}
                bank["tools"].append(item)
                known[_norm(name)] = item
                changed = True
    for key in FACT_PATTERNS:
        current = bank["facts"][key]
        for language in ("de", "en"):
            value = _profile_value(key, profile, language)
            if value and not current.get(language):
                current[language] = value
                changed = True
    if changed:
        _write_bank(bank_path, bank)
    return bank


def _tool_aliases(bank: Mapping[str, object]) -> dict[str, dict[str, object]]:
    aliases = {}
    for item in bank.get("tools", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        for value in [item.get("name"), *list(item.get("aliases") or [])]:
            if _norm(value):
                aliases[_norm(value)] = item
    return aliases


def extract_tool_names(label: object, bank: Mapping[str, object]) -> list[str]:
    text = str(label or "")
    found: list[str] = []
    aliases = _tool_aliases(bank)
    folded = _norm(text)
    for alias, item in sorted(aliases.items(), key=lambda pair: len(pair[0]), reverse=True):
        if re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", folded):
            found.append(str(item.get("name")))
    for phrase in re.findall(r"(?:working\s+with|using|experience\s+with|erfahrung\s+mit)\s+([^?;]+)", text, re.I):
        phrase = re.split(r"\b(?:if|please|or similar|oder [äa]hnlich)\b", phrase, maxsplit=1, flags=re.I)[0]
        for part in re.split(r"\s*,\s*|\s+(?:or|oder)\s+", phrase):
            value = part.strip(" .:*()")
            if value and _norm(value) not in STOP_TOOLS and len(value) <= 60:
                found.append(value)
    for value in re.findall(r"\b(?:[A-Za-z]*\d[A-Za-z0-9]*|[A-Z][a-z]+[A-Z][A-Za-z]*|[A-Z]{2,}(?:\s+[A-Z][A-Za-z]+)?)\b", text):
        if _norm(value) not in STOP_TOOLS:
            found.append(value)
    if re.search(r"\bHR\s+Tech\b", text, re.I):
        found.append("HR Tech")
    unique = []
    for value in found:
        canonical = aliases.get(_norm(value), {}).get("name", value)
        if _norm(canonical) and _norm(canonical) not in {_norm(item) for item in unique}:
            unique.append(str(canonical))
    return unique[:8]


def classify_question(label: object, bank: Mapping[str, object] | None = None) -> dict[str, object]:
    text = str(label or "")
    language = question_language(text)
    fact_keys = [key for key, pattern in FACT_PATTERNS.items() if pattern.search(text)]
    if fact_keys:
        return {"kind": "fact", "fact_keys": fact_keys, "language": language}
    tools = extract_tool_names(text, bank or empty_bank())
    if YEARS.search(text):
        return {"kind": "tool-years", "tools": tools, "language": language}
    scale = _scale(text)
    if RATING.search(text) or (scale and re.search(r"skills?|f[aä]higkeit|kenntnisse", text, re.I)):
        area = re.sub(r"(?is)^.*?(?:rate|rating|bewert|einsch[aä]tz)(?:e|en)?\s+(?:your|ihre|deine)?", "", text)
        area = re.split(r"(?:on a |auf einer? )?(?:scale|skala)", area, maxsplit=1, flags=re.I)[0].strip(" ?.*:")
        return {"kind": "self-rating", "area": area or text, "scale": list(scale) if scale else None, "language": language}
    if EXPERIENCE.search(text) or re.search(r"\bHR\s+Tech\s+domain\b", text, re.I):
        return {"kind": "tool-experience", "tools": tools, "language": language}
    return {"kind": "free", "employer_specific": bool(EMPLOYER_FREE.search(text)), "language": language}


def _profile_value(key: str, profile: Mapping[str, object], language: str) -> str:
    aliases = {
        "travel_willingness": ("travel_willingness", "travel"),
        "work_authorisation_text": ("work_authorisation_text", "work_authorization_text", "work_permit"),
    }
    raw = next((str(profile.get(name) or "").strip() for name in aliases.get(key, (key,)) if profile.get(name)), "")
    if not raw:
        return ""
    labels = {
        "notice_period": {"de": "Kündigungsfrist", "en": "Notice period"},
        "start_date": {"de": "Frühester Eintrittstermin", "en": "Earliest possible start date"},
        "salary": {"de": "Gehaltsvorstellung", "en": "Salary expectation"},
        "relocation": {"de": "Umzugsbereitschaft", "en": "Relocation"},
        "travel_willingness": {"de": "Reisebereitschaft", "en": "Travel willingness"},
        "driving_licence": {"de": "Führerschein", "en": "Driving licence"},
        "work_authorisation_text": {"de": "Arbeitserlaubnis", "en": "Work authorisation"},
        "current_employer": {"de": "Aktueller Arbeitgeber", "en": "Current employer"},
        "current_title": {"de": "Aktuelle Position", "en": "Current position"},
        "other_languages": {"de": "Weitere Sprachen", "en": "Other languages"},
    }
    return f"{labels[key][language]}: {raw}."


def _saved_answer(field: Mapping[str, object], language: str, employer: str, bank: Mapping[str, object]) -> str:
    label = str(field.get("label") or "")
    if FORBIDDEN_REUSE.search(label) or str(field.get("kind") or "") == "checkbox":
        return ""
    if str(field.get("kind") or "") in {"select", "radio"} and FORBIDDEN_REUSE.search(label):
        return ""
    for item in bank.get("saved", []):
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "")
        try:
            matches = bool(pattern and re.search(pattern, label, re.I))
        except re.error:
            matches = pattern.casefold() in label.casefold()
        if not matches:
            continue
        scope = str(item.get("scope") or "any employer").casefold()
        if scope == "this employer only" and _norm(item.get("employer")) != _norm(employer):
            continue
        answer = item.get("answer")
        if isinstance(answer, dict):
            return str(answer.get(language) or "").strip()
        return str(answer or "").strip()
    return ""


def _date_value(value: object, field: Mapping[str, object], language: str) -> str:
    text = str(value or "").strip()
    parsed = None
    for pattern in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
            break
        except ValueError:
            pass
    if parsed is None:
        return text
    label = " ".join(str(field.get(key) or "") for key in ("label", "placeholder"))
    if str(field.get("inputType") or field.get("type") or "").casefold() == "date":
        return parsed.strftime("%Y-%m-%d")
    if re.search(r"dd\s*/\s*mm\s*/\s*yyyy", label, re.I) or language == "en":
        return parsed.strftime("%d/%m/%Y")
    return parsed.strftime("%d.%m.%Y")


def _fact_value(key: str, value: object, field: Mapping[str, object], language: str) -> str:
    text = str(value or "").strip()
    if key == "start_date":
        return _date_value(text, field, language)
    if key == "salary":
        digits = "".join(re.findall(r"\d", text))
        label = str(field.get("label") or "")
        asks_text = bool(re.search(r"currency|w[aä]hrung|mit w[aä]hrung|text|betrag und", label, re.I))
        return text if asks_text else digits
    return text


def _rating_entry(area: str, bank: Mapping[str, object]) -> dict[str, object] | None:
    wanted = set(_norm(area).split()) - {"non", "ai", "your", "skills", "skill", "fahigkeiten", "kenntnisse"}
    best, score = None, 0.0
    for item in bank.get("ratings", []):
        if not isinstance(item, dict):
            continue
        candidate = set(_norm(item.get("area")).split()) - {"non", "ai", "skills", "skill", "fahigkeiten", "kenntnisse"}
        overlap = len(wanted & candidate) / max(1, len(wanted | candidate))
        if overlap > score:
            best, score = item, overlap
    return best if score >= 0.35 else None


def resolve_question(
    field: Mapping[str, object], bank: Mapping[str, object], profile: Mapping[str, object], *, employer: str = "",
    today: object = None, language: str = "",
) -> dict[str, object]:
    label = str(field.get("label") or "")
    classification = classify_question(label, bank)
    language = language if language in {"de", "en"} else str(classification["language"])
    classification["language"] = language
    kind = str(classification["kind"])
    value = ""
    source = ""
    if kind == "fact":
        keys = list(classification.get("fact_keys") or [])
        if "notice_period" in keys:
            wording = str(bank.get("facts", {}).get("notice_period", {}).get(language) or "").strip()
            if wording:
                suggestion = {"value": wording, "reason": "notice period stays manual", "score": 1.0}
        else:
            values, sources = [], []
            for key in keys:
                bank_value = str(bank.get("facts", {}).get(key, {}).get(language) or "").strip()
                profile_value = str(profile.get(key) or "").strip()
                raw = bank_value or profile_value
                if not raw:
                    values = []
                    break
                values.append(_fact_value(key, raw, field, language))
                sources.append("answer bank" if bank_value else "profile")
            if keys and values:
                value = " ".join(values)
                source = "answer bank" if "answer bank" in sources else "profile"
    elif kind in {"tool-years", "tool-experience"}:
        aliases = _tool_aliases(bank)
        tools = list(classification.get("tools") or [])
        known = [aliases.get(_norm(name)) for name in tools]
        known = [item for item in known if item]
        if kind == "tool-years" and len(known) == 1 and str(known[0].get("years") or "").strip():
            years = str(known[0]["years"]).strip()
            value = f"{years} Jahre" if language == "de" else f"{years} years"
            source = "answer bank"
        elif kind == "tool-experience" and known:
            examples = [str((item.get("example") or {}).get(language) or "").strip() for item in known]
            if all(examples):
                value, source = " ".join(examples), "answer bank"
        if not value and tools:
            categories = {str(k).casefold(): str(v) for k, v in bank.get("not_used", {}).get("tool_categories", {}).items()}
            requested_categories = {categories.get(name.casefold(), "") for name in tools}
            if len(requested_categories) == 1 and "" not in requested_categories:
                category = requested_categories.pop()
                comparable = [
                    str(item.get("name")) for item in bank.get("tools", [])
                    if isinstance(item, dict) and category.casefold() in {
                        part.strip().casefold() for part in str(item.get("category") or "").split(",")
                    }
                ]
                if comparable:
                    template = str(bank.get("not_used", {}).get("templates", {}).get(language) or "")
                    value = template.format(tool=", ".join(tools), comparables=", ".join(comparable))
                    source = "answer bank"
    elif kind == "self-rating":
        question_scale = classification.get("scale")
        item = _rating_entry(str(classification.get("area") or ""), bank)
        source_scale = list(item.get("scale") or []) if item else []
        own = _number(item.get("value")) if item else None
        if question_scale and len(source_scale) == 2 and own is not None:
            q0, q1 = map(float, question_scale)
            s0, s1 = map(float, source_scale)
            if s0 != s1:
                converted = q0 + (own - s0) * (q1 - q0) / (s1 - s0)
                shown = str(int(round(converted))) if abs(converted - round(converted)) < 1e-9 else f"{converted:.1f}"
                top = str(int(q1)) if q1.is_integer() else str(q1)
                value = f"{shown} von {top}." if language == "de" else f"{shown} out of {top}."
                source = "answer bank"
    suggestion: dict[str, object] = locals().get("suggestion", {})
    note = ""
    if not value and kind != "fact":
        from .learning import match_learned

        learned = match_learned(field, bank, employer=employer, language=language, today=today)
        if learned["mode"] == "fill":
            value, source, note = str(learned["value"]), "learned", str(learned.get("note") or "")
        elif learned["mode"] == "suggest":
            suggestion = {"value": str(learned["value"]), "reason": str(learned.get("note") or ""), "score": learned["score"]}
    if not value and not (kind == "fact" and "notice_period" in list(classification.get("fact_keys") or [])):
        value = _saved_answer(field, language, employer, bank)
        if value:
            source = "saved"
    maximum = int(field.get("maxLength") or 600)
    if value and len(value) > maximum:
        value, source = "", ""
    return {
        "value": value, "source": source, "note": note, "suggestion": suggestion,
        "classification": classification, "missing": not bool(value),
    }


def unresolved_from_log(
    path: str | Path, bank: Mapping[str, object], profile: Mapping[str, object], employers: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    try:
        rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    missing, seen = [], set()
    for row in reversed(rows):
        employer = str((employers or {}).get(str(row.get("id") or ""), ""))
        for field in row.get("fields", []):
            if not isinstance(field, dict) or field.get("kind") not in {"text", "textarea"}:
                continue
            if field.get("status") not in {"unknown", "no_match", "no_value", "manual"}:
                continue
            label = str(field.get("label") or "").strip()
            key = _norm(label)
            if not key or key in seen:
                continue
            seen.add(key)
            if key in {_norm(item) for item in bank.get("ignore", [])}:
                continue
            result = resolve_question(field, bank, profile, employer=employer)
            if result["missing"]:
                missing.append({
                    "label": label, "employer": employer, "application_id": str(row.get("id") or ""),
                    "ignore_suggested": bool(IGNORE_SUGGESTIONS.search(label)), **result["classification"],
                })
    return missing[:100]


def save_entry(path: str | Path, payload: Mapping[str, object]) -> dict[str, object]:
    bank_path = Path(path)
    bank = load_bank(bank_path)
    kind = str(payload.get("kind") or "saved")
    if kind == "ignore":
        label = str(payload.get("label") or "").strip()
        if not label:
            raise ValueError("Question label is required")
        if _norm(label) not in {_norm(item) for item in bank["ignore"]}:
            bank["ignore"].append(label)
    elif kind == "fact":
        key = str(payload.get("key") or "")
        if key not in FACT_PATTERNS:
            raise ValueError("Unknown answer-bank fact")
        bank["facts"][key] = {"de": str(payload.get("de") or "").strip(), "en": str(payload.get("en") or "").strip()}
    elif kind == "tool":
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Tool name is required")
        item = next((item for item in bank["tools"] if _norm(item.get("name")) == _norm(name)), None)
        if item is None:
            item = {"name": name}
            bank["tools"].append(item)
        item.update({
            "name": name,
            "aliases": [value.strip() for value in str(payload.get("aliases") or name).split(",") if value.strip()],
            "years": str(payload.get("years") or "").strip(), "level": str(payload.get("level") or "").strip(),
            "category": str(payload.get("category") or "").strip(),
            "example": {"de": str(payload.get("example_de") or "").strip(), "en": str(payload.get("example_en") or "").strip()},
        })
    elif kind == "rating":
        area = str(payload.get("area") or "").strip()
        if not area:
            raise ValueError("Rating area is required")
        value = _number(payload.get("value"))
        low, high = _number(payload.get("scale_min")), _number(payload.get("scale_max"))
        if value is None or low is None or high is None or low == high or not min(low, high) <= value <= max(low, high):
            raise ValueError("Rating and an explicit valid scale are required")
        item = next((item for item in bank["ratings"] if _norm(item.get("area")) == _norm(area)), None)
        if item is None:
            item = {"area": area}
            bank["ratings"].append(item)
        item.update({"area": area, "value": value, "scale": [low, high]})
    elif kind == "tool-category":
        tool = str(payload.get("tool") or "").strip()
        category = str(payload.get("category") or "").strip()
        if not tool or not category:
            raise ValueError("Tool and category are required")
        bank["not_used"]["tool_categories"][tool] = category
    else:
        label = str(payload.get("label") or payload.get("pattern") or "").strip()
        value = str(payload.get("value") or "").strip()
        language = str(payload.get("language") or question_language(label))
        scope = str(payload.get("scope") or "any employer")
        employer = str(payload.get("employer") or "").strip()
        if not label or not value or scope not in {"any employer", "this employer only"}:
            raise ValueError("Question, answer, and valid scope are required")
        if FORBIDDEN_REUSE.search(label):
            raise ValueError("Consent, declaration, privacy, and citizenship answers are never saved for reuse")
        pattern = str(payload.get("pattern") or rf"^{re.escape(label)}$")
        item = next((item for item in bank["saved"] if item.get("pattern") == pattern and item.get("scope") == scope and _norm(item.get("employer")) == _norm(employer)), None)
        if item is None:
            item = {"pattern": pattern, "answer": {"de": "", "en": ""}, "scope": scope, "employer": employer}
            bank["saved"].append(item)
        if not isinstance(item.get("answer"), dict):
            item["answer"] = {"de": "", "en": ""}
        item["answer"][language] = value
    _write_bank(bank_path, bank)
    return bank


def cleanup_unsafe_entries(path: str | Path) -> list[dict[str, str]]:
    """One-time Round 23 cleanup. Back up the private bank before removing known unsafe rows."""
    bank_path = Path(path)
    bank = load_bank(bank_path)
    backup = bank_path.with_name(bank_path.name + ".round22-backup")
    if bank_path.is_file() and not backup.exists():
        shutil.copy2(bank_path, backup)
    removed: list[dict[str, str]] = []
    junk = re.compile(r"registration form desired job title field ui typeahead input|agentur f[uü]r rbeir", re.I)
    best_team = re.compile(r"the best team wins", re.I)
    english_gap = re.compile(r"\bgap:\b|posting asks for at least", re.I)
    for section in ("learned", "saved"):
        kept = []
        for item in bank.get(section, []):
            label = str(item.get("question") or item.get("pattern") or "")
            answer = item.get("answer")
            answer_text = " ".join(map(str, answer.values())) if isinstance(answer, dict) else str(answer or "")
            unsafe = bool(
                FORBIDDEN_REUSE.search(label) or junk.search(label) or junk.search(answer_text)
                or (re.search(r"anmerkungen?", label, re.I) and english_gap.search(answer_text))
                or (best_team.search(label) and bool(re.search(r"\.\.\.|…", answer_text)))
            )
            if unsafe:
                removed.append({"section": section, "label": label, "answer": answer_text})
            else:
                kept.append(item)
        bank[section] = kept
    _write_bank(bank_path, bank)
    return removed
