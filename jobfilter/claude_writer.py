"""Cover-letter hooks written by Claude through the user's own Claude plan.

One request per run, sent with the Claude Code command line (``claude -p``) that is signed in with
the plan: no API key, no per-token bill. The request is kept small on purpose: the smallest model,
no tools, none of the user's Claude settings, plugins or CLAUDE.md files, a bounded full job description, and
each proof text only once. Before the first request ``claude auth status`` (no tokens) confirms the
sign-in is the Claude plan. Every answer goes through the same checks as a pasted one; a rejected
text is replaced by the fixed standard text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping

from .applications import load_paragraph_bank
from .hooks import RULES, _facts, _waiting, all_evidence, check_hook, import_hooks
from .metrics import append_jsonl, usage_record

AD_CHARS = 8000
BATCH = 6
MAX_REQUESTS = 4  # per run; jobs beyond 24 simply wait for the next run
TIMEOUT = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLAUDE_EXE = "claude.exe" if os.name == "nt" else "claude"
TERMINAL = "PowerShell" if os.name == "nt" else "a terminal"
if os.name == "nt":
    INSTALL_COMMAND = "irm https://claude.ai/install.ps1 | iex"
    DEFAULT_LOGIN = '& "$env:USERPROFILE\\.local\\bin\\claude.exe" auth login'
else:
    INSTALL_COMMAND = "curl -fsSL https://claude.ai/install.sh | bash"
    DEFAULT_LOGIN = "~/.local/bin/claude auth login"
NOT_INSTALLED = (
    "Claude Code's command line is not installed (the Code tab of the desktop app does not count). "
    f"In {TERMINAL} run: {INSTALL_COMMAND}  Then sign in: {DEFAULT_LOGIN}"
)
PLAN_METHODS = ("claude.ai", "oauth_token")  # sign-ins that use the Claude plan, not per-token billing
SYSTEM = "Du schreibst Textbausteine fuer deutsche Bewerbungsanschreiben und antwortest nur mit JSON."
# A key in the environment would move the request from the plan to paid API billing.
PAID_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
HOOK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"hooks": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 100},
            "intro": {"type": "string", "maxLength": 600},
            "company_paragraph": {"type": "string", "maxLength": 1400},
        }, "required": ["id", "intro", "company_paragraph"],
    }}}, "required": ["hooks"],
}
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"answers": {"type": "array", "maxItems": 30, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "maxLength": 100},
            "value": {"type": "string", "maxLength": 600},
            "reason": {"type": "string", "maxLength": 200},
        }, "required": ["id", "value"],
    }}}, "required": ["answers"],
}
GENDER_TAG = re.compile(r"\s*[(\[]\s*(?:[mwfdxi]\s*[/|,]\s*){1,3}[mwfdxi]\s*[)\]]", re.IGNORECASE)


class ClaudeUnavailable(Exception):
    """Claude could not answer. ``kind`` is not_installed, sign_in, limit or error."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def find_claude(home: Path | None = None) -> str | None:
    """The native Claude Code program: on PATH, or where its installer puts it when PATH was not updated.

    On Windows only claude.exe counts: npm's claude.cmd would run through cmd.exe, which can mangle arguments.
    """
    found = shutil.which(CLAUDE_EXE)
    if found:
        return found
    default = (home or Path.home()) / ".local" / "bin" / CLAUDE_EXE
    if default.is_file():
        return str(default)
    if home is None:
        from .osutil import claude_candidates
        found_elsewhere = claude_candidates()
        return found_elsewhere[0] if found_elsewhere else None
    return None


def login_command(executable: str) -> str:
    """The sign-in command as the user types it, whether or not Claude is on PATH."""
    if shutil.which(CLAUDE_EXE) == executable:
        return "claude auth login"
    return f'& "{executable}" auth login' if os.name == "nt" else f'"{executable}" auth login'


def _plan_env() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in PAID_KEYS}
    environment["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"  # no personal CLAUDE.md in the request
    # Claude Code 2.1.325's installed changelog documents this value as the
    # environment equivalent of ``--thinking disabled``.
    environment["MAX_THINKING_TOKENS"] = "0"
    return environment


def _work_dir() -> str:
    """An empty folder to run Claude in, so no project files or settings are picked up."""
    folder = Path(tempfile.gettempdir()) / "jobfilter_claude"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def claude_status(runner: Callable[..., object] = subprocess.run) -> dict[str, object]:
    """Is Claude installed and signed in with the plan? Asks ``claude auth status``, which costs no tokens.

    ``signed_in`` is None when the answer could not be read; the request itself then decides.
    """
    executable = find_claude()
    if not executable:
        return {"installed": False, "path": "", "signed_in": False, "method": "", "plan": False, "login": DEFAULT_LOGIN}
    status: dict[str, object] = {
        "installed": True, "path": executable, "signed_in": None, "method": "", "plan": False,
        "login": login_command(executable),
    }
    try:
        done = runner(
            [executable, "auth", "status"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=_plan_env(), timeout=60, creationflags=NO_WINDOW,
        )
        info = json.loads(getattr(done, "stdout", "") or "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return status
    if not isinstance(info, dict):
        return status
    method = str(info.get("authMethod") or "")
    status["method"] = method
    status["signed_in"] = getattr(done, "returncode", 1) == 0 and info.get("loggedIn") is not False and method not in ("", "none")
    status["plan"] = bool(status["signed_in"]) and method in PLAN_METHODS
    return status


def check_plan(runner: Callable[..., object] = subprocess.run) -> None:
    """Stop before any request unless Claude is installed and signed in with the Claude plan."""
    status = claude_status(runner)
    if not status["installed"]:
        raise ClaudeUnavailable("not_installed", NOT_INSTALLED)
    if status["signed_in"] is False:
        raise ClaudeUnavailable("sign_in", f"Claude is not signed in. In {TERMINAL} run: {status['login']}")
    if status["signed_in"] and not status["plan"]:
        raise ClaudeUnavailable(
            "paid",
            f"Claude Code is signed in with '{status['method']}', not your Claude plan, so requests may be billed "
            f"per token. Sign in with your Claude account: {status['login']}",
        )


def lean_prompt(
    output_dir: str | Path, bank_path: str | Path, *, limit: int = BATCH, skip: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Prompt for up to ``limit`` waiting packages; each proof text appears once, however many jobs use it."""
    bank = load_paragraph_bank(bank_path)
    proofs: dict[str, str] = {}
    blocks, ids = [], []
    waiting = [item for item in _waiting(Path(output_dir)) if str(item[1]["application_id"]).lower() not in (skip or set())]
    for folder, manifest in waiting[:limit]:
        facts = _facts(folder, manifest, bank)
        family = str(manifest.get("primary_cv_family", ""))[:3] or "B00"
        proofs.setdefault(family, facts["proof"])
        ids.append(facts["id"])
        blocks.append("\n".join([
            f"### id: {facts['id']}",
            f"Stelle: {facts['title']}",
            f"Arbeitgeber: {facts['employer']}",
            f"Sprachanforderung: {facts['language'] or 'keine besondere'}",
            f"Belege: {family}",
            f"Anzeige:\n{facts['ad'][:AD_CHARS]}",
            f"Verifizierte Bewerberfakten: {facts['applicant']}",
        ]))
    if not ids:
        return "", []
    evidence = [f"[{family}]\n{text}" for family, text in proofs.items()]
    further = [f"[{family}]\n" + "\n".join(pair) for family, pair in bank.items() if family not in proofs]
    return "\n\n".join([
        "Schreibe je Stelle den Einleitungssatz und einen Absatz zur Passung.",
        RULES,
        "BELEGE (stehen bereits im Anschreiben der Stellen mit dieser Kennung; nicht wiederholen)",
        *evidence,
        "WEITERE BELEGE (ebenfalls belegte Erfahrung; für den Passungsabsatz nutzen)",
        *further,
        f"STELLEN ({len(ids)})",
        *blocks,
    ]), ids


def ask_claude(
    prompt: str, *, model: str = "haiku", runner: Callable[..., object] = subprocess.run,
    usage_path: str | Path | None = None, purpose: str = "letters", jobs: int = 1,
    schema: Mapping[str, object] | None = None, fields: int | None = None,
    return_usage: bool = False, structured_output: bool = True, system_prompt: str | None = None,
) -> str | tuple[str, dict[str, object]]:
    """Send one prompt to Claude through the signed-in plan and return the answer text."""
    from . import applicant
    prompt = applicant.apply(prompt)
    system_prompt = applicant.apply(system_prompt) if system_prompt else system_prompt
    executable = find_claude()
    if not executable:
        raise ClaudeUnavailable("not_installed", NOT_INSTALLED)
    # Nothing but the prompt: no tools, no MCP servers, no skills, no user settings, plugins or hooks.
    schema_data = schema or (ANSWER_SCHEMA if purpose in {"form", "suggest"} else HOOK_SCHEMA)
    command = [
        executable, "-p", "--model", model, "--system-prompt", system_prompt or SYSTEM, "--tools", "",
        "--strict-mcp-config", "--setting-sources", "project", "--no-session-persistence",
        "--disable-slash-commands", "--thinking", "disabled", "--effort", "low", "--prompt-suggestions", "false",
        "--permission-prompts", "none",
    ]
    if structured_output:
        command.extend(["--json-schema", json.dumps(schema_data, ensure_ascii=False, separators=(",", ":"))])
    command.extend(["--output-format", "json"])
    try:
        done = runner(
            command, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=_work_dir(), env=_plan_env(), timeout=TIMEOUT, creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise ClaudeUnavailable("error", f"Claude did not answer within {TIMEOUT} seconds.") from error
    except OSError as error:
        raise ClaudeUnavailable("error", f"Claude could not be started: {error}") from error
    output = (getattr(done, "stdout", "") or "").strip()
    try:
        answer = json.loads(output)
    except ValueError:
        answer = {"is_error": True, "result": output or (getattr(done, "stderr", "") or "").strip()}
    text = str(answer.get("result") or "") if isinstance(answer, dict) else ""
    if not isinstance(answer, dict) or answer.get("is_error") or getattr(done, "returncode", 0):
        lowered = text.casefold()
        if any(word in lowered for word in ("authentication", "log in", "login", "sign in", "api key", "oauth")):
            raise ClaudeUnavailable(
                "sign_in",
                f"Claude could not sign in ({text[:80]}). In {TERMINAL} run: {login_command(executable)}  "
                "If you already did, check the internet connection and try again.",
            )
        if "limit" in lowered:
            raise ClaudeUnavailable("limit", f"Your Claude plan's usage limit is reached: {text[:200]}")
        raise ClaudeUnavailable("error", f"Claude answered with an error: {text[:300] or 'no details'}")
    record = usage_record(
        answer, purpose=purpose, jobs=jobs, model=model, fields=fields,
        chars_out=len(text),
    )
    if usage_path:
        append_jsonl(usage_path, record)
    return (text, record) if return_usage else text


def ask_openai(
    prompt: str, *, model: str, session=None, api_key: str | None = None,
    usage_path: str | Path | None = None, purpose: str = "letters", jobs: int = 1,
) -> str:
    """Use the Responses API over HTTPS; the key is never read from a file or logged."""
    from . import applicant
    prompt = applicant.apply(prompt)
    key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ClaudeUnavailable("sign_in", "OPENAI_API_KEY is not set. Standard letter text was used.")
    if session is None:
        import requests
        session = requests
    response = session.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "instructions": SYSTEM, "input": prompt}, timeout=TIMEOUT,
    )
    response.raise_for_status()
    answer = response.json()
    text = str(answer.get("output_text") or "")
    if not text:
        text = "".join(
            str(part.get("text") or "")
            for item in answer.get("output", []) if isinstance(item, dict)
            for part in item.get("content", []) if isinstance(part, dict)
        )
    if usage_path:
        append_jsonl(usage_path, usage_record(
            answer, purpose=purpose, jobs=jobs, model=model, fields=jobs, chars_out=len(text),
        ))
    if not text:
        raise ClaudeUnavailable("error", "OpenAI returned no text. Standard letter text was used.")
    return text


def standard_hook(title: str, employer: str) -> dict[str, str]:
    """Fixed opening and fit paragraph: only facts that hold for every application."""
    job = re.sub(r"\s+[–—-]\s+", ", ", GENDER_TAG.sub("", title))
    job = " ".join(re.sub(r"[\[\]<>–—]", " ", job).split()).strip(" ,")
    position = f"die Position {job}" if job and len(job.split()) <= 16 else "die ausgeschriebene Position"
    where = f" bei {employer}" if employer and len(employer.split()) <= 8 else ""
    return {
        "intro": (
            f"mit Interesse habe ich Ihre Ausschreibung für {position} gelesen. "
            f"Hiermit bewerbe ich mich{where} auf diese Stelle."
        ),
        "company_paragraph": (
            "Die in der Anzeige beschriebenen Aufgaben und Anforderungen habe ich sorgfältig geprüft. "
            "Meine dafür relevanten, belegten Erfahrungen sind in den folgenden Absätzen dargestellt. "
            "Sie zeigen, welche Arbeitsweisen und Kenntnisse ich für die Position einbringen kann. "
            "Die dort beschriebenen Beispiele bilden zugleich die Grundlage für meine Bewerbung. "
            "Gern erläutere ich Ihnen im Gespräch, wie ich diese Erfahrungen auf Ihre Aufgaben übertrage "
            "und Ihr Team dabei verlässlich unterstütze."
        ),
    }


def _mark(folder: Path, source: str) -> None:
    path = folder / "hook.json"
    hook = json.loads(path.read_text(encoding="utf-8"))
    hook["source"] = source
    path.write_text(json.dumps(hook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_hook_on_demand(
    output_dir: str | Path,
    bank_path: str | Path,
    application_id: str,
    *,
    writer: str = "claude",
    model: str = "haiku",
    runner: Callable[..., object] = subprocess.run,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, object]:
    """Write one checked hook, using a compact plain-text response.

    The answer has one fixed ``###`` marker between the two letter parts. Claude
    gets one correction attempt. A request failure or two rejected drafts use the
    deterministic standard hook immediately. Cancellation never writes a file.
    """
    destination = Path(output_dir)
    bank = load_paragraph_bank(bank_path)
    found = next(((folder, manifest) for folder, manifest in _waiting(destination)
                  if str(manifest.get("application_id")) == application_id), None)
    if found is None:
        return {"source": "existing", "reason": "letter already exists", "requests": 0, "discarded": False}
    folder, manifest = found
    facts = _facts(folder, manifest, bank)
    facts_text = " ".join((facts["title"], facts["employer"], facts["ad"], all_evidence(bank), facts["applicant"]))
    base = "\n\n".join([
        "Schreibe nur für diese Stelle den Einleitungssatz und den Absatz zur Passung.",
        RULES.split("Antworte NUR", 1)[0].strip(),
        "Antworte nur mit dem Einleitungssatz, dann einer eigenen Zeile ###, dann dem Passungsabsatz. Keine JSON, keine Erklärung.",
        f"Verifizierte Bewerberfakten: {facts['applicant']}", f"Stelle: {facts['title']}", f"Arbeitgeber: {facts['employer']}",
        f"Anzeige:\n{facts['ad'][:AD_CHARS]}",
        f"BELEGE (stehen bereits im Anschreiben; nicht wiederholen):\n{facts['proof']}",
        f"WEITERE BELEGE:\n{all_evidence(bank)}",
    ])
    rejected: list[str] = []
    requests = 0
    if writer == "claude":
        try:
            check_plan(runner)
            for attempt in range(2):
                if cancelled():
                    return {"source": "discarded", "reason": "application was set aside", "requests": requests, "discarded": True}
                correction = "" if not rejected else "\n\nKorrigiere diese Fehler des vorigen Entwurfs: " + "; ".join(rejected)
                reply = str(ask_claude(
                    base + correction, model=model, runner=runner,
                    usage_path=destination.parent / "usage.jsonl", purpose="letter", jobs=1,
                    fields=1, structured_output=False,
                    system_prompt="Schreibe zwei kurze, belegte Textbausteine auf Deutsch. Antworte nur im verlangten Format.",
                ))
                requests += 1
                parts = re.split(r"(?m)^\s*###\s*$", re.sub(r"```(?:text)?", "", reply, flags=re.I).strip())
                candidate = {
                    "intro": " ".join(parts[0].split()) if len(parts) == 2 else "",
                    "company_paragraph": " ".join(parts[1].split()) if len(parts) == 2 else "",
                }
                rejected = check_hook(candidate, facts_text, facts["proof"])
                if not rejected:
                    if cancelled():
                        return {"source": "discarded", "reason": "application was set aside", "requests": requests, "discarded": True}
                    candidate["source"] = "claude"
                    (folder / "hook.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return {"source": "claude", "reason": "", "requests": requests, "discarded": False}
        except ClaudeUnavailable as error:
            rejected = [str(error)]
        except (OSError, ValueError) as error:
            rejected = [str(error)]
    elif writer != "standard":
        rejected = [f"unsupported letter writer: {writer}"]
    if cancelled():
        return {"source": "discarded", "reason": "application was set aside", "requests": requests, "discarded": True}
    candidate = standard_hook(facts["title"], facts["employer"])
    candidate["source"] = "standard"
    (folder / "hook.json").write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "source": "standard", "reason": "; ".join(rejected) or "standard letter requested",
        "requests": requests, "discarded": False,
    }


def write_hooks(
    output_dir: str | Path, bank_path: str | Path, *, writer: str = "claude", model: str = "haiku",
    runner: Callable[..., object] = subprocess.run, session=None,
) -> dict[str, object]:
    """Give every waiting package its hook.json. Returns what Claude wrote, what got the standard text, and why."""
    destination = Path(output_dir)
    bank = load_paragraph_bank(bank_path)
    report: dict[str, object] = {"claude": [], "standard": [], "rejected": {}, "left": [], "requests": 0}
    seen: set[str] = set()
    if writer == "openai":
        prompt, ids = lean_prompt(destination, bank_path)
        if not ids or not os.environ.get("OPENAI_API_KEY"):
            _standard(destination, bank, report, only=None)
            report["reason"] = "OPENAI_API_KEY is not set; standard letter text was used."
            return report
        folders = {str(manifest["application_id"]).lower(): folder for folder, manifest in _waiting(destination)}
        try:
            reply = ask_openai(
                prompt, model=model, session=session, usage_path=destination.parent / "usage.jsonl",
                purpose="letters", jobs=len(ids),
            )
            report["requests"] = 1
            result = import_hooks(destination, bank_path, reply)
        except Exception:
            result = {"written": [], "rejected": {}}
            report["reason"] = "OpenAI could not answer; standard letter text was used."
        report["claude"] += result["written"]
        report["rejected"].update(result["rejected"])
        _standard(destination, bank, report, only=None)
        for application_id in result["written"]:
            _mark(folders[application_id], "openai")
        return report
    attempts: dict[str, int] = {}
    while writer == "claude" and report["requests"] < MAX_REQUESTS:
        done = {key for key, count in attempts.items() if count >= 2}
        prompt, ids = lean_prompt(destination, bank_path, skip=done)
        if not ids:
            break
        if not report["requests"]:
            check_plan(runner)  # once per run, before the first request
        for application_id in ids:
            attempts[application_id.lower()] = attempts.get(application_id.lower(), 0) + 1
        folders = {str(manifest["application_id"]).lower(): folder for folder, manifest in _waiting(destination)}
        reply = ask_claude(
            prompt, model=model, runner=runner, usage_path=destination.parent / "usage.jsonl",
            purpose="letters", jobs=len(ids), structured_output=False,
        )
        report["requests"] += 1
        try:
            result = import_hooks(destination, bank_path, reply)
        except ValueError as error:
            result = {"written": [], "rejected": {application_id: [str(error)] for application_id in ids}}
        report["claude"] += result["written"]
        report["rejected"].update(result["rejected"])
        for application_id in result["written"]:
            _mark(folders[application_id], "claude")
            report["rejected"].pop(application_id, None)
    if writer == "claude":
        # Only jobs Claude failed twice get the standard text; jobs never sent wait for the next run.
        failed = {key for key, count in attempts.items() if count >= 2}
        if failed:
            _standard(destination, bank, report, only=failed)
        report["waiting"] = [str(manifest["application_id"]) for _, manifest in _waiting(destination)]
    if writer != "claude":
        _standard(destination, bank, report, only=None)
    return report


def rewrite_hook(
    output_dir: str | Path, bank_path: str | Path, application_id: str, *, model: str = "haiku",
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Ask for one replacement hook and return it for review without writing any file."""
    destination = Path(output_dir)
    bank = load_paragraph_bank(bank_path)
    found = next(((folder, manifest) for folder, manifest in [
        (path.parent, json.loads(path.read_text(encoding="utf-8-sig")))
        for path in destination.glob("*/application.json")
    ] if str(manifest.get("application_id")) == application_id), None)
    if found is None:
        raise FileNotFoundError(f"Application ID not found: {application_id}")
    folder, manifest = found
    facts = _facts(folder, manifest, bank)
    prompt = "\n\n".join([
        "Schreibe nur für diese Stelle einen neuen Einleitungssatz und einen Absatz zur Passung.", RULES,
        f"Verifizierte Bewerberfakten: {facts['applicant']}", f"Stelle: {facts['title']}", f"Arbeitgeber: {facts['employer']}",
        f"Anzeige:\n{facts['ad'][:AD_CHARS]}",
        f"BELEGE (stehen bereits im Anschreiben; nicht wiederholen):\n{facts['proof']}",
        f"WEITERE BELEGE:\n{all_evidence(bank)}",
    ])
    check_plan(runner)
    payload = __import__("jobfilter.hooks", fromlist=["extract_json"]).extract_json(ask_claude(
        prompt, model=model, runner=runner, usage_path=destination.parent / "usage.jsonl",
        purpose="rewrite", jobs=1, structured_output=False,
    ))
    item = next((value for value in payload["hooks"] if isinstance(value, dict)), {})
    candidate = {key: " ".join(str(item.get(key) or "").split()) for key in ("intro", "company_paragraph")}
    warnings = check_hook(
        candidate, " ".join((facts["title"], facts["employer"], facts["ad"], all_evidence(bank), facts["applicant"])), facts["proof"],
    )
    return {**candidate, "warnings": warnings}


def _standard(destination: Path, bank, report: dict[str, object], *, only: set[str] | None) -> None:
    for folder, manifest in _waiting(destination):
        application_id = str(manifest["application_id"])
        if only is not None and application_id not in only:
            continue
        facts = _facts(folder, manifest, bank)
        hook = standard_hook(facts["title"], facts["employer"])
        problems = check_hook(hook, " ".join([facts["title"], facts["employer"], facts["ad"], facts["proof"], facts["applicant"]]))
        if problems:
            report["left"].append(application_id)
            report["rejected"].setdefault(application_id, []).extend(f"standard text: {problem}" for problem in problems)
            continue
        hook["source"] = "standard"
        (folder / "hook.json").write_text(json.dumps(hook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["standard"].append(application_id)
