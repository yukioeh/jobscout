#!/usr/bin/env python3
"""Record what Eric applied to and what came back.

  ./venv/bin/python record.py Visa applied
  ./venv/bin/python record.py "Sherwin AI Adoption" interviewed --note "recruiter screen"
  ./venv/bin/python record.py 096b782dbc581d08 ghosted
  ./venv/bin/python record.py Uber applied --date 2026-10-02
  ./venv/bin/python record.py list

The query is a fingerprint or words matched against company and title,
all of which must appear. Postings that alerted or reached a digest are
listed first, since those are the ones that were put in front of him.
When more than one matches, it asks which.

This is the outcomes table CLAUDE.md calls the place real signal
accumulates. It only writes to the local database and never contacts
anyone. Outcomes: applied, declined, interviewed, offer, ghosted.
declined means they rejected him. If he turns a role down, the outcome
stays where it was and the reason goes in --note.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

import store

MAX_SHOWN = 10


def candidates(conn, query: str) -> list[tuple]:
    """(fingerprint, company, title, latest score, surfaced) best first."""
    latest = """
        SELECT p.fingerprint, p.company, p.title,
               (SELECT total FROM scores s WHERE s.fingerprint = p.fingerprint
                ORDER BY scored_at DESC LIMIT 1) AS total,
               CASE WHEN p.fingerprint IN (SELECT fingerprint FROM alerts) THEN 'alert'
                    WHEN p.fingerprint IN (SELECT fingerprint FROM digested) THEN 'digest'
                    ELSE '' END AS surfaced
        FROM postings p
    """
    exact = conn.execute(latest + " WHERE p.fingerprint = ?", (query,)).fetchall()
    if exact:
        return exact
    words = query.split()
    where = " AND ".join("(p.company || ' ' || p.title) LIKE ?" for _ in words)
    rows = conn.execute(latest + " WHERE " + where, [f"%{w}%" for w in words]).fetchall()
    rank = {"alert": 0, "digest": 1, "": 2}
    return sorted(rows, key=lambda r: (rank[r[4]], -(r[3] or 0)))


def show(i, row) -> str:
    fp, company, title, total, surfaced = row
    score = f"{total:5.1f}" if total is not None else "    -"
    return f"  {i:>2}. {score}  {surfaced:<6}  {company} | {title[:60]}  [{fp}]"


def pick(rows: list[tuple]) -> tuple | None:
    if len(rows) == 1:
        return rows[0]
    for i, r in enumerate(rows[:MAX_SHOWN], 1):
        print(show(i, r))
    if len(rows) > MAX_SHOWN:
        print(f"  ...and {len(rows) - MAX_SHOWN} more; narrow the query")
    if not sys.stdin.isatty():
        sys.exit("more than one match; use the fingerprint")
    answer = input("which? (number, blank to cancel) ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= min(len(rows), MAX_SHOWN):
        return None
    return rows[int(answer) - 1]


def list_outcomes(conn) -> None:
    rows = conn.execute(
        """
        SELECT o.outcome, o.applied_at, o.updated_at, p.company, p.title, o.notes
        FROM outcomes o LEFT JOIN postings p ON p.fingerprint = o.fingerprint
        ORDER BY o.applied_at DESC
        """
    ).fetchall()
    if not rows:
        print("nothing recorded yet")
        return
    for outcome, applied, updated, company, title, notes in rows:
        print(f"{outcome:<11} applied {applied[:10]}  updated {updated[:10]}  "
              f"{company} | {(title or '')[:60]}")
        for line in (notes or "").splitlines():
            print(f"            {line}")


def main() -> None:
    if sys.argv[1:] == ["list"]:
        list_outcomes(store.connect())
        return

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("query", help="fingerprint, or words from company and title")
    ap.add_argument("outcome", choices=store.OUTCOMES)
    ap.add_argument("--note", help="appended to the posting's notes, dated")
    ap.add_argument("--date", type=date.fromisoformat,
                    help="when you applied (YYYY-MM-DD), if not today")
    args = ap.parse_args()

    conn = store.connect()
    rows = candidates(conn, args.query)
    if not rows:
        sys.exit(f"no posting matches {args.query!r}")
    row = pick(rows)
    if row is None:
        sys.exit("nothing recorded")

    store.record_outcome(conn, row[0], args.outcome, args.note,
                         args.date.isoformat() if args.date else None)
    print(f"recorded {args.outcome}: {row[1]} | {row[2]}")


if __name__ == "__main__":
    main()
