"""Free triage before any model call.

Company boards return every open role. Paying a model to explain why
you are a poor fit for a Staff Kernel Engineer is the single largest
avoidable cost in this system: it is the difference between roughly
$20 a month and $267.

Matching happens on the title only, which is cheap and wrong sometimes.
Rejections are logged with their reason so you can audit what it threw
away and widen the vocabulary when it throws away something good.
"""

from __future__ import annotations

import re

KEEP = [
    r"enablement", r"\bgtm\b", r"go[- ]to[- ]market", r"revenue operations", r"\brevops\b",
    r"martech", r"marketing technology", r"marketing operations",
    r"\bai\b", r"artificial intelligence", r"generative", r"\bllm\b",
    r"transformation", r"adoption", r"readiness", r"sales systems",
    r"sales tools", r"productivity", r"learning", r"training", r"curriculum",
    r"change management", r"digital experience", r"platform strategy",
]

DROP = [
    r"engineer", r"developer", r"scientist", r"researcher", r"designer",
    r"recruiter", r"accountant", r"counsel", r"attorney", r"nurse",
    r"intern\b", r"apprentice", r"contract\b", r"account executive",
    r"\bsdr\b", r"\bbdr\b", r"quota", r"technician", r"analyst i\b",
]


def keep(title: str) -> tuple[bool, str]:
    t = title.lower()
    for pattern in DROP:
        if re.search(pattern, t):
            return False, f"title matched exclusion /{pattern}/"
    for pattern in KEEP:
        if re.search(pattern, t):
            return True, f"title matched /{pattern}/"
    return False, "title matched no target vocabulary"
