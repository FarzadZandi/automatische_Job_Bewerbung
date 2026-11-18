# Troubleshooting

Always start from `out/setup_status.json` (the `problems` list) and give the person one action at a time.

| Symptom | Fix |
|---|---|
| "Python 3.11 or newer was not found" (Windows) | Install Python 3.12 from python.org with "Add python.exe to PATH"; the Microsoft Store alias does not count. Start the start file again. |
| Mac: Python too old / not found | Install Python 3.12 from python.org (macOS installer); the system python3 is 3.9. |
| Environment could not be prepared | Run the start file again; if it repeats, delete the folder `%LOCALAPPDATA%\jobfilter\venv` (Windows) or `~/Library/Application Support/jobfilter/venv` (Mac) with the person's permission and start again. |
| "claude is not recognized" | Close and reopen PowerShell or Terminal. The program is in `%USERPROFILE%\.local\bin` (Windows) or `~/.local/bin` (Mac). |
| Claude not signed in | `claude auth login`, sign in with the Pro or Max account, then `claude auth status`. |
| Extension does not load | Developer mode on, "Load unpacked", choose the `extension` folder (not a file inside it). After app updates click "Reload". |
| No PDF engine | Install Word (Windows) or LibreOffice (both). |
| Tracker cannot be written | Close the Excel file; OneDrive may lock it for a moment, the app retries. |
| Mac: "cannot be opened" | Right-click → Open; or the `chmod +x` and `xattr` lines in guide/10. |
| A web page is blocked during the market search | Use another source; never work around a block. |
| Usage limit reached | Update STATE.md, tell the person to come back later and type `continue`. |
