#!/usr/bin/env python3
"""Hourly run: ingest, normalize, dedupe, score, decide, tailor, notify.

  python run.py              one pass
  python run.py --digest     send the daily digest of 60-79 scorers
  python run.py --dry-run    score and generate, send nothing

Schedule with cron:
  0 * * * * cd /path/to/jobscout && ./venv/bin/python run.py >> data/run.log 2>&1
  30 17 * * * cd /path/to/jobscout && ./venv/bin/python run.py --digest >> data/run.log 2>&1

Nothing here submits an application. Drafts only, always.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import yaml

import store
from ingest.sources import FETCHERS, rss
from normalize import dedupe, normalize
from prefilter import keep as prefilter_keep
from schema import Level
from score import CONFIG, score_posting
from tailor.cover_letter import generate as generate_letter
from tailor.resume import tailor as tailor_resume
from tailor.select import select

ROOT = Path(__file__).parent
SOURCES = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
GATED_TITLES = {Level.MANAGER, Level.SENIOR_MANAGER, Level.IC}


def collect() -> list:
    postings = []
    for board, slugs in SOURCES.items():
        if board not in FETCHERS:
            continue
        for slug in slugs:
            try:
                postings += FETCHERS[board](slug)
            except Exception as exc:
                print(f"  {board}/{slug} failed: {exc}")
    for feed in SOURCES.get("rss", []):
        if feed["url"].startswith("REPLACE"):
            continue
        try:
            postings += rss(feed["url"], feed["label"])
        except Exception as exc:
            print(f"  rss/{feed['label']} failed: {exc}")
    return postings


def gate(posting) -> str | None:
    """Hard stops, checked before any model call is spent."""
    if posting.level in GATED_TITLES:
        return f"title at or below manager ({posting.level.value})"
    if len(posting.raw_text) < 600:
        return "posting text too thin to score"
    body = posting.raw_text.lower()
    if "security clearance" in body or "ts/sci" in body:
        return "requires clearance"
    if "phd required" in body or "j.d. required" in body:
        return "requires a degree not held"
    return None


def age_hours(posting) -> int | None:
    if not posting.posted_date:
        return None
    return int((datetime.now(timezone.utc) - posting.posted_date).total_seconds() // 3600)


def tailor_for(posting, fit, tags) -> tuple[Path, Path]:
    selection = select(tags, posting.title)
    stem = f"{posting.company}-{posting.title}".replace(" ", "")[:48]
    resume = tailor_resume(
        ROOT / "templates" / "EricHarvey-Resume-Master.docx",
        ROOT / "out" / f"EricHarvey-Resume-{stem}.docx", selection)
    top = sorted(fit.requirement_scores, key=lambda r: -r.score)[:4]
    letter = generate_letter(
        ROOT / "templates" / "EricHarvey-CoverLetter-JPMC-MartechAI.docx",
        ROOT / "out" / f"EricHarvey-CoverLetter-{stem}.docx",
        team_name=f"{posting.company} Hiring Team",
        role_title=posting.title,
        lead_id=selection.summary_lead_id,
        location_tier=posting.location_tier.value,
        pairs=[(r.requirement.text, r.evidence_ids) for r in top if r.evidence_ids])
    return resume, letter


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = store.connect()
    postings = dedupe([normalize(p) for p in collect()])
    fresh = [p for p in postings if not store.seen(conn, p.fingerprint())]
    print(f"{len(postings)} postings, {len(fresh)} new")

    alerts, digest, spend = [], [], 0.0
    for posting in fresh:
        store.remember(conn, posting)

        # Free triage first. Never spend a model call on a role that was
        # never going to be relevant.
        wanted, why = prefilter_keep(posting.title)
        if not wanted:
            store.record_score(conn, posting.fingerprint(), gated_reason=f"prefilter: {why}")
            continue

        reason = gate(posting)
        if reason:
            store.record_score(conn, posting.fingerprint(), gated_reason=reason)
            continue

        try:
            fit, tags, cost = score_posting(posting)
        except Exception as exc:
            print(f"  score failed for {posting.company} — {posting.title}: {exc}")
            continue
        spend += cost
        store.record_score(conn, posting.fingerprint(), fit=fit)

        item = {"total": fit.total, "requirement_match": fit.requirement_match,
                "level_fit": fit.level_fit, "location_fit": fit.location_fit,
                "company": posting.company, "title": posting.title, "url": posting.url,
                "fingerprint": posting.fingerprint(),
                "age_hours": age_hours(posting),
                "why": "; ".join(r.reasoning for r in sorted(
                    fit.requirement_scores, key=lambda r: -r.score)[:2])}

        if fit.total >= CONFIG["thresholds"]["tailor_and_alert"]:
            resume, letter = tailor_for(posting, fit, tags)
            item["attachments"] = [resume, letter]
            alerts.append(item)
        elif fit.total >= CONFIG["thresholds"]["digest_floor"]:
            digest.append(item)

    print(f"scoring spend this run: ${spend:.4f}")
    if args.dry_run:
        for i in sorted(alerts + digest, key=lambda x: -x["total"]):
            print(f"  {i['total']:>5}  {i['company']} — {i['title']}")
            for path in i.get("attachments", []):
                print(f"          {path}")
        return

    import notify
    if args.digest and digest:
        notify.send(f"Job digest — {len(digest)} worth a look", digest,
                    "Scored 60 to 79. No documents generated.")
    for item in alerts:
        if store.already_alerted(conn, item["fingerprint"]):
            continue
        notify.send(f"{item['total']} — {item['company']}: {item['title']}", [item],
                    "Above threshold. Drafts attached, review before sending.")
        store.mark_alerted(conn, item["fingerprint"])


if __name__ == "__main__":
    main()
