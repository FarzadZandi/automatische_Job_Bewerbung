from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_mac() -> bool:
    return sys.platform == "darwin"


def open_path(path: str | Path) -> None:
    if is_windows():
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if is_mac() else "xdg-open", str(path)])


def browser_path(preferred: str = "auto") -> str | None:
    names = [preferred] if preferred != "auto" else ["edge", "chrome"]
    candidates: dict[str, list[str]] = {
        "edge": ["msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "chrome": ["chrome", "google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    }
    if is_windows():
        roots = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("LOCALAPPDATA", "")]
        candidates["edge"] += [str(Path(r) / "Microsoft/Edge/Application/msedge.exe") for r in roots if r]
        candidates["chrome"] += [str(Path(r) / "Google/Chrome/Application/chrome.exe") for r in roots if r]
    for name in names:
        for value in candidates.get(name, []):
            found = shutil.which(value) or (value if Path(value).exists() else None)
            if found:
                return found
    return None


def notify(title: str, text: str) -> None:
    if is_mac():
        subprocess.run(["osascript", "-e", f"display notification {_osa(text)} with title {_osa(title)}"], check=False)


def _osa(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def alert(title: str, text: str) -> None:
    if is_windows():
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0)
    elif is_mac():
        subprocess.run(["osascript", "-e", f"display alert {_osa(title)} message {_osa(text)}"], check=False)
    else:
        print(f"{title}: {text}", file=sys.stderr)


def app_data_dir() -> Path:
    if is_windows():
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "jobfilter"
    if is_mac():
        return Path.home() / "Library/Application Support/jobfilter"
    return Path.home() / ".local/share/jobfilter"


def soffice_path() -> str | None:
    for value in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice", r"C:\Program Files\LibreOffice\program\soffice.exe"):
        found = shutil.which(value) or (value if Path(value).exists() else None)
        if found:
            return found
    return None


def claude_candidates() -> tuple[str, ...]:
    suffix = ".exe" if is_windows() else ""
    values = [shutil.which("claude"), str(Path.home() / f".local/bin/claude{suffix}")]
    if is_mac():
        values += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    return tuple(v for v in values if v and (shutil.which(v) or Path(v).exists()))
