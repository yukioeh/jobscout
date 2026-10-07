# Working on jobscout

Read this before changing anything. It records decisions that were
argued through already, so they don't get relitigated or quietly
reversed. `README.md` covers how to run it; this covers why it is
shaped the way it is.

## What this is

A job monitor for Eric Harvey's own search. Polls boards hourly, scores
each posting against a claims file, and above 80 generates a tailored
resume and cover letter and emails them as attachments with the apply
link. Later it becomes a product at careerscout.app, but that product
will not run on this code and nothing here should be shaped by it.

## Non-negotiable

**It drafts, it never submits.** No code path in this project contacts
an employer. Do not add one. Do not add auto-apply, do not add
auto-send, do not email anyone but Eric. The failure mode of an
automated applicant is reputational and irreversible.

**Every generated line traces to a claim id** in `dossier/claims.yaml`.
The generator selects and orders; it does not write prose. A score above
zero must cite a real id, and this is enforced in code after the model
returns, not requested in a prompt. If you find yourself writing a new
bullet at generation time, stop: add it to `config/bullets.yaml` with
its claim ids instead.

**Claims discipline is enforced in the data.** Never "consolidated 17
platforms" (he integrated IRIS across 17 systems). Never "€7.58B in
revenue" (influenced total contract value). Never "built" or "deployed"
for the catalog or the in-CRM agent (designed, prototyped, unshipped).
The `caution:` fields in claims.yaml are load-bearing.

**Style rules apply to generated text.** No em dashes. No inflated
buzzwords: pivotal, seamless, robust, delve, testament. Vary sentence
length. These come from the dossier and Eric will notice.

## Decisions already made

**Level is title standing, not headcount.** A Director title with no
direct reports scores 100, same as one with a team. Eric spent fourteen
years on SAP's expert track without reports and presents that as a
credential. Manager and below are *gated out entirely* rather than
scored low, because no requirement match should rescue a step
backwards. This corrected an earlier rubric that scored an IC role at 0
and buried the highest-matching posting in the set.

**Location is a gate, not a score.** Reversed 2026-09-09, replacing
"location never zeroes a role." Scored as a dimension it behaved as a
near-binary: the four markets in play all landed 90 to 100, everything
else 10 to 55, and it produced 29% of the total spread on a 20% weight
while requirement match produced 53% on 60%. The score was largely
measuring whether he would move there. Dropping its weight to fix that
let a Wisconsin role and an NYC one outrank Boston and Bay Area ones,
so weight was never the lever.

`dealbreakers.locations_in_play` now holds remote, Cleveland, Boston
and the Bay Area; anything else is gated before a model call. What
remains in `location.tiers` is a small preference among acceptable
markets: remote and Cleveland at 100 because neither needs a move,
Boston and the Bay Area at 90. At a 10% weight that is about a point,
which is meant to break ties and nothing more.

NYC is gated. It is referral-track, and a referral argues the location
directly. Tokyo stays commented out in `config/scoring.yaml`: his top
location overall, but a separate workstream, agency-led, gated on visa
decisions. Do not uncomment it without being asked.

**Fit and competition are separate facts.** Remote scores 100 despite
drawing heavy applicant volume. The answer to volume is speed, surfaced
as posting age in the email, not a lower score. Do not blend them.

**Referral-track roles are out of scope.** JPMorgan scores 65 on this
rubric and that is the correct read of the paper version of that role,
which Eric agrees with. The referral is the thing the model cannot see.
Do not build a special case for it.

**The Authentic .AI is conditional.** It becomes eligible only when a
posting asks for thought leadership or external visibility, and it never
leads a document. Client work is attributed to Vector Creative Labs,
because the Authentic .AI site states it does not offer commercial
services.

**Attachments, not cloud storage.** Documents are ~20KB, they attach
cleanly, and this avoids an OAuth dependency. Local copies stay in
`out/`. Drive was considered and dropped.

**Pass two is batched but not holistic.** One call, but per-requirement
output with independent scores and cited evidence. This was a cost fix
(37k input tokens per posting down to 5.8k), not a rigor change. If you
ever collapse it to a single holistic score, scores cluster and the
system stops discriminating. That failure already happened once in an
earlier Gemini-based attempt and is the reason for the two-pass design.

**Pass one is stored, pass two is not.** The `extractions` table keeps
each posting's requirements, tiers, level and tags, keyed by a hash of
model, prompt, theme list and posting text. A re-score reuses them and
runs pass two only, so a change is measured against the same questions.
Editing the pass-one prompt or model invalidates the cache by itself;
`rescore_stored.py --reextract` forces it otherwise. The eval harness
passes no connection and always extracts fresh.

**Jev stays in shadow until the shadow log says otherwise.** Both
modes in `config/scoring.yaml` log to `data/shadow.jsonl` and change no
scores; `eval/shadow_report.py` is the evidence, emailed daily at 18:00.
Switch `early_career` to `on` only once Jev catches early-career postings
the regex misses. As of 2026-10-06 no early-career posting has reached
the check (0 of 94), so agreement there proves nothing. Switch
`pass_two` to `on` only once Jev's mean on requirements the LLM scored
0 settles near 0. It was 0.46 over 314 such requirements on
2026-10-06, generous at the bottom and compressed at the top (LLM 4s
average 3.6), and it would have moved one alert below 80. Inflated
zeros are the failure this whole design guards against, so a model
that adds them does not go live on lower variance alone.

## Before you change scoring

Run `python eval/rescore.py` after any edit to `config/scoring.yaml`. It
fails if the standard deviation drops below 8. Clustering means the
dimensions have stopped doing work.

Run `python eval/live_test.py` after changing models or prompts. It
checks spread, agreement with hand scores, and whether the four
requirements with no supporting evidence still come back 0. That third
check is the important one: a model that inflates zeros looks fine on
the other two and will cost Eric real afternoons.

`eval/handscored.json` is ground truth from 2026-09-03, hand-scored
against eight real postings. It is a starting point, not tuned. The
`outcomes` table in the database is where real signal accumulates:
what got applied to, what answered. Eric records it by hand with
`record.py`. `declined` means the employer rejected him; when he turns
a role down it goes in the note, not the outcome.

## Known rough edges

- The pre-filter matches on title only, so a good role with an unusual
  title is dropped silently. Rejections log their reason; audit `scores`
  for `prefilter:` rows occasionally.
- `has_direct_reports` false-positives when a description mentions
  individual contributors as part of the team being led. Not scored,
  affects one cover letter sentence.
- RSS entries rarely carry full descriptions. The gate refuses to score
  anything under 600 characters rather than guessing from a title.
- Silent Jev failures. A rejected key or an outage prints
  `jev unavailable` to `data/run.log` and the run carries on, so a
  dead key shows up only as `data/shadow.jsonl` going stale.

## Where things live

```
config/scoring.yaml     weights, level values, location tiers, gates, thresholds
config/bullets.yaml     bullet library, headers, summary leads
config/sources.yaml     boards to poll, rss.app feed URLs
dossier/claims.yaml     the evidence file. the only source of truth
src/score.py            two passes
src/tailor/             docx editing, never rebuilding
eval/                   the harness that says whether a change was good
```
