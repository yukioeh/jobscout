"""Posting sources.

Direct board APIs first: they return structured JSON, full descriptions,
and no terms-of-service problem. RSS covers LinkedIn, which has no
public API and prohibits scraping. That split is deliberate and is the
reason rss.app stays in the picture.

Add a company to config/sources.yaml and it gets polled. The board slug
is the last path segment of its careers URL.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import feedparser
import requests

from schema import Posting

TIMEOUT = 20
UA = {"User-Agent": "jobscout/1.0 (personal job search)"}


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def greenhouse(slug: str) -> list[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    jobs = requests.get(url, headers=UA, timeout=TIMEOUT).json().get("jobs", [])
    out = []
    for j in jobs:
        out.append(Posting(
            source="greenhouse", source_id=str(j["id"]), url=j["absolute_url"],
            title=j["title"], company=slug, raw_text=_clean(j.get("content", "")),
            first_seen=datetime.now(timezone.utc),
            posted_date=_parse(j.get("updated_at")),
            locations=[j.get("location", {}).get("name", "")],
        ))
    return out


def lever(slug: str) -> list[Posting]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = requests.get(url, headers=UA, timeout=TIMEOUT).json()
    if not isinstance(data, list):
        # An unknown or renamed slug returns 404 with a JSON body like
        # {"ok": false, "error": "Document not found"} rather than an
        # empty list. Iterating that dict yields its string keys, so
        # j["id"] below would fail on the string "ok" with a confusing
        # "string indices must be integers" instead of saying what's
        # actually wrong.
        raise ValueError(f"Lever board {slug!r} did not return a postings list: {data}")
    out = []
    for j in data:
        out.append(Posting(
            source="lever", source_id=j["id"], url=j["hostedUrl"], title=j["text"],
            company=slug, raw_text=_clean(j.get("descriptionPlain") or j.get("description", "")),
            first_seen=datetime.now(timezone.utc),
            posted_date=datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc),
            locations=[j.get("categories", {}).get("location", "")],
        ))
    return out


def ashby(slug: str) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    out = []
    for j in requests.get(url, headers=UA, timeout=TIMEOUT).json().get("jobs", []):
        out.append(Posting(
            source="ashby", source_id=j["id"], url=j["jobUrl"], title=j["title"],
            company=slug, raw_text=_clean(j.get("descriptionPlain") or ""),
            first_seen=datetime.now(timezone.utc), posted_date=_parse(j.get("publishedAt")),
            locations=[j.get("location", "")],
        ))
    return out


def rss(feed_url: str, label: str) -> list[Posting]:
    """LinkedIn and anything else that only exposes a feed.

    In practice, rss.app's LinkedIn feeds carry a full description, not
    just a title and a link -- the thin-posting worry below turned out
    not to apply to these specifically. The gate still refuses to score
    anything under 600 characters rather than trusting that to hold.
    """
    out = []
    for e in feedparser.parse(feed_url).entries:
        out.append(Posting(
            source=f"rss:{label}", source_id=e.get("id", e.link), url=e.link,
            title=_title_from(e), company=_company_from(e),
            raw_text=_clean(e.get("summary", "")),
            first_seen=datetime.now(timezone.utc), posted_date=_parse(e.get("published")),
            locations=_location_from(e),
        ))
    return out


def _company_from(entry) -> str:
    """LinkedIn titles arrive via rss.app in two shapes, and neither is
    guaranteed:

      "Title at Company — Location"
      "Company hiring Title in Location"

    The old version only handled the first, and got it wrong even then
    (kept the trailing "— Location" as part of the company). Every
    "hiring" title fell back to entry.author, which rss.app sets to the
    literal string "LinkedIn" — collapsing every such posting onto one
    fake company and breaking fingerprint()'s dedupe.
    """
    title = entry.get("title", "")
    if " hiring " in title:
        return title.split(" hiring ", 1)[0].strip()
    if " at " in title:
        company = title.rsplit(" at ", 1)[-1]
        company = re.split(r"\s+[—-]\s+", company, maxsplit=1)[0]
        return company.strip()
    return entry.get("author", "unknown")


def _location_from(entry) -> list[str]:
    """The location the LinkedIn title carries, which _title_from strips.

    Feed entries have no location field, so before this the only
    location signal was the body, and a stray "Remote employees will be
    considered" in an on-site Virginia posting read as fully remote.
    The title states the real one in both shapes rss.app produces.
    """
    title = entry.get("title", "")
    if " hiring " in title:
        rest = title.split(" hiring ", 1)[1]
        return [rest.rsplit(" in ", 1)[1].strip()] if " in " in rest else []
    if " at " in title:
        tail = title.rsplit(" at ", 1)[-1]
        parts = re.split(r"\s+[—-]\s+", tail, maxsplit=1)
        return [parts[1].strip()] if len(parts) > 1 else []
    return []


def _title_from(entry) -> str:
    """Strip the same "at Company — Location" / "Company hiring ... in
    Location" wrapper _company_from() parses, leaving the job title
    alone. Without this, the same real posting pulled by two different
    feed searches (a role can easily match more than one saved search)
    carries two different title strings, and fingerprint() -- which
    hashes company, title and location together -- treats them as two
    different postings. Confirmed live: the same LinkedIn job id showed
    up under both "boston-sales-enablement" and "boston-ai-transformation"
    with two different raw titles, and generated two separate alerts and
    two separate documents for the one role.
    """
    title = entry.get("title", "")
    if " hiring " in title:
        rest = title.split(" hiring ", 1)[1]
        return rest.rsplit(" in ", 1)[0].strip()
    if " at " in title:
        return title.rsplit(" at ", 1)[0].strip()
    return title.strip()


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(value)
        except Exception:
            return None


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
