"""Small desktop notifications for every point where the pipeline waits for the user."""
from __future__ import annotations

import os
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Title and text travel in environment variables, so no quoting can break the script.
BALLOON = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
    "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
    "$n.Visible = $true; $n.ShowBalloonTip(15000, $env:JF_TITLE, $env:JF_TEXT, 'Info'); "
    "Start-Sleep -Seconds 16; $n.Dispose()"
)


def notify(title: str, text: str, *, runner=subprocess.Popen) -> bool:
    """Show a small notification window. Returns False where that is not possible (not Windows)."""
    if os.name != "nt":
        from .osutil import is_mac, notify as mac_notify
        if not is_mac():
            return False
        try:
            mac_notify(title[:60], (text or " ")[:240])
        except OSError:
            return False
        return True
    environment = dict(os.environ, JF_TITLE=title[:60], JF_TEXT=(text or " ")[:240])
    try:
        runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", BALLOON],
            env=environment, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True
