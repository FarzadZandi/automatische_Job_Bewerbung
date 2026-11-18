from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .scoring import score_text, title_gate


def score_folder(folder: str | Path, keywords: dict, thresholds: dict[str, int], out: str | Path) -> int:
    rows = []
    for path in sorted(Path(folder).rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = {}
        body = text
        if text.startswith("---"):
            _, front, body = text.split("---", 2)
            meta = yaml.safe_load(front) or {}
        title = str(meta.get("exact_title") or meta.get("title") or "")
        gate = title_gate(title, keywords)
        if gate and gate[0] == "DROP":
            rows.append({"vacancy_id": meta.get("vacancy_id", path.stem), "split": meta.get("split", ""), "claude_family": meta.get("claude_family", ""), "title": title, "passed": False, "band": "DROP", "drop_reason": gate[1], "primary_family": "", "secondary_family": "", "family_scores": "{}"})
            continue
        result = score_text(title, body, keywords, thresholds, employment_type=str(meta.get("employment_type") or ""), employer=str(meta.get("company") or ""))
        rows.append({"vacancy_id": meta.get("vacancy_id", path.stem), "split": meta.get("split", ""), "claude_family": meta.get("claude_family", ""), "title": title, "passed": result.band != "DROP", "band": result.band, "drop_reason": result.reason or "", "primary_family": result.primary_cv_family, "secondary_family": result.secondary_cv_family, "family_scores": str(result.family_scores)})
    target = Path(out); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("vacancy_id", "split", "claude_family", "title", "passed", "band", "drop_reason", "primary_family", "secondary_family", "family_scores")); writer.writeheader(); writer.writerows(rows)
    return len(rows)
