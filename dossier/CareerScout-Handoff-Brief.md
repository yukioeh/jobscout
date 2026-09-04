# Handoff Brief — Job Match & Tailoring Automation

For starting a fresh Claude Code session. Everything the new session needs to know without re-reading the chat that produced it.

---

## What to bring into the new session

Put these in the project directory before you start:

- `EricHarvey-Master-Dossier-v3.md` — source of truth for all claims. The **Claims Discipline** table in it is the guardrail for anything generated.
- `EricHarvey-Resume-Master.docx` — the general resume, and the template the generator should produce.
- `EricHarvey-Resume-JPMC-MartechAI.docx` — a worked example of tailoring against a specific JD.
- `EricHarvey-CoverLetter-JPMC-MartechAI.docx` — the T-table cover letter format.
- `EricHarvey-Resume-Tailoring-Guide.md` — the modular bullet library and the three-edit tailoring rule.
- The JPMorgan JD text — a real input to test scoring against.

That set is enough to build and evaluate end to end without any of the conversation history.

---

## Prior work worth not repeating

An earlier attempt used a single-prompt Gemini call to score resume-to-JD fit. It produced **score clustering** — nearly everything landed in a narrow band, so the scores didn't discriminate. The fix already identified was a **structured two-pass scoring** approach. Start there rather than rediscovering it.

A DOCX template processing issue was also solved previously. Check that work before rebuilding document generation from scratch.

---

## System shape

Six stages. Build and test them independently before wiring them together.

**1. Ingest.** Pull postings from wherever you're sourcing. Respect terms of service — many boards prohibit scraping, and this eventually becomes a commercial product on careerscout.app, so build on APIs and permitted feeds rather than something you'd have to rip out later.

**2. Normalize.** Parse each posting into a consistent structure: title, level, company, location and remote policy, required qualifications, preferred qualifications, responsibilities, comp if stated. Level detection matters — Director vs Senior Director vs VP vs bank-ladder ED changes everything downstream.

**3. Score.** Two passes. First pass extracts and weights the requirements from the posting itself. Second pass scores the candidate against those extracted requirements one at a time, with evidence. Per-requirement scores that roll up beat one holistic number: they discriminate better and they tell you *why* something scored what it did, which is what makes the output actionable.

Score dimensions worth separating: requirement match, level fit, location fit, and dealbreakers (relocation, clearance, degree requirements). A job can be a 90 on requirements and a 0 on location.

**4. Decide.** Threshold plus dealbreaker rules determine what surfaces. Keep this a separate, inspectable step rather than folding it into the scorer, so you can tune it without touching the model prompts.

**5. Tailor.** For anything above threshold, generate the resume variant and cover letter. This is where the dossier and the claims table do the work. The generator picks bullets from the library and reorders them; it should not invent claims. Any generated line not traceable to the dossier is a bug.

**6. Notify.** Digest rather than real-time. A batched summary with scores, reasoning, and attached drafts.

---

## Design decisions to make early

**Where does the human stay in the loop?** Recommendation: everywhere that touches the outside world. Auto-generate drafts, never auto-apply and never auto-send. The failure mode of an automated applicant is reputational and irreversible.

**How does the system know what's true?** The dossier is structured prose right now. For a multi-user product it needs to become structured data — claims with evidence, metrics with sources, a per-claim confidence or defensibility flag. Worth designing for early even if v1 just reads markdown.

**What stops fabrication?** The single biggest risk in this product category. Every generated bullet should trace to a dossier entry. Build the traceability check before you build the generator, or you'll be debugging hallucinated accomplishments in a document someone is about to send to a hiring manager.

**Evaluation.** You need a labeled set of jobs with known good/bad fit to tune scoring against. Your own search generates this naturally — score everything, record what you actually applied to and what got a response, and use it as ground truth.

---

## Starter prompt for the new session

> I'm building a job matching and application tailoring system, initially for my own search and eventually as a product at careerscout.app.
>
> In this directory: my master career dossier, a master resume, a worked example of a tailored resume and cover letter for a specific role, a tailoring guide with a modular bullet library, and one real job description.
>
> Read all of it, then help me design and build the pipeline: ingest postings, normalize them into a structured schema, score fit with a two-pass approach (extract and weight requirements from the posting, then score the candidate against each with evidence), apply threshold and dealbreaker rules, generate tailored resume and cover letter drafts, and send me a digest.
>
> A prior single-prompt scoring attempt produced score clustering; two-pass structured scoring was the identified fix. Start there.
>
> Two hard constraints. Every generated claim must trace to the dossier — no invented accomplishments. And the system drafts but never submits; I review everything before it goes out.
>
> Start with the schema and the scoring pass. Don't build the whole pipeline before we've validated that scoring discriminates.

---

## Sequencing

Scoring first, document generation second. A tailoring engine that runs on badly ranked jobs is wasted work, and scoring is the harder problem. Get discrimination working against ten real postings you can hand-rank before you automate anything downstream.
