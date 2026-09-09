"""Two-pass scoring.

Pass one reads the posting and nothing else. It does not see the
candidate, so requirements get weighted on what the employer asked for.

Pass two scores every requirement against the claims file in a single
call, but still one requirement at a time in the output: each gets its
own score, its own cited claim ids, and its own reasoning. Batching
here is a cost decision, not a rigor decision. Sending the 2,600-token
claims file once per requirement cost six times as much for the same
information.

Cited ids are checked against the claims file after the model returns.
A fabricated id raises. A score above zero without a real id is forced
back to zero.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from llm import RATES, Usage, complete, parse_json
from schema import Fit, Posting, Requirement, RequirementScore, Tier

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
CLAIMS = yaml.safe_load((ROOT / "dossier" / "claims.yaml").read_text())

VALID_IDS = {c["id"] for c in CLAIMS["claims"]}
GAP_IDS = {g["id"] for g in CLAIMS["known_gaps"]}

PASS1 = os.environ.get("JOBSCOUT_PASS1", "gemini:gemini-flash-latest")
PASS2 = os.environ.get("JOBSCOUT_PASS2", "anthropic:claude-sonnet-4-6")

THEME_TAGS = (
    "ai_adoption, ai_systems, content_production, llm_workflow, automation, martech, "
    "platform_portfolio, integration, product, roadmap, governance, controls, "
    "executive_reporting, capability_catalog, self_serve, playbook, people_leadership, "
    "coaching, training, curriculum, onboarding, change_management, revenue_impact, gtm, "
    "sales, enablement, measurement, roi, business_case, operating_model, multi_region, "
    "scale, thought_leadership, external_visibility, hands_on, saas, marketing"
)

PASS_ONE = """Read this job posting and extract what the employer requires.

Return JSON only. No preamble, no markdown fences.

Extract every distinct requirement. Do not merge two requirements
because they share a sentence, and do not split one into fragments.
Tier each:

- must_have: stated as required, or unmistakably central to the role
- strongly_preferred: stated as preferred, or clearly weighted heavily
- nice_to_have: mentioned once, peripheral, or one of many

Aim for 8 to 15. If the posting yields fewer than 6 it is too thin to
score; set "thin": true.

Also tag the posting with the themes it emphasizes, chosen only from
this list, so downstream document selection can match them:
{themes}

Schema:
{{"thin": false,
  "level": "ic|manager|senior_manager|director|senior_director|executive_director|vp|unknown",
  "level_evidence": "the words that decided level",
  "tags": ["..."],
  "requirements": [{{"text": "...", "tier": "must_have", "rationale": "why this tier"}}]}}

POSTING:
{posting}"""

PASS_TWO = """Score a candidate against a job's requirements using a fixed evidence file.

You may only use the claims below. Do not use general knowledge about
the candidate. Do not infer experience that is not written here. If the
claims do not support a requirement, the score is 0. A 0 is a correct
and useful answer. Inflating a 0 to a 2 destroys the value of this
system, and every inflated score costs the candidate a wasted day.

EVIDENCE SCALE
4: direct, recent, quantified evidence
3: direct evidence, older or unquantified
2: adjacent experience a hiring manager would accept with a follow-up
1: thin, defensible in an interview but not on paper
0: nothing in the claims supports this

Any score above 0 must cite at least one claim id that appears verbatim
in the claims below. Never invent an id. Known gaps are stated
explicitly; where a gap covers a requirement, score against that gap
honestly and cite the gap id.

Score each requirement independently. Do not let a strong score on one
requirement raise a weak one.

Return JSON only, an array in the same order as the requirements:
[{{"index": 0, "score": 0, "evidence_ids": [], "reasoning": "one or two sentences"}}]

CLAIMS:
{claims}

KNOWN GAPS:
{gaps}

REQUIREMENTS:
{requirements}"""


def extract_requirements(posting: Posting):
    text, usage = complete(
        PASS1,
        PASS_ONE.format(posting=posting.raw_text[:18000], themes=THEME_TAGS),
        max_tokens=4000,   # headroom above observed ~700-1000 completion tokens
    )
    data = parse_json(text)
    reqs = [
        Requirement(text=r["text"], tier=Tier(r["tier"]), rationale=r.get("rationale", ""))
        for r in data["requirements"]
    ]
    return reqs, data.get("level", "unknown"), data.get("tags", []), data.get("thin", False), usage


def eligible_claims(tags: list[str]) -> list[dict]:
    """Claims this posting is allowed to be scored against.

    A claim carrying conditional_on_tags is off the table unless the
    posting asks for it. The Authentic .AI writing is the case this
    exists for: it is real, but it only becomes relevant when a posting
    wants thought leadership or external visibility, and the site
    states it does not offer commercial services. select.py already
    enforced this for resume bullets. Pass two never saw the rule, so
    the scorer could cite it against anything -- and did, against "10+
    years of experience in product, research, applied research" -- and
    from there it flowed into cover letter cells.
    """
    out = []
    for c in CLAIMS["claims"]:
        cond = c.get("conditional_on_tags")
        if cond and not (set(cond) & set(tags)):
            continue
        out.append(c)
    return out


def score_requirements(reqs: list[Requirement],
                       tags: list[str] | None = None) -> tuple[list[RequirementScore], Usage]:
    allowed = eligible_claims(tags or [])
    allowed_ids = {c["id"] for c in allowed}
    numbered = "\n".join(f"{i}. [{r.tier.value}] {r.text}" for i, r in enumerate(reqs))
    prompt = PASS_TWO.format(
        claims=yaml.safe_dump(allowed, sort_keys=False),
        gaps=yaml.safe_dump(CLAIMS["known_gaps"], sort_keys=False),
        requirements=numbered,
    )
    text, usage = complete(PASS2, prompt, max_tokens=400 * len(reqs) + 500)
    rows = parse_json(text)
    if len(rows) != len(reqs):
        raise ValueError(f"Model returned {len(rows)} scores for {len(reqs)} requirements")

    out = []
    for row in sorted(rows, key=lambda r: r["index"]):
        cited = row.get("evidence_ids", []) or []
        invented = set(cited) - VALID_IDS - GAP_IDS
        if invented:
            raise ValueError(f"Model cited claim ids that do not exist: {sorted(invented)}")
        # A claim the posting never made eligible does not count, even
        # if the model produced it from somewhere. Dropping it here
        # means a score resting only on an ineligible claim falls back
        # to 0 through the existing no-evidence rule below.
        cited = [i for i in cited if i in allowed_ids or i in GAP_IDS]
        score = int(row["score"])
        if score > 0 and not any(i in allowed_ids for i in cited):
            score = 0                      # no evidence, no credit
        rs = RequirementScore(
            requirement=reqs[row["index"]], score=score,
            evidence_ids=cited, reasoning=row.get("reasoning", ""),
        )
        rs.validate()
        out.append(rs)
    return out, usage


def roll_up(scores: list[RequirementScore]) -> float:
    tiers = CONFIG["requirement_tiers"]
    earned = sum(s.score * tiers[s.requirement.tier.value] for s in scores)
    possible = sum(4 * tiers[s.requirement.tier.value] for s in scores)
    return round(100 * earned / possible, 1) if possible else 0.0


DIRECTOR_PLUS = {"director", "senior_director", "executive_director", "vp"}


def level_key(level: str, has_reports: bool, title_level: str = "unknown") -> str:
    """Which level_fit value the posting earns.

    Two readings of level exist and they are not interchangeable.
    `level` is pass one's read of the whole posting; `title_level` is
    what normalize.py read off the title alone.

    director_ic exists for a Director-titled role with no reports --
    the SAP expert track, the Workday "AI Strategist ... (Director,
    IC)" posting in the eval set -- and is worth a full 100 on purpose.
    But pass one labels any individual-contributor role "ic", including
    a Sales Enablement Specialist, and mapping every "ic" into
    director_ic handed those a 100 as well: the same standing as a
    Director, off a title that says the opposite. So an "ic" reading
    only reaches director_ic when the title itself carries
    director-or-above standing. Otherwise it is scored as what it is.
    """
    if level == "ic":
        return "director_ic" if title_level in DIRECTOR_PLUS else "ic"
    if level == "director" and not has_reports:
        return "director_ic"
    return level


def score_posting(posting: Posting) -> tuple[Fit, list[str], float]:
    """Returns the fit, the posting's theme tags, and what scoring cost."""
    reqs, level, tags, thin, u1 = extract_requirements(posting)
    if thin or not reqs:
        raise ValueError("Posting too thin to score")

    scores, u2 = score_requirements(reqs, tags)

    requirement_match = roll_up(scores)
    level_fit = float(CONFIG["candidate"]["level_fit"].get(
        level_key(level, posting.has_direct_reports,
                  posting.level.value if posting.level else "unknown"), 60))
    location_fit = float(CONFIG["location"]["tiers"].get(
        posting.location_tier.value if posting.location_tier else "", 40))

    w = CONFIG["weights"]
    total = round(requirement_match * w["requirement_match"]
                  + level_fit * w["level_fit"]
                  + location_fit * w["location_fit"], 1)

    cost = (u1.cost(RATES.get(PASS1.split(":", 1)[-1], (0, 0)))
            + u2.cost(RATES.get(PASS2.split(":", 1)[-1], (0, 0))))

    fit = Fit(
        posting_fingerprint=posting.fingerprint(),
        requirement_match=requirement_match, level_fit=level_fit,
        location_fit=location_fit, total=total, requirement_scores=scores,
    )
    return fit, tags, cost
