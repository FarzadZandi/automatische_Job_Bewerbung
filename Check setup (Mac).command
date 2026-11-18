#!/bin/bash
# Double-click to check the jobfilter setup on a Mac.
set -u
cd "$(dirname "$0")" || exit 1
python="$HOME/Library/Application Support/jobfilter/venv/bin/python"
if [ ! -x "$python" ]; then
  echo "The jobfilter environment is not installed yet. First double-click \"Start jobfilter (Mac).command\"."
  read -n 1 -s -r -p "Press any key to close this window."; exit 1
fi
mkdir -p out
"$python" -m jobfilter doctor --json out/setup_status.json
code=$?
echo
if [ "$code" = "0" ]; then echo "Setup check complete: every required check is OK."; else echo "Setup check complete: review and fix each MISSING line above."; fi
read -n 1 -s -r -p "Press any key to close this window."; echo
exit $code
