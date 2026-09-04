"""Resume tailoring.

The master .docx is edited, never rebuilt. Its styles, spacing, fonts
and margins are Eric's and stay untouched: we swap text inside existing
runs and move existing paragraph elements around. Nothing is generated
from scratch, so nothing can drift away from how the master looks.

The license, per the tailoring guide, is exactly three changes:
  1. the header line under the name
  2. the opening sentence of the summary
  3. bullet order, plus one-for-one swap-ins from the library

Bullet count per role never grows. Two pages is the ceiling.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY = yaml.safe_load((ROOT / "config" / "bullets.yaml").read_text())
MERGE_RUNS = "/mnt/skills/public/docx/scripts/merge_runs.py"

ROLE_ANCHORS = {"servicenow": "SERVICENOW", "vector": "VECTOR CREATIVE LABS", "sap": "SAP"}


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
    tmp = Path(tempfile.mkdtemp())
    unpacked = tmp / "doc"
    subprocess.run(["unzip", "-q", str(master), "-d", str(unpacked)], check=True)
    for link in unpacked.rglob("*"):
        if link.is_symlink():
            link.unlink()
    subprocess.run(["python3", MERGE_RUNS, str(unpacked)], check=True, capture_output=True)

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

    # 2. summary opening paragraph
    lead = next(l for l in LIBRARY["summary_leads"] if l["id"] == selection.summary_lead_id)
    summary_p = ps[ps.index(header_p) + 1]
    _set_runs(summary_p, [lead["text"].strip()])

    # 3. bullets: reorder in place, writing the chosen bullet into each slot
    index = {b["id"]: b for role in ("servicenow", "vector", "sap") for b in LIBRARY[role]}
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
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    subprocess.run(["zip", "-Xrq", str(out.resolve()), "."], cwd=unpacked, check=True)
    shutil.rmtree(tmp)
    return out
