"""Agreement between two family labellings (for example Claude vs the app scorer, or two raters on the holdout).

Prints accuracy, Cohen's kappa, the confusion matrix and the most confused pairs. Targets (guide/04, guide/09):
kappa at least 0.7 and accuracy at least 80 % on the holdout; at least 85 % family accuracy on main vacancies.

Usage: python tools/agreement.py LABELS_CSV COLUMN_A COLUMN_B [--where split=holdout] [--out OUT_MD]
The CSV written by `python -m jobfilter score-folder` already has claude_family, primary_family and split.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def evaluate(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    accuracy = sum(a == b for a, b in pairs) / n if n else 0.0
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    expected = sum(count_a[k] * count_b[k] for k in set(count_a) | set(count_b)) / (n * n) if n else 0.0
    kappa = (accuracy - expected) / (1 - expected) if n and expected < 1 else 1.0
    confused = Counter(tuple(sorted(p)) for p in pairs if p[0] != p[1])
    return {"n": n, "accuracy": accuracy, "kappa": kappa, "matrix": Counter(pairs), "confused": confused.most_common(5),
            "labels": sorted(set(count_a) | set(count_b))}


def report(result: dict, a: str, b: str) -> str:
    labels = result["labels"]
    lines = ["# Agreement", "", f"Rows: {result['n']}", f"Accuracy: {result['accuracy']:.1%}", f"Cohen's kappa: {result['kappa']:.2f}", "",
             f"Rows = {a}, columns = {b}", "", "| | " + " | ".join(labels) + " |", "|---|" + "---:|" * len(labels)]
    for x in labels:
        lines.append(f"| {x} | " + " | ".join(str(result["matrix"][(x, y)]) for y in labels) + " |")
    lines += ["", "Most confused pairs:"] + [f"- {p[0]} / {p[1]}: {c}" for p, c in result["confused"]] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("column_a")
    parser.add_argument("column_b")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--where", action="append", default=[], help="filter such as split=holdout (repeatable)")
    args = parser.parse_args(argv)
    with args.csv.open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    for condition in args.where:
        key, _, value = condition.partition("=")
        rows = [r for r in rows if (r.get(key) or "") == value]
    pairs = [((r.get(args.column_a) or "none").strip()[:3] or "none", (r.get(args.column_b) or "none").strip()[:3] or "none")
             for r in rows]
    text = report(evaluate(pairs), args.column_a, args.column_b)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
