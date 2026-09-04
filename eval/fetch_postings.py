#!/usr/bin/env python3
"""Pull the eval postings' full text. Run once, from your machine.

The sandbox that built this project could not reach these boards, so
the text was never saved. These are the same four postings that were
hand-scored, plus room to add your own.

    python eval/fetch_postings.py

Writes eval/postings/*.txt. If a posting has been taken down, the
fetch fails for that one and the rest continue; hand-scored entries
without text are skipped by live_test.py.
"""
import html
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path(__file__).parent / "postings"
BOARD = {   # slug: greenhouse job id
    "webflow": "7540246",
    "showpad": "7736349",
    "pendo": "8096573002",
    "customerio": "7316308",
}


def clean(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for slug, job_id in BOARD.items():
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        try:
            with urlopen(Request(url, headers={"User-Agent": "jobscout/1.0"}), timeout=20) as r:
                job = json.load(r)
            text = f"{job['title']}\n{job.get('location', {}).get('name', '')}\n\n{clean(job['content'])}"
            (OUT / f"{slug}.txt").write_text(text)
            print(f"  {slug}: {len(text)} chars")
        except Exception as exc:
            print(f"  {slug}: FAILED ({exc}). Paste the text into {OUT / (slug + '.txt')} by hand.",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
