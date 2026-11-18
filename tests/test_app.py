from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jobfilter.app import (
    App, PAGE, app_icon_ico, append_form_log, create_prepare_schedule, ensure_app_shortcut, make_handler,
)
from tests.test_hooks import INTRO, PARAGRAPH, _bank


def _project(tmp_path: Path) -> Path:
    docs = tmp_path / "Job App Docs"
    docs.mkdir()
    _bank(docs / "anschreiben_bausteine.yaml")
    (docs / "bewerberprofil.yaml").write_text('first_name: "Max"\nlast_name: "Muster"\nemail: "m@example.org"\n', encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        'contact_email: "m@example.org"\nanswer_bank_path: "Job App Docs/antworten.yaml"\n', encoding="utf-8",
    )
    shutil.copy2(Path(__file__).parent / "fixtures" / "keywords_example_analytics.json", tmp_path / "keywords.json")
    out = tmp_path / "out" / "applications"
    for application_id, status, letter in (("aaaaaaaaaaaa", "needs_hook", ""), ("bbbbbbbbbbbb", "not_submitted", "letter.docx")):
        folder = out / f"{application_id}_example"
        folder.mkdir(parents=True)
        (folder / "application.json").write_text(json.dumps({
            "manifest_version": 2, "application_id": application_id, "title": "Analyst (m/w/d)", "employer": "Example GmbH",
            "application_url": "https://jobs.example.com/1", "external_url": "https://jobs.example.com/1", "url": "",
            "primary_cv_family": "B06 Data Analytics", "selected_cv": "cv.docx", "cover_letter": letter,
            "submission_status": status, "score": 9, "source_sheet": "PRIORITY",
        }), encoding="utf-8")
        (folder / "letter_brief.json").write_text(json.dumps({"family": "B06", "ad_text": "Aufgaben: Analysen."}), encoding="utf-8")
        if letter:
            (folder / letter).write_bytes(b"docx")
    data = tmp_path / "extension" / "data"
    data.mkdir(parents=True)
    (data / "data.json").write_text(json.dumps({"packages": [{"id": "bbbbbbbbbbbb"}]}), encoding="utf-8")
    return tmp_path


def test_state_lists_packages_waiting_hooks_and_profile_gaps(tmp_path):
    app = App(_project(tmp_path) / "config.yaml")
    state = app.state()
    assert {(p["id"], p["status"], p["ready"]) for p in state["packages"]} == {
        ("bbbbbbbbbbbb", "not_submitted", True), ("aaaaaaaaaaaa", "needs_hook", False),
    }
    assert state["waiting"] == ["aaaaaaaaaaaa"]
    assert "salary" in state["profile_missing"] and state["profile_problem"] == ""
    app.current = "bbbbbbbbbbbb"
    form = app.post("/api/form-state", {"id": "bbbbbbbbbbbb", "state": "ready", "filled": 7, "attached": 2})
    assert form["state"] == "ready" and app.state()["form_state"]["attached"] == 2


def test_open_walks_the_status(tmp_path, monkeypatch):
    app = App(_project(tmp_path) / "config.yaml")
    started = []
    opened = []
    app.opener = opened.extend
    monkeypatch.setattr(app, "_top_up_extension", lambda application_id: None)
    monkeypatch.setattr(app.task, "start", lambda name, commands, after=None: started.append((name, commands)) or True)

    assert app.post("/api/open", {"id": "bbbbbbbbbbbb"}) == {"url": "https://jobs.example.com/1"}
    assert opened == ["https://jobs.example.com/1"]
    assert app.post("/api/status", {"id": "bbbbbbbbbbbb", "status": "submitted"}) == {"status": "submitted", "tracker_note": ""}
    assert (tmp_path / "Bewerbungen" / "Bewerbungs_Tracker.xlsx").exists()
    with pytest.raises(ValueError):
        app.post("/api/status", {"id": "aaaaaaaaaaaa", "status": "submitted"})
    with pytest.raises(ValueError, match="Unknown task"):
        app.post("/api/task", {"name": "format-disk"})
    app.post("/api/task", {"name": "search", "max_ads": 25, "limit": 999})
    assert started[-1] == (
        "search", [["add-links"], ["run", "--max-ads", "25"], ["prepare-applications", "--limit", "50"]],
    )


def test_server_needs_the_page_token_and_a_local_host(tmp_path):
    app = App(_project(tmp_path) / "config.yaml")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, token="", host=None, body=None):
        headers = {"X-JF-Token": token}
        if host:
            headers["Host"] = host
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    try:
        from jobfilter.app import APP_VERSION, running_version
        assert running_version(base + "/") == APP_VERSION and len(APP_VERSION) == 12
        assert running_version("http://127.0.0.1:9/") is None
        status, page = call("/")
        assert status == 200 and app.token in page and "__TOKEN__" not in page
        assert 'rel="icon" href="/favicon.ico"' in page and 'rel="manifest"' in page
        with urllib.request.urlopen(base + "/favicon.ico") as response:
            icon = response.read()
            assert response.headers.get_content_type() == "image/x-icon" and icon == app_icon_ico()
        status, manifest = call("/manifest.webmanifest")
        assert status == 200 and json.loads(manifest)["icons"][0]["src"] == "/app-icon.png"
        assert call("/api/state")[0] == 403
        assert call("/api/status", body={"id": "bbbbbbbbbbbb", "status": "skipped"})[0] == 403
        assert call("/api/state", token=app.token, host="evil.example")[0] == 403
        status, body = call("/api/state", token=app.token)
        assert status == 200 and len(json.loads(body)["packages"]) == 2
        assert call("/api/package?id=nope", token=app.token)[0] == 404
    finally:
        server.shutdown()
        server.server_close()


LETTERS = [["write-hooks"], ["prepare-applications", "--existing-only"], ["export-autofill"]]
PREPARE_ONLY = [["export-autofill"]]


def test_one_button_run_chains_every_step_and_opens_the_ready_forms(tmp_path, monkeypatch):
    app = App(_project(tmp_path) / "config.yaml")
    started, opened, shown = [], [], []
    app.opener, app.notifier = opened.extend, lambda title, text: shown.append((title, text))
    monkeypatch.setattr(app.task, "start", lambda name, commands, after=None: started.append((name, commands, after)) or True)

    app.post("/api/task", {"name": "auto", "max_ads": 40, "limit": 3, "open_forms": True})
    name, commands, after = started[-1]
    assert (name, commands) == (
        "auto", [["add-links"], ["run", "--max-ads", "40"], ["prepare-applications", "--limit", "3"], *PREPARE_ONLY],
    )
    app.post("/api/task", {"name": "auto", "open_forms": False})
    assert started[-1][2] == app.apply_next
    app.post("/api/task", {"name": "letters", "standard": True})
    assert started[-1][1][0] == ["write-hooks", "--standard"]
    app.post("/api/task", {"name": "prepare", "max_ads": 12, "limit": 4})
    assert started[-1][1] == [
        ["add-links"], ["run", "--max-ads", "12"], ["prepare-applications", "--limit", "4"], *PREPARE_ONLY,
    ]

    after()  # what runs once every step succeeded: the first form only
    app._io_jobs.join()
    assert opened == ["https://jobs.example.com/1"] and app.state()["current"] == "bbbbbbbbbbbb"
    status = {p["id"]: p["status"] for p in app.state()["packages"]}
    assert status == {"aaaaaaaaaaaa": "needs_hook", "bbbbbbbbbbbb": "opened"}
    # Nothing is opened twice, and a package without PDFs or still waiting for its letter is never opened.
    assert app.post("/api/apply-next", {})["opened"] == "" and len(opened) == 1
    app._io_jobs.join()

    app.task.name, app.task.failed, app.task.log = "auto", False, []
    app.task_done(app.task)
    assert shown[-1] == ("jobfilter finished: Prepare and apply next", "1 form(s) open for your check, 1 waiting for a letter")
    app.task.failed, app.task.log = True, ["> jobfilter write-hooks", "NEEDS YOU (sign_in): Claude is not signed in.", "Stopped: the step ended with code 3."]
    app.task_done(app.task)
    assert shown[-1] == ("jobfilter needs you", "Claude is not signed in.")
    assert [n["kind"] for n in app.state()["notices"]] == ["info", "info", "needs"]  # incl. "no more forms"


def test_app_add_links_box_appends_the_file_and_starts_intake(tmp_path, monkeypatch):
    root = _project(tmp_path)
    app = App(root / "config.yaml")
    started = []
    monkeypatch.setattr(app.task, "start", lambda name, commands, after=None: started.append((name, commands)) or True)
    result = app.post("/api/add-links", {
        "text": "Data Analyst | https://www.xing.com/jobs/berlin-data-analyst-123?utm_source=mail",
    })
    assert result == {"started": 1}
    assert started == [("links", [["add-links"]])]
    assert "Data Analyst | https://www.xing.com/jobs/" in (root / "imports" / "links.txt").read_text(encoding="utf-8")
    assert app.post("/api/retry-links", {}) == {"started": "retry"}
    assert started[-1] == ("links", [["add-links", "--retry-errors"]])
    result = app.post("/api/manual-text", {"id": "pending-1", "text": "Complete advertisement text. " * 5, "employer": "Example GmbH"})
    assert result == {"started": "pending-1", "saved": True}
    assert started[-1] == ("manual-text", [["complete-link", "--id", "pending-1"]])
    saved = json.loads((root / "imports" / "manual_drafts.json").read_text(encoding="utf-8"))
    assert saved["pending-1"]["employer"] == "Example GmbH"
    (root / "imports" / "manual_results.json").write_text(json.dumps([{
        "title": "Manual Analyst", "employer": "Example GmbH", "url": "https://jobs.example/manual",
        "band": "PRIORITY", "score": 20, "employment_format": "PART-TIME / TEILZEIT",
        "primary_cv_family": "B06 Data Analytics",
    }]), encoding="utf-8")
    manual = app.state()["manual_results"][0]
    assert manual["package_eligible"] is True and not manual["package_id"]
    assert app.post("/api/prepare-manual", {"url": manual["url"]}) == {"started": manual["url"]}
    assert started[-1] == ("prepare-manual", [["prepare-applications", "--job-url", manual["url"], "--limit", "1"]])
    removed = app.post("/api/manual-remove", {"url": manual["url"]})
    assert removed["decision"] == "Nicht beworben"
    assert json.loads((root / "imports" / "manual_results.json").read_text(encoding="utf-8")) == []
    from openpyxl import load_workbook
    tracker = load_workbook(root / "Bewerbungen" / "Bewerbungs_Tracker.xlsx")["Bewerbungen"]
    headers = [cell.value for cell in tracker[1]]
    row = {name: tracker.cell(2, index + 1).value for index, name in enumerate(headers)}
    assert row["Entscheidung"] == "Nicht beworben" and row["Quelle"] == "manual"


def test_manual_text_draft_survives_refresh_and_shows_saving_state():
    page = PAGE.read_text(encoding="utf-8")
    assert "const manualDrafts = new Map(), manualBusy = new Set()" in page
    assert 'document.activeElement.closest("#manual-pending")' in page
    assert "Saving text…" in page and "Saved on disk. Scoring" in page
    assert "Manually added jobs" in page and "Create this package" in page
    assert 'textContent:"Remove"' in page and 'api("/api/manual-remove"' in page
    assert "I submitted in Edge, next form" in page and 'message.action === "form-state"' in page
    assert "Not submitted — return to queue" in page
    assert page.count("Did you complete the final submission") == 3
    background = (PAGE.parent.parent / "extension" / "background.js").read_text(encoding="utf-8")
    assert "action: 'fill-result'" in (PAGE.parent.parent / "extension" / "core.js").read_text(encoding="utf-8")
    assert "appQueue" in background and "slice(-50)" in background and "registerApp" in background
    assert "chrome.runtime.getManifest().version" in background


@pytest.mark.parametrize(("submitted", "decision"), [(False, "Nicht beworben"), (True, "Beworben")])
def test_removing_packaged_manual_job_updates_excel_without_downgrading_applied(tmp_path, submitted, decision):
    root = _project(tmp_path)
    manifest_path = root / "out" / "applications" / "bbbbbbbbbbbb_example" / "application.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["board"] = "manual"
    if submitted:
        manifest["submission_status"] = "submitted"
        manifest["status_updated_at"] = "2026-11-15T10:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt = [{
        "title": manifest["title"], "employer": manifest["employer"], "url": manifest["application_url"],
        "location": "Berlin", "band": "PRIORITY", "score": 9,
        "employment_format": "FULL-TIME / VOLLZEIT", "primary_cv_family": "B06", "date": "2026-11-15",
    }]
    imports = root / "imports"
    imports.mkdir(exist_ok=True)
    (imports / "manual_results.json").write_text(json.dumps(receipt), encoding="utf-8")
    app = App(root / "config.yaml")
    result = app.post("/api/manual-remove", {"url": manifest["application_url"]})
    assert result["decision"] == decision
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["submission_status"] == ("submitted" if submitted else "skipped")
    from openpyxl import load_workbook
    sheet = load_workbook(root / "Bewerbungen" / "Bewerbungs_Tracker.xlsx")["Bewerbungen"]
    headers = [cell.value for cell in sheet[1]]
    row = {name: sheet.cell(2, index + 1).value for index, name in enumerate(headers)}
    assert row["Entscheidung"] == decision
    if submitted:
        assert row["Datum"] == "2026-11-15"


def test_extension_health_and_detailed_form_result_reach_the_app(tmp_path):
    root = _project(tmp_path)
    (root / "extension" / "manifest.json").write_text('{"version":"1.3.4"}', encoding="utf-8")
    app = App(root / "config.yaml")
    assert app.state()["extension"]["connected"] is False
    assert app.post("/api/extension-pong", {"version": "1.3.3"})["connected"] is True
    assert app.state()["extension"]["reload"] is True
    state = app.post("/api/form-state", {
        "id": "bbbbbbbbbbbb", "state": "needs", "filled": 4, "attached": 1,
        "uploads": [{"label": "CV", "status": "attached", "files": ["cv.pdf"]}],
        "open_labels": ["Why us?"],
    })
    assert state["uploads"][0]["files"] == ["cv.pdf"] and state["open_labels"] == ["Why us?"]
    assert app.post("/api/answers-inserted", {"id": "bbbbbbbbbbbb", "inserted": 1})["answers_inserted"] == 1
    changed = app.post("/api/answers-inserted", {
        "id": "bbbbbbbbbbbb", "inserted": 0, "page_changed": ["Why us?"],
    })
    assert changed["state"] == "needs" and changed["answer_page_changed"] == ["Why us?"]
    assert "page changed" in changed["reason"]


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler only")
def test_nightly_prepare_is_only_created_by_explicit_schedule_action(tmp_path):
    calls = []
    class Done:
        returncode, stdout, stderr = 0, "", ""
    create_prepare_schedule(tmp_path / "config.yaml", runner=lambda command, **options: calls.append(command) or Done(), python="python.exe")
    command = calls[0]
    assert command[:7] == ["schtasks.exe", "/Create", "/F", "/SC", "DAILY", "/ST", "03:00"]
    assert "jobfilter.nightly" in command[-1] and str(tmp_path / "config.yaml") in command[-1]


@pytest.mark.skipif(os.name != "nt", reason="Windows shell shortcut only")
def test_windows_app_shortcut_uses_the_extension_icon(tmp_path, monkeypatch):
    import jobfilter.app as app_module
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []
    class Done:
        returncode = 0
    def runner(command, **options):
        calls.append(command)
        Path(options["env"]["JF_SHORTCUT"]).write_bytes(b"shortcut")
        calls.append(options["env"])
        return Done()
    shortcut = ensure_app_shortcut(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "http://127.0.0.1:8791/", runner=runner)
    assert shortcut == tmp_path / "jobfilter" / "jobfilter.lnk"
    ico = (tmp_path / "jobfilter" / "jobfilter.ico").read_bytes()
    assert ico == app_icon_ico() and ico[:4] == b"\x00\x00\x01\x00"
    assert calls[1]["JF_ICON"].endswith("jobfilter.ico,0")
    assert '--app="http://127.0.0.1:8791/"' in calls[1]["JF_ARGUMENTS"]


def test_app_stops_only_after_its_window_closed_and_nothing_runs(tmp_path):
    app = App(_project(tmp_path) / "config.yaml")
    assert app.should_stop(10_000.0) is False  # no window has reported yet
    app.post("/api/ping", {})
    now = app.last_ping
    assert app.should_stop(now + 60) is False and app.should_stop(now + 901) is True
    app.post("/api/bye", {})
    app.bye_at = now + 1
    assert app.should_stop(now + 5) is False and app.should_stop(now + 10) is True
    app.task.running = True
    assert app.should_stop(now + 5000) is False
    app.task.running = False
    app.post("/api/ping", {})  # a reload: the window is back
    assert app.bye_at == 0.0


def test_claude_setup_state_and_check(tmp_path, monkeypatch):
    import jobfilter.app as app_module
    app = App(_project(tmp_path) / "config.yaml")
    monkeypatch.setattr(app_module, "find_claude", lambda: None)
    state = app.state()
    assert state["claude_installed"] is False and "install" in state["claude_install"] and "auth login" in state["claude_login"]
    monkeypatch.setattr(app_module, "find_claude", lambda: "/opt/claude")
    state = app.state()
    assert state["claude_installed"] is True and state["claude_path"] == "/opt/claude"
    monkeypatch.setattr(app_module, "claude_status", lambda: {"installed": True, "plan": True})
    assert app.post("/api/claude-check", {}) == {"installed": True, "plan": True}


def _ready_package(root: Path, application_id: str, url: str, **extra) -> Path:
    folder = root / "out" / "applications" / f"{application_id}_more"
    folder.mkdir(parents=True)
    (folder / "application.json").write_text(json.dumps({
        "manifest_version": 2, "application_id": application_id, "title": "Analyst (m/w/d)", "employer": "Other AG",
        "application_url": url, "external_url": "", "url": url, "primary_cv_family": "B06 Data Analytics",
        "selected_cv": "cv.docx", "cover_letter": "letter.docx", "submission_status": "not_submitted", "score": 5,
        "source_sheet": "PRIORITY", "attachments": ["Zeugnis.pdf"], **extra,
    }), encoding="utf-8")
    (folder / "letter.docx").write_bytes(b"docx")
    data = root / "extension" / "data" / "data.json"
    payload = json.loads(data.read_text(encoding="utf-8"))
    payload["packages"].append({"id": application_id})
    data.write_text(json.dumps(payload), encoding="utf-8")
    return folder


def test_forms_open_one_at_a_time_and_captcha_pages_come_last(tmp_path):
    root = _project(tmp_path)
    _ready_package(root, "cccccccccccc", "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1-S")
    _ready_package(root, "dddddddddddd", "https://jobs.other.example/2")
    app = App(root / "config.yaml")
    opened, shown = [], []
    app.opener, app.notifier = opened.extend, lambda title, text: shown.append(title)

    first = app.post("/api/apply-next", {})
    assert first["opened"] == "bbbbbbbbbbbb" and first["left"] == 2 and opened == ["https://jobs.example.com/1"]
    # Submitted: recorded, and the next form opens by itself; the Arbeitsagentur page waits until last.
    second = app.post("/api/apply-next", {"done": "bbbbbbbbbbbb", "outcome": "submitted"})
    assert second["opened"] == "dddddddddddd" and opened[-1] == "https://jobs.other.example/2"
    # Later: back in the queue, but not offered again in this session.
    third = app.post("/api/apply-next", {"done": "dddddddddddd", "outcome": "later"})
    assert third["opened"] == "cccccccccccc" and opened[-1].startswith("https://www.arbeitsagentur.de/")
    done = app.post("/api/apply-next", {"done": "cccccccccccc", "outcome": "skipped"})
    app._io_jobs.join()
    assert done["opened"] == "" and app.state()["current"] == "" and shown[-1] == "jobfilter: no more forms"
    status = {p["id"]: p["status"] for p in app.state()["packages"]}
    assert status["bbbbbbbbbbbb"] == "submitted" and status["dddddddddddd"] == "not_submitted"
    assert "cccccccccccc" not in status  # skipped jobs leave the list
    with pytest.raises(ValueError, match="Unknown outcome"):
        app.post("/api/apply-next", {"done": "dddddddddddd", "outcome": "deleted"})


def test_confirmation_survives_restart_and_later_clears_it(tmp_path, monkeypatch):
    root = _project(tmp_path)
    app = App(root / "config.yaml")
    app.opener = lambda urls: None
    monkeypatch.setattr(app, "_top_up_extension", lambda application_id: {})
    app.post("/api/open", {"id":"bbbbbbbbbbbb"})
    result = app.post("/api/confirm-apply", {"id":"bbbbbbbbbbbb"})
    assert result["confirmed_at"]
    restarted = App(root / "config.yaml")
    package = next(item for item in restarted.state()["packages"] if item["id"] == "bbbbbbbbbbbb")
    assert package["confirmed"] is True
    app.post("/api/apply-next", {"done":"bbbbbbbbbbbb", "outcome":"later"})
    app._io_jobs.join()
    manifest = json.loads((root / "out/applications/bbbbbbbbbbbb_example/application.json").read_text(encoding="utf-8"))
    assert "confirmed_at" not in manifest


def test_title_gate_checks_existing_letters_is_reversible_and_manual_is_exempt(tmp_path):
    root = _project(tmp_path)
    gated = _ready_package(root, "cccccccccccc", "https://jobs.other.example/lead", title="Teamleitung International Business Development")
    manual = _ready_package(root, "mmmmmmmmmmmm", "https://jobs.manual.example/lead", title="Teamleitung International Business Development", board="manual")
    app = App(root / "config.yaml")
    gated_manifest = json.loads((gated / "application.json").read_text(encoding="utf-8"))
    manual_manifest = json.loads((manual / "application.json").read_text(encoding="utf-8"))
    assert gated_manifest["submission_status"] == "skipped" and gated_manifest["status_reason"] == "title gate"
    assert manual_manifest["submission_status"] == "not_submitted"
    assert next(item for item in app.state()["title_gated"] if item["id"] == "cccccccccccc")
    assert app.post("/api/restore-title-gated", {"id":"cccccccccccc"})["status"] == "not_submitted"


def test_apply_next_returns_before_slow_tracker_and_export(tmp_path, monkeypatch):
    import time
    root = _project(tmp_path)
    app = App(root / "config.yaml")
    app.opener = lambda urls: None
    monkeypatch.setattr(app, "sync_tracker", lambda config: time.sleep(2) or "")
    monkeypatch.setattr(app, "_top_up_extension", lambda application_id: time.sleep(2) or {})
    started = time.perf_counter()
    assert app.post("/api/apply-next", {})["opened"] == "bbbbbbbbbbbb"
    assert time.perf_counter() - started < 2


def test_needs_hook_opens_token_free_then_confirmation_starts_letter_and_skip_discards_it(tmp_path, monkeypatch):
    import time
    import jobfilter.claude_writer as writer

    root = _project(tmp_path)
    app = App(root / "config.yaml")
    app.post("/api/status", {"id": "bbbbbbbbbbbb", "status": "skipped"})
    opened, started, release = [], threading.Event(), threading.Event()
    app.opener, app.notifier = opened.extend, lambda title, text: None
    monkeypatch.setattr(app, "_top_up_extension", lambda application_id: None)

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return {"source": "discarded", "reason": "application was set aside", "requests": 0, "discarded": True}

    monkeypatch.setattr(writer, "write_hook_on_demand", slow)
    result = app.post("/api/open", {"id": "aaaaaaaaaaaa"})
    assert result["url"] == "https://jobs.example.com/1" and opened == ["https://jobs.example.com/1"]
    assert not started.wait(.1)  # opening the form is token-free
    confirmed = app.post("/api/confirm-apply", {"id": "aaaaaaaaaaaa"})
    assert confirmed["confirmed"] is True and confirmed["letter_started"] is True
    assert started.wait(2)
    done = app.post("/api/apply-next", {"done": "aaaaaaaaaaaa", "outcome": "skipped"})
    release.set()
    for _ in range(100):
        if "aaaaaaaaaaaa" not in app.letter_cancellations:
            break
        time.sleep(0.01)
    folder = root / "out" / "applications" / "aaaaaaaaaaaa_example"
    assert done["opened"] == "" and not (folder / "hook.json").exists()
    assert json.loads((folder / "application.json").read_text(encoding="utf-8"))["submission_status"] == "skipped"


def test_email_route_waits_for_an_on_demand_letter(tmp_path, monkeypatch):
    from jobfilter.app import _queue_rows
    root = _project(tmp_path)
    folder = root / "out" / "applications" / "aaaaaaaaaaaa_example"
    manifest_path = folder / "application.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apply_email"] = "jobs@example.org"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    app = App(root / "config.yaml")
    drafts = []
    monkeypatch.setattr(app, "open_draft", lambda *args: drafts.append(args))
    row = next(row for row in _queue_rows(app.config.application_output_dir) if row["application_id"] == "aaaaaaaaaaaa")
    assert app.open_route(app.config, row) == "email" and drafts == []
    assert app.notices[-1]["text"].endswith("the draft follows when the letter is ready.")


def test_restart_keeps_newest_open_form_and_requeues_the_rest(tmp_path):
    root = _project(tmp_path)
    first = root / "out" / "applications" / "bbbbbbbbbbbb_example" / "application.json"
    one = json.loads(first.read_text(encoding="utf-8"))
    one.update(submission_status="opened", status_updated_at="2026-11-16T10:00:00+00:00")
    first.write_text(json.dumps(one), encoding="utf-8")
    second = _ready_package(root, "cccccccccccc", "https://jobs.other.example/3", submission_status="opened")
    two_path = second / "application.json"
    two = json.loads(two_path.read_text(encoding="utf-8"))
    two["status_updated_at"] = "2026-11-16T11:00:00+00:00"
    two_path.write_text(json.dumps(two), encoding="utf-8")

    app = App(root / "config.yaml")
    status = {row["application_id"]: row["submission_status"] for row in app.ready_rows(app.config)}
    assert app.current == "cccccccccccc" and app.startup_reset == ["bbbbbbbbbbbb"]
    assert status["bbbbbbbbbbbb"] == "not_submitted"


def test_completed_manual_packages_are_first_in_check_and_submit_and_apply_order(tmp_path):
    root = _project(tmp_path)
    manual = _ready_package(root, "mmmmmmmmmmmm", "https://jobs.manual.example/low", score=1, board="manual")
    _ready_package(root, "zzzzzzzzzzzz", "https://jobs.pipeline.example/high", score=99)
    app = App(root / "config.yaml")
    active = [row for row in app.state()["packages"] if row["status"] not in {"needs_hook", "submitted"}]
    assert active[0]["id"] == "mmmmmmmmmmmm" and active[0]["manual"] is True
    assert app.ready_rows(app.config)[0]["application_id"] == "mmmmmmmmmmmm"


def test_contact_found_after_the_captcha_is_kept_and_opens_an_email_draft(tmp_path):
    root = _project(tmp_path)
    folder = _ready_package(root, "cccccccccccc", "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1-S")
    profile = root / "Job App Docs" / "bewerberprofil.yaml"
    profile.write_text('first_name: "Max"\nlast_name: "Muster"\nemail: "m@example.org"\nphone: "+49 1"\n', encoding="utf-8")
    shared = root / "Job App Docs" / "Attachements"
    shared.mkdir()
    (shared / "Zeugnis.pdf").write_bytes(b"%PDF zeugnis")
    for name in ("Anschreiben_Max_Muster.pdf", "Lebenslauf_Max_Muster.pdf"):
        (folder / name).write_bytes(b"%PDF " + name.encode())
    app = App(root / "config.yaml")
    mails, opened = [], []
    app.mail_opener, app.opener, app.notifier = mails.append, opened.extend, lambda title, text: None

    with pytest.raises(ValueError, match="Neither"):
        app.post("/api/contact", {"id": "cccccccccccc", "email": "service@arbeitsagentur.de"})
    result = app.post("/api/contact", {"id": "cccccccccccc", "email": "mailto:jobs@other.example?subject=x"})
    assert result["email"] == "jobs@other.example" and mails == [Path(result["draft"])]
    manifest = json.loads((folder / "application.json").read_text(encoding="utf-8"))
    assert manifest["apply_email"] == "jobs@other.example"
    # Next time: the draft opens straight away, without the Arbeitsagentur page and its CAPTCHA.
    app.post("/api/status", {"id": "bbbbbbbbbbbb", "status": "skipped"})
    assert app.post("/api/apply-next", {})["via"] == "email" and opened == [] and len(mails) == 2

    other = _ready_package(root, "eeeeeeeeeeee", "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-2-S")
    link = app.post("/api/contact", {"id": "eeeeeeeeeeee", "link": "https://karriere.other.example/apply/7"})
    assert link["link"] == "https://karriere.other.example/apply/7" and link["draft"] == ""
    assert json.loads((other / "application.json").read_text(encoding="utf-8"))["external_url"] == link["link"]


def test_forms_left_open_from_before_go_back_to_the_queue(tmp_path):
    root = _project(tmp_path)
    _ready_package(root, "cccccccccccc", "https://jobs.other.example/3")
    app = App(root / "config.yaml")
    app.opener, app.notifier = (lambda urls: None), (lambda title, text: None)
    for application_id in ("bbbbbbbbbbbb", "cccccccccccc"):
        app.post("/api/open", {"id": application_id})  # the old way: several forms open at once
    assert app.current == "cccccccccccc"
    assert app.post("/api/requeue-open", {})["moved"] == ["bbbbbbbbbbbb", "cccccccccccc"]
    status = {p["id"]: p["status"] for p in app.state()["packages"]}
    assert status["bbbbbbbbbbbb"] == "not_submitted" and status["cccccccccccc"] == "not_submitted"
    assert app.current == ""


def test_answer_endpoint_uses_injected_runner_and_standard_mode_skips(tmp_path, monkeypatch):
    import jobfilter.app as app_module

    root = _project(tmp_path)
    app = App(root / "config.yaml")
    fake_runner = object()
    app.answer_runner = fake_runner
    seen = []

    def fake_answer(data_path, application_id, fields, cache_path, **kwargs):
        seen.append((Path(data_path), application_id, fields, Path(cache_path), kwargs))
        return {
            "answers": [{"uid": "f0-a", "value": "Text", "label": "Frage"}],
            "rejected": {}, "yours": [], "model": kwargs["model"],
        }

    monkeypatch.setattr(app_module, "answer_fields", fake_answer)
    result = app.post("/api/answer", {
        "id": "bbbbbbbbbbbb", "tabId": 7, "fields": [{"uid": "f0-a", "label": "Frage"}],
        "instruction": "Focus on product impact.", "force": True,
    })
    assert result["answers"][0]["value"] == "Text"
    assert seen[0][-1]["runner"] is fake_runner
    assert seen[0][-1]["model"] == "haiku"
    assert seen[0][-1]["user_instruction"] == "Focus on product impact."
    assert seen[0][-1]["force"] is True
    assert seen[0][-1]["mode"] == "resolve"  # unconfirmed pages use only deterministic answers
    assert seen[0][-1]["answer_language"] == "de"

    app.opener = lambda urls: None
    app.post("/api/open", {"id": "bbbbbbbbbbbb"})
    app.post("/api/confirm-apply", {"id": "bbbbbbbbbbbb"})
    app.post("/api/answer", {"id": "bbbbbbbbbbbb", "fields": [{"uid":"f0-a", "label":"Frage"}]})
    assert seen[-1][-1]["mode"] == "answer"

    (root / "config.yaml").write_text('contact_email: "m@example.org"\nletter_writer: "standard"\n', encoding="utf-8")
    skipped = app.post("/api/answer", {"id": "bbbbbbbbbbbb", "fields": []})
    assert skipped["answers"][0]["value"] == "Text" and seen[-1][-1]["mode"] == "resolve"


def test_answer_endpoint_returns_claude_limits_instead_of_dropping_the_fetch(tmp_path, monkeypatch):
    import jobfilter.app as app_module
    from jobfilter.claude_writer import ClaudeUnavailable

    root = _project(tmp_path)
    app = App(root / "config.yaml")

    def limited(*args, **kwargs):
        raise ClaudeUnavailable("limit", "Claude plan limit reached; resets 7:30pm (Europe/Berlin)")

    monkeypatch.setattr(app_module, "answer_fields", limited)
    result = app.post("/api/answer", {"id": "bbbbbbbbbbbb", "fields": [{"uid": "a", "label": "Question"}]})
    assert result == {
        "answers": [], "rejected": {}, "yours": [],
        "reason": "Claude plan limit reached; resets 7:30pm (Europe/Berlin)", "kind": "limit",
    }


def test_form_log_has_no_values_and_drops_oldest_complete_lines(tmp_path):
    root = _project(tmp_path)
    app = App(root / "config.yaml")
    record = {
        "action": "log", "host": "jobs.example.org", "path": "/apply", "id": "abc",
        "fields": [
            {"label": "Name", "kind": "text", "key": "first_name", "status": "filled", "required": True, "value": "SECRET"},
            {"label": "Application documents", "kind": "file", "key": "documents", "status": "attached",
             "required": True, "files": ["letter.pdf", "cv.pdf"]},
        ],
    }
    assert app.post("/api/form-log", record) == {"logged": True}
    log = root / "out" / "applications" / "form_log.jsonl"
    logged = log.read_text(encoding="utf-8")
    saved = json.loads(logged)
    assert "SECRET" not in logged and saved["fields"][1]["files"] == ["letter.pdf", "cv.pdf"]

    tiny = tmp_path / "tiny.jsonl"
    for index in range(20):
        append_form_log(tiny, {"index": index, "text": "x" * 20}, limit=150)
    from jobfilter.safeio import trim_jsonl
    trim_jsonl(tiny, 150)
    lines = tiny.read_text(encoding="utf-8").splitlines()
    assert len(tiny.read_bytes()) <= 150 and json.loads(lines[-1])["index"] == 19 and json.loads(lines[0])["index"] > 0


def test_answer_bank_page_lists_logged_gaps_and_saves_an_approved_answer(tmp_path):
    root = _project(tmp_path)
    app = App(root / "config.yaml")
    app.post("/api/form-log", {
        "action": "log", "host": "flix.careers", "path": "/job/1", "id": "bbbbbbbbbbbb",
        "fields": [{"label": "Do you have HR Tech domain?", "kind": "textarea", "key": None, "status": "unknown", "required": True}],
    })
    before = app.state()
    assert before["answer_bank"]["missing"][0]["label"] == "Do you have HR Tech domain?"
    result = app.post("/api/answer-bank-save", {
        "kind": "saved", "label": "Do you have HR Tech domain?", "value": "Approved factual answer.",
        "language": "en", "scope": "any employer", "employer": "Example GmbH",
    })
    assert result["saved"] is True
    bank_path = root / "Job App Docs" / "antworten.yaml"
    assert bank_path.is_file() and "Approved factual answer" in bank_path.read_text(encoding="utf-8")
    assert app.state()["answer_bank"]["missing"] == []


def test_saved_fact_survives_app_restart_and_wins_on_next_fill(tmp_path):
    root = _project(tmp_path)
    data_path = root / "extension" / "data" / "data.json"
    data_path.write_text(json.dumps({"profile": {"salary": "99999"}, "packages": [{
        "id": "bbbbbbbbbbbb", "employer": "Example GmbH", "title": "Analyst",
    }]}), encoding="utf-8")
    first = App(root / "config.yaml")
    saved = first.post("/api/answer-bank-save", {"kind": "fact", "key": "salary", "de": "52000", "en": "52000"})
    assert saved["message"] == "Saved"
    first.stop()
    second = App(root / "config.yaml")
    result = second.post("/api/answer", {"id": "bbbbbbbbbbbb", "mode": "resolve", "fields": [{
        "uid": "salary", "label": "Was möchten Sie als Jahresbrutto verdienen?", "kind": "text",
    }]})
    assert result["answers"] == [{
        "uid": "salary", "value": "52000", "label": "Was möchten Sie als Jahresbrutto verdienen?",
        "source": "answer bank", "note": "",
    }]
    second.stop()
