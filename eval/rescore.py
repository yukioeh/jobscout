#!/usr/bin/env python3
"""Rerun the hand-scored eval set after any config change.

Watch the spread. If stdev drops much below 8 the dimensions have
stopped discriminating and the weights need attention.
"""
import json, statistics as st, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
cfg = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
data = json.loads((ROOT / "eval" / "handscored.json").read_text())
w, lv, lt = cfg["weights"], cfg["candidate"]["level_fit"], cfg["location"]["tiers"]
REMAP = {"ic": "director_ic", "manager": None, "senior_manager": None}

rows, gated = [], []
for p in data["postings"]:
    level = REMAP.get(p["level"], p["level"])
    if level is None:
        gated.append(p); continue
    earned = sum(t * s for t, s, _ in p["reqs"])
    possible = sum(4 * t for t, _, _ in p["reqs"])
    rm = round(100 * earned / possible, 1)
    total = round(rm * w["requirement_match"] + lv[level] * w["level_fit"]
                  + lt[p["loc"]] * w["location_fit"], 1)
    rows.append((total, rm, lv[level], lt[p["loc"]], p["company"], p["title"]))

rows.sort(reverse=True)
print(f"{'TOTAL':>6} {'REQ':>6} {'LVL':>5} {'LOC':>5}  ROLE")
for t, rm, l, loc, c, ti in rows:
    print(f"{t:>6} {rm:>6} {l:>5} {loc:>5}  {c} — {ti[:40]}")
for p in gated:
    print(f"{'GATE':>6} {'':>6} {'':>5} {'':>5}  {p['company']} — {p['title'][:40]}")

tots = [r[0] for r in rows]
sd = round(st.pstdev(tots), 1)
print(f"\nspread {min(tots)}–{max(tots)}  stdev {sd}  above 80: {sum(1 for t in tots if t >= 80)}")
if sd < 8:
    print("WARNING: scores are clustering. Check the weights.")
    sys.exit(1)
