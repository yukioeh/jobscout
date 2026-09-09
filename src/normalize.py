"""Level and location detection, plus deduplication.

Level is read from the title first and the body second, because titles
lie in one specific direction: a Director title on an individual
contributor role is common, and the reverse is rare.
"""

from __future__ import annotations

import re

from schema import Level, LocationTier, Posting

# Order matters: the first pattern that matches wins, so anything
# carrying director-or-above standing is read before the junior titles
# at the bottom. "Associate Director" is a Director; "Executive
# Assistant" is not an executive.
TITLE_LEVELS = [
    (r"\b(svp|evp|senior vice president)\b", Level.VP),
    (r"\bexecutive director\b", Level.EXECUTIVE_DIRECTOR),
    (r"\b(vp|vice president)\b", Level.VP),
    (r"\b(senior|sr\.?) director\b", Level.SENIOR_DIRECTOR),
    (r"\b(head of|director)\b", Level.DIRECTOR),
    (r"\b(senior|sr\.?) manager\b", Level.SENIOR_MANAGER),
    (r"\bmanager\b", Level.MANAGER),
    # Unambiguously below director. These are gated in run.py rather
    # than scored, on the same reasoning as manager: no requirement
    # match should rescue a step backwards. Ambiguous senior titles
    # (lead, principal, head, owner, advisor, strategist) are
    # deliberately absent -- at a smaller company those are often the
    # top of the function, so they stay unknown, reach the model, and
    # are handled by level_key() in score.py instead of dropped here.
    (r"\b(specialist|coordinator|representative|assistant|analyst|intern)\b", Level.IC),
]

IC_SIGNALS = [
    r"individual contributor", r"\bno direct reports\b", r"this is an ic role",
    r"strategic,? individual contributor",
]

LOCATION_PATTERNS = [
    (LocationTier.REMOTE_US, [r"\bremote\b", r"work from anywhere", r"remote-first", r"distributed"]),
    (LocationTier.BAY_AREA, [r"san francisco", r"bay area", r"palo alto", r"mountain view", r"oakland", r"san jose"]),
    (LocationTier.CLEVELAND, [r"cleveland", r"akron", r"\bohio\b", r"columbus"]),
    (LocationTier.BOSTON, [r"boston", r"cambridge, ma", r"waltham", r"burlington, ma", r"massachusetts"]),
    (LocationTier.HYBRID_NORTHEAST, [r"new york", r"\bnyc\b", r"jersey city", r"newark", r"philadelphia", r"stamford"]),
    (LocationTier.INTERNATIONAL, [r"london", r"dublin", r"toronto", r"berlin", r"tokyo", r"singapore", r"bangalore"]),
]


def detect_level(posting: Posting) -> tuple[Level, bool]:
    """Returns (title level, has_direct_reports)."""
    title = posting.title.lower()
    level = Level.UNKNOWN
    for pattern, value in TITLE_LEVELS:
        if re.search(pattern, title):
            level = value
            break

    body = posting.raw_text.lower()
    ic = any(re.search(p, body) for p in IC_SIGNALS)
    reports = bool(re.search(r"\b\d+\s*(-|to|–)?\s*\d*\s*direct reports?\b", body)) or re.search(
        r"\b(lead|manage|build) (and \w+ )?(a )?(high-performing )?team\b", body
    )
    return level, bool(reports) and not ic


def detect_location(posting: Posting) -> LocationTier:
    blob = " ".join(posting.locations + [posting.title, posting.raw_text[:1500]]).lower()
    for tier, patterns in LOCATION_PATTERNS:
        if any(re.search(p, blob) for p in patterns):
            return tier
    return LocationTier.OTHER_US_ONSITE


def normalize(posting: Posting) -> Posting:
    level, has_reports = detect_level(posting)
    posting.level = level
    posting.location_tier = detect_location(posting)
    posting.remote_policy = "remote" if posting.location_tier == LocationTier.REMOTE_US else "onsite_or_hybrid"
    posting.has_direct_reports = has_reports
    return posting


def dedupe(postings: list[Posting]) -> list[Posting]:
    """Same role on four boards collapses to one. Prefers the source with
    the most text, since thin aggregator copies score badly."""
    best: dict[str, Posting] = {}
    for p in postings:
        key = p.fingerprint()
        if key not in best or len(p.raw_text) > len(best[key].raw_text):
            best[key] = p
    return list(best.values())
