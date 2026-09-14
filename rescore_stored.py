#!/usr/bin/env python3
"""Re-score postings already in the database, from their stored text.

  ./venv/bin/python rescore_stored.py --dry-run
  ./venv/bin/python rescore_stored.py

run.py can only score what the feeds return right now, and the rss.app
feeds are a moving window rather than an archive: three days after a
posting appears it is usually gone from them. Of 92 postings that had
reached a digest, 15 were still in the feeds; of 21 that had alerted,
one was. So after a claims or scoring change, re-running run.py does
not revisit the backlog. It cannot see it.

The database is the only durable copy of those postings, including
their full text, which is why this reads from there and never fetches.
Use it when something changed that would alter scores across the board:
a claim added to the dossier, a weight, a threshold.

Alerts already sent are never sent again. Everything else that crosses
the alert threshold emails with documents, exactly as run.py would.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import run as pipeline           # gate(), tailor_for(), the item shape
import store
from normalize import normalize
from prefilter import keep as prefilter_keep
from schema import Posting, Tier
from score import CONFIG, VALID_IDS, score_posting


def stored_postings(conn) -> list[Posting]:
    rows = conn.execute(
        "SELECT fingerprint, source, url, title, company, raw_text, first_seen, posted_date"
        " FROM postings"
    ).fetchall()
    out = []
    for _, source, url, title, company, raw_text, first_seen, posted in rows:
        out.append(normalize(Posting(
            source=source, source_id="", url=url or "", title=title, company=company,
            raw_text=raw_text or "",
            first_seen=datetime.fromisoformat(first_seen),
            posted_date=datetime.fromisoformat(posted) if posted else None,
        )))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N scored, for a cheap trial")
    args = ap.parse_args()

    conn = store.connect()
    postings = stored_postings(conn)
    print(f"{len(postings)} stored postings")

    alerts, spend, scored = [], 0.0, 0
    for posting in postings:
        # Nothing is re-alerted. A role already emailed has been seen,
        # whatever its new number is.
        if store.already_alerted(conn, posting.fingerprint()):
            continue
        wanted, _ = prefilter_keep(posting.title)
        if not wanted or pipeline.gate(posting):
            continue

        try:
            fit, tags, cost = score_posting(posting)
        except Exception as exc:
            print(f"  failed: {posting.company} — {posting.title[:40]}: {exc}")
            continue
        spend += cost
        scored += 1
        store.record_score(conn, posting.fingerprint(), fit=fit)

        must = [r for r in fit.requirement_scores if r.requirement.tier == Tier.MUST_HAVE]
        item = {"total": fit.total, "requirement_match": fit.requirement_match,
                "level_fit": fit.level_fit, "location_fit": fit.location_fit,
                "company": posting.company, "title": posting.title, "url": posting.url,
                "fingerprint": posting.fingerprint(),
                "age_hours": pipeline.age_hours(posting),
                "must_total": len(must),
                "must_strong": sum(1 for r in must if r.score >= 3),
                "must_zero": sum(1 for r in must if r.score == 0),
                "must_gaps": [r.requirement.text for r in must if r.score == 0][:3],
                "breakdown": pipeline._breakdown(
                    fit.requirement_match, fit.level_fit, fit.location_fit),
                "fit_reasons": [
                    (r.requirement.text, r.evidence_ids)
                    for r in sorted(fit.requirement_scores, key=lambda r: -r.score)
                    if r.score >= 3 and r.evidence_ids][:3]}

        if fit.total >= CONFIG["thresholds"]["tailor_and_alert"]:
            if not args.dry_run:
                resume, letter = pipeline.tailor_for(posting, fit, tags)
                item["attachments"] = [resume, letter]
            alerts.append(item)
            print(f"  {fit.total:>5}  ALERT  {posting.company} — {posting.title[:44]}")
        else:
            print(f"  {fit.total:>5}         {posting.company} — {posting.title[:44]}")

        if args.limit and scored >= args.limit:
            print(f"  (stopping at --limit {args.limit})")
            break

    print(f"\n{scored} re-scored, {len(alerts)} above threshold, spend ${spend:.4f}")
    if args.dry_run:
        print("dry run: nothing sent")
        return

    import notify
    for item in alerts:
        notify.send(f"{item['total']} — {item['company']}: {item['title']}", [item],
                    "Re-scored from stored text after a dossier change. Drafts attached.")
        store.mark_alerted(conn, item["fingerprint"])


if __name__ == "__main__":
    main()
