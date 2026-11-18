"""Counts for the market corpus, to find gaps during the search (guide/03 step 10).

Usage: python tools/corpus_stats.py VACANCY_DIR [--out OUT_MD]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vacancy_io import load, normalize  # noqa: E402

FIELDS = ("split", "source_site", "seed", "location", "seniority", "employment_type", "german_level_required",
          "freshness", "claude_family")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vacancies", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    items = load(args.vacancies)
    lines = ["# Corpus statistics", "", f"Records: {len(items)}",
             f"Snippet-only: {sum(i['snippet_only'] for i in items)}", ""]
    for field in FIELDS:
        counts = Counter(str(i["meta"].get(field) or "unknown") for i in items)
        lines += [f"## {field}", ""] + [f"- {k}: {v}" for k, v in counts.most_common(25)] + [""]
    keys = Counter((normalize(i["meta"].get("company")), normalize(i["title"]), normalize(i["meta"].get("location"))) for i in items)
    duplicates = [k for k, v in keys.items() if v > 1]
    lines += [f"## Duplicates: {len(duplicates)}", ""] + [f"- {c} | {t} | {l}" for c, t, l in duplicates] + [""]
    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 1 if duplicates else 0


if __name__ == "__main__":
    raise SystemExit(main())
