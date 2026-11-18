# Stage 13: maintenance from the job archive

Every 25 applications or once a month (the person asks "update my CVs" or you see it in STATE.md):

1. Read `Bewerbungen/Stellenarchiv/index.csv` and the full ads of applied and skipped jobs (`Bewerbungen/Stellenarchiv/<year>/`). Compare applied, skipped and dropped jobs, results per family (tracker column "Ergebnis"), recurring requirements, new titles.
2. Run `tools/ats_check.py` with the archive folder as vacancy input (the archived files carry `primary_family`; copy them to a temporary folder with `claude_family` set to that value) to find requirements the CVs do not cover. Only T1-supported terms may be added.
3. Write a proposal to `workspace/reports/maintenance_<date>.md`: what changes, which version (E evidence, R families, M master, B CVs, C letters, K keywords). Ask the person to approve each change. Never rewrite past tracker rows.
4. After changes re-run `ats_check.py`, `validate_bank.py` and the stage 9 test. Every 3 to 6 months repeat a light stage 3 (about 50 new vacancies) to see whether the market moved.
