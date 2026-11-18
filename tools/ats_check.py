"""ATS keyword check per CV family.

For each family: the 40 terms that appear in most of that family's vacancies (title, tasks, requirements, tools,
methods). Each term is: present (already in the CV), add (backed by T1 evidence but missing from the CV),
basic (only Tier 2 evidence: Grundkenntnisse line at most) or gap (no evidence: never add).

Usage: python tools/ats_check.py VACANCY_DIR CV_TXT LEDGER_JSON OUT_MD [--family B01] [--top 40]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vacancy_io import load, ngrams, normalize, term_text  # noqa: E402


def evidence_text(ledger: Path) -> tuple[str, str]:
    rows = json.loads(ledger.read_text(encoding="utf-8"))
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    def flat(row: dict) -> str:
        return " ".join(str(v) if not isinstance(v, list) else " ".join(map(str, v)) for k, v in row.items()
                        if k not in {"evidence_id", "source_file", "source_location", "audit_status", "confidence"})
    t1 = " ".join(flat(r) for r in rows if str(r.get("tier") or r.get("evidence_tier") or "").upper().startswith("T1"))
    t2 = " ".join(flat(r) for r in rows if str(r.get("tier") or r.get("evidence_tier") or "").upper().startswith("T2"))
    return f" {normalize(t1)} ", f" {normalize(t2)} "


def check(vacancies: Path, cv: Path, ledger: Path, family: str | None, top: int) -> str:
    items = load(vacancies, "main")
    groups: dict[str, list] = {}
    for item in items:
        key = item["family"] or "unlabelled"
        if family and key != family:
            continue
        groups.setdefault(key, []).append(item)
    cv_text = f" {normalize(cv.read_text(encoding='utf-8'))} "
    t1, t2 = evidence_text(ledger)
    lines = ["# ATS check", "", f"CV: {cv.name}", ""]
    for key in sorted(groups):
        members = groups[key]
        counts = Counter(term for item in members for term in ngrams(term_text(item)))
        minimum = 2 if len(members) >= 5 else 1
        terms = [t for t, c in counts.most_common() if c >= minimum]
        # Prefer longer phrases: drop a single word that only occurs inside a kept phrase with the same count.
        kept: list[str] = []
        for term in terms:
            if any(f" {term} " in f" {other} " and counts[other] == counts[term] for other in kept):
                continue
            kept.append(term)
            if len(kept) == top:
                break
        rows = []
        for term in kept:
            padded = f" {term} "
            if padded in cv_text:
                verdict = "present"
            elif padded in t1:
                verdict = "add"
            elif padded in t2:
                verdict = "basic"
            else:
                verdict = "gap"
            rows.append((term, counts[term], verdict))
        supported = sum(v in {"present", "add"} for _, _, v in rows)
        present = sum(v == "present" for _, _, v in rows)
        coverage = present / supported if supported else 0.0
        lines += [f"## {key} ({len(members)} vacancies)", "",
                  f"Coverage of supported terms: {coverage:.0%} ({present} of {supported}). Target: at least 80 %.", "",
                  "| Term | Vacancies | Verdict |", "|---|---:|---|"]
        lines += [f"| {t} | {c} | {v} |" for t, c, v in rows]
        lines += ["", "add = backed by T1 evidence, put it in the CV naturally; basic = Grundkenntnisse line only; "
                  "gap = no evidence, never add.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vacancies", type=Path)
    parser.add_argument("cv", type=Path, help="plain-text twin written by build_cv_docx.py")
    parser.add_argument("ledger", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--family")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)
    report = check(args.vacancies, args.cv, args.ledger, args.family, args.top)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
