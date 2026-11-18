# Stage 4: CV families and the optimum number (version R1.0)

The number of CVs (N) is a result, never a preset. Target: the smallest number of families that covers the person's realistic market while keeping materially different positioning.

Inputs: ledger, main vacancies. Outputs in `workspace/families/`: `capability_map.md`, `proposals.md`, `topic_check.md`, `overlap.md`, `decision.md`, `role_families.json`, `families.md`.

1. **Capability map** (`templates/capability_map.template.md`): per capability domain the strongest T1 evidence IDs, T2 knowledge, settings, adjacent job functions, gaps. Mark which domains can anchor a CV alone.
2. **Proposals:** over-generate 8 to 20 candidate families from job function, primary problem and core tasks (not from titles alone; "Business Analyst" can mean BI, process or strategy). Per candidate: name DE/EN, representative titles, core tasks, recurring requirements, strongest evidence, likely language requirement.
3. **Label** every main vacancy: `claude_family` and `claude_family_2` in its front matter.
4. **Topic cross-check:** `python3 tools/topic_check.py workspace/market/vacancies workspace/families/topic_check.md` (k from 3 to 15, five seeds). Stable k values are candidates, not decisions. For every disagreement (a family spread over two stable topics, two families sharing one topic) write a line in `decision.md`: kept, merged or split, and why. If scikit-learn cannot run, write that into decision.md and compare term lists by hand.
5. **Overlap:** `python3 tools/family_overlap.py workspace/market/vacancies workspace/families/overlap`.
6. **Tests** (write results to `decision.md`):
   - coverage: at least 90 % of main vacancies have a credible family;
   - evidence density: a profile, two strong T1 proof stories and 5–6 competence categories per family, without T2 inflation;
   - market support: at least 10–15 vacancies per family, or a justified niche with recurring employers;
   - merge: pairs above 0.70 overlap merge, unless at least two of these differ: profile, top three evidence stories, competence order, project selection, search vocabulary, letter opening and proofs;
   - split: a family with two stable sub-markets needing different primary evidence splits;
   - maintenance: every extra family costs upkeep.
   Decision rule: the smallest N that passes all tests; on a tie, fewer.
7. **Stability on unseen ads:** take the holdout files. Rater A: a fresh pass where you read only the family definitions and each holdout ad. Rater B: a subagent with the same inputs when available. Write the labels into a CSV (columns vacancy_id, rater_a, rater_b) and run `python3 tools/agreement.py <csv> rater_a rater_b`. Required: kappa at least 0.7 and accuracy at least 80 %; otherwise sharpen definitions or merge the most confused pair and repeat.
8. **Person's approval:** show a plain table (family, example titles, number of ads, what this CV emphasises). Ask per unclear family: "Keep / merge with <neighbour> / drop". Then freeze: codes B01..BNN, contiguous, in `role_families.json` (schema `templates/role_families.schema.json`) and `families.md`. Example of a finished taxonomy for one analytics profile: `templates/example_families.md` (do not copy its N).
