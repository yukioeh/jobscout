#!/usr/bin/env python3
"""Compare Jev to the current logic from data/shadow.jsonl.

    ./venv/bin/python eval/shadow_report.py

Read this before switching any jev mode to "on". The question that
matters is not average agreement but the low end: does Jev give credit
where the LLM pass, which is stingier, gave none?

It also opens with a health check, since this is the one email that
arrives every day whatever was found. Failures here are silent
otherwise: the digest crashed on every run for three weeks and a dead
Jev key only prints to run.log. Anything wrong is flagged at the top
and in the subject line.
"""
import contextlib, io, json, os, smtplib, sqlite3, statistics as st, sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "shadow.jsonl"
RUN_LOG = ROOT / "data" / "run.log"
OFFSET = ROOT / "data" / "health_offset"     # run.log size at the last emailed report
DB = ROOT / "data" / "jobscout.db"


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def health() -> tuple[list[str], list[str], int]:
    """(lines, warnings, run.log size). Reads only; the offset moves on email."""
    lines, warn = [], []

    size = RUN_LOG.stat().st_size if RUN_LOG.exists() else 0
    start = int(OFFSET.read_text()) if OFFSET.exists() else 0
    if start > size:                              # log was truncated or rotated
        start = 0
    with open(RUN_LOG, "rb") as f:
        f.seek(start)
        new = f.read().decode(errors="replace")
    counts = {
        "tracebacks": new.count("Traceback"),
        "board fetch failures": sum(1 for l in new.splitlines() if l.startswith("  ") and " failed: " in l),
        "scoring failures": new.count("score failed"),
        "jev unavailable": new.count("jev unavailable"),
    }
    lines.append("since the last report: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if counts["tracebacks"]:
        warn.append(f"{counts['tracebacks']} tracebacks in run.log")
    if counts["jev unavailable"]:
        warn.append("Jev calls failing (key or outage)")

    conn = sqlite3.connect(DB)
    newest = _hours_since(conn.execute("SELECT MAX(first_seen) FROM postings").fetchone()[0])
    lines.append(f"newest posting seen {newest:.1f}h ago" if newest is not None else "no postings")
    if newest is None or newest > 3:
        warn.append("no new postings in 3h: is the hourly run firing?")

    cfg = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())["thresholds"]
    sys.path.insert(0, str(ROOT / "src"))
    import store
    pending = len(store.pending_digest(conn, cfg["digest_floor"], cfg["tailor_and_alert"]))
    last = _hours_since(conn.execute("SELECT MAX(sent_at) FROM digested").fetchone()[0])
    conn.close()
    lines.append(f"digest: {pending} waiting, last sent "
                 + (f"{last:.0f}h ago" if last is not None else "never"))
    if pending and (last is None or last > 30):
        warn.append("digest has postings waiting and has not sent in 30h")

    bdir = Path(os.environ.get("JOBSCOUT_BACKUP_DIR") or ROOT / "data" / "backups").expanduser()
    backups = sorted(bdir.glob("jobscout-*.db.gz"))
    if backups:
        age = (datetime.now().timestamp() - backups[-1].stat().st_mtime) / 3600
        lines.append(f"newest backup {backups[-1].name}, {age:.0f}h old")
        if age > 36:
            warn.append("no database backup in 36h")
    else:
        lines.append("no database backups")
        warn.append("no database backups")
    return lines, warn, size


if not LOG.exists():
    sys.exit("no shadow log yet")
rows = [json.loads(l) for l in LOG.read_text().splitlines() if l]
_out = io.StringIO()
_tee = contextlib.redirect_stdout(_out)
_tee.__enter__()

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
h_lines, h_warn, log_size = health()
print("health: " + ("CHECK" if h_warn else "ok"))
for w in h_warn:
    print(f"  ! {w}")
for l in h_lines:
    print(f"  {l}")
print()
early = [r for r in rows if r["kind"] == "early_career"]
p2 = [r for r in rows if r["kind"] == "pass_two"]

print(f"early-career: {len(early)} judged")
dis = [r for r in early if r["regex"] != (r["jev"] >= r["threshold"])]
for r in dis:
    who = "regex says early, Jev says no" if r["regex"] else "Jev says early, regex missed"
    print(f"  {who}: jev={r['jev']:.2f}  {r['company']} — {r['title'][:50]}")
print(f"  disagreements: {len(dis)}\n")

print(f"pass two: {len(p2)} postings scored by both")
if p2:
    d = [r["jev"] - r["llm"] for r in p2]
    print(f"  requirement_match, Jev minus LLM: mean {st.mean(d):+.1f}  stdev {st.pstdev(d):.1f}  range {min(d):+.1f} to {max(d):+.1f}")
    lo_j = lo_l = n = 0
    for r in p2:
        for l, j in zip(r["llm_scores"], r["jev_scores"]):
            if l == 0:
                n += 1; lo_j += j
    if n:
        print(f"  requirements the LLM scored 0: {n}; Jev's mean on those {lo_j / n:.2f}  (generous if well above 0)")
    cross = [r for r in p2 if (r["llm"] >= 80) != (r["jev"] >= 80)]
    print(f"  would change the requirement side of an 80 alert: {len(cross)}")


_tee.__exit__(None, None, None)
text = _out.getvalue()
print(text)

if "--email" in sys.argv:
    # Mail it to Eric and nobody else, same as every other message here.
    # Nothing to say until there is data to compare, unless something
    # is broken.
    if len(p2) + len(early) < 5 and not h_warn:
        sys.exit("too little shadow data to be worth an email yet")
    msg = EmailMessage()
    msg["Subject"] = (f"{'CHECK: ' + h_warn[0] + ' | ' if h_warn else ''}"
                      f"Jev shadow report: {len(p2)} scored, {len(early)} gated")
    msg["From"] = msg["To"] = os.environ["JOBSCOUT_TO"]
    msg.set_content(text + "\nSwitch modes in config/scoring.yaml (jev:) only after reading the low-end line.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(os.environ["JOBSCOUT_FROM"], os.environ["JOBSCOUT_APP_PASSWORD"])
        srv.send_message(msg)
    OFFSET.write_text(str(log_size))
    print("emailed")
