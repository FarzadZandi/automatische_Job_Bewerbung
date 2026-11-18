"""Build the app's German cover-letter template (Anschreiben_<Stem>.docx) from bewerberprofil.yaml.

The app fills this template for every job, so its structure is a contract (jobfilter/applications.py,
tailor_cover_letter and _set_recipient_and_date):
- the first table: row 1 holds the sender block and one paragraph with a date TT.MM.JJJJ; row 2, cell 1 holds the
  recipient (company in bold, location below);
- body paragraphs: subject with "Bewerbung als", greeting "Sehr geehrte Damen und Herren,", three placeholder
  paragraphs (opening, proof 1, proof 2), a paragraph starting "An " containing "interessiert mich besonders",
  a closing starting "Einen möglichen Starttermin", "Mit freundlichen Grüßen", the name.

Usage: python tools/build_letter_template.py PROFILE_YAML OUTPUT_DOCX
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt


def build(profile_path: Path, output: Path) -> Path:
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    name = " ".join(str(profile.get(k) or "") for k in ("academic_title", "first_name", "last_name")).split()
    name = " ".join(name) or str(profile.get("name") or "")
    street = " ".join(str(profile.get(k) or "") for k in ("street", "house_number")).strip()
    city = " ".join(str(profile.get(k) or "") for k in ("postal_code", "city")).strip()
    address = [street, city] if street or city else [str(profile.get("address_line") or "")]
    sender = [name, *address, str(profile.get("phone") or ""), str(profile.get("email") or "")]
    doc = Document()
    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.top_margin, section.bottom_margin = Cm(2), Cm(2)
    section.left_margin, section.right_margin = Cm(2.5), Cm(2)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    # python-docx documents start with no paragraph, so the table is the first body element.
    table = doc.add_table(rows=2, cols=2)
    left = table.cell(0, 0)
    left.paragraphs[0].text = sender[0]
    left.paragraphs[0].runs[0].bold = True
    for line in sender[1:]:
        if line:
            left.add_paragraph(line)
    dated = table.cell(0, 1).paragraphs[0]
    dated.text = date.today().strftime("%d.%m.%Y")
    dated.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    recipient = table.cell(1, 0)
    recipient.paragraphs[0].add_run("Unternehmen").bold = True
    recipient.add_paragraph("Ort")
    doc.add_paragraph()
    subject = doc.add_paragraph()
    subject.add_run("Bewerbung als Position").bold = True
    for text in (
        "Sehr geehrte Damen und Herren,",
        "[Einleitung: schreibt die App für jede Stelle]",
        "[Nachweis 1: aus anschreiben_bausteine.yaml]",
        "[Nachweis 2: aus anschreiben_bausteine.yaml]",
        "An [Unternehmen] interessiert mich besonders [schreibt die App für jede Stelle].",
        "Einen möglichen Starttermin stimme ich gerne mit Ihnen ab. Ich freue mich, Ihnen in einem persönlichen "
        "Gespräch mehr über meine Erfahrung und meinen Beitrag zu dieser Position zu berichten.",
        "Mit freundlichen Grüßen",
        "",
        name,
    ):
        doc.add_paragraph(text)
    doc.core_properties.title = f"Anschreiben {name}".strip()
    doc.core_properties.author = name
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    print(f"Wrote {build(args.profile, args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
