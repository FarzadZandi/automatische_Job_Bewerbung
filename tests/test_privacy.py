import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_personal_and_local_files_are_gitignored_and_untracked():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    in_repo = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True).returncode == 0
    for pattern in (
        "Job App Docs/", "config.yaml", "urls.txt", "Command.txt", "imports/*.csv",
        "!imports/indeed_jobs.example.csv", "imports/links.txt", "imports/links_done.txt",
        "imports/manual_pending.json", "*.zip", "archive/", "workspace/", "Bewerbungen/", "keywords.json",
        "companies.yaml",
    ):
        assert pattern in ignore
    if not in_repo:
        return
    result = subprocess.run(
        ["git", "ls-files", "Job App Docs", "config.yaml", "urls.txt", "Command.txt", "*.zip", "archive"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == ""
