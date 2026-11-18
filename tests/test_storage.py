from jobfilter.storage import format_size, project_size
from jobfilter.__main__ import main


def test_project_size_excludes_personal_folders_and_lists_five(tmp_path):
    for index in range(7):
        (tmp_path / f"item{index}").write_bytes(b"x" * (index + 1))
    personal = tmp_path / "Job App Docs"
    personal.mkdir()
    (personal / "large").write_bytes(b"x" * 1000)
    report = project_size(tmp_path)
    assert report["bytes"] == sum(range(1, 8))
    assert len(report["largest"]) == 5 and report["largest"][0]["name"] == "item6"
    assert "excluding Job App Docs and Bewerbungen" in format_size(report)


def test_size_command_prints_summary(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    (tmp_path / "payload.bin").write_bytes(b"x" * 20)
    assert main(["--config", str(tmp_path / "config.yaml"), "size"]) == 0
    output = capsys.readouterr().out
    assert "Project:" in output and "payload.bin" in output
