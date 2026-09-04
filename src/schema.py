"""Data shapes for the pipeline. Everything downstream reads these."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import hashlib
import json


class Tier(str, Enum):
    MUST_HAVE = "must_have"
    STRONGLY_PREFERRED = "strongly_preferred"
    NICE_TO_HAVE = "nice_to_have"


class Level(str, Enum):
    IC = "ic"
    MANAGER = "manager"
    SENIOR_MANAGER = "senior_manager"
    DIRECTOR = "director"
    SENIOR_DIRECTOR = "senior_director"
    EXECUTIVE_DIRECTOR = "executive_director"
    VP = "vp"
    UNKNOWN = "unknown"


class LocationTier(str, Enum):
    BAY_AREA = "bay_area"
    CLEVELAND = "cleveland"
    BOSTON = "boston"
    REMOTE_US = "remote_us"
    HYBRID_NORTHEAST = "hybrid_northeast"
    OTHER_US_HYBRID = "other_us_hybrid"
    OTHER_US_ONSITE = "other_us_onsite"
    INTERNATIONAL = "international"


@dataclass
class Posting:
    """A normalized job posting. Source adapters all produce this."""

    source: str
    source_id: str
    url: str
    title: str
    company: str
    raw_text: str
    first_seen: datetime
    posted_date: Optional[datetime] = None
    locations: list[str] = field(default_factory=list)
    remote_policy: Optional[str] = None
    comp_min: Optional[int] = None
    comp_max: Optional[int] = None
    level: Level = Level.UNKNOWN
    has_direct_reports: bool = False
    location_tier: Optional[LocationTier] = None
    required: list[str] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        """Dedupe key. The same role on four boards collapses to one.

        Deliberately excludes source and url, and normalizes company and
        title, because aggregators rewrite both in small ways.
        """
        norm = lambda s: "".join(c for c in s.lower() if c.isalnum())
        basis = f"{norm(self.company)}|{norm(self.title)}|{norm(self.locations[0] if self.locations else '')}"
        return hashlib.sha256(basis.encode()).hexdigest()[:16]


@dataclass
class Requirement:
    """One requirement extracted and weighted by scoring pass one."""

    text: str
    tier: Tier
    rationale: str


@dataclass
class RequirementScore:
    """Pass two, one requirement at a time, with traceable evidence."""

    requirement: Requirement
    score: int              # 0-4, see evidence_scale in config
    evidence_ids: list[str] # dossier claim ids. Empty means score must be 0.
    reasoning: str

    def validate(self) -> None:
        if self.score > 0 and not self.evidence_ids:
            raise ValueError(
                f"Score {self.score} with no dossier evidence: {self.requirement.text[:60]}"
            )


@dataclass
class Fit:
    """Rolled-up result. Dimensions stay visible, never collapsed early."""

    posting_fingerprint: str
    requirement_match: float     # 0-100, weighted roll-up of pass two
    level_fit: float
    location_fit: float
    total: float
    dealbreakers: list[str] = field(default_factory=list)
    requirement_scores: list[RequirementScore] = field(default_factory=list)
    scored_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def blocked(self) -> bool:
        return bool(self.dealbreakers)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, indent=2)
