#!/usr/bin/env python3
"""Compare Jev to the current logic from data/shadow.jsonl.

    ./venv/bin/python eval/shadow_report.py

Read this before switching any jev mode to "on". The question that
matters is not average agreement but the low end: does Jev give credit
where the LLM pass, which is stingier, gave none?
"""
import contextlib, io, json, os, smtplib, statistics as st, sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "shadow.jsonl"
if not LOG.exists():
    sys.exit("no shadow log yet")
rows = [json.loads(l) for l in LOG.read_text().splitlines() if l]
_out = io.StringIO()
_tee = contextlib.redirect_stdout(_out)
_tee.__enter__()
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
    # Nothing to say until there is data to compare.
    if len(p2) + len(early) < 5:
        sys.exit("too little shadow data to be worth an email yet")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    msg = EmailMessage()
    msg["Subject"] = f"Jev shadow report: {len(p2)} scored, {len(early)} gated"
    msg["From"] = msg["To"] = os.environ["JOBSCOUT_TO"]
    msg.set_content(text + "\nSwitch modes in config/scoring.yaml (jev:) only after reading the low-end line.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
        srv.login(os.environ["JOBSCOUT_FROM"], os.environ["JOBSCOUT_APP_PASSWORD"])
        srv.send_message(msg)
    print("emailed")
