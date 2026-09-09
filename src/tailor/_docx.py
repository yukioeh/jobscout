"""Shared docx unzip/merge/rezip plumbing for resume.py and cover_letter.py.

Replaces a subprocess call to a bundled Claude skill script
(/mnt/skills/public/docx/scripts/merge_runs.py) that only exists inside
the sandbox this project was originally built in. It doesn't exist on
a real machine running this under cron, so every document generation
call failed there. This is a from-scratch, in-process replacement.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def unpack(docx: Path) -> Path:
    tmp = Path(tempfile.mkdtemp())
    unpacked = tmp / "doc"
    subprocess.run(["unzip", "-q", str(docx), "-d", str(unpacked)], check=True)
    for link in unpacked.rglob("*"):
        if link.is_symlink():
            link.unlink()
    return unpacked


def repack(unpacked: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    subprocess.run(["zip", "-Xrq", str(out.resolve()), "."], cwd=unpacked, check=True)
    return out


def _text_child(run):
    """The run's single <w:t>, if that's the run's only content besides
    formatting. A run holding a tab, a break, a field, or a drawing
    returns None here, and is left alone rather than merged across."""
    children = [c for c in run if etree.QName(c).localname != "rPr"]
    if len(children) == 1 and etree.QName(children[0]).localname == "t":
        return children[0]
    return None


def merge_runs(unpacked: Path) -> None:
    """Consolidate adjacent same-formatting <w:r> runs into one per paragraph.

    Word fragments one visually continuous span of text into several
    runs that share identical formatting -- spell-check tags, revision
    history, save artifacts. resume.py and cover_letter.py both treat
    "one run = one semantic part" (a bold label, a separator, a body),
    so leftover fragmentation makes a paragraph show more runs than
    the code expects. This merges a run into the previous one whenever
    both are plain text and their <w:rPr> (run properties) match
    exactly, in document order. Anything else -- different formatting,
    tabs, breaks, fields, drawings -- is left untouched, which also
    means it correctly acts as a break the merge won't cross.
    """
    xml = unpacked / "word" / "document.xml"
    tree = etree.parse(str(xml))
    for paragraph in tree.getroot().iter(f"{W}p"):
        prev, prev_t, prev_rpr = None, None, None
        for run in list(paragraph):
            if etree.QName(run).localname != "r":
                prev = None
                continue
            t = _text_child(run)
            rpr = run.find(f"{W}rPr")
            rpr_key = etree.tostring(rpr) if rpr is not None else b""
            if prev is not None and t is not None and prev_t is not None and rpr_key == prev_rpr:
                prev_t.text = (prev_t.text or "") + (t.text or "")
                paragraph.remove(run)
                continue
            prev, prev_t, prev_rpr = run, t, rpr_key
    tree.write(str(xml), xml_declaration=True, encoding="UTF-8", standalone=True)
