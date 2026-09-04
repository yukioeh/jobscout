"""Selection: which header, which summary lead, which bullets in which order.

Deterministic on purpose. The model's judgment already happened in
scoring, where it tagged and weighted the posting's requirements. Here
we rank library entries by overlap with those tags. That means the same
posting always produces the same resume, and when a bullet moves you can
point at the requirement that moved it.

Conditional bullets stay out unless the posting's tags call for them.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .resume import LIBRARY, Selection

ROOT = Path(__file__).resolve().parent.parent.parent
CLAIMS = yaml.safe_load((ROOT / "dossier" / "claims.yaml").read_text())
VALID_IDS = {c["id"] for c in CLAIMS["claims"]}


def _overlap(entry_tags: list[str], posting_tags: list[str]) -> int:
    return len(set(entry_tags) & set(posting_tags))


def _eligible(entry: dict, posting_tags: list[str]) -> bool:
    cond = entry.get("conditional_on_tags")
    return True if not cond else bool(set(cond) & set(posting_tags))


STOP_WORDS = {"director", "senior", "sr", "head", "of", "and", "the", "lead", "manager"}


def _title_words(title: str) -> set[str]:
    cleaned = "".join(c if c.isalnum() else " " for c in title.lower())
    return {w for w in cleaned.split() if w and w not in STOP_WORDS}


def select(posting_tags: list[str], posting_title: str = "") -> Selection:
    # The header is the highest-leverage edit on the page, so it is not
    # decided by tag overlap alone. Words from the posting title break
    # ties, because the point of the line is to echo what they called
    # the job back at them.
    words = _title_words(posting_title)
    header = max(
        LIBRARY["headers"],
        key=lambda h: (
            _overlap(h["tags"], posting_tags) + 2 * len(set(h["tags"]) & words),
            -LIBRARY["headers"].index(h),
        ),
    )
    lead = max(LIBRARY["summary_leads"], key=lambda s: _overlap(s["tags"], posting_tags))

    bullet_order: dict[str, list[str]] = {}
    swap_ins: list[tuple[str, str]] = []

    for role in ("servicenow", "vector", "sap"):
        entries = LIBRARY[role]
        in_master = [b for b in entries if b.get("in_master")]
        bench = [b for b in entries if not b.get("in_master") and _eligible(b, posting_tags)]

        ranked = sorted(in_master, key=lambda b: -_overlap(b["tags"], posting_tags))

        # A bench bullet only enters by displacing the weakest match, and
        # only if it actually scores higher. Count never grows.
        for cand in sorted(bench, key=lambda b: -_overlap(b["tags"], posting_tags)):
            if _overlap(cand["tags"], posting_tags) > _overlap(ranked[-1]["tags"], posting_tags):
                swap_ins.append((ranked[-1]["id"], cand["id"]))
                ranked[-1] = cand
                ranked.sort(key=lambda b: -_overlap(b["tags"], posting_tags))

        bullet_order[role] = [b["id"] for b in ranked]

    trace(bullet_order)
    return Selection(
        header=header["text"],
        summary_lead_id=lead["id"],
        bullet_order=bullet_order,
        swap_ins=swap_ins,
        matched_tags=sorted(set(posting_tags)),
    )


def trace(bullet_order: dict[str, list[str]]) -> None:
    """Every selected bullet must rest on a real claim. Fails loudly."""
    index = {b["id"]: b for role in ("servicenow", "vector", "sap") for b in LIBRARY[role]}
    for role, ids in bullet_order.items():
        for bid in ids:
            claim_ids = index[bid].get("claim_ids", [])
            if not claim_ids:
                raise ValueError(f"Bullet {bid} cites no claim")
            missing = set(claim_ids) - VALID_IDS
            if missing:
                raise ValueError(f"Bullet {bid} cites claims that do not exist: {sorted(missing)}")
