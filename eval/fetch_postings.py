#!/usr/bin/env python3
"""Pull the eval postings' full text. Run once, from your machine.

The sandbox that built this project could not reach these boards, so
the text was never saved. These are the postings that were hand-scored
in eval/handscored.json, plus room to add your own.

Postings close. When one 404s here, it also means the company's board
no longer has it at that job id — check the board directly rather than
assuming a bad id, and if it's gone for good, hand-score a currently
open one and add it to both this file and handscored.json.

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
BOARD = {   # stem: (greenhouse board slug, job id)
    "webflow": ("webflow", "7540246"),
    "showpad": ("showpad", "7736349"),
    "pendo": ("pendo", "8096573002"),
    "customerio": ("customerio", "7316308"),
    "databricks-ai-transformation": ("databricks", "8735590002"),
    "databricks-product-marketing-ai": ("databricks", "8487145002"),
}


def clean(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for stem, (slug, job_id) in BOARD.items():
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        try:
            with urlopen(Request(url, headers={"User-Agent": "jobscout/1.0"}), timeout=20) as r:
                job = json.load(r)
            text = f"{job['title']}\n{job.get('location', {}).get('name', '')}\n\n{clean(job['content'])}"
            (OUT / f"{stem}.txt").write_text(text)
            print(f"  {stem}: {len(text)} chars")
        except Exception as exc:
            print(f"  {stem}: FAILED ({exc}). Paste the text into {OUT / (stem + '.txt')} by hand.",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
