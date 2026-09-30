#!/usr/bin/env python3
"""Compare Jev to the current logic from data/shadow.jsonl.

    ./venv/bin/python eval/shadow_report.py

Read this before switching any jev mode to "on". The question that
matters is not average agreement but the low end: does Jev give credit
where the LLM pass, which is stingier, gave none?
"""
import json, statistics as st
from pathlib import Path

rows = [json.loads(l) for l in (Path(__file__).parent.parent / "data" / "shadow.jsonl").read_text().splitlines() if l]
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
