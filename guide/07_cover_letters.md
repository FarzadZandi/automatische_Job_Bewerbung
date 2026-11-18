# Stage 7: cover-letter building blocks and templates (version C1.0)

How the app writes a letter: it takes the letter template, puts in the two proof paragraphs of the job's family from the paragraph bank, and lets Claude Haiku write the opening sentence and the company/fit paragraph from the full ad, only after the person presses "Ich bewerbe mich". There is no live company research (LIMITATIONS L06).

Inputs: ledger, `role_families.json`, approved CVs, `templates/letter_baukasten.template.md` and `templates/letter_baukasten.example.yaml`. Outputs: `workspace/letters/baukasten.yaml` (+ `Bxx_Baukasten.md` for reading), `Job App Docs/anschreiben_bausteine.yaml`, `Job App Docs/Anschreiben_<Stem>.docx`, `workspace/letters/out/Anschreiben_Vorlage_Bxx.docx`.

1. **Baukasten per family** (in `baukasten.yaml`, format as in the example): 2–4 openings about the work or problem (not "Hiermit bewerbe ich mich"); 3–6 proof paragraphs from T1 evidence only (Handlung, Methode, Ergebnis); the recommended pair (two proofs from different competence areas); closing variants (Vollzeit, Trainee, Werkstudent, Praktikum as needed); honest wording for typical gaps. No generic praise ("dynamisches Umfeld", "Marktführer").
2. **Paragraph bank for the app:** `Job App Docs/anschreiben_bausteine.yaml` with exactly two paragraphs per family (the pair), 60–90 words each:
   ```yaml
   B01:
     - "erster Nachweisabsatz ..."
     - "zweiter Nachweisabsatz ..."
   ```
   Check: `python3 tools/validate_bank.py "Job App Docs/anschreiben_bausteine.yaml" --keywords keywords.json --answers "Job App Docs/antworten.yaml" --profile "Job App Docs/bewerberprofil.yaml"`. If `keywords.json` does not exist yet, use `workspace/families/role_families.json` order and run the check again in stage 9.
3. **App letter template:** after the profile exists (stage 11) or with a provisional profile file: `python3 tools/build_letter_template.py "Job App Docs/bewerberprofil.yaml" "Job App Docs/Anschreiben_<Stem>.docx"`. Do not edit its structure by hand; the app needs it.
4. **One template per CV for the person:** `python3 tools/build_family_letters.py workspace/letters/baukasten.yaml workspace/letters/out`. These are for manual applications and review.
5. **Person's review:** show one finished example letter per family and ask "Does this sound like you and is every statement true?" (Ja / Ändern). Record C1.0.
