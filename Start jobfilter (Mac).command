#!/bin/bash
# Double-click to start jobfilter on a Mac. First time: right-click this file, choose Open.
set -u
cd "$(dirname "$0")" || exit 1
status="out/setup_status.json"
mkdir -p out
pause() { echo; read -n 1 -s -r -p "Press any key to close this window."; echo; }
python=""
for candidate in python3.13 python3.12 python3.11 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
    python="$candidate"; break
  fi
done
if [ -z "$python" ]; then
  printf '{"python_found": false, "os": "macOS", "checked_at": "%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status"
  echo "Python 3.11 or newer was not found (the python3 that comes with macOS is too old)."
  echo "Install Python 3.12 from https://www.python.org/downloads/macos/ and double-click this file again."
  pause; exit 1
fi
venv="$HOME/Library/Application Support/jobfilter/venv"
first=0
mkdir -p "$(dirname "$venv")"
if [ ! -x "$venv/bin/python" ]; then
  first=1
  echo "Preparing the jobfilter environment. This may take a moment..."
  "$python" -m venv "$venv" || { echo "Could not create the environment."; pause; exit 1; }
fi
hash=$(shasum -a 256 pyproject.toml | awk '{print $1}')
if [ ! -f "$venv/.jobfilter-hash" ] || [ "$(cat "$venv/.jobfilter-hash")" != "$hash" ]; then
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -e . || { echo "Installing jobfilter failed. Nothing in the folder was deleted."; pause; exit 1; }
  printf '%s' "$hash" > "$venv/.jobfilter-hash"
  "$venv/bin/python" -m jobfilter doctor --json "$status" >/dev/null 2>&1
fi
if [ "$first" = "1" ]; then
  echo; echo "First-start setup check:"
  "$venv/bin/python" -m jobfilter doctor --json "$status"
  echo; echo "Review the OK and MISSING lines above. Run this check later with \"Check setup (Mac).command\"."
  pause
fi
nohup "$venv/bin/python" -m jobfilter.window >/dev/null 2>&1 &
echo "jobfilter is starting in its own window. You can close this Terminal window."
