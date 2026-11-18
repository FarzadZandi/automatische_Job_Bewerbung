"""Perform one bounded public-feed request per configured company.

This diagnostic deliberately does not fetch individual job details and never
changes companies.yaml. Its JSON output is intended for a reviewed audit report.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
import yaml


def endpoint(ats: str, token: str) -> str:
    encoded = quote(token, safe="")
    return {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{encoded}/jobs?content=true",
        "lever": f"https://api.lever.co/v0/postings/{encoded}?mode=json",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{encoded}",
        "smartrecruiters": (
            f"https://api.smartrecruiters.com/v1/companies/{encoded}/postings"
            "?limit=100&offset=0&country=de"
        ),
    }[ats]


def row_count(ats: str, payload: object) -> int:
    if ats == "lever":
        if not isinstance(payload, list):
            raise ValueError("expected a JSON list")
        return len(payload)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    key = "content" if ats == "smartrecruiters" else "jobs"
    rows = payload.get(key, [])
    if not isinstance(rows, list):
        raise ValueError(f"expected {key!r} to be a list")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--delay", type=float, default=2.5)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    raw = yaml.safe_load(args.path.read_text(encoding="utf-8")) or {}
    companies = [row for row in raw.get("companies", []) if row.get("active", True)][:args.limit]
    session = requests.Session()
    session.headers["User-Agent"] = "jobfilter-company-feed-validator/1.0 (personal research)"
    last_request: dict[str, float] = {}
    results = []
    for row in companies:
        ats, token = str(row["ats"]), str(row["token"])
        url = endpoint(ats, token)
        host = urlsplit(url).netloc
        wait = args.delay - (time.monotonic() - last_request.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        started = time.monotonic()
        try:
            response = session.get(url, timeout=args.timeout)
            last_request[host] = time.monotonic()
            response.raise_for_status()
            count = row_count(ats, response.json())
            results.append({
                "name": row["name"], "ats": ats, "status": "ok",
                "http_status": response.status_code, "rows_in_response": count,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            })
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_request[host] = time.monotonic()
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            results.append({
                "name": row["name"], "ats": ats, "status": "failed",
                "http_status": status_code, "error": str(exc)[:300],
                "elapsed_seconds": round(time.monotonic() - started, 2),
            })
    print(json.dumps({"requests": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
