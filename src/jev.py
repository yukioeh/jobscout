"""TypeSafe Jev: typed judgments, used beside the LLM passes, not instead.

Jev returns a Score, Choice or Noul and never text, so it can only ever
select or rate. That suits three places here: gating on a yes/no
question that regexes get wrong, and re-rating requirements against the
claims file. It cannot extract requirements (pass one stays an LLM).

Every entry point is safe to call blindly: no key, a network error or a
malformed reply returns None, and the caller carries on with what it
had. Modes are set in config/scoring.yaml under `jev`:

  off     never called
  shadow  called and logged to data/shadow.jsonl, result ignored
  on      result used

Shadow exists because Jev has only been validated on 103 hand-scored
requirements. The failure that matters is being generous on the low end,
and seven low-scoring requirements cannot settle that.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.typesafe.ai/v1/systemone"
SHADOW_LOG = ROOT / "data" / "shadow.jsonl"
MAX_CHARS = 18000            # same cut pass one uses; state limit is 32k tokens

LEVELS = [
    "0: nothing in the claims supports this requirement",
    "1: thin; defensible in an interview but not on paper",
    "2: adjacent experience a hiring manager would accept with a follow-up",
    "3: direct evidence, older or unquantified",
    "4: direct, recent, quantified evidence",
]

EARLY_CAREER = {
    "type": "noul",
    "instructions": (
        "Is this role itself aimed at early-career or entry-level candidates, for example "
        "a stated 0-2 years of experience or recent graduates encouraged? Answer no if the "
        "role merely manages, coaches or hires early-career people."
    ),
}


def mode(cfg: dict, key: str) -> str:
    m = (cfg.get("jev") or {}).get(key, "off")
    return m if os.environ.get("TYPESAFE_API_KEY") else "off"


def _model(cfg: dict) -> str:
    # Pinned: an alias moves when a new release ships and the answers
    # behind it change without any change here.
    return (cfg.get("jev") or {}).get("model", "jev-1.13.0")


def ask(cfg: dict, state, questions: dict) -> dict | None:
    try:
        r = requests.post(
            URL, timeout=90,
            headers={"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"]},
            json={"state": state, "model": _model(cfg), "questions": questions},
        )
        r.raise_for_status()
        return r.json()["answers"]
    except Exception as exc:
        print(f"  jev unavailable: {str(exc)[:80]}")
        return None


def log(kind: str, **fields) -> None:
    try:
        SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SHADOW_LOG.open("a") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                "kind": kind, **fields}) + "\n")
    except OSError:
        pass


def early_career(cfg: dict, raw_text: str) -> float | None:
    """Probability the role targets early-career candidates, or None."""
    ans = ask(cfg, raw_text[:MAX_CHARS], {"early": EARLY_CAREER})
    return None if ans is None else float(ans["early"]["noul"])


def score_requirements(cfg: dict, reqs, claims: list[dict], gaps: list[dict]) -> list[float] | None:
    """Expected evidence score (0-4, fractional) per requirement, in order."""
    state = {"claims": yaml.safe_dump(claims, sort_keys=False),
             "known_gaps": yaml.safe_dump(gaps, sort_keys=False)}
    qs = {
        f"r{i}": {
            "type": "score",
            "instructions": {
                "question": (
                    "Using ONLY the candidate claims in `claims` (and `known_gaps`, which state "
                    "what the candidate lacks), how strongly does the evidence support this job "
                    "requirement? Do not use general knowledge about the candidate. If the claims "
                    "do not support it, the answer is the lowest level."),
                "requirement": r.text},
            "criteria": LEVELS,
        }
        for i, r in enumerate(reqs)
    }
    ans = ask(cfg, state, qs)
    if ans is None:
        return None
    try:
        return [float(ans[f"r{i}"]["score"]) for i in range(len(reqs))]
    except (KeyError, TypeError, ValueError):
        return None
