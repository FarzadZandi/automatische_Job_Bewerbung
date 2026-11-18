"""Checked answers for non-personal questions on one application-form page.

Claude is reached only through the signed-in Claude Code command line.  Personal, legal and
consent questions are deliberately removed before a prompt is built.  Answers are cached by
application and field label so revisiting or progressively revealing a page does not spend another
request for a question that was already considered.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .answerbank import empty_bank, load_bank, question_language, resolve_question
from .claude_writer import ask_claude, check_plan
from .hooks import GAP_STATEMENT, NUMBER
from .metrics import append_jsonl
from . import applicant
from .paragraph_bank import BANNED_PHRASES, FORBIDDEN_SKILLS, GERMAN_LEVEL_CLAIM, _dash_problems

JOB_DESCRIPTION_CHARS = 8000
OPTIONAL_FREE = re.compile(r"^\s*(?:anmerkungen?|bemerkungen?|notes?|additional information|sonstiges)\s*[:*.-]*\s*$", re.I)
SENSITIVE_FREE = re.compile(r"relocat|umz(?:ug|ieh)|salary|gehalt|verg[uü]tung|\bvisa\b|visum|aufenthalt|\bdate\b|datum|termin", re.I)


def language_rule(answer_language: str) -> str:
    if answer_language == "de":
        return "SPRACHE: Antworte immer auf Deutsch, in der Ich-Form, sachlich und formal."
    return "SPRACHE: Antworte in der Sprache der jeweiligen Feldbezeichnung."

PERSONAL = re.compile(
    r"gehalt|salary|verg[uü]tung|compensation|eintritt|start|verf[uü]gbar|k[uü]ndigungsfrist|notice|"
    r"geburt|birth|staatsangeh[oö]rig|nationality|citizenship|aufenthalt|visum|visa|arbeitserlaubnis|"
    r"work permit|behinderung|disability|health|gesundheit|religion|union|gewerkschaft|criminal|"
    r"straf|marital|familienstand|gender|geschlecht|password|passwort|captcha|datenschutz|privacy|"
    r"consent|einwilligung|terms|bedingungen|deutsch|german|englisch|english|sprach|language|"
    r"f[uü]hrerschein|driver|reise|travel|umzug|relocat|standort|location|verwandt|related|"
    r"erfahrung in jahren|years of experience|berufserfahrung|wie viele|how many|"
    r"rate your.{0,80}(?:scale|skala)|bewerte.{0,80}(?:scale|skala)|vorstrafe|schicht|shift|"
    r"highest (?:level of )?education|highest degree|bildungsabschluss|hochschulabschluss|current employment|current position|"
    r"derzeitige besch[aä]ftigung|aktuelle besch[aä]ftigung",
    re.IGNORECASE,
)
SUGGEST_FORBIDDEN = re.compile(
    r"gehalt|salary|verg[uü]tung|compensation|geburt|birth|staatsangeh[oö]rig|nationality|citizenship|"
    r"aufenthalt|residence|visum|visa|work permit|arbeitserlaubnis|behinderung|disability|health|gesundheit|"
    r"religion|union|gewerkschaft|criminal|straf|marital|familienstand|gender|geschlecht|password|passwort|"
    r"captcha|datenschutz|privacy|consent|einwilligung|terms|bedingungen",
    re.IGNORECASE,
)
CONTACT_KEYS = (
    "first_name", "last_name", "email", "phone", "street", "house_number", "postal_code", "city", "country",
    "german_level", "english_level", "driving_licence", "travel", "relocation", "years_experience",
)
RULES = """Regeln:
1. Verwende ausschließlich die Fakten unten. Erfinde keine Arbeitgeber, Zahlen, Tools, Kurse, Sprachniveaus oder Erfahrungen.
2. Antworte in der Sprache der Feldbezeichnung. Deutsch: formell in der Sie-Form, {DE_STYLE}.
3. {DE_CAP} Verwende keine Gedankenstriche als Satzzeichen.
4. Verboten sind: hoch motiviert, hochmotiviert, leidenschaftlich, perfekt passend, freue mich sehr, dynamischer Teamplayer, ich brenne für, highly motivated, passionate about, excited to apply.
5. Verwende keine Zahl und kein Produkt oder Werkzeug, das nicht in den Fakten steht. Beachte jede Zeichenbegrenzung.
6. Wenn die Fakten nicht reichen, gib für value einen leeren Text zurück.
7. Antworte NUR als JSON: {"answers":[{"id":"<id>","value":"<Antwort>"}]}"""
PLAIN_SYSTEM = (
    "Du beantwortest Bewerbungsfragen ausschließlich aus verifizierten Fakten. Erfinde nichts. "
    "Beachte alle Regeln im Prompt und gib nur den Antworttext aus."
)
PLAIN_RULES = """Regeln:
1. Verwende ausschließlich die Fakten unten. Erfinde keine Arbeitgeber, Zahlen, Tools, Kurse, Sprachniveaus oder Erfahrungen.
2. Antworte in der Sprache der Feldbezeichnung. Deutsch: formell in der Sie-Form, {DE_STYLE}.
3. {DE_CAP} Verwende keine Gedankenstriche als Satzzeichen.
4. Verboten sind: hoch motiviert, hochmotiviert, leidenschaftlich, perfekt passend, freue mich sehr, dynamischer Teamplayer, ich brenne für, highly motivated, passionate about, excited to apply.
5. Verwende keine Zahl und keinen großgeschriebenen Fachbegriff, kein Produkt oder Werkzeug, das nicht wortgleich in den Fakten steht.
6. Wenn die Fakten nicht reichen, antworte mit leerem Text."""


def facts_text(profile: Mapping[str, object], package: Mapping[str, object]) -> str:
    """Stable applicant facts for one package; job-description text is a separate prompt block."""
    contact = ", ".join(f"{key}: {profile.get(key, '')}" for key in CONTACT_KEYS if profile.get(key))
    return "\n\n".join(filter(None, [
        f"Kontaktdaten: {contact}",
        f"Stelle: {package.get('title', '')}",
        f"Arbeitgeber: {package.get('employer', '')}",
        f"Quelle: {package.get('source', '')}",
        f"Lebenslauf:\n{str(package.get('cv_text') or '')}",
        f"Anschreiben:\n{str(package.get('letter_text') or '')}",
    ]))


def job_description_text(package: Mapping[str, object]) -> str:
    """The fixed full-ad prompt block, bounded independently of applicant facts."""
    return str(package.get("job_description") or package.get("job_excerpt") or "")[:JOB_DESCRIPTION_CHARS]


def build_prompt(
    fields: Sequence[Mapping[str, object]], facts: str, user_instruction: str = "", job_description: str = "",
    answer_language: str = "auto",
) -> str:
    lines = []
    for field in fields:
        parts = [
            f"id: {field.get('uid', '')}", f"Feld: {json.dumps(str(field.get('label') or ''), ensure_ascii=False)}",
            f"Art: {field.get('kind', 'text')}",
        ]
        if field.get("maxLength"):
            parts.append(f"maximal {int(field['maxLength'])} Zeichen")
        else:
            parts.append("maximal 600 Zeichen")
        if field.get("required"):
            parts.append("Pflichtfeld")
        lines.append("- " + "; ".join(parts))
    sections = [
        "Beantworte die offenen Fragen dieses Online-Bewerbungsformulars.", RULES, language_rule(answer_language),
        "ANTWORTLÄNGE: Höchstens 50 Wörter je Antwort, außer das Feld oder der Bewerber verlangt ausdrücklich mehr. Bei mehreren Punkten verwende kurze Zeilen mit '- '.",
        "FAKTEN\n" + facts,
    ]
    if job_description:
        sections.append("STELLENBESCHREIBUNG\n" + str(job_description)[:JOB_DESCRIPTION_CHARS])
    sections.append("OFFENE FELDER\n" + "\n".join(lines))
    instruction = " ".join(str(user_instruction or "").split())[:1200]
    if instruction:
        sections.append("ZUSÄTZLICHE ANWEISUNG DES BEWERBERS\n" + instruction)
    return "\n\n".join(sections)


def build_plain_prompt(
    field: Mapping[str, object], facts: str, user_instruction: str = "", job_description: str = "",
    answer_language: str = "auto",
) -> str:
    """Prompt for one free-text field; the CLI wrapper supplies usage, not answer structure."""
    maximum = min(int(field.get("maxLength") or 600), 400)
    sections = [
        PLAIN_RULES, language_rule(answer_language),
        "Antworte mit höchstens 50 Wörtern, außer das Feld oder der Bewerber verlangt ausdrücklich mehr. Bei mehreren Punkten verwende kurze Zeilen mit '- '.",
        "FAKTEN\n" + facts,
    ]
    if job_description:
        sections.append("STELLENBESCHREIBUNG\n" + str(job_description)[:JOB_DESCRIPTION_CHARS])
    sections.extend([
        f"FELD\n{str(field.get('label') or '')}\nMaximal {maximum} Zeichen.",
        f"Antworte nur mit dem Antworttext, ohne JSON, Erklärung oder Anführungszeichen, höchstens {maximum} Zeichen.",
    ])
    instruction = " ".join(str(user_instruction or "").split())[:1200]
    if instruction:
        sections.append("ZUSÄTZLICHE ANWEISUNG DES BEWERBERS\n" + instruction)
    return "\n\n".join(sections)


def build_suggest_prompt(
    fields: Sequence[Mapping[str, object]], facts: str, user_instruction: str = "", job_description: str = "",
    answer_language: str = "auto",
) -> str:
    lines = []
    for field in fields:
        options = [str(value) for value in list(field.get("options") or [])[:60]]
        lines.append(
            f"- id: {field.get('uid', '')}; Feld: {json.dumps(str(field.get('label') or ''), ensure_ascii=False)}; "
            f"Optionen: {json.dumps(options, ensure_ascii=False)}"
        )
    instruction = " ".join(str(user_instruction or "").split())[:1200]
    sections = [
        "Schlage für jedes Auswahlfeld höchstens eine angebotene Option vor.", language_rule(answer_language),
        "Wähle nur, was Lebenslauf, Anschreiben oder Profil ausdrücklich belegt. Sprachniveaus laut Profil: {LEVELS}. "
        "Kopiere die Option exakt. Wenn die Fakten nicht entscheiden, value leer lassen. reason nennt in höchstens 200 Zeichen den verwendeten Fakt. "
        'Antworte NUR als JSON: {"answers":[{"id":"<id>","value":"<exakte Option oder leer>","reason":"<Fakt>"}]}',
        "FAKTEN\n" + facts,
    ]
    if job_description:
        sections.append("STELLENBESCHREIBUNG\n" + str(job_description)[:JOB_DESCRIPTION_CHARS])
    sections.append("FELDER\n" + "\n".join(lines))
    if instruction:
        sections.append("ZUSÄTZLICHE ANWEISUNG DES BEWERBERS\n" + instruction)
    return "\n\n".join(sections)


def check_vocabulary(package: Mapping[str, object], field: Mapping[str, object]) -> str:
    """Full local text used only to reject invented names; this is never added to the prompt."""
    return "\n".join(str(value or "") for value in (
        package.get("cv_text"), package.get("letter_text"), package.get("job_description"), package.get("job_excerpt"),
        package.get("title"), package.get("employer"), field.get("label"),
    ))


# Typical endings of German nouns; a capitalised word with one of them is a noun, not a product or employer.
GERMAN_NOUN = re.compile(
    r"(?:ung|ungen|keit|keiten|heit|heiten|schaft|schaften|nis|nisse|tion|tionen|t[äa]t|t[äa]ten|ik|iken|ismus|"
    r"ment|mente|enz|anz|ie|ien|ur|uren|er|ern|en|el|eln|chen|lein|ling|linge|sse|isse|te|ten|de|den|e)$"
)


def check_answer(
    field: Mapping[str, object], value: object, facts: str, vocabulary: str,
) -> tuple[str, list[str]]:
    """Normalize an answer and return (usable value, rule violations)."""
    text = " ".join(str(value or "").split())
    problems: list[str] = []
    maximum = int(field.get("maxLength") or 600)
    if len(text) > maximum:
        problems.append(f"longer than {maximum} characters")
    asks_for_length = bool(re.search(r"\b\d+\s*(?:words?|w[oö]rter|sentences?|s[aä]tze)\b|at least|mindestens", str(field.get("label") or ""), re.I))
    if len(text.split()) > 50 and not asks_for_length and not field.get("allowLong"):
        problems.append("longer than 50 words")
    lowered = text.casefold()
    problems.extend(f"banned phrase '{phrase}'" for phrase in BANNED_PHRASES if phrase in lowered)
    for pattern in FORBIDDEN_SKILLS:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            problems.append(f"unverified Tier-2 term '{match.group(0)}'")
    suspicious = []
    for token in re.findall(r"(?u)\b[\w][\w.+#-]{2,}\b", text):
        for part in [piece for piece in token.split("-") if len(piece) >= 2] or [token]:
            letters = [character for character in part if character.isalpha()]
            looks_product = (
                any(character.isdigit() for character in part)
                or any(character.isupper() for character in part[1:])
                or (len(letters) >= 2 and all(character.isupper() for character in letters))
            )
            if looks_product and part.casefold() not in vocabulary.casefold():
                suspicious.append(part)
    if suspicious:
        problems.append("product-like term(s) not in the facts: " + ", ".join(sorted(set(suspicious))))
    invented_names = []
    folded_vocabulary = vocabulary.casefold()
    german = question_language(text) == "de"
    for match in re.finditer(r"(?u)\b[A-ZÄÖÜ][\wÄÖÜäöüß-]{3,}\b", text):
        before = text[:match.start()]
        if not before.strip() or re.search(r"[.!?]\s*$", before):
            continue  # first word of the answer or of a new sentence
        for part in [piece for piece in match.group(0).split("-") if len(piece) >= 4]:
            if not part[0].isupper() or part.casefold() in folded_vocabulary:
                continue
            if german and GERMAN_NOUN.search(part):
                continue  # German capitalises every noun: "Fähigkeiten", "Projekten" are not names
            invented_names.append(part)
    if invented_names:
        problems.append("capitalised term(s) not in the check vocabulary: " + ", ".join(sorted(set(invented_names))))
    # Remove the English-level clause before checking a sentence that mentions both languages.
    if applicant.german_capped() and GERMAN_LEVEL_CLAIM.search(re.sub(r"\bEnglisch\w*[^.,;]{0,40}", "", text)):
        problems.append(f"claims a German level above {applicant.german_level() or 'the profile level'}")
    gap = GAP_STATEMENT.search(text)
    if gap:
        problems.append(f"states a gap or missing qualification: {gap.group(0)}")
    label = str(field.get("label") or "")
    if not SENSITIVE_FREE.search(label):
        sensitive = SENSITIVE_FREE.search(text)
        if sensitive:
            problems.append(f"mentions an unasked sensitive topic: {sensitive.group(0)}")
    problems.extend(_dash_problems(text))
    invented = sorted(set(NUMBER.findall(text)) - set(NUMBER.findall(facts)))
    if invented:
        problems.append("number(s) not in the facts: " + ", ".join(invented))
    return text if not problems else "", problems


def _cache_key(application_id: str, label: object) -> str:
    return f"{application_id}\u0000{' '.join(str(label or '').casefold().split())}"


def _load_cache(path: Path) -> dict[str, object]:
    try:
        from .safeio import read_text
        value = json.loads(read_text(path))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(path: Path, cache: Mapping[str, object]) -> None:
    from .safeio import atomic_write_json
    atomic_write_json(path, cache, indent=1)


def _extract_answers(text: str) -> dict[str, object]:
    cleaned = re.sub(r"```(?:json)?", "", text or "", flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    while start >= 0:
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            start = cleaned.find("{", start + 1)
            continue
        if isinstance(value, dict) and isinstance(value.get("answers"), list):
            return value
        start = cleaned.find("{", start + 1)
    raise ValueError('Claude returned no readable {"answers": [...]} object.')


# Free-text labels that are declarations or identity questions, not questions to answer in prose.
PERSONAL_FREE = re.compile(
    r"i confirm|ich best[aä]tige|privacy|datenschutz|consent|einwillig|gender|geschlecht|pronoun|disab|behinder|"
    r"veteran|ethnic|race\b|religio|sponsorship|authori[sz]ed to work|arbeitserlaubnis|visa|visum|"
    r"most recent employer|most recent job|current (?:employer|company)|aktueller arbeitgeber|salary|gehalt|"
    r"deutschkenntnis|englischkenntnis|sprachkenntnis|language (?:skills|level)|f[uü]hrerschein|driving licen",
    re.I,
)


def answer_fields(
    data_path: str | Path, application_id: str, fields: Sequence[Mapping[str, object]], cache_path: str | Path,
    *, model: str = "haiku", runner: Callable[..., object] = subprocess.run,
    user_instruction: str = "", force: bool = False, mode: str = "answer", bank_path: str | Path | None = None,
    answer_language: str = "auto", allow_optional: bool = False,
) -> dict[str, object]:
    """Answer one page's allowed fields with at most one Claude request."""
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    package = next((item for item in data.get("packages", []) if item.get("id") == application_id), None)
    if package is None:
        raise FileNotFoundError(f"Application ID not exported: {application_id}")
    if mode == "suggest":
        allowed = [dict(field) for field in fields if field.get("kind") in {"select", "radio"}
                   and not SUGGEST_FORBIDDEN.search(str(field.get("label") or ""))]
        yours = [str(field.get("label") or "") for field in fields if field not in allowed]
        facts = facts_text(data.get("profile", {}), package)
        description = job_description_text(package)
        if not allowed:
            return {"suggestions": [], "rejected": {}, "yours": yours, "cached": False, "model": model}
        check_plan(runner)
        reply, usage = ask_claude(
            build_suggest_prompt(allowed, facts, user_instruction, description, answer_language), model=model, runner=runner,
            purpose="suggest", jobs=1, fields=len(allowed), return_usage=True,
        )
        payload = _extract_answers(reply)
        returned = {str(item.get("id") or ""): item for item in payload.get("answers", []) if isinstance(item, dict)}
        suggestions, rejected = [], {}
        for field in allowed:
            uid, label = str(field.get("uid") or ""), str(field.get("label") or "")
            item = returned.get(uid, {})
            value = str(item.get("value") or "").strip()
            reason = " ".join(str(item.get("reason") or "").split())[:200]
            options = [str(option) for option in list(field.get("options") or [])]
            if value and value not in options:
                rejected[label] = ["suggestion is not an exact offered option"]
            elif value:
                suggestions.append({"uid": uid, "label": label, "value": value, "reason": reason})
        usage["chars_out"] = sum(len(item["value"]) for item in suggestions)
        append_jsonl(Path(cache_path).parent.parent / "usage.jsonl", usage)
        return {"suggestions": suggestions, "answers": [], "rejected": rejected, "yours": yours, "cached": False, "model": model}
    profile = data.get("profile", {})
    bank = load_bank(bank_path) if bank_path else empty_bank()
    answers, needs_bank, allowed, suggestions = [], [], [], []
    learned_choice: set[str] = set()
    employer = str(package.get("employer") or "")
    for field in fields:
        if field.get("kind") in {"select", "radio"}:
            # Learned choices only; Claude never chooses an option on its own.
            from .answerbank import classify_question, question_language
            from .learning import match_learned, option_for

            uid, label = str(field.get("uid") or ""), str(field.get("label") or "")
            chosen_language = "de" if answer_language == "de" else question_language(label)
            found = match_learned(field, bank, employer=employer, language=chosen_language)
            facts_found = classify_question(label, bank)
            if found["mode"] != "fill" and facts_found.get("kind") == "fact" and len(facts_found.get("fact_keys") or []) == 1:
                wording = bank.get("facts", {}).get(facts_found["fact_keys"][0], {})
                for language in (chosen_language, "en", "de"):
                    option = option_for(str(wording.get(language) or ""), list(field.get("options") or []))
                    if option:
                        found = {"mode": "fill", "value": option, "note": "answer bank"}
                        break
            if found["mode"] == "fill":
                answers.append({"uid": uid, "value": found["value"], "label": label, "source": "learned",
                                "note": found.get("note", "")})
                learned_choice.add(uid)
            elif found["mode"] == "suggest":
                suggestions.append({"uid": uid, "label": label, "value": found["value"],
                                    "reason": found.get("note", ""), "source": "learned"})
                learned_choice.add(uid)
            continue
        if field.get("kind") not in {"text", "textarea"}:
            continue
        if re.search(r"notice period|k[uü]ndigungsfrist", str(field.get("label") or ""), re.I):
            needs_bank.append({"uid": str(field.get("uid") or ""), "label": str(field.get("label") or ""),
                               "kind": "manual", "language": question_language(field.get("label"))})
            continue
        if OPTIONAL_FREE.match(str(field.get("label") or "")) and not allow_optional:
            continue
        resolved = resolve_question(
            field, bank, profile, employer=employer,
            language="de" if answer_language == "de" else "",
        )
        if resolved["value"]:
            if GAP_STATEMENT.search(str(resolved["value"])):
                needs_bank.append({
                    "uid": str(field.get("uid") or ""), "label": str(field.get("label") or ""),
                    **resolved["classification"], "reason": "gap statements are never inserted",
                })
                continue
            answers.append({
                "uid": str(field.get("uid") or ""), "value": resolved["value"],
                "label": str(field.get("label") or ""), "source": resolved["source"],
                "note": resolved.get("note", ""),
            })
            continue
        if resolved.get("suggestion"):
            suggestions.append({
                "uid": str(field.get("uid") or ""), "label": str(field.get("label") or ""),
                "value": resolved["suggestion"]["value"], "reason": resolved["suggestion"].get("reason", ""),
                "source": "learned",
            })
        # Every free-text question goes to Claude (motivation, values, "what does X mean to you").
        # Facts, tool experience and self-ratings never do: those come from the bank or the user.
        if resolved["classification"].get("kind") == "free" and str(field.get("label") or "").strip() \
                and not PERSONAL_FREE.search(str(field.get("label") or "")):
            allowed.append({**dict(field), "allowLong": bool(re.search(
                r"\b\d+\s*(?:words?|w[oö]rter|sentences?|s[aä]tze)\b|at least|mindestens",
                str(user_instruction or ""), re.I,
            ))})
        else:
            needs_bank.append({
                "uid": str(field.get("uid") or ""), "label": str(field.get("label") or ""),
                **resolved["classification"],
            })
    yours = [str(field.get("label") or "") for field in fields
             if field.get("kind") not in {"text", "textarea"} and str(field.get("uid") or "") not in learned_choice]
    if mode == "resolve" or not allowed:
        return {
            "answers": answers, "rejected": {}, "yours": yours, "needs_bank": needs_bank,
            "suggestions": suggestions, "cached": False, "model": model,
        }
    cache_file = Path(cache_path)
    cache = _load_cache(cache_file)
    facts = facts_text(profile, package)
    description = job_description_text(package)
    rejected, missing = {}, []
    cache_dirty = False
    for field in allowed:
        key = _cache_key(application_id, field.get("label"))
        if force or key not in cache:
            missing.append(field)
            continue
        saved = cache[key] if isinstance(cache[key], dict) else {}
        value, current_problems = check_answer(
            field, saved.get("value", ""), facts, check_vocabulary(package, field),
        )
        problems = current_problems or list(saved.get("problems") or [])
        if value:
            answers.append({"uid": str(field.get("uid") or ""), "value": value, "label": str(field.get("label") or ""), "source": "Claude"})
        if problems:
            rejected[str(field.get("label") or "")] = problems
        if saved.get("value") != value or saved.get("problems") != problems:
            cache[key] = {"value": value, "problems": problems}
            cache_dirty = True
    if not missing:
        if cache_dirty:
            _write_cache(cache_file, cache)
        return {"answers": answers, "rejected": rejected, "yours": yours, "needs_bank": needs_bank,
                "suggestions": suggestions, "cached": True, "model": model}

    check_plan(runner)
    single = len(missing) == 1
    reply, usage = ask_claude(
        build_plain_prompt(missing[0], facts, user_instruction, description, answer_language)
        if single else build_prompt(missing, facts, user_instruction, description, answer_language),
        model=model, runner=runner, purpose="form", jobs=1, fields=len(missing), return_usage=True,
        structured_output=not single, system_prompt=PLAIN_SYSTEM if single else None,
    )
    if single:
        returned = {str(missing[0].get("uid") or ""): {"value": reply}}
    else:
        payload = _extract_answers(reply)
        returned = {str(item.get("id") or ""): item for item in payload.get("answers", []) if isinstance(item, dict)}
    for field in missing:
        uid = str(field.get("uid") or "")
        value, problems = check_answer(
            field, returned.get(uid, {}).get("value", ""), facts, check_vocabulary(package, field),
        )
        label = str(field.get("label") or "")
        cache[_cache_key(application_id, label)] = {"value": value, "problems": problems}
        if value:
            answers.append({"uid": uid, "value": value, "label": label, "source": "Claude"})
        if problems:
            rejected[label] = problems
    usage["chars_out"] = sum(len(item["value"]) for item in answers if item.get("source") == "Claude")
    append_jsonl(cache_file.parent.parent / "usage.jsonl", usage)
    _write_cache(cache_file, cache)
    return {"answers": answers, "rejected": rejected, "yours": yours, "needs_bank": needs_bank,
            "suggestions": suggestions, "cached": False, "model": model}


# ---------------------------------------------------------------- free chat from the extension panel

CHAT_SYSTEM = (
    "Du hilfst einem Bewerber beim Ausfüllen von Online-Bewerbungen. Du nutzt nur verifizierte Fakten aus dem "
    "Kontext und erfindest nichts. Gib nur die Antwort aus, ohne Vorrede."
)
CHAT_RULES = """Regeln:
1. Beantworte jede nummerierte Frage einzeln und in der Sprache dieser Frage. Schreibe jeden Text fertig zum Einfügen, in der Ich-Form des Bewerbers.
2. Nutze nur Fakten aus dem Kontext (Lebenslauf, Anschreiben, Stelle, Kontaktdaten). Erfinde keine Arbeitgeber, Zahlen, Tools, Kurse, Sprachniveaus oder Erfahrungen. Fehlt ein Fakt, sage in einem kurzen Satz, was der Bewerber selbst ergänzen muss.
3. Schreibe über das, was der Bewerber kann; erwähne fehlende Erfahrung nur, wenn ausdrücklich danach gefragt wird.
4. Sprachniveaus des Bewerbers: {LEVELS}. {DE_CAP}
5. Keine Gedankenstriche als Satzzeichen. Verboten: hoch motiviert, leidenschaftlich, perfekt passend, freue mich sehr, dynamischer Teamplayer, ich brenne für, highly motivated, passionate about, excited to apply.
6. Für Beispiele, Situationen, Vorgehen oder STAR-Antworten sind bis zu 150 Wörter erlaubt. Kurze Faktenfragen haben höchstens 50 Wörter. Eine vom Bewerber genannte Länge hat Vorrang.
7. Du darfst aus echten Stationen des Lebenslaufs eine nachvollziehbare Situation mit Aufgabe, Vorgehen und Ergebnis formulieren. Nutze dabei nur vorhandene Arbeitgeber, Rollen, Projekte, Methoden und Ergebnisse. Erfinde keine neue Zahl und kein neues Werkzeug.
8. Ausgabe: eine nummerierte Antwort je Frage, exakt als "1. <Antwort>", "2. <Antwort>" und so weiter. Keine Vorrede."""

CHAT_SCENARIO = re.compile(
    r"beispiel|situation|scenario|example|star\b|prozess|process|vorgehen|approach|ziel|goal|wichtigen punkte|key points",
    re.I,
)


def answer_bank_context(bank: Mapping[str, object]) -> str:
    facts = []
    for key, wording in (bank.get("facts") or {}).items():
        if isinstance(wording, dict):
            facts.append(f"{key}: DE={wording.get('de', '')}; EN={wording.get('en', '')}")
    tools = []
    for item in bank.get("tools", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        tools.append(
            f"{item.get('name')}: Jahre={item.get('years', '')}; Niveau={item.get('level', '')}; "
            f"Beispiel DE={(item.get('example') or {}).get('de', '')}; Beispiel EN={(item.get('example') or {}).get('en', '')}"
        )
    return "\n".join(["FAKTEN", *facts, "WERKZEUGE", *tools])[:12000]


def chat_questions(message: str) -> list[str]:
    """Questions in a numbered user list, or the whole message as one question."""
    found = [" ".join(match.group(1).split()) for match in re.finditer(r"(?m)^\s*\d+[.)]\s*(.+?)\s*$", message)]
    return found[:30] or [" ".join(message.split())]


def split_chat_answers(reply: str, questions: Sequence[str]) -> list[tuple[str, str]]:
    """Pair Claude's numbered answers with the user's questions by number."""
    matches = list(re.finditer(r"(?ms)^\s*(\d+)[.)]\s*(.*?)(?=^\s*\d+[.)]\s|\Z)", str(reply or "").strip()))
    values = {int(match.group(1)): " ".join(match.group(2).split()) for match in matches}
    if not values and questions:
        values[1] = " ".join(str(reply or "").split())
    return [(question, values.get(index, "")) for index, question in enumerate(questions, 1)]


def chat_answer(
    data_path: str | Path, application_id: str, message: str, history: Sequence[Mapping[str, object]] = (),
    *, model: str = "haiku", runner: Callable[..., object] = subprocess.run, usage_path: str | Path | None = None,
    answer_language: str = "auto", bank_path: str | Path | None = None,
) -> dict[str, object]:
    """One chat turn: the user's message with the job and CV as context. Returns the answer and warnings."""
    text = " ".join(str(message or "").split())[:4000] if "\n" not in str(message or "") else str(message).strip()[:4000]
    if not text:
        raise ValueError("Write a message first.")
    try:
        data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    packages = data.get("packages", []) if isinstance(data, dict) else []
    package = next((item for item in packages if item.get("id") == application_id), None) or {}
    profile = data.get("profile", {}) if isinstance(data, dict) else {}
    if not package.get("cv_text"):
        # No application recognised on this page: the CV of any prepared package still holds the facts.
        package = {**next((item for item in packages if item.get("cv_text")), {}), "title": "", "employer": "", "source": "",
                   "letter_text": ""} if not package else package
    context = facts_text(profile, package)
    description = str(package.get("job_description") or "")[:15000]
    bank_context = answer_bank_context(load_bank(bank_path)) if bank_path else ""
    turns = []
    for item in list(history)[-6:]:
        role = "Bewerber" if item.get("role") == "user" else "Claude"
        turns.append(f"{role}: {str(item.get('text') or '')[:1500]}")
    questions = chat_questions(text)
    prompt = "\n\n".join(filter(None, [
        CHAT_RULES, language_rule(answer_language), "BEWERBERFAKTEN\n" + context,
        ("STELLENBESCHREIBUNG\n" + description) if description else "",
        ("ANTWORTBANK\n" + bank_context) if bank_context else "",
        ("BISHERIGER CHAT\n" + "\n".join(turns)) if turns else "",
        "NACHRICHT DES BEWERBERS\n" + text,
    ]))
    check_plan(runner)
    reply, usage = ask_claude(
        prompt, model=model, runner=runner, purpose="chat", jobs=1, fields=len(questions), return_usage=True,
        structured_output=False, system_prompt=CHAT_SYSTEM,
    )
    answers = []
    for index, (question, raw_answer) in enumerate(split_chat_answers(str(reply or ""), questions), 1):
        checked, warnings = check_answer(
            {"label": question, "kind": "textarea", "maxLength": 4000,
             "allowLong": bool(CHAT_SCENARIO.search(question) or re.search(r"\b(?:[6-9]\d|1\d\d)\s*(?:words?|w[oö]rter)\b", text, re.I))}, raw_answer,
            context + "\n" + description,
            check_vocabulary(package, {"label": question}) + "\n" + text + "\n" + description + "\n" + bank_context,
        )
        # Chat is advisory and must never erase Claude's visible answer. Strict rejection remains
        # limited to automatic form insertion; here every issue becomes a yellow review note.
        answer = " ".join(str(raw_answer or "").split())
        answers.append({"number": index, "question": question, "answer": answer, "warnings": warnings})
    answer = "\n\n".join(f"{item['number']}. {item['answer']}" for item in answers)
    warnings = [warning for item in answers for warning in item["warnings"]]
    usage["fields"] = len(answers)
    usage["chars_out"] = sum(len(str(item["answer"])) for item in answers)
    if usage_path:
        append_jsonl(usage_path, usage)
    return {"answer": answer, "answers": answers, "warnings": warnings, "model": model,
            "package": package.get("id", ""), "questions": len(answers), "has_job_description": bool(description)}
