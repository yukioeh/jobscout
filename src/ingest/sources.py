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
    out = []
    for j in requests.get(url, headers=UA, timeout=TIMEOUT).json():
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

    Feeds carry a title and a link, rarely a full description. Postings
    from here are marked thin; the scorer refuses to score a posting it
    cannot read properly rather than guessing from a title.
    """
    out = []
    for e in feedparser.parse(feed_url).entries:
        out.append(Posting(
            source=f"rss:{label}", source_id=e.get("id", e.link), url=e.link,
            title=e.get("title", ""), company=_company_from(e),
            raw_text=_clean(e.get("summary", "")),
            first_seen=datetime.now(timezone.utc), posted_date=_parse(e.get("published")),
            locations=[],
        ))
    return out


def _company_from(entry) -> str:
    title = entry.get("title", "")
    return title.split(" at ")[-1].strip() if " at " in title else entry.get("author", "unknown")


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
