from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .adapters import adapter_for_url
from .adapters.company_ats import load_company_sources
from .adapters.imported_csv import imported_csv_source_url
from .applications import (
    APPLICATION_STATUSES,
    SKIP_REASON_TEXT,
    expire_applications,
    format_skip_summary,
    load_application_jobs,
    next_application,
    open_application_handoff,
    prepare_application_packages,
    set_application_status,
)
from .archive import TRACKER_NAME, ArchiveBusy, sync_archive
from .autofill import AutofillError, export_autofill, init_profile
from .config import Config
from .fetch import FetchError, Fetcher
from .github_projects import cache_age, catalog_problems, load_projects, project_problems, sync_github
from .known import KnownPositions
from .paragraph_bank import check_paragraph_bank, format_bank_report
from .runner import enrich_external_description, format_summary, run
from .scoring import load_keywords, score_text
from .store import canonicalize_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobfilter")
    parser.add_argument("--config", default="config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="collect, score, and export configured job sources")
    run_parser.add_argument("--url", action="append", default=[])
    run_parser.add_argument("--urls-file", type=Path)
    run_parser.add_argument("--max-pages", type=int, help="listing-page cap; 0 means all pages")
    run_parser.add_argument("--max-ads", type=int, help="advertisement cap; 0 means all advertisements")
    run_parser.add_argument("--since")
    run_parser.add_argument("--out", type=Path)
    run_parser.add_argument("--companies-file", type=Path)
    run_parser.add_argument("--no-companies", action="store_true")
    run_parser.add_argument("--indeed-csv", type=Path, help="authorized Indeed search CSV export")
    run_parser.add_argument("--no-indeed-import", action="store_true")
    score = commands.add_parser("score-text", help="score one saved student job")
    score.add_argument("--file", required=True, type=Path)
    score.add_argument("--title", default="")
    score.add_argument("--employment-type", default="")
    explain = commands.add_parser("explain", help="fetch and explain one BA job")
    explain.add_argument("--url", required=True)
    prepare = commands.add_parser(
        "prepare-applications",
        help="build full-time application packages from jobs_vollzeit.xlsx",
    )
    prepare.add_argument("--workbook", type=Path)
    prepare.add_argument("--documents-dir", type=Path)
    prepare.add_argument("--out", type=Path)
    prepare.add_argument("--limit", type=int)
    prepare.add_argument("--job-url", help="prepare only the exact scored job at this URL")
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--dry-run", action="store_true")
    prepare.add_argument(
        "--only", default="",
        help="comma-separated application IDs: rebuild only these existing packages, create none",
    )
    prepare.add_argument(
        "--existing-only", action="store_true",
        help="create no new packages; render existing ones whose hook.json is now present",
    )
    next_job = commands.add_parser(
        "next-application",
        help="show or open the next prepared application for the form-filler extension",
    )
    next_job.add_argument("--out", type=Path)
    next_job.add_argument("--open", action="store_true", dest="open_handoff")
    status = commands.add_parser(
        "application-status",
        help="record the outcome of a prepared application",
    )
    status.add_argument("--out", type=Path)
    status.add_argument("--id", required=True)
    status.add_argument("--status", required=True, choices=APPLICATION_STATUSES)
    expire = commands.add_parser(
        "expire-applications",
        help="mark prepared applications with stale posting dates or deadlines expired",
    )
    expire.add_argument("--out", type=Path)
    expire.add_argument("--max-age-days", type=int)
    commands.add_parser(
        "sync-github",
        help="refresh the public GitHub repository list and check the CV project catalog",
    )
    doctor = commands.add_parser("doctor", help="check documents, profile, Word, Claude, and extension export")
    doctor.add_argument("--json", type=Path, dest="json_path")
    folder = commands.add_parser("score-folder", help="score Markdown vacancy files without network access")
    folder.add_argument("folder", type=Path)
    folder.add_argument("--keywords", type=Path)
    folder.add_argument("--out", type=Path, default=Path("scores.csv"))
    autofill = commands.add_parser(
        "export-autofill",
        help="create the PDFs of the open packages and refresh the form-filler extension's data",
    )
    autofill.add_argument("--out", type=Path)
    autofill.add_argument("--only", default="", help="comma-separated application IDs (default: all open packages)")
    autofill.add_argument("--current", default="", help="current application ID; publish it and the next five")
    autofill.add_argument("--no-pdf", action="store_true", help="do not convert; use the PDFs that already exist")
    autofill.add_argument("--rebuild-pdf", action="store_true", help="convert again even when a PDF is up to date")
    autofill.add_argument("--pdf-engine", choices=("auto", "word", "libreoffice"), default="auto")
    autofill.add_argument("--init-profile", action="store_true", help="write an empty applicant profile and stop")
    app_parser = commands.add_parser("app", help="open the local control panel in the browser")
    app_parser.add_argument("--port", type=int, default=8791)
    app_parser.add_argument("--no-browser", action="store_true")
    archive = commands.add_parser(
        "archive",
        help="update the Excel tracker (CV and letter text of every application) and keep the sent documents",
    )
    archive.add_argument("--out", type=Path)
    archive.add_argument("--import-tracker", type=Path, help="start the tracker from an existing tracker workbook")
    hooks_auto = commands.add_parser(
        "write-hooks",
        help="write the cover-letter hooks of all waiting packages: Claude through your own plan, or the standard text",
    )
    hooks_auto.add_argument("--out", type=Path)
    hooks_auto.add_argument("--standard", action="store_true", help="use the fixed standard text, call no model")
    bank = commands.add_parser(
        "check-paragraph-bank",
        help="validate cover-letter proof paragraphs (structure, length, banned phrases, dashes)",
    )
    bank.add_argument("--file", type=Path, help="defaults to Job App Docs/anschreiben_bausteine.yaml")
    bank.add_argument(
        "--require-complete", action="store_true",
        help="also fail when a family still contains a FEHLT marker",
    )
    links = commands.add_parser("add-links", help="process manually pasted employer and job-board links")
    links.add_argument("--links-file", type=Path)
    links.add_argument("--done-file", type=Path)
    links.add_argument("--pending-file", type=Path)
    links.add_argument("--retry-errors", action="store_true", help="retry lines previously recorded as errors")
    complete_link = commands.add_parser("complete-link", help="score one saved manual advertisement draft")
    complete_link.add_argument("--id", required=True)
    complete_link.add_argument("--pending-file", type=Path)
    complete_link.add_argument("--drafts-file", type=Path)
    commands.add_parser("size", help="show project size and the five largest top-level items")
    survey = commands.add_parser("survey", help="read-only sequential survey of employer application portals")
    survey.add_argument("--portals", type=Path, default=Path("out/survey/portals.csv"))
    survey.add_argument("--out", type=Path, default=Path("out/survey"))
    survey.add_argument("--workbook", action="append", type=Path, default=[])
    survey.add_argument("--companies-file", type=Path)
    survey.add_argument("--extension-dir", type=Path)
    survey.add_argument("--limit", type=int, default=55)
    survey.add_argument("--select-only", action="store_true")
    survey.add_argument("--report-only", "--report", dest="report_only", action="store_true")
    survey.add_argument("--delay-min", type=float, default=5.0)
    survey.add_argument("--delay-max", type=float, default=10.0)
    survey.add_argument("--timeout", type=float, default=30.0)
    return parser


def _sync_archive(config: Config, output_dir: Path, *, import_from: Path | None = None, quiet: bool = True) -> int:
    """Keep the Excel tracker in step with the packages. A tracker that is open in Excel is reported, not fatal."""
    try:
        report = sync_archive(output_dir, config.application_archive_dir, import_from=import_from)
    except ArchiveBusy as error:
        print(f"Tracker not updated: {error}")
        return 1
    if not quiet or report["added"] or report["updated"]:
        print(
            f"Tracker: {report['path']} ({report['rows']} rows, {report['added']} new, "
            f"{report['updated']} changed, {report['documents_saved']} sent set(s) of documents kept)"
        )
        backfill = report.get("letter_backfill", {})
        reasons = ", ".join(f"{count} {reason}" for reason, count in dict(backfill.get("reasons") or {}).items())
        print(
            f"Letter text: {backfill.get('filled', 0)} filled, {backfill.get('cleaned', 0)} cleaned, "
            f"{backfill.get('left_empty', 0)} left empty" + (f" ({reasons})" if reasons else "")
        )
    return 0


def _urls(args: argparse.Namespace, config: Config) -> list[str]:
    values = list(args.url)
    if args.urls_file:
        values.extend(line.strip() for line in args.urls_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#"))
    companies_file = args.companies_file or config.companies_path
    if not args.no_companies and (args.companies_file is not None or config.companies_enabled):
        values.extend(load_company_sources(companies_file, config.company_ats_location_terms))
    indeed_csv = args.indeed_csv or config.indeed_import_path
    if not args.no_indeed_import and (args.indeed_csv is not None or config.indeed_import_enabled):
        values.append(imported_csv_source_url(
            indeed_csv,
            source="indeed",
            default_employment_type=config.indeed_default_employment_type,
        ))
    return list(dict.fromkeys(values))


def _github_projects(config: Config, *, force_sync: bool = False) -> list:
    """Sync when the cache is older than the configured age, then load placeable projects."""
    if not config.github_projects_enabled:
        return []
    age = cache_age(config.github_cache_path)
    auto_sync = config.github_sync_max_age_days > 0  # 0: never contact GitHub automatically
    if force_sync or (auto_sync and (age is None or age.days >= config.github_sync_max_age_days)):
        report = sync_github(
            config.github_user,
            cache_path=config.github_cache_path,
            catalog_path=config.github_catalog_path,
        )
        for line in report.lines():
            print(line)
    if not config.github_catalog_path.exists():
        print(f"GitHub project catalog not found: {config.github_catalog_path}. CV projects stay as they are.")
        return []
    projects = load_projects(config.github_catalog_path, config.github_cache_path)
    for problem in catalog_problems(projects):
        print(f"GitHub catalog: {problem} (this project is not placed)")
    usable = [project for project in projects if project.placeable and not project_problems(project)]
    print(f"GitHub projects available for CVs: {len(usable)} of {len(projects)} in the catalog")
    return usable


def _load_prepare_jobs(
    workbook_path: str | Path,
    *,
    max_age_days: int,
    include_other_formats: bool = False,
) -> list:
    """Load the normal queue, or every scored format for a rebuild/manual selection."""
    primary = Path(workbook_path)
    paths = [primary]
    other = primary.with_name("jobs_other.xlsx")
    if include_other_formats and other != primary and other.exists():
        paths.append(other)
    jobs = []
    seen: set[str] = set()
    for path in paths:
        for job in load_application_jobs(
            path,
            max_age_days=max_age_days,
            employment_format=None if include_other_formats else "FULL-TIME / VOLLZEIT",
        ):
            key = canonicalize_url(job.url)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(job)
    return jobs


def _profile_start_date(config) -> str:
    try:
        from .autofill import load_profile
        return str(load_profile(config.applicant_profile_path).get("start_date") or "")
    except (OSError, ValueError):
        return ""


def _profile_letter_facts(config) -> str:
    """Copy only verified, job-independent letter facts from the private profile."""
    try:
        from .autofill import load_profile
        profile = load_profile(config.applicant_profile_path)
    except (OSError, ValueError):
        return ""
    labels = {
        "german_level": "Deutsch",
        "english_level": "Englisch",
        "highest_education": "Höchster Bildungsabschluss",
        "current_employment": "Aktuelle Beschäftigung",
    }
    return "; ".join(
        f"{label}: {profile[key]}" for key, label in labels.items() if str(profile.get(key) or "").strip()
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config, validate=args.command != "doctor")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.command == "size":
        from .storage import format_size, project_size
        print(format_size(project_size(Path(args.config).resolve().parent)))
        return 0
    if args.command == "doctor":
        from .doctor import doctor_checks, status_report
        checks = doctor_checks(config, args.config)
        if args.json_path:
            args.json_path.parent.mkdir(parents=True, exist_ok=True)
            args.json_path.write_text(json.dumps(status_report(config, args.config), ensure_ascii=False, indent=2), encoding="utf-8")
        for name, ok, detail in checks:
            print(f"{'OK' if ok else 'MISSING'}  {name}: {detail}")
        return 0 if all(ok for _, ok, _ in checks) else 1
    if args.command == "score-folder":
        from .scorefolder import score_folder
        keywords = load_keywords(args.keywords or config.keywords_path)
        print(f"Scored {score_folder(args.folder, keywords, config.thresholds, args.out)} vacancy files into {args.out}")
        return 0
    if args.command == "score-text":
        result = score_text(args.title, args.file.read_text(encoding="utf-8"), load_keywords(config.keywords_path), config.thresholds, employment_type=args.employment_type)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "prepare-applications":
        only_ids = [value.strip().lower() for value in args.only.split(",") if value.strip()]
        rebuild_only = bool(only_ids) or args.existing_only
        # Rebuilding touches existing packages only, so neither the limit nor the age window applies.
        limit = None if rebuild_only else (args.limit or config.application_limit)
        workbook_path = args.workbook or config.application_workbook
        jobs = _load_prepare_jobs(
            workbook_path,
            max_age_days=3650 if rebuild_only else config.application_max_age_days,
            include_other_formats=rebuild_only or bool(args.job_url),
        )
        if not args.job_url and not rebuild_only:
            # Titles of another profession or a senior level never get a package or a letter.
            from .scoring import title_gate
            keywords = load_keywords(config.keywords_path)
            gated = [job for job in jobs if (title_gate(job.title, keywords) or ("",))[0] == "DROP"]
            if gated:
                print(f"Title gate: {len(gated)} job(s) skipped (other profession or senior level)")
            jobs = [job for job in jobs if job not in gated]
        if args.job_url:
            wanted = canonicalize_url(args.job_url)
            jobs = [job for job in jobs if canonicalize_url(job.url) == wanted][:1]
            if not jobs:
                print(f"No scored job found for URL: {args.job_url}")
                return 1
            limit = 1
        skipped: list[dict[str, str]] = []
        github_projects = _github_projects(config)
        packages = prepare_application_packages(
            jobs,
            documents_dir=args.documents_dir or config.job_documents_dir,
            output_dir=args.out or config.application_output_dir,
            applicant_status=config.applicant_status,
            graduation_date=config.graduation_date,
            start_date=_profile_start_date(config),
            attachment_allowlist=config.application_attachment_allowlist,
            enrolment_attachment_names=config.enrolment_attachment_names,
            limit=limit,
            force=args.force,
            dry_run=args.dry_run,
            skipped=skipped,
            github_projects=github_projects,
            only_ids=only_ids,
            existing_only=args.existing_only,
            applicant_file_stem=config.applicant_file_stem,
            applicant_facts=_profile_letter_facts(config),
            # Never prepare a job again that is in the tracker (also rows typed in by hand) or packaged.
            known=None if (args.existing_only or only_ids) else KnownPositions.load(
                config.application_archive_dir / TRACKER_NAME, args.out or config.application_output_dir,
            ),
        )
        counts: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for package in packages:
            sheet = str(package["source_sheet"])
            counts[sheet] = counts.get(sheet, 0) + 1
            statuses[str(package["status"])] = statuses.get(str(package["status"]), 0) + 1
        mode = "Planned" if args.dry_run else "Prepared"
        package_kind = "existing application" if rebuild_only else ("manual application" if args.job_url else "application")
        print(f"{mode} {len(packages)} {package_kind} packages")
        print("Queue order: " + ", ".join(f"{sheet}={counts.get(sheet, 0)}" for sheet in ("PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW")))
        if statuses:
            print("Package status: " + ", ".join(f"{name}={count}" for name, count in sorted(statuses.items())))
        placed = Counter(title for package in packages for title in package.get("github_projects", []))
        if placed:
            print("CV projects placed: " + ", ".join(f"{title} x{count}" for title, count in placed.most_common()))
        named = [entry for entry in skipped if entry.get("application_id")]
        if named:
            print(f"Not rebuilt: {len(named)} of {len(only_ids)} named package(s):")
            for entry in named:
                print(f"  {entry['application_id']}: {SKIP_REASON_TEXT.get(entry['reason'], entry['reason'])}")
        others = [entry for entry in skipped if not entry.get("application_id")]
        if others:
            print(f"Skipped {len(others)} job(s):")
            for line in format_skip_summary(others):
                print(f"  {line}")
        if not args.dry_run:
            return _sync_archive(config, args.out or config.application_output_dir)
        return 0
    if args.command == "export-autofill":
        if args.init_profile:
            created = init_profile(config.applicant_profile_path)
            print(("Created " if created else "Already exists: ") + str(config.applicant_profile_path))
            return 0
        try:
            report = export_autofill(
                args.out or config.application_output_dir,
                config.applicant_profile_path,
                config.autofill_extension_dir,
                only_ids=args.only.split(","),
                make_pdf=not args.no_pdf,
                rebuild_pdf=args.rebuild_pdf,
                attachment_order=config.application_attachment_allowlist,
                attachment_names=config.application_attachment_allowlist,
                residence_attachment_names=config.application_residence_attachment_names,
                pdf_engine=args.pdf_engine,
                attachments_dir=config.job_documents_dir / "Attachements",
                current_id=args.current,
                applicant_file_stem=config.applicant_file_stem,
                workbook_path=config.application_workbook,
                database_path=config.database_path,
                answer_bank_path=config.answer_bank_path,
            )
        except AutofillError as error:
            print(f"export-autofill: {error}")
            return 1
        print(f"Packages in the extension: {len(report['packages'])}")
        print(
            f"PDFs converted: {report['converted']} (engine: {report['engine']}; "
            f"{report['conversion_seconds']:.3f}s total, {report['seconds_per_document']:.3f}s/document)"
        )
        if report["missing_pdf"]:
            print(f"No PDF yet, left out: {', '.join(report['missing_pdf'])}")
        print(f"Extension data: {report['data_file']}")
        print("In the extension panel click 'Reload'.")
        return _sync_archive(config, args.out or config.application_output_dir)
    if args.command == "archive":
        return _sync_archive(config, args.out or config.application_output_dir, import_from=args.import_tracker, quiet=False)
    if args.command == "app":
        from .app import serve
        serve(args.config, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "write-hooks":
        from .claude_writer import ClaudeUnavailable, write_hooks
        writer = "standard" if args.standard else config.letter_writer
        try:
            report = write_hooks(
                args.out or config.application_output_dir,
                config.job_documents_dir / "anschreiben_bausteine.yaml", writer=writer,
                model=config.openai_model if writer == "openai" else config.claude_model,
            )
        except ClaudeUnavailable as error:
            print(f"NEEDS YOU ({error.kind}): {error}")
            print("Nothing was written. Fix this and run the step again, or use the standard text.")
            return 3
        print(f"Letters written by {writer}: {len(report['claude'])} (requests sent: {report['requests']})")
        print(f"Letters with the standard text: {len(report['standard'])}")
        if report.get("reason"):
            print(report["reason"])
        for application_id, problems in report["rejected"].items():
            print(f"  not used for {application_id}: " + "; ".join(problems))
        if report["left"]:
            print(f"Still waiting, write these by hand: {', '.join(report['left'])}")
        return 0
    if args.command == "sync-github":
        if not config.github_projects_enabled:
            print("Set github_projects_enabled: true and github_user in config.yaml first.")
            return 1
        _github_projects(config, force_sync=True)
        return 0
    if args.command == "check-paragraph-bank":
        bank_path = args.file or (config.job_documents_dir / "anschreiben_bausteine.yaml")
        from .scoring import family_codes, load_keywords as load_family_keywords
        from . import applicant
        families = family_codes(load_family_keywords(config.keywords_path)) if Path(config.keywords_path).exists() else None
        report = check_paragraph_bank(bank_path, families=families, german_level=applicant.german_level())
        print(format_bank_report(report, bank_path))
        if report.errors or (args.require_complete and report.missing):
            return 1
        return 0
    if args.command == "add-links":
        from .manual_links import process_links, retry_failed_links
        base = Path(args.config).resolve().parent / "imports"
        links_file = args.links_file or base / "links.txt"
        done_file = args.done_file or base / "links_done.txt"
        if args.retry_errors:
            print(f"Manual links moved back for retry: {retry_failed_links(links_file, done_file)}")
        results = process_links(
            config,
            links_file,
            done_file,
            args.pending_file or base / "manual_pending.json",
        )
        if not results:
            print("Manual links: nothing new")
        for row in results:
            print(f"{row['result']}: {row['line']}")
        return 0 if all(not row["result"].startswith("error:") for row in results) else 1
    if args.command == "complete-link":
        from .manual_links import complete_saved_draft
        base = Path(args.config).resolve().parent / "imports"
        try:
            result = complete_saved_draft(
                config, args.pending_file or base / "manual_pending.json",
                args.drafts_file or base / "manual_drafts.json", args.id,
            )
        except (FileNotFoundError, ValueError, OSError) as error:
            print(f"NEEDS YOU (manual text): {error}")
            return 1
        print(f"Manual advertisement: {result['result']} ({result.get('title', args.id)})")
        return 0
    if args.command == "next-application":
        output_dir = args.out or config.application_output_dir
        job = open_application_handoff(output_dir) if args.open_handoff else next_application(output_dir)
        if job is None:
            print("No approved applications remain in the prepared queue")
            return 0
        print(json.dumps(job, ensure_ascii=False, indent=2))
        if args.open_handoff:
            print("Opened one approved form and package. Complete CAPTCHA/empty fields and review before submitting.")
        return 0
    if args.command == "application-status":
        manifest = set_application_status(
            args.out or config.application_output_dir,
            args.id,
            args.status,
        )
        print(f"Application {manifest['application_id']} marked {manifest['submission_status']}")
        _sync_archive(config, args.out or config.application_output_dir)
        return 0
    if args.command == "expire-applications":
        expired = expire_applications(
            args.out or config.application_output_dir,
            max_age_days=args.max_age_days or config.application_max_age_days,
            archive_dir=config.jd_archive_dir,
        )
        print(f"Marked {len(expired)} application(s) expired")
        _sync_archive(config, args.out or config.application_output_dir)
        return 0
    if args.command == "survey":
        from .survey import run_command
        report = run_command(
            portals_path=args.portals, output_dir=args.out,
            extension_dir=args.extension_dir or config.autofill_extension_dir,
            workbooks=args.workbook, companies_path=args.companies_file or config.companies_path,
            select_only=args.select_only, report_only=args.report_only, limit=args.limit,
            delay_min=args.delay_min, delay_max=args.delay_max, timeout_seconds=args.timeout,
        )
        if args.select_only:
            print(f"Selected {report['selected']} portal(s): {report['portals']}")
        else:
            print(
                f"Survey: written={report['written']}, resumed={report['skipped']}, errors={report['errors']}\n"
                f"JSON lines: {report['jsonl']}\nReport: {report['report']}"
            )
        return 0
    if args.command == "run":
        full_time_xlsx, other_xlsx, stats = run(
            _urls(args, config), config, max_pages=args.max_pages, max_ads=args.max_ads,
            since=args.since, output_dir=args.out,
        )
        print(format_summary(stats))
        print(f"Vollzeit Excel: {full_time_xlsx}")
        print(f"Other Excel: {other_xlsx}")
        return 0
    adapter = adapter_for_url(args.url)
    fetcher = Fetcher(config)
    ad = adapter.parse_detail(fetcher.get(args.url, cache_detail=True).text, args.url)
    try:
        ad, _ = enrich_external_description(ad, fetcher)
    except (FetchError, ValueError) as exc:
        # Explain remains useful when a partner page is unavailable; the run
        # command logs and counts the same condition.
        logging.warning("Could not use external description: %s", exc)
    result = score_text(ad.title, ad.full_text, load_keywords(config.keywords_path), config.thresholds, employment_type=ad.employment_type)
    print(json.dumps({"job": {"title": ad.title, "employer": ad.employer, "url": ad.url}, "score": result.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
