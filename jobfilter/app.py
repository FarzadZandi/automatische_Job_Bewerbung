"""Local control panel: one page that walks through search, letters and applying.

A small web server on 127.0.0.1 only, shown in its own window. It runs the same jobfilter commands as
the command line and never submits an application. Letter writing is configurable: Claude through the
user's plan, the opt-in OpenAI Responses API, or deterministic standard text.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import secrets
import re
import struct
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import date, datetime
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import Config
from .notify import notify

TRACKER_NAME = "Bewerbungs_Tracker.xlsx"
INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex" if os.name == "nt" else "curl -fsSL https://claude.ai/install.sh | bash"
DEFAULT_LOGIN = '& "$env:USERPROFILE\\.local\\bin\\claude.exe" auth login' if os.name == "nt" else "~/.local/bin/claude auth login"


def _queue_rows(output_dir: str | Path) -> list[dict[str, object]]:
    """Lightweight manifest view for the idle app; package-building code stays out of RAM."""
    found = []
    for path in Path(output_dir).glob("*/application.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        found.append((path, manifest))
    sheet_order = {name: index for index, name in enumerate(("PRIORITY", "DE Required", "INDUSTRY-PhD", "REVIEW"))}
    def number(value):
        try: return float(value or 0)
        except (TypeError, ValueError): return 0.0
    found.sort(key=lambda item: (
        0 if str(item[1].get("board") or "").casefold() == "manual" else 1,
        sheet_order.get(str(item[1].get("source_sheet")), 99),
        -number(item[1].get("score")), item[0].parent.name.casefold(),
    ))
    rows = []
    for rank, (path, manifest) in enumerate(found, 1):
        folder = path.parent.resolve()
        external = str(manifest.get("external_url") or "").strip()
        url = str(manifest.get("url") or manifest.get("application_url") or "").strip()
        rows.append({
            "application_id": str(manifest.get("application_id") or ""), "queue_rank": rank,
            "source_sheet": str(manifest.get("source_sheet") or ""), "score": number(manifest.get("score")),
            "title": str(manifest.get("title") or ""), "employer": str(manifest.get("employer") or ""),
            "posted": str(manifest.get("posted") or ""), "deadline": str(manifest.get("deadline") or ""),
            "url": url, "external_url": external, "application_url": external or url,
            "selected_cv": str(manifest.get("selected_cv") or ""), "cover_letter": str(manifest.get("cover_letter") or ""),
            "package_dir": str(folder), "submission_status": str(manifest.get("submission_status") or "not_submitted"),
            "manual": str(manifest.get("board") or "").casefold() == "manual",
            "legacy": "" if int(number(manifest.get("manifest_version"))) >= 2 else "yes",
        })
    return rows


def set_application_status(*args, **kwargs):
    from .applications import set_application_status as function
    return function(*args, **kwargs)


def letter_text(*args, **kwargs):
    from .autofill import letter_text as function
    return function(*args, **kwargs)


def load_profile(path: str | Path) -> dict[str, str]:
    import yaml
    profile_path = Path(path)
    if not profile_path.exists():
        raise RuntimeError(f"Applicant profile not found: {profile_path}")
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"{profile_path} must contain key: value pairs")
    profile = {key: str(value or "").strip() for key, value in raw.items()}
    missing = [key for key in ("first_name", "last_name", "email") if not profile.get(key)]
    if missing:
        raise RuntimeError(f"Profile {profile_path} needs: {', '.join(missing)}")
    for key in PROFILE_OPTIONAL:
        profile.setdefault(key, "")
    return profile


def find_claude() -> str | None:
    name = "claude.exe" if os.name == "nt" else "claude"
    found = shutil.which(name)
    default = Path.home() / ".local" / "bin" / name
    return found or (str(default) if default.is_file() else None)


def login_command(executable: str) -> str:
    return "claude auth login" if shutil.which("claude.exe" if os.name == "nt" else "claude") == executable else (f'& "{executable}" auth login' if os.name == "nt" else f'"{executable}" auth login')


def claude_status():
    from .claude_writer import claude_status as function
    return function()


def answer_fields(*args, **kwargs):
    from .formanswers import answer_fields as function
    return function(*args, **kwargs)


def answer_bank_functions():
    from .answerbank import ensure_bank, save_entry, unresolved_from_log
    return ensure_bank, save_entry, unresolved_from_log


def build_draft(*args, **kwargs):
    from .maildraft import build_draft as function
    return function(*args, **kwargs)


def clean_address(*args, **kwargs):
    from .maildraft import clean_address as function
    return function(*args, **kwargs)


def append_links(*args, **kwargs):
    from .manual_links import append_links as function
    return function(*args, **kwargs)


def load_pending(path: str | Path) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = value if isinstance(value, list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidates = [str(item) for item in row.get("candidates", [])]
            joined = next((item for item in reversed(candidates) if item and " " not in item), "")
            if joined and len(str(row.get("title") or "").split()) >= 3:
                row["title"] = joined[:1].upper() + joined[1:]
            if row.get("reason") == "no 80% title and city match":
                row["reason"] = "not found on Arbeitsagentur or the employer feed"
        return [row for row in rows if isinstance(row, dict)]
    except (OSError, ValueError):
        return []


def load_manual_drafts(path: str | Path) -> dict[str, dict[str, str]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): {"text": str(item.get("text") or ""), "employer": str(item.get("employer") or "")}
        for key, item in value.items() if isinstance(item, dict)
    }


def load_manual_results(path: str | Path) -> list[dict[str, object]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _url_key(value: object) -> str:
    try:
        parts = urlsplit(str(value or ""))
        return f"{parts.scheme.casefold()}://{parts.netloc.casefold()}{parts.path.rstrip('/') or '/'}"
    except ValueError:
        return ""


def save_manual_draft(path: str | Path, pending_id: str, text: str, employer: str) -> None:
    target = Path(path)
    drafts = load_manual_drafts(target)
    drafts[pending_id] = {"text": text, "employer": employer}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def create_prepare_schedule(config_path: Path, *, runner=subprocess.run, python: str = sys.executable) -> None:
    """Create the opt-in 03:00 Windows task; nothing is scheduled until this is called."""
    if os.name != "nt":
        raise ValueError("Nightly Prepare is available on Windows only.")
    command = subprocess.list2cmdline([python, "-m", "jobfilter.nightly", "--config", str(config_path)])
    done = runner(
        ["schtasks.exe", "/Create", "/F", "/SC", "DAILY", "/ST", "03:00", "/TN", "jobfilter Prepare",
         "/TR", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    if getattr(done, "returncode", 1):
        raise ValueError((getattr(done, "stderr", "") or getattr(done, "stdout", "") or "Could not create scheduled task").strip())

PAGE = Path(__file__).with_name("app_page.html")
APP_ICON = Path(__file__).resolve().parent.parent / "extension" / "icon128.png"
# Fingerprint of the code: a restart after an update must not reconnect to an app still running old code.
APP_VERSION = hashlib.sha256(
    b"".join(path.read_bytes() for path in sorted(Path(__file__).parent.glob("*.py")))
).hexdigest()[:12]
OPEN_STATUSES = ("not_submitted", "approved", "opened")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IDLE_SECONDS = 900  # the window stopped reporting: it was closed
FORM_LOG_LIMIT = 2 * 1024 * 1024
TASK_TITLES = {
    "auto": "Prepare and apply next", "search": "Search and packages", "prepare": "Prepare",
    "letters": "Letters and PDFs", "pdf": "PDFs", "links": "Manual job links",
    "manual-text": "Manual advertisement",
    "prepare-manual": "Manual application package",
}
PROFILE_OPTIONAL = (
    "salutation", "phone", "street", "house_number", "postal_code", "city", "german_level", "english_level",
    "disability", "start_date", "salary", "notice_period", "relocation", "travel_willingness", "driving_licence",
    "work_authorisation_text",
)


def _past_start_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date() < date.today()
    except (TypeError, ValueError):
        return False


def _form_log_summary(path: Path) -> dict[str, object]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        rows = []
    return {"count": len(rows), "last": str(rows[-1].get("time") or "") if rows else ""}


def _step_items(command: list[str], lines: list[str]) -> int:
    """Extract the main item count printed by a command, without retaining command-specific state."""
    patterns = {
        "export-autofill": r"Packages in the extension:\s*(\d+)",
        "prepare-applications": r"Prepared\s+(\d+)",
        "write-hooks": r"Letters (?:written by \w+|with the standard text):\s*(\d+)",
        "run": r"(?:collected|processed|scored)\D{0,20}(\d+)",
        "add-links": r"(?:processed|added|matched)\D{0,20}(\d+)",
    }
    pattern = patterns.get(command[0], r"\b(\d+)\b")
    values = [int(match.group(1)) for line in lines for match in re.finditer(pattern, line, re.IGNORECASE)]
    return sum(values) if command[0] == "write-hooks" else (values[-1] if values else 0)


class Task:
    """One background job at a time: a list of jobfilter commands run in order."""

    def __init__(self, config_path: Path, on_done=None, timings_path: Path | None = None) -> None:
        self.config_path = config_path
        self.on_done = on_done
        self.lock = threading.Lock()
        self.name = ""
        self.running = False
        self.failed = False
        self.code = 0
        self.log: list[str] = []
        self.steps: list[dict[str, object]] = []
        self.timings_path = timings_path
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen | None = None
        self.stop_requested = threading.Event()

    def start(self, name: str, commands: list[list[str]], after=None) -> bool:
        """Run the commands in order; ``after`` runs once all of them succeeded."""
        with self.lock:
            if self.running:
                return False
            self.name, self.running, self.failed, self.code, self.log, self.steps = name, True, False, 0, [], []
            self.stop_requested.clear()
        self.thread = threading.Thread(target=self._run, args=(commands, after), name="jobfilter-task", daemon=True)
        self.thread.start()
        return True

    def _run(self, commands: list[list[str]], after=None) -> None:
        package_parent = str(Path(__file__).resolve().parent.parent)
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, environment.get("PYTHONPATH", "")]))
        try:
            for command in commands:
                if self.stop_requested.is_set():
                    break
                started = time.perf_counter()
                self.log.append("> jobfilter " + " ".join(command))
                process = subprocess.Popen(
                    [sys.executable, "-m", "jobfilter", "--config", str(self.config_path), *command],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.config_path.parent,
                    env=environment, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                )
                self.process = process
                assert process.stdout is not None
                for line in process.stdout:
                    self.log.append(line.rstrip())
                    del self.log[:-400]
                code = process.wait()
                self.process = None
                seconds = round(time.perf_counter() - started, 3)
                recent = self.log[-25:]
                step = {"step": command[0], "seconds": seconds, "items": _step_items(command, recent)}
                self.steps.append(step)
                if self.timings_path:
                    from .metrics import append_jsonl
                    append_jsonl(self.timings_path, {"time": datetime_now(), "task": self.name, **step})
                if code != 0:
                    self.failed, self.code = True, process.returncode
                    self.log.append(f"Stopped: the step ended with code {process.returncode}.")
                    break
            if after is not None and not self.failed:
                after()
        except (OSError, ValueError) as error:
            self.failed = True
            self.log.append(f"Could not finish: {error}")
        finally:
            self.process = None
            self.running = False
            if self.on_done is not None:
                self.on_done(self)

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_requested.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def snapshot(self) -> dict[str, object]:
        return {"name": self.name, "running": self.running, "failed": self.failed, "log": self.log[-60:], "steps": self.steps}


class App:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        self.token = secrets.token_urlsafe(24)
        initial = Config.load(self.config_path)
        metrics_dir = initial.output_dir if initial.output_dir.is_absolute() else self.config_path.parent / initial.output_dir
        self.task = Task(self.config_path, self.task_done, metrics_dir / "timings.jsonl")
        self.notices: list[dict[str, str]] = []
        self.notifier = notify
        self.opener = open_urls
        self.mail_opener = open_file
        self.answer_runner = subprocess.run
        self.current = ""  # the application whose form is open right now (one at a time)
        self.form_states: dict[str, dict[str, object]] = {}
        self.letter_states: dict[str, dict[str, object]] = {}
        self.letter_cancellations: dict[str, threading.Event] = {}
        self.letter_cancel_outcomes: dict[str, str] = {}
        # Explicit, per-session permission to spend tokens for one opened application.  Merely
        # opening a form never writes a letter and never asks Claude.
        self.confirmed: set[str] = set()
        self.deferred: set[str] = set()  # "Later" in this session: not offered again until restart
        self.processing: set[str] = set()
        self._stopping = threading.Event()
        self._threads_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self._io_jobs: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._io_thread = threading.Thread(target=self._io_worker, name="jobfilter-io", daemon=True)
        self._io_thread.start()
        self.last_ping = 0.0
        self.bye_at = 0.0
        self.storage_summary = self._storage_summary()
        self.extension_version = ""
        self.extension_seen = 0.0
        self.startup_reset: list[str] = []
        self.startup_gated: list[str] = []
        from .safeio import trim_jsonl
        trim_jsonl(self.config.application_output_dir / "form_log.jsonl", FORM_LOG_LIMIT)
        self._restore_opened_and_gate_waiting()

    def _io_worker(self) -> None:
        """Serialize slow OneDrive/tracker/export work away from HTTP requests."""
        while True:
            name, action = self._io_jobs.get()
            if action is None:
                self._io_jobs.task_done()
                return
            started = time.perf_counter()
            error = ""
            try:
                action()
            except Exception as caught:  # the foreground route is already usable
                error = str(caught)[:300]
                self.notice("jobfilter background step", f"{name}: {error}", "error")
            finally:
                try:
                    from .metrics import append_jsonl
                    append_jsonl(self.config.output_dir / "timings.jsonl", {
                        "time": datetime_now(), "task": "apply", "step": name,
                        "seconds": round(time.perf_counter() - started, 3), "items": 1,
                        **({"error": error} if error else {}),
                    })
                finally:
                    self._io_jobs.task_done()

    def _enqueue_io(self, name: str, action) -> None:
        if not self._stopping.is_set():
            self._io_jobs.put((name, action))

    def _start_background(self, target, *args, name: str) -> threading.Thread | None:
        if self._stopping.is_set():
            return None
        def run() -> None:
            try:
                target(*args)
            finally:
                with self._threads_lock:
                    self._threads.discard(threading.current_thread())
        thread = threading.Thread(target=run, name=name, daemon=True)
        with self._threads_lock:
            self._threads.add(thread)
        thread.start()
        return thread

    def stop(self, timeout: float = 15.0) -> None:
        """Finish or cancel owned workers so no test/app instance writes after shutdown."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        for cancellation in tuple(self.letter_cancellations.values()):
            cancellation.set()
        self.task.stop(timeout)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._threads_lock:
                threads = tuple(self._threads)
            if not threads or time.monotonic() >= deadline:
                break
            for thread in threads:
                thread.join(max(0.0, min(0.2, deadline - time.monotonic())))
        self._io_jobs.join()
        self._io_jobs.put(("stop", None))
        self._io_thread.join(max(0.0, deadline - time.monotonic()))

    def _write_manifest(self, row: dict[str, object], manifest: dict[str, object]) -> bool:
        from .safeio import atomic_write_json
        return atomic_write_json(Path(str(row["package_dir"])) / "application.json", manifest)

    def _confirmed_from_disk(self) -> set[str]:
        confirmed = set()
        for row in _queue_rows(self.config.application_output_dir):
            try:
                if self.manifest(row).get("confirmed_at"):
                    confirmed.add(str(row["application_id"]))
            except (OSError, ValueError):
                pass
        return confirmed

    def _restore_opened_and_gate_waiting(self) -> None:
        """Keep only the newest open form after restart and gate stale waiting titles once."""
        config = self.config
        output = config.application_output_dir
        opened = [row for row in _queue_rows(output) if row["submission_status"] == "opened" and not row["legacy"]]
        if opened:
            def changed(row):
                try: return str(self.manifest(row).get("status_updated_at") or "")
                except (OSError, ValueError): return ""
            current = max(opened, key=changed)
            self.current = str(current["application_id"])
            for row in opened:
                application_id = str(row["application_id"])
                if application_id == self.current:
                    continue
                try:
                    set_application_status(output, application_id, "not_submitted")
                    self.startup_reset.append(application_id)
                except (OSError, ValueError):
                    pass
        self.startup_gated.extend(item["label"] for item in self._apply_title_gate(_queue_rows(output)))
        self.confirmed = self._confirmed_from_disk()
        if self.startup_reset or self.startup_gated:
            from .metrics import append_jsonl
            append_jsonl(config.output_dir / "timings.jsonl", {
                "time": datetime_now(), "task": "startup", "step": "queue-cleanup", "seconds": 0,
                "items": len(self.startup_reset) + len(self.startup_gated),
                "reset": self.startup_reset, "gated": self.startup_gated,
            })

    def _apply_title_gate(self, rows: list[dict[str, object]]) -> list[dict[str, str]]:
        """Retire every queued non-manual package rejected by the current title rules."""
        from .scoring import load_keywords, title_gate
        try:
            keywords = load_keywords(self.config.keywords_path)
        except (OSError, ValueError):
            return []
        gated: list[dict[str, str]] = []
        for row in rows:
            status = str(row.get("submission_status") or "")
            if status not in {"needs_hook", "not_submitted", "approved"} or bool(row.get("manual")):
                continue
            gate = title_gate(str(row.get("title") or ""), keywords)
            if not gate or gate[0] != "DROP":
                continue
            manifest = self.manifest(row)
            manifest["title_gate_previous_status"] = status
            manifest["submission_status"] = "skipped"
            manifest["status_reason"] = "title gate"
            manifest["status_updated_at"] = datetime_now()
            manifest.pop("confirmed_at", None)
            self._write_manifest(row, manifest)
            application_id = str(row["application_id"])
            self.archive_outcome(application_id, "skipped", reason="title gate")
            self.confirmed.discard(application_id)
            gated.append({
                "id": application_id, "title": str(row.get("title") or ""),
                "employer": str(row.get("employer") or ""),
                "label": f"{row['title']} ({application_id})",
            })
        return gated

    def title_gated_rows(self) -> list[dict[str, str]]:
        rows = []
        for row in _queue_rows(self.config.application_output_dir):
            try:
                manifest = self.manifest(row)
            except (OSError, ValueError):
                continue
            if row["submission_status"] == "skipped" and manifest.get("status_reason") == "title gate":
                rows.append({"id": str(row["application_id"]), "title": str(row["title"]), "employer": str(row["employer"])})
        return rows

    def restore_title_gated(self, application_id: str) -> dict[str, object]:
        row = next((item for item in _queue_rows(self.config.application_output_dir)
                    if item["application_id"] == application_id), None)
        if row is None:
            raise FileNotFoundError("Application not found")
        manifest = self.manifest(row)
        if manifest.get("status_reason") != "title gate":
            raise ValueError("This application was not removed by the title filter.")
        previous = str(manifest.pop("title_gate_previous_status", "") or "not_submitted")
        manifest.pop("status_reason", None)
        manifest["submission_status"] = previous if previous in {"needs_hook", "not_submitted", "approved"} else "not_submitted"
        manifest["status_updated_at"] = datetime_now()
        self._write_manifest(row, manifest)
        return {"restored": application_id, "status": manifest["submission_status"]}

    def _storage_summary(self) -> str:
        from .storage import human_size, project_size
        report = project_size(self.config_path.parent)
        names = ", ".join(f"{item['name']} {human_size(int(item['bytes']))}" for item in report["largest"][:3])
        return f"Project {human_size(int(report['bytes']))}; largest: {names}"

    def notice(self, title: str, text: str, kind: str = "info") -> None:
        """A small notification window plus a line at the top of the app."""
        self.notices.append({"title": title, "text": text, "kind": kind, "time": time.strftime("%H:%M")})
        del self.notices[:-8]
        self.notifier(title, text)

    def task_done(self, task: Task) -> None:
        title = TASK_TITLES.get(task.name, task.name)
        if task.failed:
            needs = [line for line in task.log if line.startswith("NEEDS YOU")]
            if needs:
                self.notice("jobfilter needs you", needs[-1].split(": ", 1)[-1], "needs")
            else:
                tail = next((line for line in reversed(task.log[:-1]) if line.strip()), "See the log in the app.")
                self.notice(f"jobfilter stopped: {title}", tail, "error")
            return
        self.storage_summary = self._storage_summary()
        state = self.state(with_task=False)
        ready = sum(1 for p in state["packages"] if p["status"] in {"needs_hook", "not_submitted", "approved"} and p["ready"])
        opened = sum(1 for p in state["packages"] if p["status"] == "opened")
        waiting = len(state["waiting"])
        parts = [f"{ready} ready to open" if ready else "", f"{opened} form(s) open for your check" if opened else "",
                 f"{waiting} waiting for a letter" if waiting else ""]
        self.notice(f"jobfilter finished: {title}", ", ".join(filter(None, parts)) or "Nothing new to apply for.")

    @property
    def config(self) -> Config:
        config = Config.load(self.config_path)
        # Paths left at their defaults are relative: anchor them at the project folder, not the working directory.
        base = self.config_path.parent
        fixes = {
            name: base / getattr(config, name)
            for name in (
                "keywords_path", "cache_dir", "database_path", "output_dir", "companies_path",
                "indeed_import_path", "job_documents_dir", "application_workbook", "application_output_dir",
                "github_catalog_path", "github_cache_path", "applicant_profile_path", "answer_bank_path",
                "autofill_extension_dir", "application_archive_dir",
            )
            if not Path(getattr(config, name)).is_absolute()
        }
        return replace(config, **fixes) if fixes else config

    def bank(self, config: Config) -> Path:
        return config.job_documents_dir / "anschreiben_bausteine.yaml"

    def manual_paths(self) -> tuple[Path, Path, Path, Path, Path]:
        imports = self.config_path.parent / "imports"
        return (
            imports / "links.txt", imports / "links_done.txt", imports / "manual_pending.json",
            imports / "manual_drafts.json", imports / "manual_results.json",
        )

    def exported(self, config: Config) -> set[str]:
        """IDs of the packages whose PDFs are in the extension, so their forms can be filled."""
        data_file = config.autofill_extension_dir / "data" / "data.json"
        if not data_file.exists():
            return set()
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
            return {
                item["id"] for item in data["packages"]
                if not all(kind in item for kind in ("cv", "letter")) or all(
                    (config.autofill_extension_dir / str(item[kind]["path"])).is_file()
                    for kind in ("cv", "letter")
                )
            }
        except (ValueError, KeyError, TypeError):
            return set()

    def ready_rows(self, config: Config) -> list[dict[str, object]]:
        """Packages whose form can be opened, in queue order; Arbeitsagentur pages behind a CAPTCHA come last."""
        output = config.application_output_dir
        current_rows = _queue_rows(output) if output.is_dir() else []
        newly_gated = self._apply_title_gate(current_rows)
        if newly_gated:
            self.startup_gated.extend(item["label"] for item in newly_gated)
            current_rows = _queue_rows(output)
        exported = self.exported(config)
        rows = [
            row for row in current_rows
            if not row["legacy"] and row["submission_status"] in {"needs_hook", "not_submitted", "approved"}
            and row["application_id"] in exported and row["application_url"]
            and row["application_id"] not in self.deferred and row["application_id"] not in self.processing
        ]
        return sorted(rows, key=lambda row: self.needs_captcha(row))

    @staticmethod
    def manifest(row: dict[str, object]) -> dict[str, object]:
        return json.loads((Path(str(row["package_dir"])) / "application.json").read_text(encoding="utf-8-sig"))

    def needs_captcha(self, row: dict[str, object]) -> bool:
        host = urlsplit(str(row["application_url"])).netloc
        return host.endswith("arbeitsagentur.de") and not self.manifest(row).get("apply_email")

    def observations(self):
        from .learning import ObservationStore
        return ObservationStore(self.config.application_output_dir / "observed.json")

    def archive_outcome(self, application_id: str, status: str, *, reason: str = "") -> None:
        """Stellenarchiv entry for a package; failures are logged, never raised."""
        from .jdarchive import archive_package
        row = next((item for item in _queue_rows(self.config.application_output_dir)
                    if item["application_id"] == application_id), None)
        if row:
            archive_package(self.config.jd_archive_dir, row["package_dir"], status, reason=reason)

    def record_outcome(self, application_id: str, outcome: str) -> dict[str, int]:
        """Submitted teaches the answer bank; skipped forgets what was typed; later keeps it."""
        if not application_id or outcome not in {"submitted", "skipped"}:
            return {}
        archive = getattr(self, "archive_outcome", None)
        if archive is not None:
            archive(application_id, "applied" if outcome == "submitted" else "skipped")
        store = self.observations()
        if outcome == "skipped":
            store.discard(application_id)
            return {}
        observed = store.take(application_id)
        if not observed:
            return {}
        row = next((item for item in _queue_rows(self.config.application_output_dir)
                    if item["application_id"] == application_id), {})
        from .learning import learn_submission
        try:
            counts = learn_submission(
                self.config.answer_bank_path, observed, employer=str(row.get("employer") or ""),
                title=str(row.get("title") or ""), package_id=application_id,
            )
        except (OSError, ValueError) as error:
            self.notice("jobfilter: answer bank not updated", str(error)[:200])
            return {}
        new = counts.get("learned", 0) + counts.get("updated", 0)
        if new or counts.get("pending"):
            text = f"{new} answer(s) learned"
            if counts.get("pending"):
                text += f", {counts['pending']} fact(s) to confirm on the Answer bank page"
            self.notice("jobfilter: answer bank learned", text + ".")
        return counts

    def apply_next(self, done: str = "", outcome: str = "") -> dict[str, object]:
        """Record the result of the open form, then open the next one. Never submits anything."""
        started = time.perf_counter()
        config = self.config
        output = config.application_output_dir
        finish_done = None
        if done:
            if outcome not in {"submitted", "skipped", "later"}:
                raise ValueError("Unknown outcome")
            cancellation = self.letter_cancellations.get(done)
            if cancellation:
                self.letter_cancel_outcomes[done] = outcome
                cancellation.set()
            self.processing.add(done)
            def finish_done(done=done, outcome=outcome):
                try:
                    current_status = next((str(row["submission_status"]) for row in _queue_rows(output)
                                           if row["application_id"] == done), "")
                    if outcome == "later" and current_status == "needs_hook":
                        done_row = next((row for row in _queue_rows(output) if row["application_id"] == done), None)
                        if done_row:
                            manifest = self.manifest(done_row)
                            manifest.pop("confirmed_at", None)
                            self._write_manifest(done_row, manifest)
                    else:
                        set_application_status(output, done, "not_submitted" if outcome == "later" else outcome)
                    self.record_outcome(done, outcome)
                finally:
                    self.processing.discard(done)
            if outcome == "later":
                self.deferred.add(done)
            if self.current == done:
                self.current = ""
            self.confirmed.discard(done)
            self.form_states.pop(done, None)
        rows = self.ready_rows(config)
        if not rows:
            self.current = ""
            if finish_done:
                self._enqueue_io("status writes + tracker sync", lambda: (finish_done(), self.sync_tracker(config)))
            self.notice("jobfilter: no more forms", "Every ready application is done or set aside for later.")
            elapsed = time.perf_counter() - started
            from .metrics import append_jsonl
            self._enqueue_io("apply timing record", lambda: append_jsonl(config.output_dir / "timings.jsonl", {
                "time": datetime_now(), "task": "apply", "step": "open route",
                "seconds": 0, "request_seconds": round(elapsed, 3), "items": 0,
            }))
            return {"opened": "", "left": 0, "tracker_note": ""}
        row = rows[0]
        application_id = str(row["application_id"])
        needs_letter = row["submission_status"] == "needs_hook"
        self.processing.add(application_id)
        self.current = application_id
        route_started = time.perf_counter()
        via = self.open_route(config, row)
        route_seconds = time.perf_counter() - route_started

        def after_open():
            try:
                if finish_done:
                    finish_done()
                status_started = time.perf_counter()
                if row["submission_status"] == "not_submitted":
                    set_application_status(output, application_id, "approved")
                if not needs_letter:
                    set_application_status(output, application_id, "opened")
                from .metrics import append_jsonl
                append_jsonl(config.output_dir / "timings.jsonl", {
                    "time": datetime_now(), "task": "apply", "step": "status writes",
                    "seconds": round(time.perf_counter() - status_started, 3), "items": 1,
                })
                export_started = time.perf_counter()
                self._top_up_extension(application_id)
                append_jsonl(config.output_dir / "timings.jsonl", {
                    "time": datetime_now(), "task": "apply", "step": "export top-up",
                    "seconds": round(time.perf_counter() - export_started, 3), "items": 1,
                })
                tracker_started = time.perf_counter()
                self.sync_tracker(config)
                append_jsonl(config.output_dir / "timings.jsonl", {
                    "time": datetime_now(), "task": "apply", "step": "tracker sync",
                    "seconds": round(time.perf_counter() - tracker_started, 3), "items": 1,
                })
            finally:
                self.processing.discard(application_id)

        self._enqueue_io("apply follow-up", after_open)
        request_seconds = time.perf_counter() - started
        from .metrics import append_jsonl
        # This small record is queued behind the follow-up; no OneDrive write delays this request.
        self._enqueue_io("apply timing record", lambda: append_jsonl(config.output_dir / "timings.jsonl", {
            "time": datetime_now(), "task": "apply", "step": "open route",
            "seconds": round(route_seconds, 3), "request_seconds": round(request_seconds, 3), "items": 1,
        }))
        return {
            "opened": application_id, "title": row["title"], "employer": row["employer"], "via": via,
            "left": len(rows) - 1, "tracker_note": "",
        }

    def confirm_apply(self, application_id: str) -> dict[str, object]:
        """Record the user's token-spend confirmation, then prepare this application."""
        if not application_id or application_id != self.current:
            raise ValueError("This is no longer the open application. Open it again before confirming.")
        row = next((item for item in _queue_rows(self.config.application_output_dir)
                    if item["application_id"] == application_id), None)
        if row is None or row["submission_status"] not in {"needs_hook", "not_submitted", "approved", "opened"}:
            raise ValueError("This application is no longer available for confirmation.")
        self.confirmed.add(application_id)
        manifest = self.manifest(row)
        manifest["confirmed_at"] = datetime_now()
        self._write_manifest(row, manifest)
        needs_letter = row["submission_status"] == "needs_hook"
        if needs_letter:
            self._start_letter(application_id, row, "email" if self.manifest(row).get("apply_email") else "form")
        elif row["submission_status"] in {"not_submitted", "approved"}:
            set_application_status(self.config.application_output_dir, application_id, "opened")
            self._enqueue_io("export confirmation", lambda: self._top_up_extension(application_id))
        return {"confirmed": True, "confirmed_at": manifest["confirmed_at"], "id": application_id, "letter_started": needs_letter}

    def _start_letter(self, application_id: str, row: dict[str, object], via: str, *, prefetch: bool = False) -> None:
        if application_id in self.letter_cancellations:
            return
        cancel = threading.Event()
        self.letter_cancellations[application_id] = cancel
        self.letter_states[application_id] = {"state": "writing", "text": "Writing your letter", "source": ""}
        self._start_background(
            self._write_letter, application_id, row, via, cancel, prefetch,
            name=f"jobfilter-letter-{application_id}",
        )

    def _write_letter(
        self, application_id: str, row: dict[str, object], via: str,
        cancel: threading.Event, prefetch: bool,
    ) -> None:
        config = self.config
        folder = Path(str(row["package_dir"]))
        started = time.perf_counter()
        source = "standard"
        reason = ""
        try:
            from .claude_writer import write_hook_on_demand
            result = write_hook_on_demand(
                config.application_output_dir, self.bank(config), application_id,
                writer=config.letter_writer, model=config.claude_model,
                runner=self.answer_runner, cancelled=cancel.is_set,
            )
            source, reason = str(result.get("source") or "standard"), str(result.get("reason") or "")
            write_seconds = time.perf_counter() - started
            if cancel.is_set() or result.get("discarded"):
                self._discard_letter(folder, self.letter_cancel_outcomes.get(application_id, "later"))
                self.letter_states[application_id] = {"state": "discarded", "text": "Letter discarded", "source": source}
                return
            render_started = time.perf_counter()
            from .applications import render_existing_letter, set_application_status
            profile = load_profile(config.applicant_profile_path)
            render_existing_letter(
                config.application_output_dir, config.job_documents_dir, application_id,
                applicant_status=config.applicant_status, graduation_date=config.graduation_date,
                start_date=profile.get("start_date", ""), applicant_file_stem=config.applicant_file_stem,
            )
            render_seconds = time.perf_counter() - render_started
            if cancel.is_set():
                self._discard_letter(folder, self.letter_cancel_outcomes.get(application_id, "later"))
                self.letter_states[application_id] = {"state": "discarded", "text": "Letter discarded", "source": source}
                return
            export_started = time.perf_counter()
            export_report = self._top_up_extension(self.current or application_id)
            export_total = time.perf_counter() - export_started
            pdf_seconds = float(export_report.get("conversion_seconds") or 0)
            export_seconds = max(0.0, export_total - pdf_seconds)
            if not prefetch and self.current == application_id:
                set_application_status(config.application_output_dir, application_id, "approved")
                set_application_status(config.application_output_dir, application_id, "opened")
            text = f"Letter ready: {source}"
            if reason:
                text += f" ({reason})"
            self.letter_states[application_id] = {
                "state": "ready", "text": text, "source": source, "reason": reason,
                "ready_at": datetime_now(),
            }
            from .metrics import append_jsonl
            self.sync_tracker(config)
            for step, seconds in (("write", write_seconds), ("render", render_seconds), ("pdf", pdf_seconds), ("export", export_seconds)):
                append_jsonl(config.output_dir / "timings.jsonl", {
                    "time": datetime_now(), "task": "letter", "step": step,
                    "application_id": application_id, "seconds": round(seconds, 3), "items": 1,
                })
            if via == "email" and not prefetch and self.current == application_id:
                address = str(self.manifest(row).get("apply_email") or "")
                if address:
                    self.open_draft(config, row, address)
            if not prefetch:
                self.notice("jobfilter: letter ready", f"{row['title']}: {source}. Check it before you submit.")
            if config.letter_prefetch and not prefetch:
                follower = next((item for item in self.ready_rows(config)
                                 if item["application_id"] != application_id and item["submission_status"] == "needs_hook"), None)
                if follower:
                    self._start_letter(str(follower["application_id"]), follower, "prefetch", prefetch=True)
        except Exception as error:
            self.letter_states[application_id] = {
                "state": "error", "text": f"Letter could not be prepared: {error}", "source": source,
            }
            self.notice("jobfilter: letter needs you", str(error)[:300], "error")
        finally:
            self.letter_cancellations.pop(application_id, None)
            self.letter_cancel_outcomes.pop(application_id, None)

    @staticmethod
    def _discard_letter(folder: Path, outcome: str = "later") -> None:
        try:
            manifest = json.loads((folder / "application.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            manifest = {}
        names = [str(manifest.get("cover_letter") or ""), "hook.json"]
        names.extend(path.name for path in folder.glob("Anschreiben_*.pdf"))
        for name in names:
            path = folder / name
            if name and path.is_file():
                try: path.unlink()
                except OSError: pass
        manifest_path = folder / "application.json"
        if manifest:
            manifest["cover_letter"] = ""
            manifest["submission_status"] = "skipped" if outcome == "skipped" else "needs_hook"
            manifest["status_updated_at"] = datetime_now()
            try:
                from .safeio import atomic_write_json
                atomic_write_json(manifest_path, manifest)
            except OSError: pass

    def _top_up_extension(self, current_id: str) -> dict[str, object]:
        """Publish the current package plus five followers without blocking the opened form."""
        config = self.config
        try:
            from .autofill import export_autofill
            return export_autofill(
                config.application_output_dir, config.applicant_profile_path, config.autofill_extension_dir,
                current_id=current_id, attachment_order=config.application_attachment_allowlist,
                attachment_names=config.application_attachment_allowlist,
                residence_attachment_names=config.application_residence_attachment_names,
                attachments_dir=config.job_documents_dir / "Attachements",
                applicant_file_stem=config.applicant_file_stem,
                workbook_path=config.application_workbook,
                database_path=config.database_path,
                answer_bank_path=config.answer_bank_path,
                backfill_descriptions=False,
            )
        except Exception:
            # The already opened form remains usable. The next explicit Prepare reports export errors.
            return {}

    def open_route(self, config: Config, row: dict[str, object]) -> str:
        """Open the form, or the e-mail draft when the job is applied for by e-mail."""
        address = str(self.manifest(row).get("apply_email") or "")
        if address:
            if str(row.get("submission_status")) == "needs_hook":
                self.notice("E-mail application waiting", f"{row['title']}: the draft follows when the letter is ready.")
                return "email"
            self.open_draft(config, row, address)
            return "email"
        self.opener([str(row["application_url"])])  # the extension fills it, or asks for the CAPTCHA once
        return "form"

    def open_draft(self, config: Config, row: dict[str, object], address: str) -> Path:
        draft, left_out = build_draft(
            Path(str(row["package_dir"])), self.manifest(row), load_profile(config.applicant_profile_path), address,
            attachments_dir=config.job_documents_dir / "Attachements", order=config.application_attachment_allowlist,
            attachment_names=config.application_attachment_allowlist,
        )
        self.mail_opener(draft)
        extra = f" Left out for size: {', '.join(left_out)}." if left_out else ""
        self.notice("E-mail draft ready in Outlook", f"{row['title']}: check the draft and press Send, then click Submitted.{extra}")
        return draft

    def contact(self, application_id: str, email: str = "", link: str = "") -> dict[str, object]:
        """The extension found how to apply after the CAPTCHA: remember it, so the CAPTCHA is never needed again."""
        config = self.config
        row = next(item for item in _queue_rows(config.application_output_dir) if item["application_id"] == application_id)
        path = Path(str(row["package_dir"])) / "application.json"
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        address = clean_address(email)
        target = link if urlsplit(link).scheme in {"http", "https"} and not urlsplit(link).netloc.endswith("arbeitsagentur.de") else ""
        if address:
            manifest["apply_email"] = address
        if target and not manifest.get("external_url"):
            manifest["external_url"] = target
        if not (address or target):
            raise ValueError("Neither an e-mail address nor a link to the employer was found.")
        from .safeio import atomic_write_json
        atomic_write_json(path, manifest)
        draft = str(self.open_draft(config, row, address)) if address else ""
        if target and not address:
            self.notice("Employer form found", f"{row['title']}: the employer's own form opens now and is filled there.")
        return {"saved": True, "email": address, "link": target, "draft": draft}

    def state(self, with_task: bool = True) -> dict[str, object]:
        config = self.config
        output = config.application_output_dir
        rows = _queue_rows(output) if output.is_dir() else []
        exported = self.exported(config)
        packages = []
        for row in rows:
            status = str(row["submission_status"])
            if row["legacy"] or status in {"expired", "skipped"}:
                continue
            folder = Path(str(row["package_dir"]))
            manifest = json.loads((folder / "application.json").read_text(encoding="utf-8"))
            packages.append({
                "id": row["application_id"], "title": row["title"], "employer": row["employer"],
                "location": manifest.get("location", ""), "status": status, "score": row["score"],
                "sheet": row["source_sheet"], "posted": row["posted"], "url": row["application_url"],
                "source_url": row["url"], "external_url": row["external_url"],
                "cv": row["selected_cv"], "family": manifest.get("primary_cv_family", ""),
                "language": manifest.get("language_flags", ""),
                "manual": bool(row.get("manual")),
                "confirmed": bool(manifest.get("confirmed_at")) or row["application_id"] in self.confirmed,
                "confirmed_at": str(manifest.get("confirmed_at") or ""),
                "has_hook": (folder / "hook.json").exists(),
                "hook_source": _hook_source(folder),
                "ready": row["application_id"] in exported,
                "ready_reason": "" if row["application_id"] in exported else "CV PDF missing; run Prepare",
            })
        package_urls = {
            _url_key(url): package
            for package in packages
            for url in (package.get("url"), package.get("source_url"), package.get("external_url"))
            if _url_key(url)
        }
        manual_results = []
        for item in reversed(load_manual_results(self.manual_paths()[4])):
            package = package_urls.get(_url_key(item.get("url")))
            manual_results.append({
                **item,
                "package_id": package.get("id", "") if package else "",
                "package_status": package.get("status", "") if package else "",
                "package_ready": bool(package and package.get("ready")),
                "package_eligible": bool(item.get("url")),
            })
        profile_problem, missing, start_date_passed, profile = "", [], False, {}
        try:
            profile = load_profile(config.applicant_profile_path)
            missing = [key for key in PROFILE_OPTIONAL if not profile[key]]
            start_date_passed = _past_start_date(profile.get("start_date", ""))
        except RuntimeError as error:
            profile_problem = str(error)
        ensure_bank, _, unresolved_from_log = answer_bank_functions()
        cv_texts = []
        try:
            exported = json.loads((config.autofill_extension_dir / "data" / "data.json").read_text(encoding="utf-8"))
            cv_texts = [item.get("cv_text", "") for item in exported.get("packages", []) if isinstance(item, dict)]
        except (OSError, ValueError):
            pass
        bank = ensure_bank(config.answer_bank_path, profile, cv_texts)
        employers = {str(item.get("id") or ""): str(item.get("employer") or "") for item in packages}
        answer_bank = {
            "path": str(config.answer_bank_path), "facts": bank["facts"], "tools": bank["tools"],
            "not_used": bank["not_used"], "ratings": bank["ratings"], "saved": bank["saved"],
            "learned": bank.get("learned", []), "pending": bank.get("pending", []),
            "missing": unresolved_from_log(config.application_output_dir / "form_log.jsonl", bank, profile, employers),
        }
        return {
            "packages": packages,
            "waiting": [p["id"] for p in packages if p["status"] == "needs_hook" and not p["has_hook"]],
            "profile_problem": profile_problem, "profile_missing": missing,
            "start_date_passed": start_date_passed,
            "profile_path": str(config.applicant_profile_path),
            "extension_dir": str(config.autofill_extension_dir),
            "tracker": str(config.application_archive_dir / TRACKER_NAME),
            "tracker_exists": (config.application_archive_dir / TRACKER_NAME).exists(),
            "writer": config.letter_writer, **self.claude_found(),
            "notices": self.notices[-5:],
            "current": self.current,
            "letter_state": self.letter_states.get(self.current, {}),
            "startup_reset": self.startup_reset, "startup_gated": self.startup_gated,
            "title_gated": self.title_gated_rows(),
            "form_state": self.form_states.get(self.current, {}),
            "manual_pending": load_pending(self.manual_paths()[2]),
            "manual_drafts": load_manual_drafts(self.manual_paths()[3]),
            "manual_results": manual_results,
            "task": self.task.snapshot() if with_task else {},
            "storage": self.storage_summary,
            "usage": __import__("jobfilter.metrics", fromlist=["usage_summary"]).usage_summary(config.output_dir / "usage.jsonl"),
            "forms_logged": _form_log_summary(config.application_output_dir / "form_log.jsonl"),
            "answer_bank": answer_bank,
            "extension": self.extension_health(config),
        }

    def extension_health(self, config: Config) -> dict[str, object]:
        try:
            expected = str(json.loads((config.autofill_extension_dir / "manifest.json").read_text(encoding="utf-8"))["version"])
        except (OSError, ValueError, KeyError):
            expected = ""
        connected = bool(self.extension_seen and time.monotonic() - self.extension_seen < 45)
        return {"connected": connected, "version": self.extension_version, "expected": expected,
                "reload": connected and bool(expected) and expected != self.extension_version}

    def claude_found(self) -> dict[str, object]:
        """Where Claude Code is (a quick file check; the sign-in is checked on request)."""
        path = find_claude() or ""
        return {
            "claude_installed": bool(path), "claude_path": path,
            "claude_login": login_command(path) if path else DEFAULT_LOGIN, "claude_install": INSTALL_COMMAND,
        }

    def package(self, application_id: str) -> dict[str, object]:
        config = self.config
        for row in _queue_rows(config.application_output_dir):
            if row["application_id"] != application_id:
                continue
            folder = Path(str(row["package_dir"]))
            manifest = json.loads((folder / "application.json").read_text(encoding="utf-8"))
            letter = folder / str(row["cover_letter"]) if row["cover_letter"] else None
            editable = {}
            if letter and letter.is_file():
                from .letter_editor import read_letter
                try:
                    editable = read_letter(letter)
                except (OSError, ValueError):
                    editable = {}
            return {
                "id": application_id, "title": row["title"], "employer": row["employer"],
                "letter": letter_text(letter) if letter and letter.is_file() else "",
                "family": manifest.get("primary_cv_family", ""), "second": manifest.get("secondary_cv_family", ""),
                "hits": manifest.get("family_hits", ""), "cv": row["selected_cv"],
                "projects": [item.get("title", "") for item in manifest.get("cv_github_projects", [])],
                "folder": str(folder),
                "letter_path": str(letter) if letter else "", **editable,
            }
        raise FileNotFoundError(f"Application ID not found: {application_id}")

    def sync_tracker(self, config: Config) -> str:
        """Update the tracker after a status change; a tracker open in Excel must not block the change."""
        try:
            from .archive import ArchiveBusy, sync_archive
            sync_archive(config.application_output_dir, config.application_archive_dir)
        except ArchiveBusy as error:
            return str(error)
        return ""

    def letters_chain(self) -> list[list[str]]:
        return [["prepare-applications", "--existing-only"], ["export-autofill"]]

    def write_chain(self, standard: bool = False) -> list[list[str]]:
        return [["write-hooks", *(["--standard"] if standard else [])], *self.letters_chain()]

    def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        config = self.config
        output = config.application_output_dir
        if path == "/api/task":
            name = str(body.get("name"))
            after = None
            if name == "auto":
                ads = max(0, int(body.get("max_ads") or 0))
                limit = max(1, min(50, int(body.get("limit") or 10)))
                commands = [["add-links"], ["run", "--max-ads", str(ads)], ["prepare-applications", "--limit", str(limit)], ["export-autofill"]]
                after = self.apply_next
            elif name == "search":
                ads = max(0, int(body.get("max_ads") or 0))
                limit = max(1, min(50, int(body.get("limit") or 10)))
                commands = [["add-links"], ["run", "--max-ads", str(ads)], ["prepare-applications", "--limit", str(limit)]]
            elif name == "prepare":
                ads = max(0, int(body.get("max_ads") or 0))
                limit = max(1, min(50, int(body.get("limit") or 10)))
                commands = [["add-links"], ["run", "--max-ads", str(ads)], ["prepare-applications", "--limit", str(limit)], ["export-autofill"]]
            elif name == "letters":
                commands = self.write_chain(standard=bool(body.get("standard")))
            elif name == "pdf":
                # Builds missing or outdated PDFs only (a CV or letter changed in Word is newer than its PDF).
                commands = [["export-autofill"]]
            else:
                raise ValueError("Unknown task")
            if not self.task.start(name, commands, after):
                raise ValueError("Another step is still running.")
            return {"started": name}
        if path == "/api/schedule-prepare":
            create_prepare_schedule(self.config_path)
            return {"scheduled": "03:00", "name": "jobfilter Prepare"}
        if path == "/api/add-links":
            if self.task.running:
                raise ValueError("Another step is still running.")
            count = append_links(self.manual_paths()[0], str(body.get("text") or ""))
            if not count:
                raise ValueError("Paste at least one job link.")
            if not self.task.start("links", [["add-links"]]):
                raise ValueError("Another step is still running.")
            return {"started": count}
        if path == "/api/retry-links":
            if self.task.running:
                raise ValueError("Another step is still running.")
            if not self.task.start("links", [["add-links", "--retry-errors"]]):
                raise ValueError("Another step is still running.")
            return {"started": "retry"}
        if path == "/api/letter-save":
            from .letter_editor import save_package
            return save_package(
                config, str(body["id"]), str(body.get("intro") or ""),
                str(body.get("company_paragraph") or ""),
            )
        if path == "/api/letter-open":
            package = self.package(str(body["id"]))
            open_file(Path(str(package["letter_path"])))
            return {"opened": package["letter_path"]}
        if path == "/api/letter-rewrite":
            if config.letter_writer != "claude":
                raise ValueError("Rewrite with Claude is available only when letter_writer is claude.")
            from .claude_writer import rewrite_hook
            return rewrite_hook(
                output, self.bank(config), str(body["id"]), model=config.claude_model,
                runner=self.answer_runner,
            )
        if path == "/api/manual-text":
            if self.task.running:
                raise ValueError("Another step is still running.")
            pending_id = str(body.get("id") or "")
            text = str(body.get("text") or "")
            if len(text.strip()) < 80:
                raise ValueError("Paste the complete advertisement text.")
            save_manual_draft(self.manual_paths()[3], pending_id, text, str(body.get("employer") or ""))
            if not self.task.start("manual-text", [["complete-link", "--id", pending_id]]):
                raise ValueError("Another step is still running. Your pasted text was saved; try again when it finishes.")
            return {"started": pending_id, "saved": True}
        if path == "/api/prepare-manual":
            if self.task.running:
                raise ValueError("Another step is still running.")
            url = str(body.get("url") or "")
            item = next((row for row in load_manual_results(self.manual_paths()[4]) if _url_key(row.get("url")) == _url_key(url)), None)
            if item is None:
                raise FileNotFoundError("Manual job result not found")
            if not self.task.start("prepare-manual", [["prepare-applications", "--job-url", url, "--limit", "1"]]):
                raise ValueError("Another step is still running.")
            return {"started": url}
        if path == "/api/manual-remove":
            if self.task.running:
                raise ValueError("Another step is still running.")
            url = str(body.get("url") or "")
            if not _url_key(url):
                raise ValueError("This manual job has no valid URL.")
            item = next(
                (row for row in load_manual_results(self.manual_paths()[4]) if _url_key(row.get("url")) == _url_key(url)),
                None,
            )
            if item is None:
                raise FileNotFoundError("Manual job result not found")
            package_matches = [row for row in _queue_rows(output) if any(
                _url_key(row.get(key)) == _url_key(url) for key in ("url", "external_url", "application_url")
            )]
            package_row = next(
                (row for row in package_matches if str(self.manifest(row).get("board") or "").casefold() == "manual"),
                package_matches[0] if package_matches else None,
            )
            decision = "Nicht beworben"
            if package_row is not None:
                application_id = str(package_row["application_id"])
                status = str(package_row["submission_status"])
                item = {**item, "application_id": application_id}
                note = self.sync_tracker(config)  # save the current letter text before any status change
                if note:
                    raise ValueError(note)
                if status == "submitted":
                    decision = "Beworben"
                else:
                    if status not in {"skipped", "expired"}:
                        set_application_status(output, application_id, "skipped")
                        self.record_outcome(application_id, "skipped")
                    note = self.sync_tracker(config)
                    if note:
                        raise ValueError(note)
            if decision == "Nicht beworben":
                from .archive import record_manual_not_applied
                record_manual_not_applied(config.application_archive_dir, item)
            from .manual_links import remove_manual_result
            remove_manual_result(self.manual_paths()[4], url)
            return {"removed": True, "decision": decision, "tracker": str(config.application_archive_dir / TRACKER_NAME)}
        if path == "/api/manual-open":
            url = str(body.get("url") or "")
            if urlsplit(url).scheme not in {"http", "https"}:
                raise ValueError("Invalid job link")
            self.opener([url])
            return {"opened": url}
        if path == "/api/form-state":
            application_id = str(body.get("id") or "")
            state = str(body.get("state") or "")
            if state not in {"needs", "ready"}:
                raise ValueError("Unknown form state")
            self.form_states[application_id] = {
                "id": application_id, "state": state,
                "reason": str(body.get("reason") or "")[:500],
                "filled": max(0, int(body.get("filled") or 0)),
                "attached": max(0, int(body.get("attached") or 0)),
                "open": max(0, int(body.get("open") or 0)),
                "consent": max(0, int(body.get("consent") or 0)),
                "manual_uploads": max(0, int(body.get("manual_uploads") or 0)),
                "upload_warning": str(body.get("upload_warning") or "")[:800],
                "uploads": list(body.get("uploads") or [])[:30],
                "open_labels": [str(value)[:300] for value in list(body.get("open_labels") or [])[:30]],
                "answers_inserted": int(self.form_states.get(application_id, {}).get("answers_inserted") or 0),
            }
            return self.form_states[application_id]
        if path == "/api/answers-inserted":
            application_id = str(body.get("id") or "")
            state = self.form_states.setdefault(application_id, {"id": application_id, "state": "needs"})
            state["answers_inserted"] = max(0, int(body.get("inserted") or 0))
            changed = [str(value)[:300] for value in list(body.get("page_changed") or [])[:30]]
            state["answer_page_changed"] = changed
            if changed:
                state["state"] = "needs"
                state["reason"] = "page changed before Claude's answer; review these fields again"
            return state
        if path == "/api/form-answer-result":
            application_id = str(body.get("id") or "")
            state = self.form_states.setdefault(application_id, {"id": application_id, "state": "needs"})
            rows = []
            changed = {str(value) for value in list(body.get("page_changed") or [])}
            for item in list(body.get("answers") or []):
                if isinstance(item, dict):
                    rows.append({"label": str(item.get("label") or "")[:300], "value": str(item.get("value") or "")[:600],
                                 "reason": str(item.get("source") or "")[:40],
                                 "status": "page changed" if str(item.get("label") or "") in changed else "inserted"})
            for item in list(body.get("suggestions") or []):
                if isinstance(item, dict):
                    rows.append({"label": str(item.get("label") or "")[:300], "value": str(item.get("value") or "")[:600],
                                 "reason": str(item.get("reason") or "")[:200], "status": "suggested"})
            for label, problems in dict(body.get("rejected") or {}).items():
                rows.append({"label": str(label)[:300], "value": "", "reason": "; ".join(map(str, problems))[:600], "status": "rejected"})
            for item in list(body.get("needs_bank") or []):
                if isinstance(item, dict):
                    rows.append({"label": str(item.get("label") or "")[:300], "value": "",
                                 "reason": "add to answer bank", "status": "answer bank"})
            state["answer_rows"] = rows[:30]
            return state
        if path == "/api/extension-pong":
            self.extension_version = str(body.get("version") or "")[:30]
            self.extension_seen = time.monotonic()
            return {"connected": True, "version": self.extension_version}
        if path == "/api/apply-next":
            if self.task.running:
                raise ValueError("Another step is still running.")
            return self.apply_next(str(body.get("done") or ""), str(body.get("outcome") or ""))
        if path == "/api/confirm-apply":
            return self.confirm_apply(str(body.get("id") or ""))
        if path == "/api/requeue-open":
            # Forms marked open from before (for example by the old "Open all"): back into the queue.
            moved = [
                str(row["application_id"]) for row in _queue_rows(output)
                if row["submission_status"] == "opened" and not row["legacy"]
            ]
            for application_id in moved:
                set_application_status(output, application_id, "not_submitted")
                self.confirmed.discard(application_id)
                self.form_states.pop(application_id, None)
            if self.current in moved:
                self.current = ""
            return {"moved": moved, "tracker_note": self.sync_tracker(config) if moved else ""}
        if path == "/api/restore-title-gated":
            return self.restore_title_gated(str(body.get("id") or ""))
        if path == "/api/contact":
            return self.contact(str(body["id"]), str(body.get("email") or ""), str(body.get("link") or ""))
        if path == "/api/answer-bank-save":
            _, save_entry, _ = answer_bank_functions()
            bank = save_entry(config.answer_bank_path, body)
            from .answerbank import load_bank
            reread = load_bank(config.answer_bank_path)
            if str(body.get("kind") or "saved") == "fact":
                key = str(body.get("key") or "")
                if reread.get("facts", {}).get(key) != bank.get("facts", {}).get(key):
                    raise OSError("The answer bank was written but could not be verified. Close Excel or OneDrive preview and try again.")
            return {"saved": True, "message": "Saved", "facts": reread["facts"], "tools": reread["tools"],
                    "ratings": reread["ratings"], "saved_answers": reread["saved"], "ignore": reread.get("ignore", [])}
        if path == "/api/answer-bank-draft":
            from .claude_writer import ClaudeUnavailable
            from .formanswers import chat_answer
            if config.letter_writer != "claude":
                return {"error": "Claude is switched off (letter_writer is standard in config.yaml)."}
            try:
                return chat_answer(
                    config.autofill_extension_dir / "data" / "data.json", str(body.get("id") or ""),
                    str(body.get("label") or ""), (), model=config.claude_model, runner=self.answer_runner,
                    usage_path=config.output_dir / "usage.jsonl", answer_language=config.answer_language,
                    bank_path=config.answer_bank_path,
                )
            except (ClaudeUnavailable, RuntimeError, ValueError) as error:
                return {"error": str(error), "kind": getattr(error, "kind", "error")}
        if path == "/api/answer":
            from .claude_writer import ClaudeUnavailable
            try:
                requested_mode = str(body.get("mode") or "answer")
                if requested_mode not in {"answer", "suggest", "resolve"}:
                    requested_mode = "answer"
                if config.letter_writer != "claude" and requested_mode == "answer":
                    requested_mode = "resolve"
                application_id = str(body["id"])
                confirmed = application_id in self.confirmed
                if requested_mode in {"answer", "suggest"} and not confirmed:
                    if str(body.get("source") or "") == "panel":
                        return {"answers": [], "suggestions": [], "rejected": {}, "yours": [],
                                "reason": "Press Ich bewerbe mich before asking Claude.", "kind": "confirmation"}
                    requested_mode = "resolve"  # profile, bank and learned answers use no tokens
                result = answer_fields(
                    config.autofill_extension_dir / "data" / "data.json", application_id,
                    list(body.get("fields") or []), output / "form_answers.json",
                    model=config.claude_model, runner=self.answer_runner,
                    user_instruction=str(body.get("instruction") or "")[:1200],
                    force=bool(body.get("force")),
                    mode=requested_mode,
                    bank_path=config.answer_bank_path,
                    answer_language=config.answer_language,
                    allow_optional=bool(body.get("allow_optional")),
                )
                self.observations().produced(str(body["id"]), list(result.get("answers") or []))
                return result
            except (ClaudeUnavailable, RuntimeError) as error:
                return {"answers": [], "rejected": {}, "yours": [], "reason": str(error), "kind": getattr(error, "kind", "error")}
        if path == "/api/chat":
            from .claude_writer import ClaudeUnavailable
            from .formanswers import chat_answer
            if config.letter_writer != "claude":
                return {"error": "Claude is switched off (letter_writer is standard in config.yaml)."}
            application_id = str(body.get("id") or "")
            if application_id and application_id not in self.confirmed:
                return {"error": "Press Ich bewerbe mich before asking Claude.", "kind": "confirmation"}
            try:
                history = [item for item in list(body.get("history") or [])[-6:] if isinstance(item, dict)]
                return chat_answer(
                    config.autofill_extension_dir / "data" / "data.json", application_id,
                    str(body.get("message") or ""), history, model=config.claude_model, runner=self.answer_runner,
                    usage_path=config.output_dir / "usage.jsonl",
                    answer_language=config.answer_language,
                    bank_path=config.answer_bank_path,
                )
            except (ClaudeUnavailable, RuntimeError, ValueError) as error:
                return {"error": str(error), "kind": getattr(error, "kind", "error")}
        if path == "/api/observe":
            try:
                records = body.get("fields") if isinstance(body.get("fields"), list) else [body]
                store = self.observations()
                kept = sum(1 for record in records[:50] if isinstance(record, dict)
                           and store.observe(str(body.get("id") or ""), {**record, "page": body.get("page") or record.get("page")}))
                return {"observed": kept}
            except PermissionError:
                # OneDrive locks are queued by safeio. This guard keeps observation relay
                # traffic non-blocking even if a third-party write raises unexpectedly.
                return {"observed": 0, "queued": True}
        if path == "/api/learned-edit":
            from .learning import edit_learned
            bank = edit_learned(
                config.answer_bank_path, str(body.get("key") or ""), str(body.get("group") or "text"),
                answer=body.get("answer"), scope=body.get("scope"), delete=bool(body.get("delete")),
                language=str(body.get("language") or ""),
            )
            return {"saved": True, "learned": bank["learned"]}
        if path == "/api/pending-resolve":
            from .learning import resolve_pending
            bank = resolve_pending(config.answer_bank_path, int(body.get("index", -1)), bool(body.get("keep")))
            return {"saved": True, "pending": bank["pending"], "facts": bank["facts"]}
        if path == "/api/form-log":
            record = {
                "time": datetime_now(), "host": str(body.get("host") or "")[:253],
                "path": str(body.get("path") or "")[:1000], "id": str(body.get("id") or "")[:100],
                "fields": [
                    {
                        **{key: field.get(key) for key in ("label", "kind", "key", "status", "required")},
                        **({"files": [str(name)[:260] for name in list(field.get("files") or [])[:10]]}
                           if field.get("kind") == "file" else {}),
                    }
                    for field in list(body.get("fields") or []) if isinstance(field, dict)
                ],
            }
            append_form_log(output / "form_log.jsonl", record)
            return {"logged": True}
        if path == "/api/claude-check":
            return claude_status()
        if path == "/api/doctor":
            from .doctor import doctor_checks
            checks = doctor_checks(config, self.config_path)
            return {"ok": all(ok for _, ok, _ in checks), "lines": [
                {"status": "OK" if ok else "MISSING", "name": name, "detail": detail}
                for name, ok, detail in checks
            ]}
        if path == "/api/ping":
            self.last_ping, self.bye_at = time.monotonic(), 0.0
            return {"ok": True}
        if path == "/api/bye":
            self.bye_at = time.monotonic()
            return {"ok": True}
        if path == "/api/status":
            application_id = str(body["id"])
            status = str(body["status"])
            if application_id in self.letter_cancellations and status in {"skipped", "not_submitted"}:
                self.letter_cancel_outcomes[application_id] = "skipped" if status == "skipped" else "later"
                self.letter_cancellations[application_id].set()
            manifest = set_application_status(output, application_id, status)
            if status in {"skipped", "not_submitted"}:
                self.confirmed.discard(application_id)
            self.record_outcome(application_id, status)
            return {"status": manifest["submission_status"], "tracker_note": self.sync_tracker(config)}
        if path == "/api/open":
            application_id = str(body["id"])
            row = next(item for item in _queue_rows(output) if item["application_id"] == application_id)
            if row["submission_status"] == "needs_hook":
                self.current = application_id
                via = self.open_route(self.config, row)
                self._start_background(
                    self._top_up_extension, application_id, name=f"jobfilter-export-{application_id}",
                )
                return {"url": row["application_url"]}
            if row["submission_status"] == "not_submitted":
                set_application_status(output, application_id, "approved")
            if row["submission_status"] in {"not_submitted", "approved"}:
                set_application_status(output, application_id, "opened")
            self.current = application_id
            self.open_route(self.config, row)
            return {"url": row["application_url"]}
        if path == "/api/tracker":
            from .osutil import open_path
            note = self.sync_tracker(config)
            tracker = config.application_archive_dir / TRACKER_NAME
            open_path(tracker)
            return {"opened": str(tracker), "tracker_note": note}
        if path == "/api/folder":
            from .osutil import open_path
            folder = self.package(str(body["id"]))["folder"]
            open_path(folder)
            return {"opened": folder}
        raise FileNotFoundError(path)

    def should_stop(self, now: float) -> bool:
        """True once the app window was closed (or went silent) and nothing is running."""
        if self.task.running or not self.last_ping:
            return False
        if self.bye_at and now - self.bye_at > 8 and self.last_ping <= self.bye_at:
            return True
        return now - self.last_ping > IDLE_SECONDS

    def get(self, path: str, query: dict[str, list[str]]) -> dict[str, object]:
        if path == "/api/state":
            return self.state()
        if path == "/api/package":
            return self.package(query.get("id", [""])[0])
        raise FileNotFoundError(path)


def _hook_source(folder: Path) -> str:
    try:
        return str(json.loads((folder / "hook.json").read_text(encoding="utf-8")).get("source", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def datetime_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def append_form_log(path: str | Path, record: dict[str, object], *, limit: int = FORM_LOG_LIMIT) -> None:
    """Append one value-free JSON line; app startup performs the bounded trim."""
    from .safeio import append_jsonl as safe_append
    safe_append(path, record, compact=True)


def edge_path() -> str | None:
    """Configured Chromium browser; retained as a compatibility alias."""
    from .osutil import browser_path
    return browser_path("auto")


def open_urls(urls: list[str]) -> None:
    """Open pages as tabs of the normal Edge window (the default browser when Edge is missing)."""
    edge = edge_path()
    if edge:
        subprocess.Popen([edge, *urls], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    for url in urls:
        webbrowser.open(url)


def open_file(path: Path) -> None:
    """Open a file with the operating system's associated program."""
    from .osutil import open_path
    open_path(path)


def app_icon_ico() -> bytes:
    """Wrap the extension's PNG in an ICO container without duplicating the artwork."""
    png = APP_ICON.read_bytes()
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 128, 128, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def ensure_app_shortcut(edge: str, url: str, *, runner=subprocess.run) -> Path | None:
    """Create the Edge app shortcut whose icon Windows uses for the taskbar button."""
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        return None
    folder = Path(os.environ["LOCALAPPDATA"]) / "jobfilter"
    folder.mkdir(parents=True, exist_ok=True)
    icon = folder / "jobfilter.ico"
    wanted = app_icon_ico()
    if not icon.exists() or icon.read_bytes() != wanted:
        temporary = icon.with_suffix(".tmp")
        temporary.write_bytes(wanted)
        temporary.replace(icon)
    shortcut = folder / "jobfilter.lnk"
    script = (
        "$w=New-Object -ComObject WScript.Shell;"
        "$s=$w.CreateShortcut($env:JF_SHORTCUT);"
        "$s.TargetPath=$env:JF_EDGE;$s.Arguments=$env:JF_ARGUMENTS;"
        "$s.WorkingDirectory=$env:JF_WORKDIR;$s.IconLocation=$env:JF_ICON;"
        "$s.Description='jobfilter';$s.Save()"
    )
    environment = dict(
        os.environ, JF_SHORTCUT=str(shortcut), JF_EDGE=edge,
        JF_ARGUMENTS=f'--app="{url}" --window-size=1120,920',
        JF_WORKDIR=str(Path(edge).parent), JF_ICON=f"{icon},0",
    )
    done = runner(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        stdin=subprocess.DEVNULL, capture_output=True, creationflags=NO_WINDOW, env=environment,
    )
    return shortcut if getattr(done, "returncode", 1) == 0 and shortcut.is_file() else None


def open_window(url: str) -> None:
    """The app in a window of its own: no tabs, no address bar."""
    edge = edge_path()
    if edge:
        shortcut = ensure_app_shortcut(edge, url)
        if shortcut:
            from .osutil import open_path
            open_path(shortcut)
            return
        subprocess.Popen([edge, f"--app={url}", "--window-size=1120,920"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def running_version(url: str) -> str | None:
    """Code fingerprint of a jobfilter app already answering at ``url`` ("" for an older app), else None."""
    try:
        with urllib.request.urlopen(url + "api/hello", timeout=2) as response:  # noqa: S310 - fixed local address
            hello = json.loads(response.read())
    except (OSError, ValueError):
        return None
    if not isinstance(hello, dict) or hello.get("app") != "jobfilter":
        return None
    return str(hello.get("version") or "")


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep the console quiet
            return

        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, data: object) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in {"127.0.0.1", "localhost"}

        def _guarded(self, action) -> None:
            try:
                self._json(HTTPStatus.OK, action())
            except (FileNotFoundError, StopIteration) as error:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(error) or "not found"})
            except (ValueError, KeyError, RuntimeError, OSError) as error:
                print(f"BAD_REQUEST {self.path}: {type(error).__name__}: {error}", flush=True)
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_GET(self) -> None:  # noqa: N802
            if not self._local():
                self._json(HTTPStatus.FORBIDDEN, {"error": "local use only"})
                return
            parts = urlsplit(self.path)
            if parts.path == "/api/hello":
                self._json(HTTPStatus.OK, {"app": "jobfilter", "version": APP_VERSION})
                return
            if parts.path in {"/favicon.ico", "/app-icon.ico"}:
                self._send(HTTPStatus.OK, app_icon_ico(), "image/x-icon")
                return
            if parts.path == "/app-icon.png":
                self._send(HTTPStatus.OK, APP_ICON.read_bytes(), "image/png")
                return
            if parts.path == "/manifest.webmanifest":
                self._json(HTTPStatus.OK, {
                    "name": "jobfilter", "short_name": "jobfilter", "start_url": "/", "display": "standalone",
                    "background_color": "#1a1f26", "theme_color": "#1a1f26",
                    "icons": [{"src": "/app-icon.png", "sizes": "128x128", "type": "image/png"}],
                })
                return
            if parts.path == "/":
                page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.headers.get("X-JF-Token") != app.token:
                self._json(HTTPStatus.FORBIDDEN, {"error": "missing token"})
                return
            self._guarded(lambda: app.get(parts.path, parse_qs(parts.query)))

        def do_POST(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            token = self.headers.get("X-JF-Token")
            if parts.path == "/api/bye":  # sent while the window closes, where no header can be set
                token = parse_qs(parts.query).get("t", [""])[0]
            if not self._local() or token != app.token:
                self._json(HTTPStatus.FORBIDDEN, {"error": "missing token"})
                return
            length = min(int(self.headers.get("Content-Length") or 0), 2_000_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            self._guarded(lambda: app.post(parts.path, body if isinstance(body, dict) else {}))

    return Handler


def serve(config_path: str | Path, *, port: int = 8791, open_browser: bool = True) -> None:
    app = App(config_path)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    except OSError:
        running = f"http://127.0.0.1:{port}/"
        if running_version(running) == APP_VERSION:  # started twice: show the app that is already there
            print(f"jobfilter is already running: {running}")
            if open_browser:
                open_window(running)
            return
        # The port is taken by something else, or by an app still running older code: use a free port.
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"jobfilter app: {url}")
    print("Close the jobfilter window to stop the app.")
    if open_browser:
        open_window(url)

        def watch() -> None:
            while not app.should_stop(time.monotonic()):
                time.sleep(4)
            server.shutdown()

        threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.stop()
