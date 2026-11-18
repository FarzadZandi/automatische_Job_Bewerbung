# Stage 5: master CV (version M1.0)

Inputs: ledger, `role_families.json`, `templates/master_cv.structure.md`. Output: `workspace/cv/master.md`, `workspace/cv/master_audit.md`.

1. Write the German master CV (Baukasten): Teil A Stammlebenslauf (every employer, role, date, degree, project, publication, language; gaps under "Studien- und Berufsunterbrechung"), Teil B one profile per family (60–90 words), Teil C bullet pool per family (Handlung, Methode, Ergebnis; each bullet with its evidence ID), Teil D 6–8 ordered competence categories per family with tools inline, Teil E evidence tiers, Grundkenntnisse (T2) and open issues. The master is a superset and is never trimmed.
2. Audit (subagent or cold): every claim has an evidence ID, dates and employers match the ledger, T2 never appears as applied work, no causal claim without evidence. Output `master_audit.md`; fix every finding.
3. Show the person Teil A and ask "Are all stations, dates and degrees correct?" (Ja / Korrektur). Record M1.0.
