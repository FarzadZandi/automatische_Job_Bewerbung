"""The answer bank learns from submitted forms.

The extension reports what is in each open-question field while the user works (``observe``).
Only when the user records the application as submitted are those final values written to the
private bank (``learn_submission``).  Later forms reuse them through deterministic matching
(``match_learned``): no model, no tokens.  Skipped applications teach nothing.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Mapping, Sequence

FILL_AT = 0.85
SUGGEST_AT = 0.6
STALE_DAYS = 180
MAX_LEARNED = 1000
MAX_HISTORY = 5
MAX_LABELS = 8

# Profile data never enters the learned bank: it already lives in the profile.
ID_KEYS = {
    "first_name", "last_name", "full_name", "email", "email_repeat", "phone", "street", "house_number",
    "street_full", "postal_code", "city", "postal_city", "address_full", "country", "birth_date",
    "birth_day", "birth_month", "birth_year", "salutation", "academic_title", "nationality",
    "linkedin", "xing", "github", "website",
}
NEVER = re.compile(
    r"passwor|captcha|datenschutz|privacy|consent|einwilligung|terms|bedingungen|agb|i agree|ich stimme|"
    r"best[aä]tig|confirm|personenbezogen|newsletter|gender|geschlecht|pronoun|sponsorship|visa\b|visum|aufenthalt|"
    r"disab|behinder|ethnic|race\b|veteran|religio|nationalit|staatsangeh|sexual|orientation",
    re.I,
)
# Page furniture and placeholder labels are not questions ("Sprache auswählen", "Auswählen", "dps dps 19").
JUNK_LABEL = re.compile(
    r"^\s*(?:(?:bitte\s+)?(?:aus)?w[aä]hlen|select(?:\.\.\.)?|choose|please select|option|"
    r"(?:sprache|language)(?:\s+(?:aus)?w[aä]hlen|\s+select)?)\s*\*?\s*$|^(\w+)(?:\s+\1){1,}\b|"
    r"registration form desired job title field ui typeahead input",
    re.I,
)
EMPLOYER_CHOICE = re.compile(r"standort|location|site\b|niederlassung|office|city preference|einsatzort|bereich|team|abteilung", re.I)


def learnable_label(label: object) -> bool:
    text = str(label or "").strip()
    return bool(text) and len(re.findall(r"[^\W\d_]", text)) >= 4 and not NEVER.search(text) and not JUNK_LABEL.search(text)
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})
_GENDER = re.compile(
    r"\((?:\s*(?:m|w|d|f|x|div|all genders?|gn)\s*[/|,]?\s*)+\)|[*:_/]innen\b|[*:_/]in\b",
    re.I,
)
# Phrases first (order matters), then single words, each mapped to one concept token.
_PHRASES = [
    (r"notice periods?|kuendigungsfrist(?:en)?", " notice "),
    (r"(?:earliest (?:possible )?)?start(?:ing)? date|when (?:could|can|would) you start|"
     r"(?:fruehestmoeglich(?:e[rn]?)? )?(?:eintritts?termin|eintrittsdatum|eintritt)|verfuegbar ab|available from|"
     r"availability|verfuegbarkeit", " startdate "),
    (r"at the earliest|earliest possible|fruehestens|fruehestmoeglich(?:e[rn]?)?", " "),
    (r"salary expectations?|gehaltsvorstellung(?:en)?|gehaltswunsch|compensation expectations?", " salary "),
    (r"driving licen[cs]e|fuehrerschein", " licence "),
    (r"work permit|work authori[sz]ation|arbeitserlaubnis|arbeitsgenehmigung|visa sponsorship", " workpermit "),
    (r"how did you (?:hear|learn|find out) (?:about|of) (?:us|this (?:job|position|role|vacancy|opening)|the (?:job|position|role))|"
     r"wie (?:sind|haben) sie (?:von|auf) (?:uns|diese stelle|die stelle|dieses stellenangebot) (?:erfahren|aufmerksam geworden)|"
     r"aufmerksam geworden(?: durch)?|where did you (?:see|find) (?:this|the) (?:job|position|ad)", " source "),
    (r"how many|wie viele|wieviele", " howmany "),
    (r"willing(?:ness)? to travel|reisebereitschaft", " travel "),
    (r"willing(?:ness)? to relocate|umzugsbereitschaft|umzugsbereit", " relocation "),
]
_WORDS = {
    "gehalt": "salary", "verguetung": "salary", "salary": "salary", "compensation": "salary",
    "reisen": "travel", "travel": "travel", "travelling": "travel", "traveling": "travel",
    "umzug": "relocation", "relocate": "relocation", "relocation": "relocation", "relocating": "relocation",
    "erfahrung": "experience", "erfahrungen": "experience", "experience": "experience", "experiences": "experience",
    "berufserfahrung": "experience", "experienced": "experience",
    "kenntnisse": "skills", "skills": "skills", "skill": "skills", "faehigkeiten": "skills", "kompetenzen": "skills",
    "warum": "why", "weshalb": "why", "wieso": "why", "why": "why",
    "motivation": "motivation", "motivated": "motivation", "motiviert": "motivation", "motivates": "motivation",
    "unternehmen": "company", "company": "company", "firma": "company", "organisation": "company",
    "organization": "company", "arbeitgeber": "company", "employer": "company",
    "stelle": "role", "position": "role", "role": "role", "rolle": "role", "job": "role", "taetigkeit": "role",
    "bewerbung": "application", "application": "application", "bewerben": "application", "apply": "application",
    "sprache": "language", "sprachen": "language", "language": "language", "languages": "language",
    "englisch": "english", "english": "english", "englischkenntnisse": "english",
    "deutsch": "german", "german": "german", "deutschkenntnisse": "german",
    "jahre": "years", "jahren": "years", "years": "years", "year": "years",
    "interessiert": "interest", "interest": "interest", "interested": "interest", "interests": "interest",
    "reizt": "interest", "excites": "interest", "excite": "interest", "appeals": "interest", "begeistert": "interest",
    "staerken": "strengths", "strengths": "strengths", "strength": "strengths",
    "schwaechen": "weaknesses", "weaknesses": "weaknesses", "weakness": "weaknesses",
    "beschreiben": "describe", "describe": "describe", "beschreibe": "describe", "schildern": "describe",
    "gehaltsvorstellung": "salary", "visa": "workpermit", "visum": "workpermit",
    "aufmerksam": "source", "erfahren": "source", "hear": "source", "heard": "source", "found": "source",
    "quelle": "source", "referral": "source",
}
_STOP = set("""
a an the your you yours do does did are is was were what which who whom how please briefly if yes no to of in on at for
with and or this that these those our us we can could would will shall should be been being have has had any some
there here as by from about into than then it its my me i mine also just only not very more most such per via
ihre ihr ihrer ihren ihrem sie du dein deine deiner der die das den dem des ein eine einer eines einem und oder zu
zum zur mit bei fuer von vom im in am an auf ist sind war waren haben hat habe hast koennen kannst koennten
welche welcher welches was wie bitte kurz uns unser unsere unserer wir es als ab bis ueber unter nach vor aus
applicable optional falls sofern gegebenenfalls ggf e g z b etc so sich mich mir
""".split())


def fold(value: object) -> str:
    return str(value or "").casefold().translate(_FOLD)


def concepts(label: object) -> list[str]:
    """Canonical tokens of a question: same meaning in German and English gives the same tokens."""
    text = _GENDER.sub(" ", str(label or ""))
    text = fold(text)
    for pattern, replacement in _PHRASES:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"[0-9]+", " ", text)
    tokens = []
    for word in re.findall(r"[a-z]+", text):
        word = _WORDS.get(word, word)
        if len(word) < 2 or word in _STOP:
            continue
        if word not in tokens:
            tokens.append(word)
    return tokens


def question_key(label: object) -> str:
    return " ".join(sorted(concepts(label)))


def similarity(left: object, right: object) -> float:
    a, b = set(concepts(left)), set(concepts(right))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    common = len(a & b)
    jaccard = common / len(a | b)
    small = min(len(a), len(b))
    containment = common / small if small >= 3 else 0.0
    return round(max(jaccard, 0.9 * containment), 3)


def kind_group(kind: object) -> str:
    return "choice" if str(kind or "") in {"select", "radio"} else "text"


_GENERIC_EMPLOYER = set("""
gmbh ag se kg co mbh ohg ug kgaa group gruppe holding deutschland germany international services service
solutions technologies technology systems consulting partner partners europe europa the und and
""".split())


def employer_tokens(employer: object) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", fold(employer)) if len(word) >= 4 and word not in _GENERIC_EMPLOYER}


def same_employer(left: object, right: object) -> bool:
    a, b = employer_tokens(left), employer_tokens(right)
    return bool(a and b and (a <= b or b <= a or len(a & b) >= 2))


def detect_scope(answer: object, employer: object, title: object = "") -> str:
    text = fold(answer)
    words = set(re.findall(r"[a-z0-9]+", text))
    if employer_tokens(employer) & words:
        return "this employer only"
    phrase = " ".join(re.findall(r"[a-z0-9]+", fold(_GENDER.sub(" ", str(title or "")))))
    if phrase and len(phrase) >= 8 and phrase in " ".join(re.findall(r"[a-z0-9]+", text)):
        return "this employer only"
    return "any employer"


def _today(today: date | str | None) -> date:
    if isinstance(today, date):
        return today
    if today:
        return date.fromisoformat(str(today)[:10])
    return date.today()


def _past_date_in(text: object, today: date) -> bool:
    for day, month, year in re.findall(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", str(text or "")):
        try:
            if date(int(year), int(month), int(day)) < today:
                return True
        except ValueError:
            continue
    for year, month, day in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", str(text or "")):
        try:
            if date(int(year), int(month), int(day)) < today:
                return True
        except ValueError:
            continue
    return False


def option_for(answer: object, options: Sequence[object]) -> str:
    """The offered option that carries the learned answer, or '' when none clearly does."""
    wanted = fold(answer).strip()
    clean = [str(option) for option in options if str(option).strip()]
    for option in clean:
        if fold(option).strip() == wanted:
            return option
    wanted_words = " ".join(re.findall(r"[a-z0-9]+", wanted))
    if not wanted_words:
        return ""

    def words(option: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", fold(option)))

    hits = [option for option in clean if words(option) == wanted_words]
    if not hits:  # "Ja" -> "Ja, sofort"; "LinkedIn" -> "LinkedIn Jobs"; never a word inside another word
        hits = [option for option in clean if words(option).startswith(wanted_words + " ")]
    if not hits and len(wanted_words) >= 3:
        hits = [option for option in clean if re.search(rf"(?:^| ){re.escape(wanted_words)}(?: |$)", words(option))]
    return hits[0] if len(hits) == 1 else ""


def match_learned(
    field: Mapping[str, object], bank: Mapping[str, object], *, employer: str = "",
    language: str = "", today: date | str | None = None,
) -> dict[str, object]:
    """Best learned answer for a field: mode 'fill', 'suggest' or '' (nothing usable)."""
    label = str(field.get("label") or "")
    group = kind_group(field.get("kind"))
    current = _today(today)
    best, best_score = None, 0.0
    for entry in bank.get("learned", []) or []:
        if not isinstance(entry, dict) or not str(entry.get("answer") or "").strip():
            continue
        if entry.get("group") != group:
            continue
        if group == "text" and language and entry.get("language") not in {language, ""}:
            continue
        if entry.get("scope") == "this employer only" and not same_employer(entry.get("employer"), employer):
            continue
        variants = [entry.get("question")] + list(entry.get("labels") or [])
        key = question_key(label)
        score = 1.0 if key and key == entry.get("key") else max(similarity(label, variant) for variant in variants if variant)
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < SUGGEST_AT:
        return {"mode": "", "value": "", "score": best_score}
    value = str(best.get("answer") or "")
    mode = "fill" if best_score >= FILL_AT else "suggest"
    try:
        last_used = date.fromisoformat(str(best.get("last_used") or "")[:10])
    except ValueError:
        last_used = current
    stale = (current - last_used).days > STALE_DAYS or _past_date_in(value, current)
    if stale:
        mode = "suggest"
    if group == "choice":
        option = option_for(value, list(field.get("options") or []))
        if option:
            value = option
        else:
            mode = "suggest"
    note = f"learned from {best.get('employer') or 'an earlier form'}, {str(best.get('last_used') or '')[:10]}"
    return {"mode": mode, "value": value, "score": best_score, "note": note, "stale": stale, "key": best.get("key")}


# ---------------------------------------------------------------- observation store

class ObservationStore:
    """Values the user is putting into open questions, per application, until it is submitted."""

    def __init__(self, path: str | Path, *, max_packages: int = 60, max_fields: int = 200) -> None:
        self.path = Path(path)
        self.max_packages, self.max_fields = max_packages, max_fields

    def load(self) -> dict[str, object]:
        try:
            from .safeio import read_text
            value = json.loads(read_text(self.path))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: Mapping[str, object]) -> None:
        from .safeio import atomic_write_json
        keep = sorted(data.items(), key=lambda item: str((item[1] or {}).get("updated") or ""), reverse=True)
        atomic_write_json(self.path, dict(keep[: self.max_packages]), indent=None)

    @staticmethod
    def _field_key(label: object, page: object = "") -> str:
        return f"{page}\u0000{' '.join(fold(label).split())}"

    def observe(self, package_id: str, record: Mapping[str, object]) -> bool:
        label = str(record.get("label") or "").strip()
        kind = str(record.get("kind") or "")
        key = str(record.get("key") or "")
        if not package_id or not label or kind not in {"text", "textarea", "select", "radio"}:
            return False
        if key in ID_KEYS or not learnable_label(label):
            return False
        data = self.load()
        package = data.setdefault(package_id, {"fields": {}, "produced": {}})
        fields = package.setdefault("fields", {})
        field_key = self._field_key(label, record.get("page"))
        value = str(record.get("value") or "")[:2000]
        entry = fields.get(field_key)
        produced = package.get("produced", {}).get(" ".join(fold(label).split()), {})
        if entry is None:
            if len(fields) >= self.max_fields:
                return False
            source = str(record.get("source") or "")
            if not source and value and produced and produced.get("value") == value:
                source = str(produced.get("source") or "")
            entry = {
                "label": label[:300], "kind": kind, "key": key, "page": str(record.get("page") or "")[:300],
                "options": [str(option)[:200] for option in list(record.get("options") or [])[:60]],
                "first_value": value, "first_source": source or ("user" if value else ""), "value": value,
            }
            fields[field_key] = entry
        else:
            if not entry.get("first_value") and value:
                source = str(record.get("source") or "")
                if not source and produced and produced.get("value") == value:
                    source = str(produced.get("source") or "")
                entry["first_value"], entry["first_source"] = value, source or "user"
            entry["value"] = value
        package["updated"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)
        return True

    def produced(self, package_id: str, answers: Sequence[Mapping[str, object]], default_source: str = "claude") -> None:
        """Remember what the app itself put into fields, so the source of a value is known."""
        if not package_id or not answers:
            return
        data = self.load()
        package = data.setdefault(package_id, {"fields": {}, "produced": {}})
        produced = package.setdefault("produced", {})
        for answer in answers:
            label = " ".join(fold(answer.get("label")).split())
            if label and answer.get("value"):
                produced[label] = {"value": str(answer["value"])[:2000], "source": str(answer.get("source") or default_source)}
        package["updated"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)

    def take(self, package_id: str) -> list[dict[str, object]]:
        """Remove and return the observed fields of one application."""
        data = self.load()
        package = data.pop(package_id, None) or {}
        self._save(data)
        return list((package.get("fields") or {}).values())

    def discard(self, package_id: str) -> None:
        data = self.load()
        if data.pop(package_id, None) is not None:
            self._save(data)


# ---------------------------------------------------------------- learning

def learn_submission(
    bank_path: str | Path, observed: Sequence[Mapping[str, object]], *, employer: str = "", title: str = "",
    package_id: str = "", today: date | str | None = None,
) -> dict[str, int]:
    """Write the final values of a submitted application into the bank. Returns counts."""
    from .answerbank import classify_question, load_bank, question_language, write_bank

    bank = load_bank(bank_path)
    learned: list[dict[str, object]] = bank.setdefault("learned", [])
    pending: list[dict[str, object]] = bank.setdefault("pending", [])
    stamp = _today(today).isoformat()
    counts = {"learned": 0, "updated": 0, "reused": 0, "pending": 0, "skipped": 0}
    for record in observed:
        label = str(record.get("label") or "").strip()
        value = " ".join(str(record.get("value") or "").split()) if record.get("kind") != "textarea" \
            else str(record.get("value") or "").strip()
        ignored = {" ".join(fold(item).split()) for item in bank.get("ignore", [])}
        if not label or not value or " ".join(fold(label).split()) in ignored \
                or str(record.get("key") or "") in ID_KEYS or not learnable_label(label):
            counts["skipped"] += 1
            continue
        first = str(record.get("first_value") or "").strip()
        first_source = str(record.get("first_source") or "user")
        edited = bool(first) and " ".join(first.split()) != " ".join(value.split())
        source = "user" if edited or not first else first_source
        group = kind_group(record.get("kind"))
        language = question_language(label) if group == "text" else ""
        key = question_key(label)
        if not key:
            counts["skipped"] += 1
            continue
        if source == "profile":
            counts["skipped"] += 1  # the profile already holds it
            continue
        classification = classify_question(label, bank)
        if group == "text" and classification.get("kind") == "fact" and len(classification.get("fact_keys") or []) == 1:
            fact = str(classification["fact_keys"][0])
            current = str((bank.get("facts", {}).get(fact) or {}).get(language) or "")
            if " ".join(current.split()) != " ".join(value.split()) and not any(
                item.get("fact") == fact and item.get("language") == language and item.get("value") == value for item in pending
            ):
                pending.append({"fact": fact, "language": language, "value": value[:600], "label": label[:300],
                                "employer": employer[:200], "date": stamp})
                counts["pending"] += 1
            continue
        scope = detect_scope(value, employer, title) if group == "text" else "any employer"
        if EMPLOYER_CHOICE.search(label) or detect_scope(label, employer) == "this employer only":
            scope = "this employer only"
        entry = next((
            item for item in learned
            if item.get("key") == key and item.get("group") == group and item.get("language", "") == language
            and item.get("scope") == scope and (scope == "any employer" or same_employer(item.get("employer"), employer))
        ), None)
        if entry is not None and entry.get("answer") == value:
            entry["last_used"] = stamp
            entry["times_used"] = int(entry.get("times_used") or 0) + 1
            if label not in entry.get("labels", []):
                entry["labels"] = (list(entry.get("labels") or []) + [label[:300]])[-MAX_LABELS:]
            counts["reused"] += 1
            continue
        if entry is not None:
            history = list(entry.get("history") or [])
            history.insert(0, {"answer": entry.get("answer"), "employer": entry.get("employer"), "date": entry.get("last_used")})
            entry.update({
                "answer": value[:2000], "source": source, "edited": edited, "employer": employer[:200],
                "package": package_id, "last_used": stamp, "times_used": int(entry.get("times_used") or 0) + 1,
                "history": history[:MAX_HISTORY],
            })
            if label not in entry.get("labels", []):
                entry["labels"] = (list(entry.get("labels") or []) + [label[:300]])[-MAX_LABELS:]
            if group == "choice":
                entry["options"] = list(record.get("options") or [])[:60]
            counts["updated"] += 1
            continue
        learned.append({
            "key": key, "question": label[:300], "labels": [label[:300]], "group": group, "language": language,
            "answer": value[:2000], "options": list(record.get("options") or [])[:60] if group == "choice" else [],
            "source": source, "edited": edited, "employer": employer[:200], "package": package_id,
            "scope": scope, "first_seen": stamp, "last_used": stamp, "times_used": 1, "history": [],
        })
        counts["learned"] += 1
    if len(learned) > MAX_LEARNED:
        learned.sort(key=lambda item: str(item.get("last_used") or ""), reverse=True)
        del learned[MAX_LEARNED:]
    write_bank(bank_path, bank)
    return counts


def resolve_pending(bank_path: str | Path, index: int, keep: bool) -> dict[str, object]:
    """Keep (write into the facts) or discard one learned fact waiting for confirmation."""
    from .answerbank import load_bank, write_bank

    bank = load_bank(bank_path)
    pending = bank.setdefault("pending", [])
    if not 0 <= index < len(pending):
        raise ValueError("No such learned fact")
    item = pending.pop(index)
    if keep:
        facts = bank.setdefault("facts", {})
        facts.setdefault(item["fact"], {"de": "", "en": ""})[item.get("language") or "de"] = item["value"]
    write_bank(bank_path, bank)
    return bank


def edit_learned(bank_path: str | Path, key: str, group: str, *, answer: str | None = None,
                 scope: str | None = None, delete: bool = False, language: str = "") -> dict[str, object]:
    from .answerbank import load_bank, write_bank

    bank = load_bank(bank_path)
    learned = bank.setdefault("learned", [])
    for position, item in enumerate(learned):
        if item.get("key") == key and item.get("group") == group and item.get("language", "") == language:
            if delete:
                learned.pop(position)
            else:
                if answer is not None and answer.strip():
                    item["answer"] = answer.strip()[:2000]
                    item["source"], item["edited"] = "user", True
                if scope in {"any employer", "this employer only"}:
                    item["scope"] = scope
            write_bank(bank_path, bank)
            return bank
    raise ValueError("No such learned answer")
