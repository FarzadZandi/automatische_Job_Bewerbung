"""Build an ATS-safe German CV (DOCX) and a plain-text twin from a CV YAML file (templates/cv_content.schema.yaml).

Layout: A4, 2 cm margins, Calibri, one column, contact data in the body (not in header or footer), standard
German headings, real list bullets, dates right-aligned with a tab stop. No layout tables, text boxes or images
(an optional photo is the only image). Option --pdf converts with LibreOffice when available and prints the
page count (target: two pages).

Usage: python tools/build_cv_docx.py CV_YAML OUTPUT_DOCX [--pdf]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor

HEADINGS = (
    ("profile", "Profil"), ("experience", "Berufserfahrung"), ("education", "Ausbildung"),
    ("competencies", "Fachkompetenzen"), ("projects", "Projekte"), ("publications", "Publikationen"),
    ("languages", "Sprachen"), ("career_breaks", "Studien- und Berufsunterbrechung"), ("interests", "Interessen"),
)


def _heading(doc, text: str, lines: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(10)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0, 0, 0)
    lines.append(text)


def _entry(doc, left: str, dates: str, lines: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.tab_stops.add_tab_stop(Cm(17), WD_TAB_ALIGNMENT.RIGHT)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(4)
    paragraph.add_run(left).bold = True
    if dates:
        paragraph.add_run("\t" + dates)
    lines.append(f"{left} {dates}".strip())


def _bullet(doc, text: str, lines: list[str]) -> None:
    doc.add_paragraph(text, style="List Bullet")
    lines.append(text)


def build(source: Path, output: Path) -> list[str]:
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    doc = Document()
    section = doc.sections[0]
    section.page_height, section.page_width = Cm(29.7), Cm(21)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Cm(2)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)
    lines: list[str] = []
    header = data.get("header") or {}
    name = str(header.get("name") or "")
    title = doc.add_paragraph()
    run = title.add_run(name)
    run.bold, run.font.size = True, Pt(16)
    lines.append(name)
    contact = [str(header.get(k)) for k in ("address_line", "phone", "email") if header.get(k)]
    contact += [str(link) for link in header.get("links") or []]
    for line in contact:
        doc.add_paragraph(line)
        lines.append(line)
    if data.get("photo"):
        photo = source.parent / str(data["photo"])
        if photo.is_file():
            doc.add_picture(str(photo), width=Cm(3.5))
    for key, heading in HEADINGS:
        value = data.get(key)
        if not value:
            continue
        _heading(doc, heading, lines)
        if isinstance(value, str):
            doc.add_paragraph(value)
            lines.append(value)
            continue
        if key == "languages":
            text = ", ".join(map(str, value))
            doc.add_paragraph(text)
            lines.append(text)
            continue
        for item in value:
            if isinstance(item, str):
                _bullet(doc, item, lines)
                continue
            if key == "competencies":
                paragraph = doc.add_paragraph(style="List Bullet")
                paragraph.add_run(f"{item.get('name', '')}: ").bold = True
                paragraph.add_run(str(item.get("details", "")))
                lines.append(f"{item.get('name', '')}: {item.get('details', '')}")
                continue
            first = item.get("role") or item.get("degree") or item.get("name") or item.get("title") or ""
            second = item.get("employer") or item.get("institution") or ""
            place = item.get("location") or ""
            left = ", ".join(str(x) for x in (first, second, place) if x)
            _entry(doc, left, str(item.get("dates") or ""), lines)
            if item.get("grade"):
                doc.add_paragraph(str(item["grade"]))
                lines.append(str(item["grade"]))
            for bullet in item.get("bullets") or []:
                _bullet(doc, str(bullet), lines)
            if item.get("description"):
                doc.add_paragraph(str(item["description"]))
                lines.append(str(item["description"]))
        if key == "competencies" and data.get("basic_knowledge"):
            text = "Grundkenntnisse: " + ", ".join(map(str, data["basic_knowledge"]))
            doc.add_paragraph(text)
            lines.append(text)
    doc.core_properties.title = f"Lebenslauf {name}".strip()
    doc.core_properties.author = name
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    output.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
    return lines


def to_pdf(docx: Path) -> int | None:
    sys_path = Path(__file__).resolve().parents[1]
    import sys
    sys.path.insert(0, str(sys_path))
    try:
        from jobfilter.osutil import soffice_path
        office = soffice_path()
    except ImportError:
        office = shutil.which("soffice")
    if not office:
        print("LibreOffice not found: open the DOCX in Word and check that it fits on two pages.")
        return None
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", scratch, str(docx)],
                       check=True, capture_output=True, timeout=180)
        produced = Path(scratch) / (docx.stem + ".pdf")
        target = docx.with_suffix(".pdf")
        shutil.move(str(produced), target)
    from pypdf import PdfReader
    pages = len(PdfReader(str(target)).pages)
    print(f"PDF: {target} ({pages} page(s))")
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args(argv)
    lines = build(args.source, args.output)
    words = sum(len(line.split()) for line in lines)
    print(f"Wrote {args.output} ({words} words; target 800 to 950)")
    if args.pdf:
        pages = to_pdf(args.output)
        if pages and pages > 2:
            print("More than two pages: shorten low-relevance bullets (guide/06).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
