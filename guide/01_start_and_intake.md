# Stage 1: start and intake

Inputs: `templates/intake.template.yaml`. Outputs: `workspace/intake.yaml`, documents in `workspace/sources/`.

1. **Explain** in six plain sentences: (1) we collect every real fact from your documents, (2) I search the job market myself for a few hundred real ads that fit you, (3) I group them and find the smallest number of CV versions you need, (4) I write those CVs and letter building blocks in German, (5) I test them on ads I set aside, (6) then we install the app that finds jobs, fills forms and writes the job-specific part of each letter. Done when the person says to go on.

2. **Intake box 1** (question tool):
   - "Which language should we use in this chat?" (Deutsch / English / other)
   - "Where do you want to work?" options: "Only near my city", "Anywhere in Germany", "Remote preferred", other (free text: city and distance)
   - "Which kinds of positions?" multi-select: Vollzeit, Teilzeit, Werkstudent, Praktikum, Trainee/Berufseinstieg, Promotion/Forschung
   - "Which career level?" options: Berufseinstieg (0–2 Jahre), Professional (2–5 Jahre), Senior (5+ Jahre)
   Then ask in free text for the home city (for distances).

3. **Intake box 2:**
   - "How good is your German?" A2 or lower / B1 / B2 / C1 / C2 or native
   - "How good is your English?" same options
   - "Do you study right now?" Ich studiere / Ich schließe gerade ab / Abschluss vorhanden / Ich arbeite
   - "Should your CV have a photo?" Nein, ATS-sicher (recommended) / Ja, deutscher Standard
   Free text afterwards: other languages with level; graduation date if studying or graduating; earliest start date.

4. **Intake box 3** (free text allowed): professions, industries or employers to exclude; preferred sectors or company sizes; constraints (hours, travel, driving licence); anything employers must not see.

5. Write `workspace/intake.yaml` (every field answered or explicitly empty). Update STATE.md.

6. **Documents.** Create `workspace/sources/` and ask the person to put in: every CV version, Arbeitszeugnisse, transcripts (Notenspiegel), degree and language certificates, other certificates, a publication list, portfolio or GitHub links (as a text file), a LinkedIn PDF export, earlier application history (tracker or a list) and ads that led to interviews. In the desktop app the person drags files into the folder in Explorer or Finder; tell them where the folder is. List the received file names back. Done when the person confirms the list is complete.
