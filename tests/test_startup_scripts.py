from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_start_checks_real_python_and_runs_doctor_once_before_app():
    text = (ROOT / "Start jobfilter (Windows).bat").read_text(encoding="utf-8")
    assert 'sys.version_info >= (3,11)' in text
    assert "where py" in text and "where python" in text
    assert "\\WindowsApps\\" in text
    assert "https://www.python.org/downloads/windows/" in text
    assert "Add python.exe to PATH" in text
    assert 'if not exist "%JFPY%" set "JFFIRST=1"' in text
    assert 'if defined JFFIRST' in text
    assert '"%JFPY%" -m jobfilter doctor' in text
    assert text.index('"%JFPY%" -m jobfilter doctor') < text.index('start "" "%JFVENV%\\Scripts\\pythonw.exe"')


def test_double_click_setup_check_uses_external_venv_and_keeps_window_open():
    text = (ROOT / "Check setup (Windows).bat").read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%\\jobfilter\\venv\\Scripts\\python.exe" in text
    assert '"%JFPY%" -m jobfilter doctor' in text
    assert "pause" in text
    assert 'First double-click "Start jobfilter (Windows).bat"' in text


def test_replication_notes_cover_the_new_computer_dependencies():
    text = (ROOT / "guide/10_install_app.md").read_text(encoding="utf-8")
    for required in ("Python 3.11", "Add python.exe to PATH", "Microsoft Edge", "Load unpacked", "Claude Code", "Microsoft Word"):
        assert required in text
