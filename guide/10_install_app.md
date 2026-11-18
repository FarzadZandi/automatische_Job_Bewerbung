# Stage 10: install the app

Detect the OS (get_device_info platform, or the shell) and show the person only the matching part, one step at a time. Ask before anything is installed. After each double-click, read `out/setup_status.json` (or run `python -m jobfilter doctor --json out/setup_status.json` if you have a shell on the person's computer) and give the next single action.

## Windows
The app needs Python 3.11 or newer (3.12 recommended).

1. **Python** (3.11 or newer, 3.12 recommended): if the status file says `python_found: false` or the start file reports no Python: the person downloads Python 3.12 from https://www.python.org/downloads/windows/ and, in the installer, ticks "Add python.exe to PATH". The Microsoft Store alias does not count.
2. **Browser:** Microsoft Edge is already installed; Chrome works too.
3. **PDF:** Microsoft Word, or LibreOffice (free, https://www.libreoffice.org) when Word is missing.
4. **Claude Code** (writes letters with the person's own plan): the person opens PowerShell (Start menu, type "PowerShell") and pastes `irm https://claude.ai/install.ps1 | iex`. Then closes and reopens PowerShell and runs `claude auth login`, signs in with the Pro or Max account in the browser, and checks with `claude auth status`. Git for Windows is optional.
5. **Start:** double-click `Start jobfilter (Windows).bat`. The first start prepares the environment (a few minutes), shows OK and MISSING lines and writes `out/setup_status.json`.

## Mac
1. **Python:** Python 3.12 from https://www.python.org/downloads/macos/ (the python3 that comes with macOS is too old).
2. **Browser:** Chrome or Microsoft Edge.
3. **PDF:** LibreOffice (required on Mac), https://www.libreoffice.org.
4. **Claude Code:** in Terminal (Spotlight, type "Terminal") paste `curl -fsSL https://claude.ai/install.sh | bash`, then `claude auth login` and `claude auth status`.
5. **Start:** right-click `Start jobfilter (Mac).command` → Open → Open (only the first time). If macOS says it cannot be opened or is not executable, give the person these two lines for Terminal, with the real folder path filled in:
   `chmod +x "<folder>/Start jobfilter (Mac).command" "<folder>/Check setup (Mac).command"`
   `xattr -dr com.apple.quarantine "<folder>"`

## Both
6. **Extension:** open `edge://extensions` (or `chrome://extensions`), switch on Developer mode, click "Load unpacked", choose the `extension` folder inside this package, and pin the jobfilter icon. Ask the person to confirm they see the icon.
7. Done when `out/setup_status.json` shows: python_found, venv_ok, a browser, a pdf_engine, `claude_signed_in: true`, `extension_folder_ok: true`. The other MISSING lines (profile, CVs, keywords) are fixed in stage 11. For problems see `guide/troubleshooting.md`.
