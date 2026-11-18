from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMS = {
    "fbclid", "gclid", "ijt", "rru", "trk", "trkid", "trackingid", "ref", "refid", "source",
    "campaign", "campaignid", "mc_cid", "mc_eid",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith(("utm_", "trk_", "tracking_")) and key.casefold() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def identity_hash(title: str, employer: str) -> str:
    identity = " ".join(f"{title}\n{employer}".lower().split())
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()


class SeenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS seen_ads (
                    canonical_url TEXT PRIMARY KEY,
                    identity_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    employer TEXT NOT NULL,
                    board TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_seen_identity ON seen_ads(identity_hash);
                CREATE TABLE IF NOT EXISTS run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )

    def is_seen(self, url: str, title: str = "", employer: str = "") -> bool:
        canonical = canonicalize_url(url)
        digest = identity_hash(title, employer) if title or employer else None
        with self.connect() as db:
            if digest:
                row = db.execute(
                    "SELECT 1 FROM seen_ads WHERE canonical_url = ? OR identity_hash = ? LIMIT 1",
                    (canonical, digest),
                ).fetchone()
            else:
                row = db.execute("SELECT 1 FROM seen_ads WHERE canonical_url = ?", (canonical,)).fetchone()
        return row is not None

    def mark_seen(self, url: str, title: str, employer: str, board: str) -> None:
        canonical = canonicalize_url(url)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO seen_ads(canonical_url, identity_hash, title, employer, board, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    identity_hash=excluded.identity_hash,
                    title=excluded.title,
                    employer=excluded.employer,
                    board=excluded.board,
                    last_seen=excluded.last_seen
                """,
                (canonical, identity_hash(title, employer), title, employer, board, now, now),
            )

    def start_run(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO run_log(started_at, status) VALUES (?, 'running')",
                (now,),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE run_log SET finished_at=?, status=?, summary_json=? WHERE id=?",
                (now, status, json.dumps(summary, ensure_ascii=False), run_id),
            )

    def maintain(self, packaged_urls: set[str] | None = None, *, days: int = 60) -> int:
        """Prune optional cached text tables, when present, and compact the database."""
        packaged = {canonicalize_url(url) for url in (packaged_urls or set())}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        removed = 0
        with self.connect() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ad_text_cache" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(ad_text_cache)")}
                date_column = "fetched_at" if "fetched_at" in columns else "last_seen" if "last_seen" in columns else ""
                url_column = "canonical_url" if "canonical_url" in columns else "url" if "url" in columns else ""
                if date_column and url_column:
                    candidates = db.execute(
                        f"SELECT rowid, {url_column} FROM ad_text_cache WHERE {date_column} < ?", (cutoff,)
                    ).fetchall()
                    rowids = [row[0] for row in candidates if canonicalize_url(str(row[1])) not in packaged]
                    if rowids:
                        db.executemany("DELETE FROM ad_text_cache WHERE rowid = ?", ((rowid,) for rowid in rowids))
                        removed = len(rowids)
        with sqlite3.connect(self.path) as db:
            db.execute("VACUUM")
        return removed


