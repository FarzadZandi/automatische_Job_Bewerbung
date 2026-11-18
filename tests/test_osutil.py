from __future__ import annotations

import subprocess
from pathlib import Path

from jobfilter import notify, osutil


def _calls(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: calls.append(args))
    return calls


def test_mac_open_alert_and_notify_use_macos_tools(monkeypatch):
    monkeypatch.setattr(osutil.sys, "platform", "darwin")
    calls = _calls(monkeypatch)
    osutil.open_path("/tmp/x.xlsx")
    osutil.alert('Fehler "A"', "Text \\ mit Backslash")
    osutil.notify("Titel", 'Hallo "Welt"')
    assert calls[0] == ["open", "/tmp/x.xlsx"]
    assert calls[1][:2] == ["osascript", "-e"] and '\\"A\\"' in calls[1][2] and "\\\\" in calls[1][2]
    assert calls[2][:2] == ["osascript", "-e"] and calls[2][2].startswith("display notification")


def test_notify_module_routes_mac_and_skips_linux(monkeypatch):
    calls = _calls(monkeypatch)
    monkeypatch.setattr(notify.os, "name", "posix")
    monkeypatch.setattr(osutil.sys, "platform", "linux")
    assert notify.notify("a", "b") is False
    monkeypatch.setattr(osutil.sys, "platform", "darwin")
    assert notify.notify("a", "b") is True and calls and calls[-1][0] == "osascript"


def test_app_data_dir_per_platform(monkeypatch):
    monkeypatch.setattr(osutil.sys, "platform", "darwin")
    assert osutil.app_data_dir() == Path.home() / "Library/Application Support/jobfilter"
    monkeypatch.setattr(osutil.sys, "platform", "linux")
    assert osutil.app_data_dir() == Path.home() / ".local/share/jobfilter"


def test_browser_and_office_lookup_on_mac(monkeypatch):
    monkeypatch.setattr(osutil.sys, "platform", "darwin")
    monkeypatch.setattr(osutil.shutil, "which", lambda value: None)
    existing = {"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"}
    monkeypatch.setattr(osutil.Path, "exists", lambda self: str(self) in existing)
    assert osutil.browser_path("auto").endswith("Google Chrome")
    assert osutil.browser_path("edge") is None
    assert osutil.soffice_path().endswith("MacOS/soffice")


def test_claude_lookup_includes_homebrew_on_mac(monkeypatch):
    monkeypatch.setattr(osutil.sys, "platform", "darwin")
    monkeypatch.setattr(osutil.shutil, "which", lambda value: None)
    monkeypatch.setattr(osutil.Path, "exists", lambda self: str(self) == "/opt/homebrew/bin/claude")
    assert osutil.claude_candidates() == ("/opt/homebrew/bin/claude",)
