"""Cover letter generation, T-table format.

Same principle as the resume: the template is edited, never rebuilt, so
the layout, the table borders and the signature image survive untouched.

The left column is the employer's own words, shortened. The right column
is assembled from claim text and metrics. No sentence is written at
generation time that isn't traceable to a claim id.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from lxml import etree

from tailor._docx import merge_runs, repack, unpack

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent.parent
CLAIMS = {c["id"]: c for c in yaml.safe_load((ROOT / "dossier" / "claims.yaml").read_text())["claims"]}

# Opening paragraphs. One per lead, matching the resume's summary lead
# so the two documents tell the same story.
OPENERS = {
    "LEAD-ADOPTION": (
        "I lead enablement teams that build AI systems rather than just run AI training. At "
        "ServiceNow I led the AI Sales Coach enablement program that took adoption to 85% across a "
        "6,000-person go-to-market organization, and directed the build of an AI content production "
        "system that cut enablement asset creation time by 80%. The {role} role is that same work, "
        "and it is the work I want to be doing."
    ),
    "LEAD-SYSTEMS": (
        "I build the AI systems and lead the teams that deliver on top of them. At ServiceNow I "
        "directed the build of an AI content production system that cut enablement asset creation "
        "time by 80%, with an automated review pass on its own output, and led the enablement "
        "program that took AI Sales Coach adoption to 85% across 6,000 sellers. The {role} role is "
        "that same work, and it is the work I want to be doing."
    ),
    "LEAD-PLATFORM": (
        "I lead platform portfolios and the AI programs that make them worth owning. At SAP I was "
        "business owner for IRIS and integrated it across a 17-system sales and content stack, "
        "lifting adoption 80%. At ServiceNow I directed the build of an AI content production system "
        "that cut asset creation time by 80% and led the program that took AI adoption to 85% across "
        "6,000 people. The {role} role is that same work, and it is the work I want to be doing."
    ),
    "LEAD-REVENUE": (
        "I lead enablement programs that move pipeline, not just attendance. At ServiceNow I directed "
        "the team that generated $76.3M in influenced pipeline and $45M in net annual contract value, "
        "and led the enablement program that took AI Sales Coach adoption to 85% across 6,000 sellers. "
        "The {role} role is that same work, and it is the work I want to be doing."
    ),
}

DEFAULT_TEAM_SENTENCE = (
    "I led a team of seven at ServiceNow and spent as much time coaching them to build with AI "
    "as I did building myself. That is the operating model I would bring here."
)

# Home is Chagrin Falls, OH, in the Eastern time zone. These lines are
# the one part of a generated document that does not trace to a claim
# id, so they are written to be literally true. Two of them were not:
# the Boston line put Eric in a city he does not live in, and the Bay
# Area line described a move already under way. Openness to relocation
# is the honest version of both, and it is what a hiring manager
# actually needs to know.
# Home is Chagrin Falls, OH, in the Eastern time zone. These lines are
# the one part of a generated document that does not trace to a claim
# id, so they are written to be literally true. For Boston and the Bay
# Area the resume carries a target-market address, so these commit to
# being on site rather than describing where Eric currently lives:
# saying "open to relocating" underneath a Brookline address would
# contradict the page.
CLOSERS = {
    "remote_us": "I work from the Eastern time zone and am set up for a fully distributed team.",
    "boston": "I can be on site in the Greater Boston area as the role requires.",
    "bay_area": "I can be on site in the Bay Area as the role requires.",
    "cleveland": "I am in the Greater Cleveland area and can be on site as the role requires.",
    "hybrid_northeast": "I am open to relocating for this role and can be on site as it requires.",
    "other_us_hybrid": "I am open to relocating for this role and can be on site as it requires.",
    "other_us_onsite": "I am open to relocating for this role and can be on site as it requires.",
}


def _sentences(text: str) -> list[str]:
    """Split on sentence ends only. A period inside $76.3M is not one."""
    out, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in ".?!" and (i + 1 >= len(text) or text[i + 1] == " "):
            if not (ch == "." and i + 1 < len(text) and text[i + 1 : i + 2].isdigit()):
                out.append(buf.strip())
                buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def trim(text: str, limit: int) -> str:
    """Cut to the limit on a word boundary. No ellipsis: a cell that
    trails off reads worse than one that simply stops."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")


def evidence_line(claim_ids: list[str], limit: int = 225) -> str:
    """Assemble the right-hand cell from claim text and metrics.

    Metrics are the point of the cell, so they are placed first in the
    budget and prose is trimmed around them. Trimming happens on whole
    sentences: a cell that ends mid-figure is worse than a short cell.
    """
    # Pass two is allowed to cite a known_gap id, and does: it is how a
    # requirement gets scored honestly against a gap. Those ids are not
    # in CLAIMS, so indexing them raised KeyError and killed the run
    # partway through generating an above-threshold alert. They also
    # have no business in this cell, which states qualifications -- a
    # gap belongs in the interview, not in the letter.
    claim_ids = [cid for cid in claim_ids if cid in CLAIMS]
    if not claim_ids:
        return ""

    # A conditional claim never leads. It can support a cell, but the
    # first sentence a reader sees is a claim that stands on its own.
    claim_ids = sorted(claim_ids, key=lambda cid: bool(CLAIMS[cid].get("conditional_on_tags")))

    # Reserve each claim's metrics as that claim earns its way in, not
    # for every candidate up front. Reserving for the whole list meant
    # a cell citing three metric-heavy claims budgeted for all of their
    # numbers, left too little room for even the first sentence, and
    # rendered completely empty -- a blank cell in the T-table.
    body, used, reserve = "", [], 0
    for cid in claim_ids:
        cost = sum(len(m) + 2 for m in CLAIMS[cid].get("metrics", []))
        before = body
        for sentence in _sentences(" ".join(CLAIMS[cid]["text"].split())):
            candidate = (body + " " + sentence).strip()
            if len(candidate) > limit - (reserve + cost):
                break
            body = candidate
        if body != before:
            used.append(cid)
            reserve += cost

    # Whole-sentence trimming has nothing to fall back on when the
    # first sentence alone exceeds the budget, and returned an empty
    # cell. A trimmed sentence beats a blank column, so cut the leading
    # claim to fit rather than saying nothing.
    if not body:
        lead = claim_ids[0]
        cost = sum(len(m) + 2 for m in CLAIMS[lead].get("metrics", []))
        cut = trim(" ".join(CLAIMS[lead]["text"].split()), max(60, limit - cost))
        # Back up to the last clause boundary. A word-boundary cut can
        # stop on "working with", and the metrics tail then reads as
        # the end of that clause: "working with 80% lift in adoption",
        # which is not a sentence and is not true.
        boundary = max(cut.rfind(", "), cut.rfind("; "), cut.rfind(") "))
        if boundary > len(cut) * 0.5:
            cut = cut[:boundary]
        body = cut.rstrip(" ,;:-") + "."
        used = [lead]

    metrics = [m for cid in used for m in CLAIMS[cid].get("metrics", [])]
    tail = (" " + ", ".join(metrics) + ".") if metrics else ""
    return (body + tail).strip()


def _write_cell(cell, text: str) -> None:
    paragraphs = cell.findall(f"{W}p")
    runs = [r for r in paragraphs[0].findall(f"{W}r") if r.find(f"{W}t") is not None]
    if not runs:
        raise ValueError("Table cell has no writable run")
    runs[0].find(f"{W}t").text = text
    runs[0].find(f"{W}t").set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for r in runs[1:]:
        r.find(f"{W}t").text = ""
    for extra in paragraphs[1:]:
        cell.remove(extra)


def _set_para(paragraph, text: str) -> None:
    runs = [r for r in paragraph.findall(f"{W}r") if r.find(f"{W}t") is not None]
    runs[0].find(f"{W}t").text = text
    for r in runs[1:]:
        r.find(f"{W}t").text = ""


def generate(
    template: Path,
    out: Path,
    *,
    team_name: str,
    role_title: str,
    lead_id: str,
    location_tier: str,
    team_sentence: str | None = None,
    pairs: list[tuple[str, list[str]]],   # (requirement text, claim ids)
) -> Path:
    if not 1 <= len(pairs) <= 3:
        raise ValueError("The T-table holds one to three requirement pairs")

    unpacked = unpack(template)
    merge_runs(unpacked)

    xml = unpacked / "word" / "document.xml"
    tree = etree.parse(str(xml))
    body = tree.getroot().find(f"{W}body")
    paras = [el for el in body if etree.QName(el).localname == "p"]
    table = next(el for el in body if etree.QName(el).localname == "tbl")

    _set_para(paras[1], f"{team_name},")
    _set_para(paras[2], OPENERS[lead_id].format(role=role_title))

    rows = table.findall(f"{W}tr")
    body_rows = rows[1:]
    for row, (requirement, claim_ids) in zip(body_rows, pairs):
        cells = row.findall(f"{W}tc")
        # Pass one writes requirements at whatever length it likes
        # (median 116 characters, up to 256). The left column is the
        # employer's ask restated, not a transcript of it.
        _write_cell(cells[0], trim(requirement, 120))
        _write_cell(cells[1], evidence_line(claim_ids))
    for row in body_rows[len(pairs):]:
        table.remove(row)

    # The template carries JPMorgan's team size in this paragraph. It is
    # replaced every time, never inherited.
    team_para = next(p for p in paras if "team of seven at ServiceNow" in "".join(t.text or "" for t in p.iter(f"{W}t")))
    _set_para(team_para, team_sentence or DEFAULT_TEAM_SENTENCE)

    closer = next(p for p in paras if "on site as the role requires" in "".join(t.text or "" for t in p.iter(f"{W}t")))
    _set_para(closer, CLOSERS.get(location_tier, CLOSERS["other_us_hybrid"])
              + " I would welcome the chance to talk about what the first twelve months here would look like.")

    tree.write(str(xml), xml_declaration=True, encoding="UTF-8", standalone=True)
    repack(unpacked, out)
    shutil.rmtree(unpacked.parent)
    return out
