"""Check the app's paragraph bank (Job App Docs/anschreiben_bausteine.yaml) with the app's own rules:
exactly two proof paragraphs of 60–90 words per configured family, no banned phrases, no Tier 2 skills,
no German level above the profile, no dashes as connectors.

Usage: python tools/validate_bank.py BANK_YAML --keywords keywords.json [--answers antworten.yaml] [--profile bewerberprofil.yaml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobfilter import applicant  # noqa: E402
from jobfilter.paragraph_bank import check_paragraph_bank, format_bank_report  # noqa: E402
from jobfilter.scoring import family_codes, load_keywords  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bank", type=Path)
    parser.add_argument("--keywords", type=Path, required=True)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args(argv)
    applicant.configure(args.profile, args.answers)
    families = family_codes(load_keywords(args.keywords))
    import yaml
    tier2 = tuple((yaml.safe_load(args.answers.read_text(encoding="utf-8")) or {}).get("tier2_skills") or ()) if args.answers else ()
    report = check_paragraph_bank(args.bank, families=families, tier2_skills=tier2, german_level=applicant.german_level())
    print(format_bank_report(report, args.bank))
    return 1 if report.errors or report.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
