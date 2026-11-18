# Offline tools

- `build_cv_docx.py input.yaml output.docx`: build an ATS-safe German CV and text twin.
- `build_letter_template.py profile.yaml output.docx`: build the app-compatible letter template.
- `build_family_letters.py baukasten.yaml out/`: build human family letters.
- `validate_bank.py bank.yaml --keywords keywords.json`: validate two proof paragraphs per family.
- `ats_check.py vacancies/ cv.txt ledger.json out.md`: report supported term coverage.
- `topic_check.py vacancies/ out.md`: lexical NMF cross-check; code 2 without scikit-learn.
- `family_overlap.py vacancies/ out-prefix`: family overlap Markdown and CSV.
- `agreement.py labels.csv column_a column_b`: accuracy, kappa, and confusion matrix.
- `corpus_stats.py vacancies/`: corpus counts and duplicates.
- `check_privacy.py folder --profile file --answers file --config file`: runtime private-data scan.
