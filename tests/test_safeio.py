from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path

from jobfilter.safeio import append_jsonl


def _append_worker(path: str, start: int, count: int) -> None:
    for value in range(start, start + count):
        append_jsonl(path, {"value": value})


def test_two_processes_append_every_json_line(tmp_path):
    path = tmp_path / "shared.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_worker, args=(str(path), 0, 200)),
        context.Process(target=_append_worker, args=(str(path), 200, 200)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    rows = [json.loads(line)["value"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 400 and set(rows) == set(range(400))


def test_locked_append_is_kept_and_flushed_with_the_next_line(tmp_path, monkeypatch):
    import jobfilter.safeio as safeio

    path = tmp_path / "usage.jsonl"
    real = safeio._append_once
    monkeypatch.setattr(safeio, "_append_once", lambda target, line: (_ for _ in ()).throw(PermissionError("locked")))
    assert not safeio.append_jsonl(path, {"value": 1}, total_wait=0)
    assert path.resolve() in safeio.pending_paths()
    monkeypatch.setattr(safeio, "_append_once", real)
    assert safeio.append_jsonl(path, {"value": 2})
    assert [json.loads(line)["value"] for line in path.read_text(encoding="utf-8").splitlines()] == [1, 2]
    assert path.resolve() not in safeio.pending_paths()


def test_replacement_temp_is_unique_and_removed_after_failure(tmp_path):
    import jobfilter.safeio as safeio

    path = tmp_path / "data.json"
    seen = []

    def locked(source, target):
        seen.append(Path(source).name)
        raise PermissionError("locked")

    assert not safeio.atomic_write_bytes(
        path, b"one", attempts=1, total_wait=0, lock_wait=0, replacer=locked,
    )
    assert not safeio.atomic_write_bytes(
        path, b"two", attempts=1, total_wait=0, lock_wait=0, replacer=locked,
    )
    assert len(set(seen)) == 2
    assert all(not (tmp_path / name).exists() for name in seen)


def test_stale_cross_process_lock_is_recovered(tmp_path):
    from jobfilter.safeio import file_lock

    target = tmp_path / "observed.json"
    lock = target.with_name(target.name + ".lock")
    lock.write_text("dead process", encoding="utf-8")
    old = time.time() - 60
    os.utime(lock, (old, old))
    with file_lock(target, timeout=0.1, stale_after=30) as owned:
        assert owned is True
    assert not lock.exists()
