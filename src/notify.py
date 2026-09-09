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
    if item.get("must_total"):
        line += f"\nmust-haves {item.get('must_strong', 0)}/{item['must_total']} evidenced"
        if item.get("must_zero"):
            line += f", {item['must_zero']} with no evidence"
    return f"{line}\n{item['url']}"


def _row(item: dict) -> str:
    age = f"{item['age_hours']}h old" if item.get("age_hours") is not None else "age unknown"
    stale = " · <b>posted a while ago</b>" if (item.get("age_hours") or 0) > 72 else ""
    docs = "<div style=\"margin-top:4px;color:#666;font-size:13px\">Resume and cover letter attached</div>" if item.get("attachments") else ""
    return f"""
    <tr><td style="padding:16px 0;border-bottom:1px solid #e5e5e5">
      <div style="font-size:24px;font-weight:600">{item['total']}</div>
      <div style="font-weight:600;font-size:15px">{item['company']} — {item['title']}</div>
      <div style="color:#666;font-size:13px">
        requirements {item['requirement_match']} · level {item['level_fit']} ·
        location {item['location_fit']} · {age}{stale}
      </div>
      {_coverage(item)}
      <div style="margin-top:8px;font-size:14px">{item['why']}</div>
      <div style="margin-top:8px"><a href="{item['url']}">Apply</a></div>{docs}
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
