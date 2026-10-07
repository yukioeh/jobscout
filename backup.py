#!/usr/bin/env python3
"""Copy the database somewhere it can be recovered from.

  ./venv/bin/python backup.py

data/jobscout.db is the only archive this project has. The feeds are a
moving window, so a posting older than a few days exists nowhere else,
and the outcomes table is typed in by hand. Time Machine was the only
copy and it had stopped mounting its destination without anyone
noticing.

Uses SQLite's backup API rather than a file copy, so a backup taken
while the hourly run is writing is still consistent. Gzipped, one per
day, the newest KEEP kept.

Backups go to data/backups/ unless JOBSCOUT_BACKUP_DIR names somewhere
else. A copy on the same disk survives a bad migration or a mistaken
delete, not a dead disk; point it at an external drive or a synced
folder for that.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import store

KEEP = 14


def main() -> None:
    dest = Path(os.environ.get("JOBSCOUT_BACKUP_DIR") or ROOT / "data" / "backups").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    raw = dest / f"jobscout-{date.today():%Y%m%d}.db"
    out = raw.with_suffix(".db.gz")

    src = sqlite3.connect(store.DB)
    snap = sqlite3.connect(raw)
    with snap:
        src.backup(snap)
    snap.close()
    src.close()

    with open(raw, "rb") as f, gzip.open(out, "wb") as g:
        shutil.copyfileobj(f, g)
    raw.unlink()

    # Refuse to prune on a backup that cannot be read back.
    check = dest / "verify.db"
    with gzip.open(out, "rb") as g, open(check, "wb") as f:
        shutil.copyfileobj(g, f)
    try:
        conn = sqlite3.connect(check)
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        conn.close()
    finally:
        check.unlink()
    if ok != "ok":
        sys.exit(f"backup {out.name} failed integrity check: {ok}")

    old = sorted(dest.glob("jobscout-*.db.gz"))[:-KEEP]
    for p in old:
        p.unlink()
    print(f"backup {out} ({out.stat().st_size / 1e6:.1f} MB, {n} postings), "
          f"pruned {len(old)}")


if __name__ == "__main__":
    main()
