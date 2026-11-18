# Stage 6: one ATS CV per family (version B1.0)

Inputs: `workspace/cv/master.md`, `role_families.json`, ledger, `templates/cv_content.schema.yaml` (example: `templates/cv_content.example.yaml`). Outputs: `workspace/cv/Bxx.yaml`, `workspace/cv/out/`, `Job App Docs/Lebenslauf_<Nachname>_<Vorname>_NN.docx`.

File stem: `<Nachname>_<Vorname>` without spaces or umlauts problems (ä → ae). The app finds CV NN by the ending `_NN.docx` and takes the stem from the letter template name, so both must use the same stem.

1. **Content per family** (`workspace/cv/B01.yaml` ...): the family profile from Teil B; 6–8 competence categories from Teil D in that order, tools inline in parentheses; every station from Teil A with bullets weighted by relevance (most relevant station 3 bullets, medium 2, low 1); relevant projects; publications if the family values them; languages on one line; the "Studien- und Berufsunterbrechung" section when there are gaps; Grundkenntnisse (T2) only in `basic_knowledge`; photo only if the intake says so. Length 800–950 words unless the history justifies more. Next to every bullet keep the evidence ID as a YAML comment.
2. **Build:** `python3 tools/build_cv_docx.py workspace/cv/B01.yaml workspace/cv/out/Lebenslauf_<Stem>_01.docx --pdf` (without LibreOffice skip `--pdf` and ask the person later to check two pages in Word). The layout is ATS-safe: one column, contact data in the body, standard headings, real bullets, no tables, text boxes or graphics.
3. **ATS check:** `python3 tools/ats_check.py workspace/market/vacancies workspace/cv/out/Lebenslauf_<Stem>_01.txt workspace/evidence/ledger.json workspace/cv/out/ats_B01.md --family B01`. Work the verdicts: "add" terms go into the CV where they are true (bullet or competence line); "basic" terms only on the Grundkenntnisse line; "gap" terms never. Target: coverage of supported terms at least 80 %. Rebuild after changes.
4. **Audit table** per CV: claim | evidence_id | verified. Remove any claim without an ID.
5. **Person's review** per CV (question tool): "CV <family name>: approve or change?" (Freigeben / Ändern, with free text). Show the PDF or DOCX if possible.
6. Copy approved files to `Job App Docs/` as `Lebenslauf_<Stem>_01.docx` ... `_NN.docx` (NN = family number). Record B1.0.
