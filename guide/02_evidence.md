# Stage 2: evidence ledger (version E1.0)

Inputs: `workspace/sources/`, `templates/evidence_ledger.schema.json`. Outputs: `workspace/evidence/ledger.json`, `ledger.md`, `unresolved.md`, `audit.md`.

1. **Read every source.** PDFs and DOCX as text; scanned pages as images. Note per file what it proves (employer, period, role, duties, grades, certificates).
2. **Build the ledger.** One row per employable fact, fields as in the schema: evidence_id (E001...), category, employer_or_source, role_or_project, start_date, end_date, action, method, tools, result, metric (only if stated in a source), decision_relevance, skills, source_file, source_location, source_excerpt (at most 25 words), contribution_scope (individual / team / unclear), tier (T1 / T2 / U), confidence, notes. Rules:
   - a tool named in a skills list but never shown in use is T2 at most;
   - team results are not individual results;
   - official course, thesis and publication titles stay as written;
   - learning plans are U.
   Write `ledger.json` and a readable `ledger.md` grouped by station.
3. **Unresolved facts.** Contradictions, missing dates, unclear titles, team vs individual, numbers without source, "did you really use X?" go to `unresolved.md`. Ask the person in boxes of up to four questions with concrete options (for example "Your references say 03/2019, your CV says 04/2019. Which is right?" → 03/2019 / 04/2019 / other). Update the ledger with the answers (source: "Auskunft der Person, <date>").
4. **Audit.** Subagent or cold audit with this checklist, per row: VERIFIED / PARTIAL / UNSUPPORTED / CONTRADICTED / AMBIGUOUS; check employer names, dates, titles, tools, methods, metrics, causal claims, grades, courses, publications, individual vs team. Output `audit.md` with A corrections, B claims to remove, C contradictions for the person, D clean T1 list, E clean T2 list. Apply A and B.
5. **Gate A:** no material contradiction left unresolved (or explicitly carried as U). Show the person a ten-line summary (stations, degrees, strongest T1 skills) and ask "Is this complete and correct?" (Ja / Etwas fehlt / Etwas ist falsch). Record E1.0 in STATE.md.
