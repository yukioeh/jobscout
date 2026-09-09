"""Email delivery. Attachments, no cloud storage.

Documents are ~20KB each, so they attach cleanly and open on any device
without an OAuth flow, a folder id, or a link that only resolves on one
machine. Local copies stay in out/ for editing before you apply.

Sends to Eric and nobody else. There is no code path in this project
that contacts an employer, and there should never be one.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

TO = os.environ["JOBSCOUT_TO"]
FROM = os.environ["JOBSCOUT_FROM"]
APP_PASSWORD = os.environ["JOBSCOUT_APP_PASSWORD"]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _coverage(item: dict) -> str:
    """Must-have coverage, shown next to the score and never folded into
    it. How many of the employer's hard requirements you can evidence
    says more about whether you get a reply than the blended total
    does, and a hard requirement with no evidence at all is the one
    most likely to screen you out before a person reads anything."""
    total = item.get("must_total")
    if not total:
        return ""
    strong, zero = item.get("must_strong", 0), item.get("must_zero", 0)
    bar = f"<b>{strong}/{total}</b> must-haves evidenced"
    if not zero:
        return f"<div style=\"margin-top:6px;font-size:13px;color:#1a7f37\">{bar}, none unevidenced</div>"
    gaps = "".join(
        f"<div style=\"font-size:12px;color:#a40e26;margin-left:10px\">no evidence: {g}</div>"
        for g in item.get("must_gaps", [])
    )
    return (f"<div style=\"margin-top:6px;font-size:13px;color:#a40e26\">{bar}, "
            f"<b>{zero} with no evidence</b></div>{gaps}")


def _plain(item: dict) -> str:
    """Plain-text fallback body. Same facts, no markup."""
    line = f"{item['total']}  {item['company']} — {item['title']}"
    for label, score, weight in item.get("breakdown") or []:
        line += f"\n  {label:14} {score:>5g} x {int(weight*100):>2}% = {score*weight:>5.1f}"
    if item.get("must_total"):
        line += f"\n  must-haves {item.get('must_strong', 0)}/{item['must_total']} evidenced"
        if item.get("must_zero"):
            line += f", {item['must_zero']} with no evidence"
    for text, ids in item.get("fit_reasons") or []:
        line += f"\n  + {_trim(text)} ({', '.join(ids)})"
    return f"{line}\n{item['url']}"


def _trim(text: str, limit: int = 88) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")


def _score_table(item: dict) -> str:
    """The arithmetic behind the total, not just its parts."""
    rows = item.get("breakdown") or []
    if not rows:
        return ""
    cells = "".join(
        f"<tr><td style=\"padding:1px 10px 1px 0;color:#666\">{label}</td>"
        f"<td style=\"padding:1px 8px 1px 0;text-align:right\">{score:g}</td>"
        f"<td style=\"padding:1px 8px 1px 0;color:#999\">&times;&nbsp;{int(weight*100)}%</td>"
        f"<td style=\"padding:1px 0;text-align:right\">{score*weight:.1f}</td></tr>"
        for label, score, weight in rows
    )
    return (f"<table style=\"margin-top:8px;font-size:12px;border-collapse:collapse\">{cells}"
            f"<tr><td colspan=\"3\" style=\"padding:3px 10px 0 0;border-top:1px solid #ddd\">total</td>"
            f"<td style=\"padding:3px 0 0;text-align:right;border-top:1px solid #ddd\">"
            f"<b>{item['total']}</b></td></tr></table>")


def _why_fits(item: dict) -> str:
    """Why this one is worth the morning, built from the requirements
    that actually scored and the claims behind them. Not the model's
    prose about the candidate."""
    reasons = item.get("fit_reasons") or []
    if not reasons:
        return ""
    items = "".join(
        f"<li style=\"margin-bottom:2px\">{_trim(text)}"
        f"<span style=\"color:#999\"> &middot; {', '.join(ids)}</span></li>"
        for text, ids in reasons
    )
    return ("<div style=\"margin-top:10px;font-size:13px\"><b>Why this fits</b>"
            f"<ul style=\"margin:4px 0 0;padding-left:18px\">{items}</ul></div>")


def _row(item: dict) -> str:
    age = f"{item['age_hours']}h old" if item.get("age_hours") is not None else "age unknown"
    stale = " · <b>posted a while ago</b>" if (item.get("age_hours") or 0) > 72 else ""
    docs = "<div style=\"margin-top:6px;color:#666;font-size:13px\">Resume and cover letter attached</div>" if item.get("attachments") else ""
    return f"""
    <tr><td style="padding:18px 0;border-bottom:1px solid #e5e5e5">
      <div style="font-weight:600;font-size:16px">{item['company']} — {item['title']}</div>
      <div style="color:#666;font-size:12px;margin-top:2px">{age}{stale}</div>
      {_score_table(item)}
      {_coverage(item)}
      {_why_fits(item)}
      <div style="margin-top:10px"><a href="{item['url']}">Apply</a></div>{docs}
    </td></tr>"""


def send(subject: str, items: list[dict], intro: str = "") -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM
    msg["To"] = TO
    msg.set_content(
        "\n\n".join(_plain(i) for i in items) or "Nothing above threshold."
    )
    msg.add_alternative(
        "<html><body style=\"font-family:-apple-system,Segoe UI,sans-serif;max-width:640px\">"
        f"<p>{intro}</p><table style=\"width:100%\">{''.join(_row(i) for i in items)}</table>"
        "<p style=\"color:#888;font-size:12px\">Drafts only. Review before sending.</p>"
        "</body></html>",
        subtype="html",
    )

    for item in items:
        for path in item.get("attachments", []):
            path = Path(path)
            if not path.exists():
                continue
            msg.add_attachment(
                path.read_bytes(),
                maintype="application",
                subtype=DOCX.split("/", 1)[1],
                filename=path.name,
            )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(FROM, APP_PASSWORD)
        s.send_message(msg)
