# jobscout

A job monitor for one person. Polls boards hourly, scores each posting
against a claims file, and for anything above 80 generates a tailored
resume and cover letter and emails them with the apply link.

It drafts. It never submits.

---

## Setup

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill it in
./venv/bin/python run.py --dry-run
```

`.env` needs a model key, a Gmail app password (not your account
password), and the two rss.app feed URLs in `config/sources.yaml`.

Which model runs which pass is set per pass:

```
JOBSCOUT_PASS1=gemini:gemini-flash-latest      # extraction
JOBSCOUT_PASS2=anthropic:claude-sonnet-4-6     # judgment
```

Pass one is mechanical extraction and a cheap model handles it. Pass
two is judgment against a fixed evidence file, and its failure mode is
agreeableness: scoring a 2 where the honest answer is 0. Test before
trusting a cheaper model there:

```bash
python eval/fetch_postings.py     # once, pulls the eval postings' text
python eval/live_test.py          # scores them, compares to hand scores
```

It checks three things: that scores still spread (stdev near 10), that
requirement matching tracks the hand scores, and that the four
requirements with no supporting evidence still come back 0. It exits
non-zero if the configuration is not usable. Change `.env` and rerun to
compare configurations. Costs about forty cents per run.

Schedule it:

```
0  *  * * *  cd ~/jobscout && ./venv/bin/python run.py            >> data/run.log 2>&1
30 17 * * *  cd ~/jobscout && ./venv/bin/python run.py --digest   >> data/run.log 2>&1
```

Above 80 emails immediately with documents. 60 to 79 rolls into one
digest at 5:30pm with no documents. Below 60 is logged and never shown.

---

## How it works

**Ingest** (`src/ingest/sources.py`) hits Greenhouse, Lever and Ashby
board APIs directly for full posting text, and reads RSS for LinkedIn,
which has no public API and prohibits scraping. Add a company to
`config/sources.yaml` and it gets polled.

**Normalize** (`src/normalize.py`) detects title level and location
tier, and deduplicates. The same role appears on four boards; the
fingerprint collapses them and keeps the copy with the most text.

**Score** (`src/score.py`) runs two passes. Pass one reads the posting
without seeing the candidate and extracts requirements with weights, so
the employer's priorities are set by the employer. Pass two scores one
requirement at a time against `dossier/claims.yaml`, and any score above
zero must cite claim ids that exist. A fabricated id raises. No
evidence, no credit.

Four dimensions stay separate: requirement match (60%), level (20%),
location (20%), plus hard gates. A role can be a 90 on requirements and
still not reach you.

**Tailor** (`src/tailor/`) edits the master .docx rather than rebuilding
it, so fonts, spacing and margins are untouched. Four changes only: the
header line, the location segment of the contact line, the summary
opening, and bullet order with one-for-one swap-ins. Bullet count never
grows and the page count never moves.

The location line names the market a posting is hiring for and says
Eric is open to relocating there. Home is Chagrin Falls, OH. It never
claims he already lives somewhere he doesn't; the wording lives in
`config/bullets.yaml` under `location_lines`.

**Notify** (`src/notify.py`) attaches both documents and mails Eric and
nobody else. No cloud storage, no OAuth. Local copies stay in `out/`.

Before any of that, `src/prefilter.py` drops postings whose titles were
never relevant. Company boards return every open role, and paying a
model to explain why you are a poor fit for a Staff Kernel Engineer is
the largest avoidable cost here: roughly $20 a month with the filter,
$267 without. Rejections are logged with their reason, so audit
`scores` for `prefilter:` rows now and then and widen the vocabulary in
that file when it drops something good.

---

## The rules this thing follows

Every generated line traces to a claim id in `dossier/claims.yaml`. The
generator selects and orders; it does not write. If a sentence appears
in a document that isn't in the claims file, that's a bug worth
stopping for.

Claims discipline from the dossier is enforced in the data, not in a
prompt. Never "consolidated 17 platforms," never "€7.58B in revenue,"
never "built" for the two prototypes.

Title aims up. Manager and below are gated out entirely rather than
scored low, because no requirement match should rescue a step
backwards. A Director title with no direct reports is a full-value role;
headcount is not what level measures here.

The Authentic .AI is a conditional claim. It only becomes eligible when
a posting asks for thought leadership or external visibility, and it
never leads a document.

Referral-track roles are out of scope by design. JPMorgan scores 65 on
this rubric and that is the correct read of the paper version of that
role. The referral is the thing the model can't see.

---

## Known rough edges

- `has_direct_reports` false-positives when a description mentions
  individual contributors as part of the team being led. It isn't
  scored, so the only effect is one sentence in the cover letter.
- RSS entries carry a title and a link but rarely a full description.
  The gate refuses to score a posting under 600 characters rather than
  guessing from a title, so LinkedIn finds may need a manual fetch.
- The pre-filter matches on title only. A well-suited role with an
  unusual title gets dropped for free and silently. Check the logged
  rejections occasionally.
- Scoring has been validated against eight postings hand-scored on
  2026-09-03 (`eval/handscored.json`). That's a starting point, not a
  tuned model. Record what you applied to and what answered, in the
  `outcomes` table with `record.py`, and retune against real signal.

---

## Tuning

`config/scoring.yaml` holds every number: weights, level values,
location tiers, thresholds, gates. Change it and rerun the eval:

```bash
./venv/bin/python eval/rescore.py
```

If scores start clustering, the weights are doing nothing and the
dimension causing it is the one to look at first.
