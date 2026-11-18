# Stage 12: first applications together

1. Set `max_ads: 50` in config.yaml for the first run. The person starts the app (start file) and clicks Prepare. Explain the queue: PRIORITY first, then REVIEW.
2. For the first three applications, stay with the person:
   - "Apply next" opens one form; the extension fills it; the person checks every field and attachment.
   - The person presses the green "Ich bewerbe mich" button; only now Claude Haiku writes the letter's opening and company paragraph. Read the letter together: true, specific, German level right?
   - The person submits the form themselves and marks the job as submitted. Skipped jobs are marked skipped.
3. Check: the tracker (`Bewerbungen/Bewerbungs_Tracker.xlsx`) has the rows with CV and letter text; `Bewerbungen/Stellenarchiv/index.csv` has the applied and skipped jobs and their full ads.
4. Explain the answer bank: answers the person types in forms are learned after "submitted"; close matches fill automatically later, loose matches are suggested; personal questions (salary, visa, declarations) always stay with the person.
5. If a letter or CV statement was weak or wrong: fix the bank paragraph or CV in stages 6/7 (with the person's approval) and note the change in STATE.md. Then set `max_ads` back to 0 (unlimited).
