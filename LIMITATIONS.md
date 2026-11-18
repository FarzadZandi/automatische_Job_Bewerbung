# Limitations

Each limitation lists impact, current workaround, and a future fix.

- **L01 Market:** Germany only and German documents, including English ads. Workaround: review German output. Fix: add English CV, letter, and answer rules.
- **L02 Mac:** ported but untested on a real Mac. Workaround: run Check setup and one application first. Fix: log and repair every Mac issue.
- **L03 Job sources:** Arbeitsagentur and public Greenhouse, Lever, Ashby, and SmartRecruiters feeds; authorised Indeed CSV only; LinkedIn, StepStone, and XING links are manual. Fix: add Personio XML, softgarden, and join adapters.
- **L04 Browser:** unpacked extension in Edge or Chrome developer mode. Workaround: load and pin it manually. Fix: publish signed extensions.
- **L05 Uploads:** hidden file widgets can remain manual. Workaround: upload by hand. Fix: validate drop-event support per portal.
- **L06 Letters:** use the job ad, not live employer research, with about 5,000 Haiku output tokens. Workaround: verify the company paragraph. Fix: add sourced live research.
- **L07 Account:** Claude Pro or Max with Claude Code signed in; plan limits apply; Haiku only. Workaround: resume later. Fix: support another local writer.
- **L08 Nightly Prepare:** Windows only. Workaround: run preparation manually on Mac. Fix: add launchd scheduling.
- **L09 E-mail drafts:** EML opens as a draft only in Outlook for Windows; on Mac it opens as a message. Workaround: copy into a draft. Fix: add Mail integration.
- **L10 PDF:** Windows uses Word or LibreOffice; Mac uses LibreOffice; page breaks differ. Workaround: inspect every PDF. Fix: add renderer regression tests.
- **L11 Classification:** keyword based, without embeddings. Workaround: validate and maintain stage 9. Fix: add a local semantic model.
- **L12 Discovery:** web reach and blocks limit coverage; posting dates can be unknown; same-chat raters are partly dependent. Workaround: use multiple sources and holdout. Fix: add independent review.
- **L13 Topic modelling:** lexical TF-IDF plus NMF is only a cross-check. Workaround: decide from tasks and evidence. Fix: compare stronger local models.
- **L14 Vacancy files:** discovery stores extracted phrases, not full postings; full text is archived only for app jobs. Workaround: retain source URLs. Fix: add licensed source snapshots.
- **L15 Evidence:** limited to supplied material; reference language is read cautiously; foreign titles remain official. Workaround: ask the person. Fix: add source verification workflows.
- **L16 Work authorisation:** never inferred and no legal advice. Workaround: keep fields manual until confirmed. Fix: obtain qualified advice.
- **L17 Installation:** one person per installation. Workaround: use separate folders. Fix: add profiles.
- **L18 Tracker locking:** Excel must be closed; OneDrive may lock briefly. Workaround: close it and retry. Fix: add a database-backed writer.
- **L19 Long chats:** compaction occurs. Workaround: `STATE.md` and `continue`. Fix: automate stage checkpoints.
