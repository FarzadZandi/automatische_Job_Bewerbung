# Stage 3: market search (the most important stage)

You search the job market yourself, deeply, with web search and page reading. Do not use the app's scrapers or any other tool for this stage. The quality of every later stage depends on this corpus.

Inputs: `workspace/evidence/ledger.json`, `workspace/intake.yaml`, `templates/vacancy.template.md`. Outputs: `workspace/market/seeds.md`, `workspace/market/search_log.md`, `workspace/market/vacancies/*.md`, `workspace/market/index.csv` (header in `templates/vacancy_index.columns.txt`).

## 1. Seeds
Write 30 to 60 seeds to `seeds.md`, each with one line "why": German and English job titles; task phrases ("Absatzprognosen erstellen"); tool + function pairs ("SQL Reporting Vertrieb"); industry contexts; adjacent professions where the person's T1 evidence is valuable under another title. Search outward from the evidence, not only from titles the person already used. Show the person the seed list grouped in 5 to 8 themes and ask "Is a kind of work missing that you would like to do?" (free text allowed).

## 2. Sources (Germany)
Bundesagentur Jobsuche web pages, StepStone, Indeed, XING, LinkedIn public job pages, company career pages and their ATS boards (Personio, Greenhouse, Lever, SmartRecruiters, join.com, softgarden, Workday, SuccessFactors), public sector (interamt.de, service.bund.de), field-specific boards for the person's profession, graduate and trainee portals, research institutes and universities when the intake includes research roles. Prefer pages you can read (company ATS pages usually are).

## 3. Search loop (per seed)
1. Search "<seed> Stellenangebot <region>" and an English variant; vary the region (city, Bundesland, "remote").
2. Open single postings (not list pages). Skip expired, duplicate, wrong seniority and clearly unrelated ads.
3. For each relevant posting write `vacancies/V0001.md` (running number) from the template:
   front matter (vacancy_id, split, company, exact_title, title_de, title_en, location, remote, employment_type, seniority, german_level_required, other_language_requirements, posting_date, source_url, source_site, fetched_on, freshness, seed, snippet_only); sections Aufgaben, Muss-Anforderungen, Kann-Anforderungen, Tools, Methoden, Kontext/Branche, Team/Funktion, Bedingungen as **short phrases in the posting's own words** (never whole paragraphs or the full ad); and the Normalisiert block (job_function, primary_problem, core_tasks 5–10, competencies 5–10, stakeholders, output_responsibility) in your own words.
4. Append one line to `index.csv` and one line to `search_log.md` (query, results seen, new vacancies).
5. **Saturation:** move to the next seed when two searches in a row add at most one new relevant vacancy.

## 4. Depth
- **Snowball:** after every 50 vacancies read the core tasks and titles; add seeds for recurring titles or task clusters you have not searched yet.
- **Holdout:** every sixth relevant vacancy gets `split: holdout`. Do not look at holdout files again before stage 8.
- **Negatives:** collect 25 ads in the region that look similar but are not target work (same words, different profession); `split: negative`.
- **Targets:** broad profile 200–300 main + 40 holdout; narrow profile 100–150 main + 30 holdout. Stop earlier only when every seed is saturated, and write why into STATE.md.

## 5. Quality
- Current postings only (freshness: active / stale / unclear), realistic seniority from the intake, deduplicated (company + title + location).
- German level required stated explicitly (for example "C1", "sehr gut", "nicht genannt").
- A page you cannot read (blocked, script-only): note it in the search log and use another source; never work around a block. A record built only from a search snippet gets `snippet_only: true` (it counts half as market support).

## 6. Checkpoints (every 50 vacancies)
Run `python3 tools/corpus_stats.py workspace/market/vacancies --out workspace/market/stats.md`. Fill gaps (seeds, regions or sources with few records), fix duplicates, update STATE.md (counts per split, last seed, next seed) and tell the person in one line: "<n> of about <target> ads collected; about <k> more sittings."

Done when the targets (or documented saturation) are reached and `corpus_stats.py` reports no duplicates.
