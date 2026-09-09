"""Level and location detection, plus deduplication.

Level is read from the title first and the body second, because titles
lie in one specific direction: a Director title on an individual
contributor role is common, and the reverse is rare.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from schema import Level, LocationTier, Posting

# dedupe() has to rank locations to pick which copy of a multi-metro
# posting survives, and the ranking already lives in the scoring config.
LOCATION_VALUES = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "config" / "scoring.yaml").read_text()
)["location"]["tiers"]

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
    # "associate" is safe here only because Associate Director matches
    # the director pattern above and never reaches this line.
    (r"\b(specialist|coordinator|representative|assistant|analyst|intern|associate)\b", Level.IC),
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


# Body phrases that actually establish a remote role, as opposed to a
# passing mention of the word. LinkedIn titles carry the office metro
# even for remote postings, so the stated location alone would bury a
# genuinely remote role at other_us_onsite; a bare \bremote\b anywhere
# in the body does the opposite and promoted an on-site Richmond role
# to 100. These are the phrasings the feeds actually use.
STRONG_REMOTE = [
    r"fully remote", r"100% remote", r"remote[- ]first", r"work from anywhere",
    r"work remotely", r"\bremote position\b", r"\bremote role\b", r"telecommute",
    r"\bremote\s*\(\s*us", r"\bu\.?s\.?\s*remote\b", r"\bremote\s*[-–]\s*us\b",
    r"\bamericas remote\b", r"\bremote\s*[-–]\s*americas\b",
]


def _tier_for(text: str, skip_remote: bool = False) -> LocationTier | None:
    for tier, patterns in LOCATION_PATTERNS:
        if skip_remote and tier is LocationTier.REMOTE_US:
            continue
        if any(re.search(p, text) for p in patterns):
            return tier
    return None


def detect_location(posting: Posting) -> LocationTier:
    """The posting's own location field wins over anything in the body.

    Scanning the body first read "join our team in Richmond, VA ...
    Remote employees will be considered" as remote, because \bremote\b
    matches anywhere and REMOTE_US is checked first. That turned an
    on-site Virginia role into location_fit 100 and pushed it over the
    alert threshold. A posting that names a city is on site in that
    city; a line further down saying remote might be considered is a
    hedge, not the arrangement.

    So: if the posting states a location, classify from that alone,
    falling back to other_us_onsite when it names somewhere unranked.
    Only a posting with no stated location is read from its body.
    """
    stated = " ".join(posting.locations).lower().strip()
    blob = " ".join([posting.title, posting.raw_text[:1500]]).lower()

    if stated and _tier_for(stated) is LocationTier.REMOTE_US:
        return LocationTier.REMOTE_US
    # Whole body, not the first 1500 characters: postings state the
    # arrangement well down the page (median position ~3,900 chars, so
    # the window caught 1 of 39). These are exact phrases, so reading
    # further costs nothing, unlike the geographic patterns below where
    # a stray city in the boilerplate would misfire.
    if any(re.search(p, posting.raw_text.lower()) for p in STRONG_REMOTE):
        return LocationTier.REMOTE_US
    if stated:
        return _tier_for(stated) or LocationTier.OTHER_US_ONSITE
    # STRONG_REMOTE already had its chance above, so the body fallback
    # does not get to call something remote off a bare mention.
    return _tier_for(blob, skip_remote=True) or LocationTier.OTHER_US_ONSITE


def normalize(posting: Posting) -> Posting:
    level, has_reports = detect_level(posting)
    posting.level = level
    posting.location_tier = detect_location(posting)
    posting.remote_policy = "remote" if posting.location_tier == LocationTier.REMOTE_US else "onsite_or_hybrid"
    posting.has_direct_reports = has_reports
    return posting


def _location_value(posting: Posting) -> float:
    tier = posting.location_tier.value if posting.location_tier else ""
    return LOCATION_VALUES.get(tier, 0)


def dedupe(postings: list[Posting]) -> list[Posting]:
    """Same role on four boards, or in six metros, collapses to one.

    Best location wins first: a role open in both Boston and London is
    the Boston one, and that is the copy worth surfacing. Text length
    breaks ties, since thin aggregator copies score badly.
    """
    best: dict[str, Posting] = {}
    for p in postings:
        key = p.fingerprint()
        current = best.get(key)
        if current is None or (_location_value(p), len(p.raw_text)) > (
                _location_value(current), len(current.raw_text)):
            best[key] = p
    return list(best.values())
