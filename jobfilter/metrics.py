"""Small append-only usage and timing logs used by the local app."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping


def append_jsonl(path: str | Path, record: Mapping[str, object]) -> None:
    from .safeio import append_jsonl as safe_append
    safe_append(path, record)


def usage_record(
    answer: Mapping[str, object], *, purpose: str, jobs: int, model: str,
    fields: int | None = None, chars_out: int = 0,
) -> dict[str, object]:
    usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
    result: dict[str, object] = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": purpose, "jobs": max(0, int(jobs)), "model": model,
        "fields": max(0, int(jobs if fields is None else fields)),
        "chars_out": max(0, int(chars_out)),
    }
    for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        result[key] = int(usage.get(key) or answer.get(key) or 0)
    for key in ("total_cost_usd", "duration_ms"):
        value = answer.get(key)
        if value is not None:
            result[key] = float(value)
    return result


def usage_summary(path: str | Path) -> dict[str, object]:
    now = datetime.now().astimezone()
    today = now.date()
    week = now - timedelta(days=7)
    today_tokens = week_tokens = letters = letter_tokens = forms = form_tokens = 0
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(str(row.get("time")))
        except (ValueError, TypeError):
            continue
        tokens = sum(int(row.get(key) or 0) for key in (
            "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"
        ))
        if when.date() == today:
            today_tokens += tokens
        if when >= week:
            week_tokens += tokens
        if row.get("purpose") in {"letter", "letters"}:
            count = int(row.get("jobs") or 0)
            letters += count
            letter_tokens += tokens
        if row.get("purpose") in {"form", "suggest"}:
            count = int(row.get("jobs") or 0)
            forms += count
            form_tokens += tokens
    return {
        "today_tokens": today_tokens, "week_tokens": week_tokens,
        "per_letter": round(letter_tokens / letters) if letters else 0,
        "per_form": round(form_tokens / forms) if forms else 0,
    }
