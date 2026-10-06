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
  fingerprint TEXT PRIMARY KEY, applied_at TEXT, outcome TEXT, notes TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (fingerprint TEXT PRIMARY KEY, sent_at TEXT);
CREATE TABLE IF NOT EXISTS digested (fingerprint TEXT PRIMARY KEY, sent_at TEXT);
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    _migrate_outcomes(conn)
    conn.executescript(SCHEMA)
    return conn


# declined: the employer rejected him. His own turn-downs go in notes.
OUTCOMES = ("applied", "declined", "interviewed", "offer", "ghosted")


def _migrate_outcomes(conn) -> None:
    """Replace the first outcomes shape (applied flag, response, note).

    It was never written to, so it is dropped while empty. If it ever
    holds rows, stop rather than lose them.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(outcomes)")]
    if "applied" not in cols:
        return
    if conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]:
        raise RuntimeError("outcomes has rows in the old shape; migrate them by hand")
    conn.execute("DROP TABLE outcomes")
    conn.commit()


def record_outcome(conn, fingerprint: str, outcome: str, notes: str | None = None,
                   applied_at: str | None = None) -> None:
    """One row per posting, updated as it moves (applied, then interviewed).

    applied_at is kept from the first record unless given again. Notes
    accumulate, dated, so the history of a posting survives updates.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute("SELECT applied_at, notes FROM outcomes WHERE fingerprint=?",
                       (fingerprint,)).fetchone()
    old_applied, old_notes = row if row else (None, None)
    if notes:
        notes = f"{now[:10]} {outcome}: {notes}"
        notes = f"{old_notes}\n{notes}" if old_notes else notes
    else:
        notes = old_notes
    conn.execute(
        "INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?)",
        (fingerprint, applied_at or old_applied or now, outcome, notes, now),
    )
    conn.commit()


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


def mark_digested(conn, fingerprint: str) -> None:
    conn.execute("INSERT OR REPLACE INTO digested VALUES (?,?)",
                 (fingerprint, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def pending_digest(conn, floor: float, ceiling: float) -> list:
    """Everything scored into the digest band and not yet shown.

    The digest used to be assembled from whatever the 17:30 run happened
    to find in that one execution. Postings are discovered within an
    hour or two of going up, so the hourly runs found nearly all of
    them and the digest run found almost nothing: 60 to 79 scorers were
    recorded and silently never surfaced. This reads them back out of
    the database instead, so a role is shown once no matter which run
    scored it.

    Only the most recent score per posting counts, since re-scoring
    appends a row rather than replacing one.
    """
    return conn.execute(
        """
        SELECT p.company, p.title, p.url, p.posted_date, p.fingerprint,
               s.total, s.requirement_match, s.level_fit, s.location_fit, s.detail
        FROM scores s
        JOIN postings p ON p.fingerprint = s.fingerprint
        WHERE s.scored_at = (SELECT MAX(scored_at) FROM scores x
                             WHERE x.fingerprint = s.fingerprint)
          AND s.total >= ? AND s.total < ?
          AND s.fingerprint NOT IN (SELECT fingerprint FROM digested)
          AND s.fingerprint NOT IN (SELECT fingerprint FROM alerts)
        ORDER BY s.total DESC
        """,
        (floor, ceiling),
    ).fetchall()
