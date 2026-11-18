# Stage 8: validation on unseen vacancies

Inputs: holdout vacancies (`split: holdout`), `role_families.json`, approved CVs and letters. Output: `workspace/validation/report.md`.

1. For every holdout ad: primary and second-best family; the five most important requirements, each mapped to evidence IDs or marked as a real gap; what would need tailoring; whether the chosen family is clearly better than the second.
2. Aggregate: confusion between families (use the stage 4 rater CSV and `tools/agreement.py`), uncovered job types, redundant families, weak market support, repeated evidence gaps.
3. If a pair of families is confused repeatedly or a family gets almost no ads: propose merge, split or sharper definitions; ask the person (Zusammenlegen / Behalten / Ändern) and repeat the stage 4 tests for the affected families. Never create a family for one unusual ad.
4. Done when most holdout ads map clearly to one family, no family needs invented skills, every family has evidence and market support, and the person can explain every CV claim in an interview.
