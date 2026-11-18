from __future__ import annotations

from pathlib import Path

from jobfilter.config import Config
from jobfilter.doctor import replication_checks


def test_old_config_loads_with_replication_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('contact_email: "person@example.org"\n', encoding="utf-8")
    config = Config.load(path, validate=False)
    assert config.home_city == ""
    assert config.home_coordinates is None
    assert config.answer_bank_path == Path("Job App Docs/antworten.yaml")


def test_doctor_reports_replication_and_size_problems(tmp_path):
    docs = tmp_path / "Job App Docs"
    attachments = docs / "Attachements"
    attachments.mkdir(parents=True)
    large = attachments / "Bundle.pdf"
    with large.open("wb") as stream:
        stream.truncate(6 * 1024 * 1024)
    extension_data = tmp_path / "extension" / "data"
    extension_data.mkdir(parents=True)
    with (extension_data / "large.bin").open("wb") as stream:
        stream.truncate(16 * 1024 * 1024)
    path = tmp_path / "config.yaml"
    path.write_text(
        '\n'.join([
            'contact_email: "person@example.org"', 'job_documents_dir: "Job App Docs"',
            'autofill_extension_dir: "extension"', 'claude_model: "sonnet"',
            'application_attachment_allowlist: ["Bundle.pdf"]',
        ]), encoding="utf-8",
    )
    config = Config.load(path, validate=False)
    checks = {name: (ok, detail) for name, ok, detail in replication_checks(config, path)}
    assert checks["config keys"][0] is False and "answer_bank_path" in checks["config keys"][1]
    assert checks["Claude model"] == (False, "sonnet")
    assert checks["attachment upload size"][0] is False
    assert checks["extension data size"][0] is False
    assert checks["configured Job App Docs files"][0] is False
