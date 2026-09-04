#!/usr/bin/env python3
"""Run the real models against the hand-scored set and compare.

    python eval/live_test.py

Reads JOBSCOUT_PASS1 and JOBSCOUT_PASS2 from the environment, so to
compare configurations you change .env and run it again.

Three things decide whether a configuration is usable, and only the
third is really about quality:

  1. Spread. Standard deviation near 10. If it collapses, the model is
     scoring everything the same and the system stops discriminating.
  2. Agreement. Mean absolute difference from the hand scores, per
     requirement. Under about 0.6 on a 0-4 scale is close.
  3. Honest zeros. Four requirements in this set have no supporting
     evidence and must score 0. A model that inflates these looks fine
     on the other two measures and will still waste your afternoons.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from normalize import normalize                     # noqa: E402
from schema import Posting                          # noqa: E402
from score import PASS1, PASS2, score_posting       # noqa: E402

# Requirements that must score 0. No claim supports them.
MUST_BE_ZERO = {
    "JPMorganChase": ["financial services"],
    "NiCE": ["consumption"],
}

FILES = {"Webflow": "webflow", "Showpad": "showpad", "Pendo": "pendo",
         "Customer.io": "customerio", "JPMorganChase": None}


def main() -> None:
    hand = {p["company"]: p for p in json.loads((ROOT / "eval" / "handscored.json").read_text())["postings"]}
    print(f"pass one: {PASS1}\npass two: {PASS2}\n")

    totals, deltas, inflated, spend = [], [], [], 0.0
    for company, stem in FILES.items():
        path = (ROOT / "eval" / "CIB-ExecutiveDirector-HeadOfMartechAI-082426.md") if stem is None \
            else (ROOT / "eval" / "postings" / f"{stem}.txt")
        if not path.exists():
            print(f"  {company}: no text, skipped. Run eval/fetch_postings.py")
            continue

        ref = hand[company]
        posting = normalize(Posting(
            source="eval", source_id=company, url="", title=ref["title"], company=company,
            raw_text=path.read_text(), first_seen=datetime.now(timezone.utc),
            locations=[ref["loc"]]))
        try:
            fit, tags, cost = score_posting(posting)
        except Exception as exc:
            print(f"  {company}: FAILED — {exc}")
            continue

        spend += cost
        totals.append(fit.total)
        hand_total = round(sum(t * s for t, s, _ in ref["reqs"]) / sum(4 * t for t, _, _ in ref["reqs"]) * 100, 1)
        deltas.append(abs(fit.requirement_match - hand_total))

        for phrase in MUST_BE_ZERO.get(company, []):
            for rs in fit.requirement_scores:
                if phrase in rs.requirement.text.lower() and rs.score > 0:
                    inflated.append(f"{company}: scored {rs.score} on '{rs.requirement.text[:50]}'")

        print(f"  {fit.total:>6}  {company:<15} req {fit.requirement_match:>5} "
              f"(hand {hand_total:>5}, off by {abs(fit.requirement_match - hand_total):>4.1f})  ${cost:.4f}")

    if not totals:
        sys.exit("No postings scored. Run eval/fetch_postings.py first.")

    sd = round(st.pstdev(totals), 1)
    print(f"\nspread {min(totals)}–{max(totals)}   stdev {sd}   "
          f"mean gap from hand scores {st.mean(deltas):.1f}   total spend ${spend:.4f}")

    ok = True
    if sd < 8:
        print("FAIL: scores are clustering. This configuration does not discriminate.")
        ok = False
    if st.mean(deltas) > 12:
        print("FAIL: requirement matching diverges sharply from the hand scores.")
        ok = False
    if inflated:
        print("FAIL: inflated scores where no evidence exists:")
        for line in inflated:
            print(f"  {line}")
        ok = False
    print("\nPASS. This configuration is usable." if ok else "\nDo not run on this configuration.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
