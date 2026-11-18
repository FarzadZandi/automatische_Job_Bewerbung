from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    contact_email: str = "replace-with-your-email@example.com"
    keywords_path: Path = Path("keywords.json")
    cache_dir: Path = Path("cache")
    cache_enabled: bool = False
    database_path: Path = Path("seen.sqlite")
    output_dir: Path = Path("out")
    companies_enabled: bool = False
    companies_path: Path = Path("companies.yaml")
    indeed_import_enabled: bool = False
    indeed_import_path: Path = Path("imports/indeed_jobs.csv")
    indeed_default_employment_type: str = "Vollzeit"
    job_documents_dir: Path = Path("Job App Docs")
    application_workbook: Path = Path("out/jobs_vollzeit.xlsx")
    application_output_dir: Path = Path("out/applications")
    application_limit: int = 20
    application_max_age_days: int = 21
    applicant_status: str = "needs_confirmation"
    graduation_date: str = ""
    application_attachment_allowlist: tuple[str, ...] = ()
    application_residence_attachment_names: tuple[str, ...] = ()
    enrolment_attachment_names: tuple[str, ...] = ("immatbescheinigung.pdf",)
    checkpoint_every: int = 100
    github_projects_enabled: bool = False
    github_user: str = ""
    github_catalog_path: Path = Path("Job App Docs/github_projekte.yaml")
    github_cache_path: Path = Path("cache/github_repos.json")
    github_sync_max_age_days: int = 7
    applicant_profile_path: Path = Path("Job App Docs/bewerberprofil.yaml")
    answer_bank_path: Path = Path("Job App Docs/antworten.yaml")
    applicant_file_stem: str = ""
    autofill_extension_dir: Path = Path("extension")
    application_archive_dir: Path = Path("Bewerbungen")
    letter_writer: str = "claude"
    letter_prefetch: int = 0
    claude_model: str = "haiku"
    answer_language: str = "de"
    openai_model: str = "gpt-5-mini"
    home_city: str = ""
    home_coordinates: tuple[float, float] | None = None
    browser: str = "auto"
    jd_archive_dir: Path = Path("Bewerbungen/Stellenarchiv")
    company_ats_location_terms: tuple[str, ...] = (
        "germany", "deutschland", "baden-württemberg", "bavaria", "bayern", "berlin", "bonn",
        "cologne", "düsseldorf", "frankfurt", "hamburg", "hannover", "heidelberg",
        "köln", "munich", "münchen", "nuremberg", "stuttgart", "remote germany", "germany remote",
        "remote - germany", "dach", "emea", "europe",
    )
    staffing_agency_names: tuple[str, ...] = ("FERCHAU", "Brunel")
    min_delay: float = 2.5
    max_delay: float = 6.0
    long_pause_every: int = 25
    long_pause_min: float = 15.0
    long_pause_max: float = 30.0
    position_delay_min: float = 3.0
    position_delay_max: float = 5.0
    position_batch_size_min: int = 50
    position_batch_size_max: int = 100
    position_batch_pause_min: float = 60.0
    position_batch_pause_max: float = 120.0
    max_retries: int = 3
    request_timeout: float = 30.0
    max_ads: int = 0
    max_pages: int = 0
    arbeitsagentur_use_api: bool = True
    thresholds: dict[str, int] = field(default_factory=lambda: {"priority": 12, "review": 6})
    user_agents: tuple[str, ...] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )

    @classmethod
    def load(cls, path: str | Path = "config.yaml", *, validate: bool = True, **overrides: Any) -> "Config":
        config_path = Path(path).expanduser().resolve()
        raw: dict[str, Any] = {}
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        allowed = {item.name for item in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        raw.update({key: value for key, value in overrides.items() if value is not None})
        base = config_path.parent
        for key in (
            "keywords_path",
            "cache_dir",
            "database_path",
            "output_dir",
            "companies_path",
            "indeed_import_path",
            "job_documents_dir",
            "application_workbook",
            "application_output_dir",
            "github_catalog_path",
            "github_cache_path",
            "applicant_profile_path",
            "answer_bank_path",
            "autofill_extension_dir",
            "application_archive_dir",
            "jd_archive_dir",
        ):
            if key in raw:
                value = Path(raw[key]).expanduser()
                raw[key] = value if value.is_absolute() else (base / value).resolve()
        for key in (
            "user_agents",
            "application_attachment_allowlist",
            "application_residence_attachment_names",
            "enrolment_attachment_names",
            "staffing_agency_names",
            "home_coordinates",
            "company_ats_location_terms",
        ):
            if key in raw and raw[key] is not None:
                raw[key] = tuple(raw[key])
        if "jd_archive_dir" not in raw:
            archive = raw.get("application_archive_dir") or (base / "Bewerbungen").resolve()
            raw["jd_archive_dir"] = Path(archive) / "Stellenarchiv"
        result = cls(**raw)
        if validate:
            result.validate()
        from . import applicant
        applicant.configure(result.applicant_profile_path, result.answer_bank_path)
        return result

    def validate(self) -> None:
        if not self.contact_email or "@" not in self.contact_email:
            raise ValueError("contact_email must be a real contact email")
        if self.letter_writer not in {"claude", "openai", "standard"}:
            raise ValueError("letter_writer must be claude, openai, or standard")
        if "haiku" not in self.claude_model.casefold():
            raise ValueError("claude_model must be Haiku, the lowest Claude model used by jobfilter")
        if self.answer_language not in {"de", "auto"}:
            raise ValueError("answer_language must be de or auto")
        if self.letter_prefetch not in {0, 1}:
            raise ValueError("letter_prefetch must be 0 or 1")
        if self.min_delay < 2.5 or self.max_delay < self.min_delay:
            raise ValueError("delays must satisfy 2.5 <= min_delay <= max_delay")
        if self.long_pause_every < 1:
            raise ValueError("long_pause_every must be positive")
        if self.position_delay_min < 0 or self.position_delay_max < self.position_delay_min:
            raise ValueError("position delays must satisfy 0 <= position_delay_min <= position_delay_max")
        if self.position_batch_size_min < 1 or self.position_batch_size_max < self.position_batch_size_min:
            raise ValueError("position batch sizes must satisfy 1 <= position_batch_size_min <= position_batch_size_max")
        if self.position_batch_pause_min < 0 or self.position_batch_pause_max < self.position_batch_pause_min:
            raise ValueError(
                "position batch pauses must satisfy 0 <= position_batch_pause_min <= position_batch_pause_max"
            )
        if not 0 <= self.max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if self.max_ads < 0:
            raise ValueError("max_ads must be zero (unlimited) or positive")
        if self.max_pages < 0:
            raise ValueError("max_pages must be zero (unlimited) or positive")
        if self.application_limit < 1:
            raise ValueError("application_limit must be positive")
        if self.application_max_age_days < 1:
            raise ValueError("application_max_age_days must be positive")
        if self.applicant_status not in {"enrolled", "graduating", "graduated", "professional", "needs_confirmation"}:
            raise ValueError(
                "applicant_status must be enrolled, graduating, graduated, professional, or needs_confirmation"
            )
        if self.applicant_status == "graduated" and not self.graduation_date.strip():
            raise ValueError("graduation_date is required when applicant_status is graduated")
        if self.checkpoint_every < 1:
            raise ValueError("checkpoint_every must be positive")
        if self.github_projects_enabled and not self.github_user.strip():
            raise ValueError("github_user is required when github_projects_enabled is true")
        if self.github_sync_max_age_days < 0:
            raise ValueError("github_sync_max_age_days must be 0 (never sync automatically) or positive")
        if not self.user_agents:
            raise ValueError("at least one user agent is required")
        if bool(self.home_city.strip()) != (self.home_coordinates is not None):
            raise ValueError("Set home_city and home_coordinates in config.yaml (guide/11)")
        if self.home_coordinates is not None and (len(self.home_coordinates) != 2 or not all(isinstance(value, (int, float)) for value in self.home_coordinates)):
            raise ValueError("home_coordinates must contain latitude and longitude")
        if self.browser not in {"auto", "edge", "chrome"}:
            raise ValueError("browser must be auto, edge, or chrome")
        if not self.company_ats_location_terms:
            raise ValueError("company_ats_location_terms must not be empty")
