"""Cross-process-safe writes for files held briefly by OneDrive or Office."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping


_LOCK = threading.RLock()
_PENDING: dict[Path, bytes] = {}
_PENDING_LINES: dict[Path, list[str]] = {}


def _key(path: str | Path) -> Path:
    return Path(path).resolve()


def _lock_path(target: Path) -> Path:
    return target.with_name(target.name + ".lock")


@contextmanager
def file_lock(
    path: str | Path, *, timeout: float = 5.0, stale_after: float = 30.0,
    sleeper: Callable[[float], object] = time.sleep,
) -> Iterator[bool]:
    """Own a sibling O_EXCL lock file, recovering one left by a dead process."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(target)
    deadline = time.monotonic() + max(0.0, timeout)
    owned = False
    while True:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(descriptor, f"{os.getpid()} {time.time()}\n".encode("ascii"))
            finally:
                os.close(descriptor)
            owned = True
            break
        except (FileExistsError, PermissionError) as error:
            if isinstance(error, PermissionError) and not lock.exists():
                # Windows: a lock file another process is deleting refuses new opens for a moment.
                if time.monotonic() >= deadline:
                    raise
                sleeper(0.01)
                continue
            try:
                stale = time.time() - lock.stat().st_mtime > stale_after
            except FileNotFoundError:
                continue
            if stale:
                try:
                    lock.unlink()
                    continue
                except FileNotFoundError:
                    continue
                except PermissionError:
                    pass
            if time.monotonic() >= deadline:
                break
            sleeper(min(0.05, max(0.0, deadline - time.monotonic())))
    try:
        yield owned
    finally:
        if owned:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass


def _temporary(target: Path) -> Path:
    return target.with_name(
        f"{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )


def pending_bytes(path: str | Path) -> bytes | None:
    with _LOCK:
        return _PENDING.get(_key(path))


def read_bytes(path: str | Path) -> bytes:
    pending = pending_bytes(path)
    return pending if pending is not None else Path(path).read_bytes()


def read_text(path: str | Path, *, encoding: str = "utf-8") -> str:
    return read_bytes(path).decode(encoding)


def _replace_locked(
    target: Path, data: bytes, *, attempts: int, total_wait: float,
    replacer: Callable[[str | Path, str | Path], object], sleeper: Callable[[float], object],
) -> bool:
    temporary = _temporary(target)
    count = max(1, int(attempts))
    delay = max(0.0, float(total_wait)) / max(1, count - 1)
    try:
        temporary.write_bytes(data)
        for attempt in range(count):
            try:
                replacer(temporary, target)
                return True
            except PermissionError:
                if attempt + 1 < count:
                    sleeper(delay)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_bytes(
    path: str | Path,
    data: bytes,
    *,
    attempts: int = 10,
    total_wait: float = 3.0,
    lock_wait: float = 5.0,
    replacer: Callable[[str | Path, str | Path], object] | None = None,
    sleeper: Callable[[float], object] = time.sleep,
) -> bool:
    """Atomically replace *path* under a cross-process lock, or queue it."""
    target = Path(path)
    key = _key(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    replace = replacer or os.replace
    with _LOCK:
        with file_lock(target, timeout=lock_wait, sleeper=sleeper) as owned:
            saved = owned and _replace_locked(
                target, data, attempts=attempts, total_wait=total_wait,
                replacer=replace, sleeper=sleeper,
            )
        if saved:
            _PENDING.pop(key, None)
            return True
        _PENDING[key] = bytes(data)
        return False


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8", **kwargs) -> bool:
    return atomic_write_bytes(path, text.encode(encoding), **kwargs)


def atomic_write_json(path: str | Path, value: object, *, indent: int | None = 2) -> bool:
    return atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=indent))


def _append_once(target: Path, line: str) -> None:
    # One append-mode write of one complete line; flush before releasing the lock.
    with target.open("a", encoding="utf-8", newline="") as stream:
        stream.write(line)
        stream.flush()


def append_jsonl(
    path: str | Path, record: Mapping[str, object], *, compact: bool = False,
    total_wait: float = 3.0, sleeper: Callable[[float], object] = time.sleep,
) -> bool:
    """Append one JSON line, retaining locked lines for the next append."""
    target = Path(path)
    key = _key(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    separators = (",", ":") if compact else None
    line = json.dumps(dict(record), ensure_ascii=False, separators=separators) + "\n"
    with _LOCK:
        lines = [*_PENDING_LINES.pop(key, []), line]
        deadline = time.monotonic() + max(0.0, total_wait)
        with file_lock(target, timeout=5.0, sleeper=sleeper) as owned:
            if not owned:
                _PENDING_LINES[key] = lines
                return False
            for index, pending in enumerate(lines):
                while True:
                    try:
                        _append_once(target, pending)
                        break
                    except PermissionError:
                        if time.monotonic() >= deadline:
                            _PENDING_LINES[key] = lines[index:]
                            return False
                        sleeper(min(0.05, max(0.0, deadline - time.monotonic())))
        return True


def trim_jsonl(path: str | Path, limit: int) -> bool:
    """Trim to newest complete lines once at an explicit lifecycle boundary."""
    target = Path(path)
    if limit <= 0 or not target.exists() or target.stat().st_size <= limit:
        return True
    with _LOCK:
        with file_lock(target, timeout=5.0) as owned:
            if not owned:
                return False
            data = target.read_bytes()
            if len(data) <= limit:
                return True
            kept = data[-limit:]
            newline = kept.find(b"\n")
            kept = kept[newline + 1:] if newline >= 0 else b""
            saved = _replace_locked(
                target, kept, attempts=10, total_wait=3.0,
                replacer=os.replace, sleeper=time.sleep,
            )
            if not saved:
                _PENDING[_key(target)] = kept
            return saved


def pending_paths() -> tuple[Path, ...]:
    with _LOCK:
        return tuple(set(_PENDING) | set(_PENDING_LINES))
