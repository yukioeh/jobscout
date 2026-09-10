"""Resume tailoring.

The master .docx is edited, never rebuilt. Its styles, spacing, fonts
and margins are Eric's and stay untouched: we swap text inside existing
runs and move existing paragraph elements around. Nothing is generated
from scratch, so nothing can drift away from how the master looks.

The license, per the tailoring guide, is four changes:
  1. the header line under the name
  2. the location segment of the contact line
  3. the opening sentence of the summary
  4. bullet order, plus one-for-one swap-ins from the library

Number 2 was added after the master was found to state a city Eric
does not live in on every application. It names the market a posting
is hiring for and says he is open to relocating there. It never
claims he is already there.

Bullet count per role never grows. Two pages is the ceiling.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml
from lxml import etree

from tailor._docx import merge_runs, repack, unpack

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY = yaml.safe_load((ROOT / "config" / "bullets.yaml").read_text())

# Role key -> the text its block starts with in the master. Order
# matters: sap_ai anchors on the SAP company line and takes the first
# bullet block after it, so it has to be the block that comes first.
# sap_digital anchors on its own role heading further down. Presales
# (2010-2013) is deliberately absent: that block stays static.
ROLE_ANCHORS = {
    "servicenow": "SERVICENOW",
    "vector": "VECTOR CREATIVE LABS",
    "sap_ai": "SAP",
    "sap_digital": "GLOBAL PROGRAM DIRECTOR, DIGITAL",
}
ROLES = tuple(ROLE_ANCHORS)


class TailorError(Exception):
    """Raised when the document doesn't look like the master we expect."""


@dataclass
class Selection:
    """What the selector decided. Fully inspectable before generation."""

    header: str
    summary_lead_id: str
    bullet_order: dict[str, list[str]]      # role -> ordered bullet ids
    swap_ins: list[tuple[str, str]]         # (replaced_id, new_id)
    matched_tags: list[str]
    location_line: str = ""                 # contact-line location segment


def _text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{W}t"))


def _set_runs(paragraph, parts: list[str]) -> None:
    """Write parts into the paragraph's existing runs, in order.

    Formatting lives on the runs, so writing into them keeps the bold
    label, the separator and the body exactly as the master has them.
    """
    runs = paragraph.findall(f"{W}r")
    runs = [r for r in runs if r.find(f"{W}t") is not None]
    if len(runs) < len(parts):
        raise TailorError(f"Expected {len(parts)} runs, found {len(runs)}: {_text(paragraph)[:60]!r}")
    for run, value in zip(runs, parts):
        t = run.find(f"{W}t")
        t.text = value
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for run in runs[len(parts):]:
        run.find(f"{W}t").text = ""


def _set_location(paragraph, text: str) -> None:
    """Rewrite the location segment of the contact line in place.

    The line runs LOCATION • AVAILABILITY • PHONE • EMAIL • PROFILE.
    Only the first two segments move, so this consumes runs up to and
    including the second separator and leaves the contact details
    alone. Word fragments this line across ten runs, so it is written
    into the first of them and the rest of the consumed ones are
    cleared, the same way _set_runs works.
    """
    runs = [r for r in paragraph.findall(f"{W}r") if r.find(f"{W}t") is not None]
    consumed, seen = [], 0
    for run in runs:
        consumed.append(run)
        seen += (run.find(f"{W}t").text or "").count("•")
        if seen >= 2:
            break
    if seen < 2:
        raise TailorError("Contact line is not shaped the way the master's is")
    head = consumed[0].find(f"{W}t")
    head.text = text
    head.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for run in consumed[1:]:
        run.find(f"{W}t").text = ""


def _bullets_for(body, anchor: str) -> list:
    """Every list paragraph belonging to the role starting at anchor."""
    ps = body.findall(f"{W}p")
    start = next((i for i, p in enumerate(ps) if _text(p).upper().startswith(anchor)), None)
    if start is None:
        raise TailorError(f"Could not find role anchor {anchor!r} in the master")
    out = []
    for p in ps[start + 1:]:
        if p.find(f".//{W}numPr") is not None:
            out.append(p)
        elif out and _text(p).strip():
            break        # ran past the end of this role's bullet block
    return out


def tailor(master: Path, out: Path, selection: Selection) -> Path:
    unpacked = unpack(master)
    merge_runs(unpacked)

    xml = unpacked / "word" / "document.xml"
    tree = etree.parse(str(xml))
    body = tree.getroot().find(f"{W}body")
    ps = body.findall(f"{W}p")

    # 1. header line: the all-caps positioning line under the name
    header_p = next(
        (p for p in ps[:6] if _text(p).isupper() and "LEADER" in _text(p).upper()), None
    )
    if header_p is None:
        raise TailorError("Could not find the header line in the master")
    _set_runs(header_p, [selection.header])

    # 1b. contact line: the market this posting is hiring for. A fourth
    # edit beyond the three the tailoring guide licenses, added because
    # the master's own location line was stating a city Eric does not
    # live in on every application.
    if selection.location_line:
        contact_p = next((p for p in ps[:6] if "@" in _text(p)), None)
        if contact_p is None:
            raise TailorError("Could not find the contact line in the master")
        _set_location(contact_p, selection.location_line)

    # 2. summary opening paragraph
    lead = next(l for l in LIBRARY["summary_leads"] if l["id"] == selection.summary_lead_id)
    summary_p = ps[ps.index(header_p) + 1]
    _set_runs(summary_p, [lead["text"].strip()])

    # 3. bullets: reorder in place, writing the chosen bullet into each slot
    index = {b["id"]: b for role in ROLES for b in LIBRARY[role]}
    for role, order in selection.bullet_order.items():
        slots = _bullets_for(body, ROLE_ANCHORS[role])
        if len(order) != len(slots):
            raise TailorError(
                f"{role}: {len(order)} bullets for {len(slots)} slots. Count must not change."
            )
        for slot, bullet_id in zip(slots, order):
            b = index[bullet_id]
            _set_runs(slot, [b["label"], "  \u2013  ", b["body"].strip()])

    tree.write(str(xml), xml_declaration=True, encoding="UTF-8", standalone=True)
    repack(unpacked, out)
    shutil.rmtree(unpacked.parent)
    return out
