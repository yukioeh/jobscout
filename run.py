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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import yaml

import store
from ingest.sources import FETCHERS, rss
from normalize import dedupe, normalize
from prefilter import keep as prefilter_keep
from schema import Level, Tier
from score import CONFIG, VALID_IDS, score_posting
from tailor.cover_letter import generate as generate_letter
from tailor.resume import tailor as tailor_resume
from tailor.select import select

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
        return f"title below director standing ({posting.level.value})"
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


def _digest_item(row) -> dict:
    """Rebuild an email item from a stored posting and its Fit JSON."""
    (company, title, url, posted_date, fingerprint,
     total, requirement_match, level_fit, location_fit, detail) = row
    fit = json.loads(detail) if detail else {"requirement_scores": []}
    must = [r for r in fit["requirement_scores"]
            if r["requirement"]["tier"] == Tier.MUST_HAVE.value]
    age = None
    if posted_date:
        try:
            age = int((datetime.now(timezone.utc)
                       - datetime.fromisoformat(posted_date)).total_seconds() // 3600)
        except ValueError:
            age = None
    return {"total": total, "requirement_match": requirement_match,
            "level_fit": level_fit, "location_fit": location_fit,
            "company": company, "title": title, "url": url,
            "fingerprint": fingerprint, "age_hours": age,
            "must_total": len(must),
            "must_strong": sum(1 for r in must if r["score"] >= 3),
            "must_zero": sum(1 for r in must if r["score"] == 0),
            "must_gaps": [r["requirement"]["text"] for r in must if r["score"] == 0][:3],
            "why": "; ".join(r["reasoning"] for r in sorted(
                fit["requirement_scores"], key=lambda r: -r["score"])[:2])}


def tailor_for(posting, fit, tags) -> tuple[Path, Path]:
    selection = select(tags, posting.title,
                       posting.location_tier.value if posting.location_tier else "")
    stem = f"{posting.company}-{posting.title}".replace(" ", "")[:48]
    resume = tailor_resume(
        ROOT / "templates" / "EricHarvey-Resume-Master.docx",
        ROOT / "out" / f"EricHarvey-Resume-{stem}.docx", selection)
    # Only requirements backed by a real claim can fill a T-table cell.
    # A requirement whose only citation is a known_gap has nothing to
    # say on the right-hand side, and a score above zero already
    # guarantees at least one real claim id.
    citable = [r for r in fit.requirement_scores
               if any(i in VALID_IDS for i in r.evidence_ids)]
    top = sorted(citable, key=lambda r: -r.score)[:4]
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

        # Must-have coverage, kept separate from the blended total. The
        # total mixes must_have with nice_to_have, so a role you match
        # on every hard requirement and a role you half-match on
        # everything can land on the same number. They are not the same
        # bet: a must_have scoring 0 is the requirement most likely to
        # screen you out before a human reads anything.
        must = [r for r in fit.requirement_scores if r.requirement.tier == Tier.MUST_HAVE]
        item = {"total": fit.total, "requirement_match": fit.requirement_match,
                "level_fit": fit.level_fit, "location_fit": fit.location_fit,
                "company": posting.company, "title": posting.title, "url": posting.url,
                "fingerprint": posting.fingerprint(),
                "age_hours": age_hours(posting),
                "must_total": len(must),
                "must_strong": sum(1 for r in must if r.score >= 3),
                "must_zero": sum(1 for r in must if r.score == 0),
                "must_gaps": [r.requirement.text for r in must if r.score == 0][:3],
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
            cover = f"must-haves {i['must_strong']}/{i['must_total']}"
            gaps = f", {i['must_zero']} with no evidence" if i["must_zero"] else ""
            print(f"  {i['total']:>5}  [{cover}{gaps}]  {i['company']} — {i['title']}")
            for gap in i.get("must_gaps", []):
                print(f"            no evidence: {gap[:66]}")
            for path in i.get("attachments", []):
                print(f"          {path}")
        return

    import notify
    if args.digest:
        # Read the band back out of the database rather than using this
        # run's batch. The hourly runs find nearly everything first, so
        # a per-run digest showed almost nothing.
        pending = [_digest_item(row) for row in store.pending_digest(
            conn, CONFIG["thresholds"]["digest_floor"],
            CONFIG["thresholds"]["tailor_and_alert"])]
        if pending:
            notify.send(f"Job digest — {len(pending)} worth a look", pending,
                        "Scored 60 to 79. No documents generated.")
            for item in pending:
                store.mark_digested(conn, item["fingerprint"])
    for item in alerts:
        if store.already_alerted(conn, item["fingerprint"]):
            continue
        notify.send(f"{item['total']} — {item['company']}: {item['title']}", [item],
                    "Above threshold. Drafts attached, review before sending.")
        store.mark_alerted(conn, item["fingerprint"])


if __name__ == "__main__":
    main()
