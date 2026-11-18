"""Pairwise overlap of CV families from labelled main vacancies.

Per family: the set of normalised core tasks, competencies, tools and methods. Pairs are compared with Jaccard
(shared / all) and containment (shared / smaller set). Pairs above 0.70 are merge candidates unless the
positioning differs in at least two of the six dimensions (guide/04). Market support counts main vacancies
(snippet-only records count half).

Usage: python tools/family_overlap.py VACANCY_DIR OUT_PREFIX     (writes OUT_PREFIX.md and OUT_PREFIX.csv)
"""
from __future__ import annotations

import argparse
import csv
import itertools
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vacancy_io import item_list, load, normalize  # noqa: E402

THRESHOLD = 0.70


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vacancies", type=Path)
    parser.add_argument("out_prefix", type=Path)
    args = parser.parse_args(argv)
    sets: dict[str, set[str]] = defaultdict(set)
    support: dict[str, float] = defaultdict(float)
    for item in load(args.vacancies, "main"):
        family = item["family"]
        if not family:
            continue
        support[family] += 0.5 if item["snippet_only"] else 1.0
        sets[family].update(normalize(x) for x in item["core_tasks"] + item["competencies"])
        for name in ("Tools", "Methoden"):
            sets[family].update(item_list(item["sections"].get(name, "")))
    rows = []
    for a, b in itertools.combinations(sorted(sets), 2):
        shared = sets[a] & sets[b]
        union = sets[a] | sets[b]
        jaccard = len(shared) / len(union) if union else 0.0
        containment = len(shared) / min(len(sets[a]), len(sets[b])) if sets[a] and sets[b] else 0.0
        rows.append((a, b, round(jaccard, 3), round(containment, 3), max(jaccard, containment) > THRESHOLD))
    lines = ["# Family overlap", "", "| Family | Market support | Distinct terms |", "|---|---:|---:|"]
    lines += [f"| {f} | {support[f]:g} | {len(sets[f])} |" for f in sorted(sets)]
    lines += ["", "| Pair | Jaccard | Containment | Merge candidate |", "|---|---:|---:|---|"]
    lines += [f"| {a} / {b} | {j:.2f} | {c:.2f} | {'yes' if flag else ''} |" for a, b, j, c, flag in rows]
    lines += ["", f"Merge candidate = Jaccard or containment above {THRESHOLD:.2f}. Market support below 10 needs a "
              "justified niche (guide/04).", ""]
    prefix = args.out_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    with prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("family_a", "family_b", "jaccard", "containment", "merge_candidate"))
        writer.writerows(rows)
    print(f"Wrote {prefix.with_suffix('.md')} and {prefix.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
