"""Small scheduled Prepare entry point; each heavy step remains a short-lived child process."""
from __future__ import annotations

import argparse
import subprocess
import sys


STEPS = (
    ("add-links",), ("run",), ("prepare-applications",), ("export-autofill",),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)
    for step in STEPS:
        done = subprocess.run([sys.executable, "-m", "jobfilter", "--config", args.config, *step], check=False)
        if done.returncode:
            return int(done.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
