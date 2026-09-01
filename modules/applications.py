"""Counting the jobs he has actually applied to, from the confirmations they send.

The tracker used to depend on him remembering to type each one in, which is exactly the
admin a job search sheds first. Every serious employer sends an acknowledgement within
minutes, so the record already exists in his mailbox and the app only has to read it.

Deterministic. An acknowledgement is a form letter and form letters are the easiest thing
in the world to match; a model here would cost money to occasionally hallucinate a company
he never applied to, which would be worse than counting nothing.

Idempotent by message id, so scanning twice never double counts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("jobapp.applications")

# What an acknowledgement says. All of these are subject lines, because a body search
# turns up rejections and newsletters that mention the word application.
_ACK_SUBJECT = re.compile(
    r"thank you for (?:applying|your application|your interest)|"
    r"application (?:received|confirmation|submitted)|"
    r"we(?:'ve| have) received your application|"
    r"your application (?:to|for|has been received)|"
    r"received: your application", re.I)

# And what it definitely is not. A rejection is still evidence he applied, but it is not
# a new application, and counting both would double every job he is turned down for.
_NOT_AN_ACK = re.compile(
    r"unfortunately|not (?:be )?(?:moving|progress)|unsuccessful|"
    r"unable to (?:progress|proceed|offer|move)|we regret|"
    r"decided to (?:move|proceed) with|no longer under consideration|"
    r"withdraw|job alert|new jobs|recommended for you", re.I)

# "received your application for the following position: R-570561 Lead Analytics Consultant"
_ROLE_IN_BODY = (
    re.compile(r"application for the following position:?\s*([^\n<]{3,90})", re.I),
    re.compile(r"your application for(?: the role of)?:?\s*([^\n<.]{3,90})", re.I),
    re.compile(r"applied (?:to|for)(?: the)?(?: position of)?:?\s*([^\n<.]{3,90})", re.I),
)

# Northwind Bank writes "Northwind Bank Careers: Thank you for applying". The company is the
# part before the colon far more often than it is anything else in the message.
_COMPANY_IN_SUBJECT = re.compile(r"^([A-Z][\w&.,' -]{2,40}?)\s*(?:careers?|recruiting|"
                                 r"talent|hr)?\s*[:\-–]", re.I)

_REQ_ID = re.compile(r"\b([A-Z]{1,3}-?\d{4,9})\b")
_MAIL_NOISE = {"mail", "email", "careers", "notification", "notifications", "no-reply",
               "noreply", "jobs", "recruiting", "talent", "myworkday", "workday",
               "icims", "greenhouse", "lever", "smartrecruiters", "successfactors"}


@dataclass
class Application:
    """One confirmed application. Fields it cannot read stay empty, never guessed."""
    message_id: str
    company: str = ""
    title: str = ""
    applied_at: Optional[datetime] = None
    subject: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"message_id": self.message_id, "company": self.company,
                "title": self.title, "subject": self.subject,
                "applied_at": self.applied_at.isoformat() if self.applied_at else None}


def looks_like_an_acknowledgement(subject: str) -> bool:
    subject = subject or ""
    return bool(_ACK_SUBJECT.search(subject)) and not _NOT_AN_ACK.search(subject)


def company_from(sender: str, subject: str) -> str:
    """The employer, from the subject where it says so and the domain where it does not."""
    match = _COMPANY_IN_SUBJECT.match(_clean(subject))
    if match:
        name = match.group(1).strip(" -:")
        if name and name.lower() not in _MAIL_NOISE:
            return name

    domain = re.search(r"@([\w.-]+)", sender or "")
    if not domain:
        return ""
    parts = [p for p in domain.group(1).lower().split(".")
             if p not in _MAIL_NOISE and len(p) > 2 and p not in ("com", "net", "org", "co")]
    return parts[-1].replace("-", " ").title() if parts else ""


def title_from(body: str, subject: str) -> str:
    """The role, from the body. Requisition ids are stripped: they are not a job title."""
    # Cleaned first. Run against raw HTML, the longest and most specific pattern breaks
    # on a tag sitting between two words, and a shorter one wins with a worse answer:
    # "the following position" instead of "Lead Analytics Consultant".
    text = _clean(body)
    for pattern in _ROLE_IN_BODY:
        match = pattern.search(text)
        if match:
            title = _clean(match.group(1))
            title = _REQ_ID.sub("", title).strip(" -:\u2013,")
            # Acknowledgements run the role into the next sentence. Cut at the first
            # boundary rather than carrying half a paragraph onto the tracker.
            title = re.split(r"\s+[-\u2013]\s+|\.\s|\bWe \b|\bYou \b|\bThank\b",
                             title)[0].strip(" -:,")
            if 3 <= len(title) <= 90:
                return title
    return ""


def _clean(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def scan(days: int = 30, limit: int = 40, svc: Any = None) -> List[Application]:
    """Every application acknowledgement in the last `days`, newest first."""
    from modules import gmail

    svc = svc or gmail.service()
    query = ('subject:("thank you for applying" OR "your application" OR '
             '"application received" OR "we received your application" OR '
             '"application submitted" OR "thank you for your interest")')
    messages = gmail.search(query, days=days, limit=limit, svc=svc)

    out: List[Application] = []
    for message in messages:
        if not looks_like_an_acknowledgement(message.subject):
            continue
        try:
            body = gmail.body_text(message.id, svc=svc)
        except Exception as exc:  # noqa: BLE001 - one unreadable mail is not a failure
            log.warning("applications: could not read %s: %s", message.id, exc)
            body = ""
        if _NOT_AN_ACK.search(_clean(body)[:600]):
            continue
        out.append(Application(
            message_id=message.id,
            company=company_from(message.sender, message.subject),
            title=title_from(body, message.subject),
            applied_at=message.received,
            subject=message.subject,
        ))

    log.info("applications: %d acknowledgement(s) in %d message(s)", len(out), len(messages))
    return out


def record(db: Any, found: Sequence[Application]) -> int:
    """Write them to the tracker. Idempotent on the message id, so rescanning is free."""
    from database.models import Application as Row
    from modules import tracker

    # log_application returns the existing row when the reference is already known, and
    # an existing row is indistinguishable from a new one by inspection. So the set of
    # references is read first and the difference counted, rather than asking each row
    # whether it had just been created.
    before = {r.external_ref for r in db.query(Row).all() if r.external_ref}

    for app in found:
        tracker.log_application(
            db, company=app.company or None, title=app.title or None,
            source="gmail", external_ref=f"gmail:{app.message_id}",
            applied_at=app.applied_at, notes=app.subject or None)

    after = {r.external_ref for r in db.query(Row).all() if r.external_ref}
    return len(after - before)
