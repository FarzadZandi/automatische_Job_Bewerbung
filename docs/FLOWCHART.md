# Flowchart

How the package works, from the first chat message to a submitted application and later CV updates. The first two diagrams are the same as in the README. GitHub draws them; in other viewers paste them into https://mermaid.live.

## 1. Replication: from this repository to a working setup

```mermaid
flowchart TD
    subgraph GET["Get the package"]
        direction TB
        G1["Download or clone this repository"]
        G2["Claude desktop app: new chat,<br/>connect the folder"]
        G3["Type: Read START_HERE.md<br/>and follow it"]
        G1 --> G2 --> G3
    end

    subgraph CHAT["Part 1, one Claude chat over several sittings: discovery and documents"]
        direction TB
        C0["Capability check: folder, shell, Python tools,<br/>web search, OS; resume from workspace/STATE.md"]
        S1["1 Intake: region, job formats,<br/>career level, languages"]
        S1d["Person adds CVs, references,<br/>transcripts, certificates"]
        S2["2 Evidence ledger: every fact<br/>T1 applied, T2 basic, U unclear"]
        S2q{"Gaps or<br/>contradictions?"}
        S2a["Ask the person"]
        S3["3 Market search done by Claude itself<br/>200-300 real ads, 40 holdout, 25 negatives"]
        S4["4 Families: proposals, topic model,<br/>overlap and coverage tests"]
        S4q{"Smallest N that passes all tests,<br/>holdout kappa at least 0.7?"}
        S4a["Person approves families B01 to BNN"]
        S5["5 Master CV, Teil A to E"]
        S6["6 One ATS CV per family<br/>build_cv_docx + ats_check"]
        S7["7 Letter blocks: paragraph bank,<br/>app template, one template per CV"]
        S8["8 Validation on the holdout ads"]
        S8q{"Families stable?"}
        S9["9 keywords.json for the app,<br/>tested with score-folder"]
        C0 --> S1 --> S1d --> S2 --> S2q
        S2q -- "yes" --> S2a --> S2
        S2q -- "no" --> S3 --> S4 --> S4q
        S4q -- "no: merge or split" --> S4
        S4q -- "yes" --> S4a --> S5 --> S6 --> S7 --> S8 --> S8q
        S8q -- "no" --> S4
        S8q -- "yes" --> S9
    end

    subgraph APP["Part 2 on the person's computer: install and configure"]
        direction TB
        S10["10 Install: Python 3.11+, start file,<br/>Claude Code sign-in, browser extension"]
        S10q{"out/setup_status.json<br/>all OK?"}
        S10f["Next single fix from<br/>guide/troubleshooting.md"]
        S11["11 Configure: profile, answer bank,<br/>company feeds, searches, attachments"]
        S10 --> S10q
        S10q -- "no" --> S10f --> S10
        S10q -- "yes" --> S11
    end

    G3 --> C0
    S9 --> S10
    S11 --> R(["12 First three applications together,<br/>then daily use"])
```

## 2. Job application: daily use

```mermaid
flowchart TD
    subgraph FIND["Prepare: find and score jobs, no Claude tokens"]
        direction TB
        F1["Arbeitsagentur searches<br/>urls.txt"]
        F2["Company career feeds<br/>Greenhouse, Lever, Ashby, SmartRecruiters"]
        F3["Manual links<br/>XING, LinkedIn, StepStone alerts"]
        F4["Authorised CSV import"]
        F5["Skip jobs already seen<br/>seen.sqlite"]
        F6["Score with keywords.json: formats,<br/>exclusions, title gate, CV family"]
        F7{"Band"}
        F8["Package per job: CV of its family,<br/>full ad text, form data"]
        F1 --> F5
        F2 --> F5
        F3 --> F5
        F4 --> F5
        F5 --> F6 --> F7
        F7 -- "PRIORITY, REVIEW,<br/>DE REQUIRED" --> F8
    end

    subgraph APPLY["Apply: one job at a time"]
        direction TB
        A1["Queue in the app: PRIORITY first,<br/>Arbeitsagentur CAPTCHA jobs last"]
        A2["Apply next: opens one form<br/>or prepares an e-mail application"]
        A3["Extension fills known fields from profile,<br/>answer bank and learned answers;<br/>uploads the CV, letter slot waits"]
        A4{"Person decides"}
        A5["Green button: Ich bewerbe mich"]
        A6["Claude Haiku on the person's own plan:<br/>opening and fit paragraph from the full ad"]
        A7["Letter DOCX and PDF: template plus<br/>the family's two proof paragraphs"]
        A8["Extension attaches the letter;<br/>Ask Claude or chat for open questions"]
        A9["Person checks every field and submits,<br/>or sends the e-mail draft"]
        A1 --> A2 --> A3 --> A4
        A4 -- "apply" --> A5 --> A6 --> A7 --> A8 --> A9
    end

    subgraph RECORD["Record and learn"]
        direction TB
        R1["Marked submitted"]
        R2["Answer bank learns the typed answers"]
        R3["Tracker row with CV and letter text<br/>Bewerbungs_Tracker.xlsx"]
        R4[("Stellenarchiv: applied, full ad")]
        R5[("Stellenarchiv: skipped, full ad")]
        R6[("Stellenarchiv: dropped or expired,<br/>index line only")]
        R1 --> R2
        R1 --> R3
        R1 --> R4
    end

    F8 --> A1
    F7 -- "DROP" --> R6
    A4 -- "skip" --> R5
    A9 --> R1
    R1 -.->|"next job"| A1
```

## 3. Maintenance (stage 13)

```mermaid
flowchart LR
    A[("Stellenarchiv:<br/>applied, skipped, dropped")] --> B["Every 25 applications<br/>or monthly"]
    B --> C["Compare outcomes per family,<br/>uncovered requirements, new titles"]
    C --> D["Proposal with versions<br/>E R M B C K"]
    D --> E{"Person approves?"}
    E -- "yes" --> F["Update CVs, letters, keywords;<br/>re-run the checks"]
    E -- "no" --> G["Keep as is"]
```

Never automated: submitting, consent boxes, CAPTCHAs, passwords, salary, visa and sponsorship answers.
