"""Shared helpers for the offline tools: read vacancy Markdown files and normalise text like the app."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})
STOP = set("""
aber alle allem allen aller alles als also am an andere anderen auch auf aus bei bein beim bereits bis bist
bzw da dabei damit dann das dass dein deine dem den denen der deren des dessen die dies diese diesem diesen dieser
dieses doch dort du durch ein eine einem einen einer eines eigene eigenen er es etc etwa euch fuer gegen gerne
gute guten gutes hat hast hatte haben hier ihr ihre ihrem ihren ihrer im in ins ist ja jede jeden jeder jedes
kann keine koennen mit mehr mindestens mw mwd nach neben nicht noch nur ob oder ohne sehr sein seine sich sie
sind so sowie ueber um und uns unser unsere unter viel vom von vor wenn wer werden wie wir wird wo zu zum zur
zwischen idealerweise insbesondere bieten bietest bringst bringen suchen sucht deine deinen deiner ihnen
a an and are as at be by for from has have in into is it its of on or our the their this to we will with you your
who what which within across able ability strong good experience plus etc including such using use
""".split())
SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
TERM_SECTIONS = ("Aufgaben", "Muss-Anforderungen", "Kann-Anforderungen", "Tools", "Methoden")


def normalize(value: Any) -> str:
    text = str(value or "").casefold().translate(FOLD)
    return " ".join(re.sub(r"[^a-z0-9+#./ -]", " ", text).replace("/", " ").split())


def parse(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
    sections: dict[str, str] = {}
    parts = SECTION.split(body)
    for index in range(1, len(parts), 2):
        sections[parts[index].strip()] = parts[index + 1].strip()
    normal: dict[str, Any] = {}
    block = sections.get("Normalisiert", "")
    if ":" in block:
        try:
            loaded = yaml.safe_load(block)
            normal = loaded if isinstance(loaded, dict) else {}
        except yaml.YAMLError:
            normal = {}
    def listed(key: str) -> list[str]:
        value = meta.get(key) or normal.get(key) or []
        return [str(item) for item in (value if isinstance(value, list) else [value]) if str(item).strip()]
    return {
        "path": path, "meta": meta, "sections": sections, "body": body,
        "id": str(meta.get("vacancy_id") or path.stem), "split": str(meta.get("split") or "main"),
        "family": str(meta.get("claude_family") or ""), "family_2": str(meta.get("claude_family_2") or ""),
        "title": str(meta.get("exact_title") or meta.get("title") or ""),
        "core_tasks": listed("core_tasks"), "competencies": listed("competencies"),
        "snippet_only": bool(meta.get("snippet_only")),
    }


def load(folder: Path, split: str | None = None) -> list[dict[str, Any]]:
    items = [parse(path) for path in sorted(Path(folder).rglob("*.md"))]
    return [item for item in items if split is None or item["split"] == split]


def term_text(item: dict[str, Any]) -> str:
    """Text that describes the work: title, tasks, requirements, tools, methods, normalised fields."""
    parts = [item["title"], *(item["sections"].get(name, "") for name in TERM_SECTIONS), *item["core_tasks"], *item["competencies"]]
    if not any(item["sections"].get(name) for name in TERM_SECTIONS):
        parts.append(item["sections"].get("Normalisiert", ""))
    return "\n".join(str(part) for part in parts if part)


def ngrams(text: str, sizes: Iterable[int] = (1, 2, 3)) -> set[str]:
    words = [word for word in normalize(text).split() if len(word) > 1]
    found: set[str] = set()
    for size in sizes:
        for start in range(len(words) - size + 1):
            gram = words[start:start + size]
            if gram[0] in STOP or gram[-1] in STOP or any(len(w) < 3 and not w.isalnum() for w in gram):
                continue
            if size == 1 and (len(gram[0]) < 3 or gram[0].isdigit()):
                continue
            found.add(" ".join(gram))
    return found


def item_list(text: str) -> list[str]:
    """Split a section written as bullets, commas or semicolons into normalised items."""
    raw = re.split(r"[\n,;•]|^\s*[-*]\s*", text, flags=re.M)
    return [normalize(piece.strip(" -*")) for piece in raw if normalize(piece.strip(" -*"))]
