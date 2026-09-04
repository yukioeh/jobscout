"""Local state. SQLite because it is one file and needs no server.

Two jobs: remember which postings have already been seen so an hourly
poll doesn't re-alert on the same role, and keep every score for tuning.
The score history is the labelled set the handoff brief asked for. Record
what you applied to and what answered, and it becomes ground truth.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "jobscout.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
  fingerprint TEXT PRIMARY KEY, source TEXT, url TEXT, title TEXT, company TEXT,
  level TEXT, location_tier TEXT, first_seen TEXT, posted_date TEXT, raw_text TEXT
);
CREATE TABLE IF NOT EXISTS scores (
  fingerprint TEXT, scored_at TEXT, total REAL, requirement_match REAL,
  level_fit REAL, location_fit REAL, gated_reason TEXT, detail TEXT,
  PRIMARY KEY (fingerprint, scored_at)
);
CREATE TABLE IF NOT EXISTS outcomes (
  fingerprint TEXT PRIMARY KEY, applied INTEGER DEFAULT 0, response TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS alerts (fingerprint TEXT PRIMARY KEY, sent_at TEXT);
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    return conn


def seen(conn, fingerprint: str) -> bool:
    return conn.execute("SELECT 1 FROM postings WHERE fingerprint=?", (fingerprint,)).fetchone() is not None


def remember(conn, posting) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO postings VALUES (?,?,?,?,?,?,?,?,?,?)",
        (posting.fingerprint(), posting.source, posting.url, posting.title, posting.company,
         posting.level.value, posting.location_tier.value if posting.location_tier else None,
         posting.first_seen.isoformat(),
         posting.posted_date.isoformat() if posting.posted_date else None,
         posting.raw_text[:40000]),
    )
    conn.commit()


def record_score(conn, fingerprint: str, fit=None, gated_reason: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?)",
        (fingerprint, datetime.now(timezone.utc).isoformat(),
         fit.total if fit else None, fit.requirement_match if fit else None,
         fit.level_fit if fit else None, fit.location_fit if fit else None,
         gated_reason, fit.to_json() if fit else None),
    )
    conn.commit()


def already_alerted(conn, fingerprint: str) -> bool:
    return conn.execute("SELECT 1 FROM alerts WHERE fingerprint=?", (fingerprint,)).fetchone() is not None


def mark_alerted(conn, fingerprint: str) -> None:
    conn.execute("INSERT OR REPLACE INTO alerts VALUES (?,?)",
                 (fingerprint, datetime.now(timezone.utc).isoformat()))
    conn.commit()
