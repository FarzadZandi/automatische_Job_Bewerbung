from __future__ import annotations

from datetime import datetime

from jobfilter.metrics import append_jsonl, usage_summary


def test_usage_summary_reports_today_week_and_per_letter(tmp_path):
    path = tmp_path / "usage.jsonl"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    append_jsonl(path, {"time": now, "purpose": "letters", "jobs": 2, "input_tokens": 80, "output_tokens": 20})
    append_jsonl(path, {"time": now, "purpose": "form", "jobs": 1, "input_tokens": 5, "output_tokens": 5})
    assert usage_summary(path) == {"today_tokens": 110, "week_tokens": 110, "per_letter": 50, "per_form": 10}
