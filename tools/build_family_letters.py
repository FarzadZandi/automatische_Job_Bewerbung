"""Build one human-facing cover-letter template per CV family (Anschreiben_Vorlage_Bxx.docx and .md).

Input: a Baukasten YAML (templates/letter_baukasten.example.yaml):
  B01: {name: ..., openings: [...], proofs: [...], pair: [0, 1], closings: {vollzeit: ..., ...}, risks: [...]}
A plain paragraph bank (B01: [p1, p2]) is accepted too. The company paragraph stays a scaffold: it needs two
current, verified facts at application time.

Usage: python tools/build_family_letters.py BAUKASTEN_YAML OUT_DIR
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from docx import Document
from docx.shared import Pt

SCAFFOLD = ("An [UNTERNEHMEN: Name] interessiert mich besonders [UNTERNEHMEN: verifizierter Fakt 1 aus der Anzeige], "
            "weil [Bezug zu einer eigenen Erfahrung]. [UNTERNEHMEN: verifizierter Fakt 2, aktuell] zeigt, dass ...")


def letter(code: str, parts) -> tuple[str, list[str]]:
    if isinstance(parts, list):
        parts = {"proofs": parts, "pair": [0, 1]}
    openings = list(parts.get("openings") or ["[EINSTIEG: Aufgabe oder Ziel aus der Anzeige], bewerbe ich mich als [POSITION]."])
    proofs = list(parts.get("proofs") or [])
    pair = list(parts.get("pair") or [0, 1])
    chosen = [proofs[i] for i in pair if i < len(proofs)]
    closings = parts.get("closings") or {"vollzeit": "Einen möglichen Starttermin stimme ich gerne mit Ihnen ab."}
    body = ["Bewerbung als [POSITION]", "Sehr geehrte Damen und Herren,", openings[0], *chosen, SCAFFOLD,
            next(iter(closings.values())), "Mit freundlichen Grüßen", "[NAME]"]
    notes = [f"Familie {code}: {parts.get('name', '')}".strip(), "", "Weitere Einstiege:", *[f"- {o}" for o in openings[1:]],
             "", "Weitere Nachweise:", *[f"- {p}" for i, p in enumerate(proofs) if i not in pair],
             "", "Abschlüsse:", *[f"- {k}: {v}" for k, v in closings.items()],
             "", "Risikohinweise:", *[f"- {r}" for r in parts.get("risks") or []]]
    return "\n\n".join(body), notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    data = yaml.safe_load(args.source.read_text(encoding="utf-8")) or {}
    args.out.mkdir(parents=True, exist_ok=True)
    for code in sorted(data):
        text, notes = letter(code, data[code])
        (args.out / f"Anschreiben_Vorlage_{code}.md").write_text(text + "\n\n---\n\n" + "\n".join(notes) + "\n", encoding="utf-8")
        doc = Document()
        doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Calibri", Pt(11)
        for paragraph in text.split("\n\n"):
            doc.add_paragraph(paragraph)
        doc.add_page_break()
        for line in notes:
            doc.add_paragraph(line)
        doc.save(args.out / f"Anschreiben_Vorlage_{code}.docx")
    print(f"Wrote {len(data)} family letter template(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
