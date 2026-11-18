from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .adapters.base import JobAd
from .config import Config
from .geography import LocationInfo, enrich_location
from .scoring import (
    SCORING_RULE_VERSION,
    ScoreResult,
    doctoral_title_hits,
    load_keywords,
    priority_title_hits,
    score_text,
    title_gate,
)
from .store import canonicalize_url


KEPT_COLUMNS = [
    "score", "band", "employment_format", "primary_cv_family", "secondary_cv_family",
    "title", "employer", "is_staffing_agency", "location", "federal_state", "distance_from_home_km",
    "posted", "deadline", "board", "url", "external_url",
    "family_hits", "analysis_task_hits", "method_hits", "tool_hits", "language_flags",
    "eligibility_flags", "other_flags", "penalties", "bonuses", "kill_reason", "snippet",
    "rule_version", "fetched_date", "raw_employment_type", "full_text",
]
INDUSTRY_COLUMNS = [*KEPT_COLUMNS, "industry_trigger", "university_partner"]
DROPPED_COLUMNS = ["title", "employer", "url", "band", "date"]
WIDTHS = [8, 11, 25, 40, 40, 48, 32, 18, 23, 24, 27, 13, 20, 18, 48, 48, 45, 40, 35, 42, 32, 36, 40, 32, 28, 32, 60, 22, 14, 24, 90]
DROPPED_WIDTHS = [48, 32, 58, 12, 14]
HEADER_FILL = PatternFill("solid", fgColor="174A6E")
HEADER_FONT = Font(color="FFFFFF", bold=True)
PRIORITY_FILL = PatternFill("solid", fgColor="D9EAF7")
AMBER_FILL = PatternFill("solid", fgColor="FCE4A6")
SHEET_NAMES = ("PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW", "Academische", "DROPPED")
DAILY_WORKBOOK_PATTERN = re.compile(
    r"^jobs_(vollzeit|other)_(\d{4}-\d{2}-\d{2})(?:_(\d+))?\.xlsx$",
    re.IGNORECASE,
)
DEFAULT_KEYWORDS_PATH = Path(__file__).resolve().parent.parent / "keywords.json"


@dataclass(frozen=True)
class ScoredJob:
    ad: JobAd
    board: str
    result: ScoreResult


def _join(values: Iterable[str]) -> str:
    return "; ".join(dict.fromkeys(value for value in values if value))


def _hits(result: ScoreResult, predicate) -> str:
    values: list[str] = []
    for group, hits in result.matched_terms.items():
        if predicate(group):
            values.extend(hits)
    return _join(values)


def _kept_row(item: ScoredJob, fetched_date: date, home_coordinates: tuple[float, float] | None) -> list[object]:
    ad, result = item.ad, item.result
    family_hits = _hits(result, lambda group: group.startswith("B") and group.endswith((".strong", ".weak")))
    if home_coordinates:
        location_info = enrich_location(ad.location, home_coordinates)
    else:
        located = enrich_location(ad.location, (0.0, 0.0))
        location_info = LocationInfo(located.federal_state, None)
    return [
        result.score, result.band, result.employment_format, result.primary_cv_family,
        result.secondary_cv_family, ad.title, ad.employer,
        bool(ad.raw_meta.get("is_staffing_agency")), ad.location,
        location_info.federal_state, location_info.distance_from_home_km,
        ad.posted_date, ad.deadline, item.board, ad.url,
        str(ad.raw_meta.get("external_final_url") or ad.raw_meta.get("external_url") or ""), family_hits,
        _join(result.matched_terms.get("analysis_task", ())),
        _join(result.matched_terms.get("methods", ())),
        _join((*result.matched_terms.get("tools_strong", ()), *result.matched_terms.get("tools_basic_or_adjacent", ()))),
        _join(result.language_flags), _join(result.eligibility_flags), _join(result.other_flags),
        _join(result.penalties), _join(result.bonuses), result.reason or "",
        " ".join(ad.full_text.split())[:500],
        SCORING_RULE_VERSION, fetched_date.isoformat(), ad.employment_type,
        " ".join(ad.full_text.split())[:32000],
    ]


def _industry_row(item: ScoredJob, fetched_date: date, home_coordinates: tuple[float, float]) -> list[object]:
    return [*_kept_row(item, fetched_date, home_coordinates), item.result.industry_trigger, item.result.university_partner]


def _style_sheet(sheet, widths: list[int]) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[1].height = 28
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=cell.column >= 3)
    headers = {cell.value: cell.column for cell in sheet[1]}
    if "distance_from_home_km" in headers:
        column = get_column_letter(headers["distance_from_home_km"])
        for cell in sheet[column][1:]:
            cell.number_format = "0.0"
    for internal_header in ("rule_version", "fetched_date", "raw_employment_type", "full_text"):
        if internal_header in headers:
            sheet.column_dimensions[get_column_letter(headers[internal_header])].hidden = True


def _parse_posted(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _item_sort_key(item: ScoredJob) -> tuple[date, int]:
    return _parse_posted(item.ad.posted_date) or date.min, item.result.score


def _industry_sort_key(item: ScoredJob) -> tuple[int, int, date]:
    deadline = _parse_posted(item.ad.deadline) or date.max
    return (0 if item.result.band == "TOP" else 1, -item.result.score, deadline)


def _write_workbook(
    items: list[ScoredJob], xlsx_path: Path, fetched_date: date, home_coordinates: tuple[float, float]
) -> None:
    by_band = {
        band: sorted(
            (item for item in items if item.result.band == band),
            key=_item_sort_key,
            reverse=True,
        )
        for band in ("PRIORITY", "REVIEW", "DE REQUIRED", "ACADEMISCHE", "TOP", "CHECK", "DROP")
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for band, sheet_name in (
        ("PRIORITY", "PRIORITY"),
        ("DE REQUIRED", "DE Required"),
        ("REVIEW", "REVIEW"),
        ("ACADEMISCHE", "Academische"),
    ):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(KEPT_COLUMNS)
        for item in by_band[band]:
            sheet.append(_kept_row(item, fetched_date, home_coordinates))
            row = sheet.max_row
            url_column = KEPT_COLUMNS.index("url") + 1
            sheet.cell(row, url_column).hyperlink = item.ad.url
            sheet.cell(row, url_column).style = "Hyperlink"
            external_url = str(item.ad.raw_meta.get("external_final_url") or item.ad.raw_meta.get("external_url") or "")
            if external_url:
                external_column = KEPT_COLUMNS.index("external_url") + 1
                sheet.cell(row, external_column).hyperlink = external_url
                sheet.cell(row, external_column).style = "Hyperlink"
            if band == "PRIORITY":
                sheet.cell(row, 2).fill = PRIORITY_FILL
            if band == "DE REQUIRED" or any("GERMAN C1" in value for value in item.result.language_flags):
                sheet.cell(row, KEPT_COLUMNS.index("language_flags") + 1).fill = AMBER_FILL
        _style_sheet(sheet, WIDTHS)

    industry = workbook.create_sheet("INDUSTRY-PhD", 2)
    industry.append(INDUSTRY_COLUMNS)
    industry_items = sorted((*by_band["TOP"], *by_band["CHECK"]), key=_industry_sort_key)
    for item in industry_items:
        industry.append(_industry_row(item, fetched_date, home_coordinates))
        row = industry.max_row
        url_column = INDUSTRY_COLUMNS.index("url") + 1
        industry.cell(row, url_column).hyperlink = item.ad.url
        industry.cell(row, url_column).style = "Hyperlink"
        external_url = str(item.ad.raw_meta.get("external_final_url") or item.ad.raw_meta.get("external_url") or "")
        if external_url:
            external_column = INDUSTRY_COLUMNS.index("external_url") + 1
            industry.cell(row, external_column).hyperlink = external_url
            industry.cell(row, external_column).style = "Hyperlink"
        if item.result.band == "TOP":
            industry.cell(row, INDUSTRY_COLUMNS.index("band") + 1).fill = PRIORITY_FILL
        if any("GERMAN C1" in value for value in item.result.language_flags):
            industry.cell(row, INDUSTRY_COLUMNS.index("language_flags") + 1).fill = AMBER_FILL
    _style_sheet(industry, [*WIDTHS, 24, 36])

    dropped = workbook.create_sheet("DROPPED")
    dropped.append(DROPPED_COLUMNS)
    for item in by_band["DROP"]:
        dropped.append([
            item.ad.title, item.ad.employer, item.ad.url, "DROP",
            item.ad.posted_date or fetched_date.isoformat(),
        ])
        url_column = DROPPED_COLUMNS.index("url") + 1
        dropped.cell(dropped.max_row, url_column).hyperlink = item.ad.url
        dropped.cell(dropped.max_row, url_column).style = "Hyperlink"
    _style_sheet(dropped, DROPPED_WIDTHS)

    workbook.save(xlsx_path)


def _record_key(record: Mapping[str, object]) -> str:
    url = str(record.get("url") or "").strip()
    if url:
        return f"url:{canonicalize_url(url)}"
    identity = "\n".join(
        " ".join(str(record.get(column) or "").casefold().split())
        for column in ("title", "employer", "location")
    )
    return f"identity:{identity}"


def _read_records(
    path: Path, master_group: str = "", home_coordinates: tuple[float, float] | None = None
) -> list[dict[str, object]]:
    workbook = load_workbook(path, read_only=True, data_only=False)
    records: list[dict[str, object]] = []
    try:
        for sheet in workbook.worksheets:
            if sheet.title not in SHEET_NAMES:
                continue
            headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
            for values in sheet.iter_rows(min_row=2, values_only=True):
                if not any(value not in (None, "") for value in values):
                    continue
                record = {
                    str(header): value
                    for header, value in zip(headers, values)
                    if header not in (None, "")
                }
                if "distance_from_home_km" not in record and "distance_from_previous_home_km" in record:
                    record["distance_from_home_km"] = record.pop("distance_from_previous_home_km")
                if home_coordinates is not None:
                    location_info = enrich_location(str(record.get("location") or ""), home_coordinates)
                    record["federal_state"] = location_info.federal_state
                    record["distance_from_home_km"] = location_info.distance_from_home_km
                record["_sheet"] = sheet.title
                record["_master_group"] = master_group
                records.append(record)
    finally:
        workbook.close()
    return records


def _deduplicate_records(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    unique: dict[str, dict[str, object]] = {}
    for record in records:
        key = _record_key(record)
        existing = unique.get(key)
        if existing is None:
            unique[key] = dict(record)
            continue
        incoming_wins = _record_freshness_key(record) >= _record_freshness_key(existing)
        winner, loser = (record, existing) if incoming_wins else (existing, record)
        combined = dict(loser)
        for column, value in winner.items():
            if value not in (None, ""):
                combined[column] = value
        combined["_sheet"] = winner["_sheet"]
        combined["_master_group"] = winner.get("_master_group", "")
        unique[key] = combined
    return list(unique.values())


def _record_sort_key(record: Mapping[str, object]) -> tuple[date, float]:
    posted = _parse_posted(record.get("posted")) or date.min
    try:
        score = float(record.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return posted, score


def _record_freshness_key(record: Mapping[str, object]) -> tuple[date, date, float]:
    fetched = _parse_posted(record.get("fetched_date")) or date.min
    posted, score = _record_sort_key(record)
    return fetched, posted, score


def _sheet_for_band(band: str) -> str:
    return {
        "PRIORITY": "PRIORITY",
        "REVIEW": "REVIEW",
        "DE REQUIRED": "DE Required",
        "ACADEMISCHE": "Academische",
        "TOP": "INDUSTRY-PhD",
        "CHECK": "INDUSTRY-PhD",
        "DROP": "DROPPED",
    }[band]


def _apply_result_to_record(record: dict[str, object], result: ScoreResult) -> None:
    record.update({
        "score": result.score,
        "band": result.band,
        "employment_format": result.employment_format or record.get("employment_format", ""),
        "primary_cv_family": result.primary_cv_family,
        "secondary_cv_family": result.secondary_cv_family,
        "family_hits": _hits(result, lambda group: group.startswith("B") and group.endswith((".strong", ".weak"))),
        "analysis_task_hits": _join(result.matched_terms.get("analysis_task", ())),
        "method_hits": _join(result.matched_terms.get("methods", ())),
        "tool_hits": _join((*result.matched_terms.get("tools_strong", ()), *result.matched_terms.get("tools_basic_or_adjacent", ()))),
        "language_flags": _join(result.language_flags),
        "eligibility_flags": _join(result.eligibility_flags),
        "other_flags": _join(result.other_flags),
        "penalties": _join(result.penalties),
        "bonuses": _join(result.bonuses),
        "kill_reason": result.reason or "",
        "drop_stage": result.drop_stage,
        "drop_reason": result.reason or ("score_below_review" if result.band == "DROP" else ""),
        "matched_term": result.matched_term,
        "industry_trigger": result.industry_trigger,
        "university_partner": result.university_partner,
        "_sheet": _sheet_for_band(result.band),
    })


def _reclassify_record(record: dict[str, object], keywords: Mapping[str, Any]) -> dict[str, object]:
    """Reapply current rules when enough historical evidence exists.

    Legacy rows without a stored description can still receive deterministic
    title gates and Priority demotion, but are never promoted from body evidence.
    """
    record = dict(record)
    if str(record.get("rule_version") or "") == SCORING_RULE_VERSION:
        return record
    title = str(record.get("title") or "")
    full_text = str(record.get("full_text") or "")
    raw_employment = str(record.get("raw_employment_type") or record.get("employment_format") or "")
    if full_text:
        result = score_text(
            title,
            full_text,
            keywords,
            employment_type=raw_employment,
            employer=str(record.get("employer") or ""),
        )
        _apply_result_to_record(record, result)
        record["rule_version"] = SCORING_RULE_VERSION
        return record

    if doctoral_title_hits(title, keywords):
        result = score_text(
            title,
            "",
            keywords,
            employment_type=raw_employment,
            employer=str(record.get("employer") or ""),
        )
        _apply_result_to_record(record, result)
        record["rule_version"] = f"{SCORING_RULE_VERSION}-title-only"
        return record

    deterministic_gate = title_gate(title, keywords)
    if deterministic_gate:
        band, reason, matched_term = deterministic_gate
        record.update({
            "band": band,
            "score": 0,
            "kill_reason": reason,
            "drop_reason": reason if band == "DROP" else "",
            "drop_stage": (
                "seniority_gate" if reason == "excluded_senior_or_leadership_role"
                else "commercial_role_gate" if reason == "excluded_einkauf_seller_or_sales_role"
                else "pure_it_title_gate" if band == "DROP" else ""
            ),
            "matched_term": matched_term,
            "_sheet": _sheet_for_band(band),
        })
    elif str(record.get("_sheet") or "") == "PRIORITY" and not priority_title_hits(title, keywords):
        record.update({
            "band": "REVIEW",
            "kill_reason": "priority_requires_target_title_evidence",
            "_sheet": "REVIEW",
        })
    record["rule_version"] = f"{SCORING_RULE_VERSION}-title-only-legacy"
    return record


def _write_records_workbook(records: Iterable[dict[str, object]], xlsx_path: Path) -> None:
    grouped = {sheet_name: [] for sheet_name in SHEET_NAMES}
    for record in records:
        sheet_name = str(record.get("_sheet") or "")
        if sheet_name in grouped:
            grouped[sheet_name].append(record)

    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name in SHEET_NAMES:
        columns = DROPPED_COLUMNS if sheet_name == "DROPPED" else INDUSTRY_COLUMNS if sheet_name == "INDUSTRY-PhD" else KEPT_COLUMNS
        widths = DROPPED_WIDTHS if sheet_name == "DROPPED" else [*WIDTHS, 24, 36] if sheet_name == "INDUSTRY-PhD" else WIDTHS
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(columns)
        ordered = (
            sorted(
                grouped[sheet_name],
                key=lambda record: (
                    0 if str(record.get("band")) == "TOP" else 1,
                    -float(record.get("score") or 0),
                    _parse_posted(record.get("deadline")) or date.max,
                ),
            )
            if sheet_name == "INDUSTRY-PhD"
            else sorted(grouped[sheet_name], key=_record_sort_key, reverse=True)
        )
        for record in ordered:
            current = dict(record)
            if sheet_name == "DROPPED":
                current["band"] = "DROP"
                current["date"] = current.get("posted") or current.get("date") or current.get("fetched_date")
            else:
                posted = _parse_posted(current.get("posted"))
                if posted and posted < date.today() - timedelta(days=60):
                    current["full_text"] = ""
            sheet.append([current.get(column) for column in columns])
            row = sheet.max_row
            url_column = columns.index("url") + 1
            url = str(record.get("url") or "")
            if url:
                sheet.cell(row, url_column).hyperlink = url
                sheet.cell(row, url_column).style = "Hyperlink"
            if sheet_name != "DROPPED":
                external_url = str(record.get("external_url") or "")
                if external_url:
                    external_column = columns.index("external_url") + 1
                    sheet.cell(row, external_column).hyperlink = external_url
                    sheet.cell(row, external_column).style = "Hyperlink"
            if sheet_name == "PRIORITY":
                sheet.cell(row, KEPT_COLUMNS.index("band") + 1).fill = PRIORITY_FILL
            if sheet_name == "DE Required" or "GERMAN C1" in str(record.get("language_flags") or ""):
                if sheet_name != "DROPPED":
                    sheet.cell(row, KEPT_COLUMNS.index("language_flags") + 1).fill = AMBER_FILL
        _style_sheet(sheet, widths)

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = xlsx_path.with_name(f".{xlsx_path.stem}.tmp.xlsx")
    workbook.save(temporary_path)
    workbook.close()
    from .safeio import atomic_write_bytes
    atomic_write_bytes(xlsx_path, temporary_path.read_bytes())
    temporary_path.unlink(missing_ok=True)


def _target_master_group(record: Mapping[str, object]) -> str:
    employment_format = str(record.get("employment_format") or "")
    if employment_format:
        return "vollzeit" if employment_format == "FULL-TIME / VOLLZEIT" else "other"
    source_group = str(record.get("_master_group") or "")
    return source_group if source_group in {"vollzeit", "other"} else "other"


def _rebuild_master_pair(
    output_dir: Path,
    new_sources: Iterable[tuple[Path, str]] = (),
    keywords: Mapping[str, Any] | None = None,
    home_coordinates: tuple[float, float] = Config().home_coordinates,
) -> tuple[Path, Path]:
    destinations = {
        "vollzeit": output_dir / "jobs_vollzeit.xlsx",
        "other": output_dir / "jobs_other.xlsx",
    }
    records: list[dict[str, object]] = []
    for group, destination in destinations.items():
        if destination.exists():
            records.extend(_read_records(destination, group, home_coordinates))
    for path, group in new_sources:
        if path.exists():
            records.extend(_read_records(path, group, home_coordinates))
    active_keywords = keywords or load_keywords(DEFAULT_KEYWORDS_PATH)
    unique = [_reclassify_record(record, active_keywords) for record in _deduplicate_records(records)]
    for group, destination in destinations.items():
        _write_records_workbook(
            (record for record in unique if _target_master_group(record) == group),
            destination,
        )
    return destinations["vollzeit"], destinations["other"]


def _deduplicate_scored_jobs(items: Iterable[ScoredJob]) -> list[ScoredJob]:
    unique: dict[str, ScoredJob] = {}
    for item in items:
        record = {
            "url": item.ad.url,
            "title": item.ad.title,
            "employer": item.ad.employer,
            "location": item.ad.location,
        }
        key = _record_key(record)
        existing = unique.get(key)
        if existing is None or _item_sort_key(item) >= _item_sort_key(existing):
            unique[key] = item
    return list(unique.values())


def write_checkpoint_outputs(
    jobs: Iterable[ScoredJob],
    output_dir: str | Path,
    run_id: int,
    run_date: date | None = None,
    home_coordinates: tuple[float, float] = Config().home_coordinates,
) -> tuple[Path, Path]:
    """Atomically replace recoverable partial workbooks without rebuilding masters."""
    run_date = run_date or date.today()
    checkpoint_dir = Path(output_dir) / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    items = _deduplicate_scored_jobs(jobs)
    groups = (
        ("vollzeit", [item for item in items if item.result.employment_format == "FULL-TIME / VOLLZEIT"]),
        ("other", [item for item in items if item.result.employment_format != "FULL-TIME / VOLLZEIT"]),
    )
    paths: list[Path] = []
    for group, group_items in groups:
        path = checkpoint_dir / f"run_{run_id}_jobs_{group}.xlsx"
        temporary = path.with_name(f".{path.stem}.tmp.xlsx")
        _write_workbook(group_items, temporary, run_date, home_coordinates)
        temporary.replace(path)
        paths.append(path)
    return paths[0], paths[1]


def write_outputs(
    jobs: Iterable[ScoredJob],
    output_dir: str | Path,
    run_date: date | None = None,
    keywords: Mapping[str, Any] | None = None,
    home_coordinates: tuple[float, float] = Config().home_coordinates,
) -> tuple[Path, Path]:
    run_date = run_date or date.today()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    items = _deduplicate_scored_jobs(jobs)
    full_time = [item for item in items if item.result.employment_format == "FULL-TIME / VOLLZEIT"]
    other = [item for item in items if item.result.employment_format != "FULL-TIME / VOLLZEIT"]
    base_stem = run_date.isoformat()
    stem = base_stem
    sequence = 2
    while any(
        (output_dir / name).exists()
        for name in (
            f"run/jobs_vollzeit_{stem}.xlsx",
            f"run/jobs_other_{stem}.xlsx",
        )
    ):
        stem = f"{base_stem}_{sequence}"
        sequence += 1
    full_time_path = run_dir / f"jobs_vollzeit_{stem}.xlsx"
    other_path = run_dir / f"jobs_other_{stem}.xlsx"
    _write_workbook(full_time, full_time_path, run_date, home_coordinates)
    _write_workbook(other, other_path, run_date, home_coordinates)
    _rebuild_master_pair(
        output_dir, ((full_time_path, "vollzeit"), (other_path, "other")), keywords, home_coordinates
    )
    return full_time_path, other_path
