# Start here

**For the person (6 steps):**
1. You need a Windows 10/11 or Mac computer, the Claude desktop app, a Claude Pro or Max plan, and your documents (CVs, references, transcripts, certificates).
2. Put this folder somewhere on your computer.
3. In the Claude desktop app, start a new chat and connect this folder (Add folder).
4. Type: `Read START_HERE.md in the connected folder and follow it.`
5. Answer Claude's questions. You can stop at any time; later, open the same chat and type `continue`.
6. Nothing is ever sent to an employer without you. You do not need to know programming.

---

## Instructions for Claude

You are setting up a job-application system for one person in the German market. You do all file work. The person only answers questions, drops documents into one folder, double-clicks start files, pastes at most two commands and loads a browser extension.

### Step 0: capability check (every new session)
1. Find out what you have and write it into `workspace/STATE.md` (section "Capabilities"):
   - folder access: device tools on the person's computer, or local file tools (Claude Code opened in this folder);
   - a shell on the person's computer (Claude Code) or only a sandbox shell (desktop chat: device_bash is a Linux VM that sees this folder);
   - in the sandbox: `python3 --version` (3.11+ needed for the tools), `python3 -c "import docx, yaml, sklearn"`, `soffice --version`. Missing packages: try `pip install python-docx pyyaml scikit-learn pypdf` (or `uv pip install`), and record what failed;
   - web search and page reading; the question tool (AskUserQuestion); subagents; the person's OS (get_device_info platform, or the shell).
2. No web search: stop and ask the person to turn web search on in the chat settings. No folder access: explain in two sentences how to connect this folder (Add folder) and wait.

### Step 1: resume or start
- If `workspace/STATE.md` exists: read it, then `guide/00_rules.md`, then the guide file of the current stage, and do its "Next action". Tell the person in one line where you continue.
- Otherwise: create `workspace/` with `sources/ evidence/ market/vacancies/ families/ cv/out/ letters/out/ validation/ keywords/ reports/`, copy `templates/STATE.template.md` to `workspace/STATE.md`, and start stage 1.

### Stage map
| Stage | Guide | What happens | Main outputs |
|---|---|---|---|
| 1 | guide/01_start_and_intake.md | goals, region, languages, documents | workspace/intake.yaml, workspace/sources/ |
| 2 | guide/02_evidence.md | every fact from the documents, tiered | workspace/evidence/ledger.json |
| 3 | guide/03_market_search.md | **most important**: Claude's own deep web search, 200–300 real vacancies | workspace/market/vacancies/*.md |
| 4 | guide/04_families_optimum_n.md | groups of jobs, smallest sound number of CVs | workspace/families/role_families.json |
| 5 | guide/05_master_cv.md | one German master CV | workspace/cv/master.md |
| 6 | guide/06_baseline_cvs.md | one ATS CV per family | Job App Docs/Lebenslauf_<Stem>_NN.docx |
| 7 | guide/07_cover_letters.md | letter building blocks and templates | Job App Docs/anschreiben_bausteine.yaml, letter templates |
| 8 | guide/08_validation.md | test on unseen vacancies | workspace/validation/report.md |
| 9 | guide/09_keywords_scoring.md | keyword dictionary for the app, tested | keywords.json |
| 10 | guide/10_install_app.md | Python, app, Claude sign-in, extension | out/setup_status.json all OK |
| 11 | guide/11_configure_app.md | profile, answers, companies, searches | config.yaml, Job App Docs/*.yaml |
| 12 | guide/12_first_applications.md | first three applications together | tracker and archive entries |
| 13 | guide/13_maintenance.md | later updates from the job archive | new versions |

At every session start read `guide/00_rules.md` and only the current stage file. Background on the method: `guide/method_reference.md`. Known limits: `LIMITATIONS.md`. Problems: `guide/troubleshooting.md`.

### Tell the person at the start
- Stages 1 to 9 take several sittings (the market search alone needs hundreds of page reads); usage limits may pause the work. Coming back and typing `continue` is enough.
- The strongest available model gives the best result for stages 2 to 9. The app itself later uses the small Claude Haiku model through the person's own plan, only after they press "Ich bewerbe mich".
