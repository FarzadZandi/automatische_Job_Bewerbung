# Stage 9: keyword dictionary for the app (version K1.0)

The app finds and ranks jobs with a keyword dictionary (`keywords.json` in the package root). It must describe the person's families, not anyone else's. `templates/keywords_example_analytics.json` shows a finished dictionary for one analytics profile: use it as a pattern only.

Inputs: `role_families.json`, ledger, intake, all vacancies. Output: `keywords.json`, `workspace/keywords/report.md`.

1. Copy `templates/keywords.template.json` to `keywords.json` and fill every section (each has a `_how_to_fill` note). Terms are lowercase with ä → ae, ö → oe, ü → ue, ß → ss.
   - `cv_families`: keys `B01_<short_name>` ... contiguous; per family label, weight, at least 15 strong terms (titles and specific tasks that alone indicate the family) and weak terms (supporting words). Take the terms from the family's vacancies (`ats_check.py` output helps).
   - `cv_family_tie_breakers.machine_rules`: for neighbouring families, `{"prefer": "B03", "field": "title" | "full_text", "terms": [...]}`.
   - `title_boost`, `analysis_task` (the field's own task terms), `methods`, `tools_strong` (T1 tools), `tools_basic_or_adjacent` (T2), `context_bonus`.
   - `employment_gate`: the formats from the intake.
   - `hard_exclusion`: seniority above the target, professions the person excluded. Never the person's own profession.
   - `kill_conditional`: only professions the person does not want.
   - German penalty and English bonus only if the person's German is below C1.
   - `industry_phd.enabled`: false unless the person wants doctoral roles.
2. **Test** on the corpus (from the package root):
   `python3 -m jobfilter score-folder workspace/market/vacancies --keywords keywords.json --out workspace/keywords/scores.csv`
   `python3 tools/agreement.py workspace/keywords/scores.csv claude_family primary_family --where split=main`
   `python3 tools/agreement.py workspace/keywords/scores.csv claude_family primary_family --where split=holdout`
   Required: at least 90 % of main and holdout ads pass (column `passed`), family accuracy at least 85 % (main) and 80 % (holdout), at least 90 % of negatives dropped (`split=negative`, `passed` false). Iterate on terms, not on the labels.
   If the sandbox cannot import the app (`python3 -c "import jobfilter"` from the package root fails because a package is missing), install it there (`pip install requests beautifulsoup4 lxml openpyxl pyyaml geopy pypdf pillow countrystatecity-countries`) or run the test after stage 10 on the person's computer.
3. Write the numbers to `workspace/keywords/report.md` and record K1.0. Note in the report that the corpus files hold extracted phrases; after the first real searches, compare with full ads in the archive (stage 13).
