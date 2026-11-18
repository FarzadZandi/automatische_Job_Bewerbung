"""Starts the app without a console window (``pythonw -m jobfilter.window``).

Output goes to ``jobfilter_app.log`` next to config.yaml; a start-up error is shown in a message box,
because there is no console to print it to.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def main(config_path: str = "config.yaml") -> int:
    log = open(Path(config_path).resolve().parent / "jobfilter_app.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stderr = log
    try:
        from .app import serve
        serve(config_path)
    except Exception:  # noqa: BLE001 - last resort: the user must see why nothing opened
        details = traceback.format_exc()
        print(details)
        try:
            from .osutil import alert
            alert("jobfilter could not start", details[-1200:])
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
