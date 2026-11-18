# Architecture

## Data flow

1. `runner.py` uses adapters in `jobfilter/adapters/` and the paced `Fetcher` to collect advertisements. `store.py` deduplicates them in the private SQLite database.
2. `scoring.py` applies deterministic rules from the configured keyword JSON. `export.py` writes dated run workbooks and the two current master workbooks in `out/`.
3. `manual_links.py` reads private pasted links, never fetches job-board pages, and sends unmatched links to the local paste queue.
4. `applications.py` selects a CV family, copies one CV DOCX into an application package, and records package state in `application.json`. The app reads manifests directly; no CSV or HTML queue is generated.
5. `claude_writer.py` may write checked job-specific letter sections through Claude Haiku. `applications.py` renders the final DOCX. No model submits forms or supplies unknown personal facts.
6. `autofill.py` converts current documents to PDF and publishes a bounded six-package window to `extension/data/`. Only the current package receives temporary combined PDFs. A large general attachment PDF is cached beside its private original as `_optimised.pdf`.
7. The Edge extension recognises an employer form, fills deterministic profile and answer-bank values, uploads allowed files, and stops for login, CAPTCHA, consent, open facts, review, and submission.
8. `archive.py` records the confirmed outcome, exact CV text, and exact cover-letter text in the private tracker and archives submitted documents.

## Module map

| Area | Main modules |
|---|---|
| Collection and pacing | `runner.py`, `fetch.py`, `adapters/` |
| Deduplication and manual intake | `store.py`, `known.py`, `manual_links.py` |
| Deterministic scoring and Excel | `scoring.py`, `geography.py`, `export.py` |
| Package and letter preparation | `applications.py`, `claude_writer.py`, `hooks.py`, `paragraph_bank.py` |
| Form facts and answer reuse | `formanswers.py`, `answerbank.py`, `learning.py` |
| Local UI and browser handoff | `app.py`, `app_page.html`, `autofill.py`, `extension/` |
| Tracking and diagnostics | `archive.py`, `doctor.py`, `metrics.py`, `storage.py` |

## File locations and privacy

| Location | Purpose | Git status |
|---|---|---|
| `config.yaml` | Local paths, home coordinates, model and workflow settings | Private, ignored |
| `Job App Docs/` | Profile, CVs, template, paragraph bank, answers, attachments | Private, ignored |
| `seen.sqlite` | Seen-ad database | Private, ignored |
| `imports/` | Manual links and local import state | Private, ignored except schema example |
| `out/` | Workbooks, packages, logs, temporary PDFs | Private, ignored |
| `extension/data/` | Bounded local form-filler payload and PDFs | Private, ignored |
| `Bewerbungen/` | Tracker and submitted document archive | Private, ignored |
| `profile.example.yaml`, `answers.example.yaml`, `config.example.yaml` | Schemas with invented values | Public |

The repository contains code, deterministic rules, local HTML fixtures, and documentation only. No private application material belongs in Git.
