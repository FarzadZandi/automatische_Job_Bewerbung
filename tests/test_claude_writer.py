from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobfilter import claude_writer
from jobfilter.__main__ import build_parser
from jobfilter.claude_writer import (
    ClaudeUnavailable, ask_claude, ask_openai, check_plan, claude_status, find_claude, lean_prompt, standard_hook,
    rewrite_hook, write_hook_on_demand, write_hooks,
)
from jobfilter.hooks import check_hook
from tests.test_hooks import INTRO, PARAGRAPH, _bank, _package


def _reply(hooks, **extra):
    payload = {"is_error": False, "result": "Hier:\n" + json.dumps({"hooks": hooks}, ensure_ascii=False), **extra}
    return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)


def _runner(hooks=(), *, auth=("claude.ai", 0), calls=None):
    """Stands in for the Claude command line: answers 'auth status' and '-p' requests."""
    def run(command, **options):
        if command[1:3] == ["auth", "status"]:
            method, code = auth
            return SimpleNamespace(stdout=json.dumps({"loggedIn": code == 0, "authMethod": method}), stderr="", returncode=code)
        if calls is not None:
            calls.append(options["input"])
        return _reply(list(hooks))
    return run


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(claude_writer, "find_claude", lambda: "claude")


def test_lean_prompt_sends_each_proof_once_and_the_bounded_full_ad(tmp_path):
    out = tmp_path / "out"
    first, second = _package(out, "aaaaaaaaaaaa"), _package(out, "bbbbbbbbbbbb")
    brief = json.loads((first / "letter_brief.json").read_text(encoding="utf-8"))
    brief["ad_text"] = "SHORT EXCERPT"
    brief["ad_text_full"] = "A" * 1600 + " END_BEYOND_1500 " + "B" * 7000 + " OUTSIDE_CAP"
    (first / "letter_brief.json").write_text(json.dumps(brief), encoding="utf-8")
    prompt, ids = lean_prompt(out, _bank(tmp_path / "bank.yaml"))
    assert ids == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    own = prompt.split("\n\nWEITERE BELEGE (", 1)[0]
    assert own.count("2.500 Profile") == 1 and prompt.count("Belege: B06") == 2
    assert "END_BEYOND_1500" in prompt and "OUTSIDE_CAP" not in prompt and "SHORT EXCERPT" not in prompt
    assert "Antworte NUR mit JSON" in prompt and second.name not in prompt


def test_on_demand_and_rewrite_prompts_use_the_bounded_full_ad(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    folder = _package(out, "aaaaaaaaaaaa")
    brief_path = folder / "letter_brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["ad_text"] = "SHORT EXCERPT"
    brief["ad_text_full"] = "A" * 1600 + " END_BEYOND_1500 " + "B" * 7000 + " OUTSIDE_CAP"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    prompts = []

    def rejected(command, **options):
        if command[1:3] == ["auth", "status"]:
            return _runner()(command, **options)
        prompts.append(options["input"])
        return SimpleNamespace(stdout=json.dumps({"is_error": False, "result": "too short\n###\ntoo short"}), stderr="", returncode=0)

    write_hook_on_demand(out, bank, "aaaaaaaaaaaa", runner=rejected)
    assert prompts and all("END_BEYOND_1500" in prompt and "OUTSIDE_CAP" not in prompt for prompt in prompts)

    prompts.clear()
    def rewritten(command, **options):
        if command[1:3] == ["auth", "status"]:
            return _runner()(command, **options)
        prompts.append(options["input"])
        result = json.dumps({"hooks": [{"id": "aaaaaaaaaaaa", "intro": INTRO, "company_paragraph": PARAGRAPH}]})
        return SimpleNamespace(stdout=json.dumps({"is_error": False, "result": result}), stderr="", returncode=0)

    rewrite_hook(out, bank, "aaaaaaaaaaaa", runner=rewritten)
    assert len(prompts) == 1 and "END_BEYOND_1500" in prompts[0] and "OUTSIDE_CAP" not in prompts[0]


def test_claude_is_called_once_on_the_plan_with_no_tools_and_no_api_key(tmp_path, installed, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-used")
    calls = []

    def runner(command, **options):
        calls.append((command, options))
        assert Path(options["cwd"]).is_dir() and list(Path(options["cwd"]).iterdir()) == []
        return _reply([])

    ask_claude("Prompt", model="haiku", runner=runner)
    command, options = calls[0]
    assert command[:4] == ["claude", "-p", "--model", "haiku"]
    assert command[command.index("--tools") + 1] == "" and "--no-session-persistence" in command
    # None of the user's own Claude settings, plugins, hooks or CLAUDE.md files go into the request.
    assert command[command.index("--setting-sources") + 1] == "project" and "--strict-mcp-config" in command
    assert options["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert options["env"]["MAX_THINKING_TOKENS"] == "0"
    assert command[command.index("--thinking") + 1] == "disabled"
    assert command[command.index("--effort") + 1] == "low"
    schema = json.loads(command[command.index("--json-schema") + 1])
    assert schema["properties"]["hooks"]["maxItems"] == 12
    assert "ANTHROPIC_API_KEY" not in options["env"] and options["input"] == "Prompt"


def test_usage_is_logged_from_claude_json(tmp_path, installed):
    path = tmp_path / "usage.jsonl"
    answer = _reply([], usage={"input_tokens": 12, "output_tokens": 4, "cache_read_input_tokens": 3},
                    total_cost_usd=0.01, duration_ms=250)
    ask_claude("Prompt", runner=lambda command, **options: answer, usage_path=path, purpose="form", jobs=1)
    row = json.loads(path.read_text(encoding="utf-8"))
    assert (row["purpose"], row["input_tokens"], row["output_tokens"], row["cache_read_input_tokens"]) == ("form", 12, 4, 3)
    assert row["fields"] == 1 and row["chars_out"] > 0


def test_plain_claude_reply_keeps_usage_wrapper_but_removes_answer_schema(installed):
    calls = []
    answer = SimpleNamespace(
        stdout=json.dumps({"is_error": False, "result": "Plain answer", "usage": {"output_tokens": 3}}),
        stderr="", returncode=0,
    )
    result = ask_claude(
        "Prompt", runner=lambda command, **options: calls.append((command, options)) or answer,
        purpose="form", structured_output=False, system_prompt="Short safe system",
    )
    command, options = calls[0]
    assert result == "Plain answer" and "--json-schema" not in command
    assert command[command.index("--system-prompt") + 1] == "Short safe system"
    assert command[command.index("--output-format") + 1] == "json"
    assert options["input"] == "Prompt"


def test_openai_responses_api_uses_environment_key_and_fake_http_only(tmp_path, monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self): return None
        def json(self):
            return {"output_text": "answer", "usage": {"input_tokens": 9, "output_tokens": 2}}
    class Session:
        def post(self, url, **kwargs): calls.append((url, kwargs)); return Response()
    monkeypatch.setenv("OPENAI_API_KEY", "secret-only-in-environment")
    usage = tmp_path / "usage.jsonl"
    assert ask_openai("Prompt", model="gpt-test", session=Session(), usage_path=usage, jobs=2) == "answer"
    assert calls[0][0].endswith("/responses") and calls[0][1]["json"]["model"] == "gpt-test"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret-only-in-environment"
    assert "secret-only-in-environment" not in usage.read_text(encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ClaudeUnavailable, match="OPENAI_API_KEY"):
        ask_openai("Prompt", model="gpt-test", session=Session())


def test_claude_problems_are_named_so_the_app_can_ask_the_user(installed, monkeypatch):
    def failing(text, code=1):
        return lambda command, **options: SimpleNamespace(stdout=json.dumps({"is_error": True, "result": text}), stderr="", returncode=code)

    with pytest.raises(ClaudeUnavailable) as sign_in:
        ask_claude("x", runner=failing("Authentication error · please try again"))
    with pytest.raises(ClaudeUnavailable) as limit:
        ask_claude("x", runner=failing("You've hit your session limit · resets 3pm"))
    with pytest.raises(ClaudeUnavailable) as other:
        ask_claude("x", runner=lambda command, **options: SimpleNamespace(stdout="boom", stderr="", returncode=2))

    def slow(command, **options):
        raise subprocess.TimeoutExpired(command, 1)

    with pytest.raises(ClaudeUnavailable) as timeout:
        ask_claude("x", runner=slow)
    monkeypatch.setattr(claude_writer, "find_claude", lambda: None)
    with pytest.raises(ClaudeUnavailable) as missing:
        ask_claude("x")
    assert [e.value.kind for e in (sign_in, limit, other, timeout, missing)] == ["sign_in", "limit", "error", "error", "not_installed"]


def test_checked_claude_text_is_used_and_a_rejected_one_gets_the_standard_text(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    good, bad, forgotten = (_package(out, name) for name in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"))
    done = _package(out, "dddddddddddd", status="not_submitted")
    calls = []
    runner = _runner([
        {"id": "aaaaaaaaaaaa", "intro": INTRO, "company_paragraph": PARAGRAPH},
        {"id": "bbbbbbbbbbbb", "intro": INTRO, "company_paragraph": PARAGRAPH.replace("sieben", "siebzig, genau 70,")},
    ], calls=calls)

    report = write_hooks(out, bank, runner=runner)
    # A rejected or unanswered job gets a second chance in the same run before the standard text.
    assert len(calls) == 2 and report["requests"] == 2
    assert report["claude"] == ["aaaaaaaaaaaa"] and report["standard"] == ["bbbbbbbbbbbb", "cccccccccccc"] and report["left"] == []
    assert "70" in " ".join(report["rejected"]["bbbbbbbbbbbb"])
    hooks = {folder.name[:12]: json.loads((folder / "hook.json").read_text(encoding="utf-8")) for folder in (good, bad, forgotten)}
    assert hooks["aaaaaaaaaaaa"] == {"intro": INTRO, "company_paragraph": PARAGRAPH, "source": "claude"}
    assert hooks["bbbbbbbbbbbb"]["source"] == hooks["cccccccccccc"]["source"] == "standard"
    assert "70" not in json.dumps(hooks["bbbbbbbbbbbb"]) and not (done / "hook.json").exists()
    # Nothing is waiting any more, so a second run sends no request at all.
    assert write_hooks(out, bank, runner=runner)["requests"] == 0 and len(calls) == 2


def test_on_demand_letter_retries_once_then_uses_standard_text(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    folder = _package(out, "aaaaaaaaaaaa")
    requests = []

    def runner(command, **options):
        if command[1:3] == ["auth", "status"]:
            return _runner()(command, **options)
        requests.append(options["input"])
        return SimpleNamespace(stdout=json.dumps({"is_error": False, "result": "too short\n###\nstill too short"}), stderr="", returncode=0)

    result = write_hook_on_demand(out, bank, "aaaaaaaaaaaa", runner=runner)
    assert result["source"] == "standard" and result["requests"] == 2
    assert len(requests) == 2 and "Korrigiere diese Fehler" in requests[1]
    assert json.loads((folder / "hook.json").read_text(encoding="utf-8"))["source"] == "standard"


def test_on_demand_letter_uses_standard_immediately_at_plan_limit(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    folder = _package(out, "aaaaaaaaaaaa")

    def runner(command, **options):
        if command[1:3] == ["auth", "status"]:
            return _runner()(command, **options)
        return SimpleNamespace(
            stdout=json.dumps({"is_error": True, "result": "usage limit reached"}), stderr="", returncode=1,
        )

    result = write_hook_on_demand(out, bank, "aaaaaaaaaaaa", runner=runner)
    assert result["source"] == "standard" and result["requests"] == 0 and "limit" in result["reason"]
    assert json.loads((folder / "hook.json").read_text(encoding="utf-8"))["source"] == "standard"


def test_unavailable_claude_writes_nothing_and_standard_mode_calls_no_model(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    folder = _package(out, "aaaaaaaaaaaa")
    def limit(command, **options):
        if command[1:3] == ["auth", "status"]:
            return _runner()(command, **options)
        return SimpleNamespace(stdout=json.dumps({"is_error": True, "result": "usage limit reached"}), stderr="", returncode=1)

    with pytest.raises(ClaudeUnavailable) as stopped:
        write_hooks(out, bank, runner=limit)
    assert stopped.value.kind == "limit" and not (folder / "hook.json").exists()

    def never(command, **options):
        raise AssertionError("standard mode must not call Claude")

    report = write_hooks(out, bank, writer="standard", runner=never)
    assert report == {"claude": [], "standard": ["aaaaaaaaaaaa"], "rejected": {}, "left": [], "requests": 0}


@pytest.mark.parametrize("title, employer", [
    ("Business Analyst (m/w/d)", "Example GmbH"),
    ("IT Business Analyst / Requirements Manager – Finanzwesen (m/w/d)", "DIS AG"),
    ("Junior Data Analyst [w|m|d] - Reporting 2027", ""),
    ("Referent " + "Controlling und Reporting " * 9, "Konzern " * 12),
])
def test_standard_text_passes_the_letter_rules_for_any_title(title, employer):
    hook = standard_hook(title, employer)
    assert check_hook(hook, f"{title} {employer}") == []
    assert "m/w/d" not in hook["intro"] and "–" not in hook["intro"] and hook["intro"].startswith("mit Interesse")


def test_write_hooks_command_is_registered():
    args = build_parser().parse_args(["write-hooks", "--standard"])
    assert (args.command, args.standard) == ("write-hooks", True)


def test_claude_is_found_where_the_installer_puts_it_even_when_path_misses_it(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(claude_writer.shutil, "which", lambda name: asked.append(name) or None)
    assert find_claude(home=tmp_path) is None
    program = tmp_path / ".local" / "bin" / claude_writer.CLAUDE_EXE
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    assert find_claude(home=tmp_path) == str(program)
    # Only the native program counts (on Windows claude.exe, never npm's claude.cmd).
    assert set(asked) == {claude_writer.CLAUDE_EXE}
    monkeypatch.setattr(claude_writer.shutil, "which", lambda name: "/usr/bin/claude")
    assert find_claude(home=tmp_path) == "/usr/bin/claude"


def test_sign_in_is_checked_without_a_request(installed, monkeypatch):
    def status(auth=None, stdout=None):
        def run(command, **options):
            assert command[1:] == ["auth", "status"] and "input" not in options
            if stdout is not None:
                return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
            return _runner(auth=auth)(command, **options)
        return claude_status(run)

    assert (status(("claude.ai", 0))["plan"], status(("oauth_token", 0))["plan"]) == (True, True)
    paid = status(("api_key", 0))
    assert paid["signed_in"] is True and paid["plan"] is False
    assert status(("none", 1))["signed_in"] is False
    assert status(stdout="not json")["signed_in"] is None

    kinds = []
    for auth in (("api_key", 0), ("none", 1)):
        with pytest.raises(ClaudeUnavailable) as stopped:
            check_plan(_runner(auth=auth))
        kinds.append(stopped.value.kind)
    assert kinds == ["paid", "sign_in"]
    check_plan(_runner(auth=("claude.ai", 0)))  # no exception
    monkeypatch.setattr(claude_writer, "find_claude", lambda: None)
    assert claude_status()["installed"] is False
    with pytest.raises(ClaudeUnavailable, match="install"):
        check_plan()


def test_no_request_is_sent_when_claude_would_bill_per_token(tmp_path, installed):
    out, bank = tmp_path / "out", _bank(tmp_path / "bank.yaml")
    folder = _package(out, "aaaaaaaaaaaa")
    calls = []
    with pytest.raises(ClaudeUnavailable) as stopped:
        write_hooks(out, bank, runner=_runner(auth=("api_key", 0), calls=calls))
    assert stopped.value.kind == "paid" and calls == [] and not (folder / "hook.json").exists()
