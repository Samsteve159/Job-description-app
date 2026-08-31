"""Job alerts, read out of his own inbox.

LinkedIn and Naukri refuse to be searched by a program, and that refusal is the reason
the scout was dropped. But they will both happily email him the same listings, and mail
in his own mailbox is his to read. So this is the scout after all, arriving by the one
route that involves scraping nobody: he sets the alerts on their site, they do the
searching, and the app reads the results.

Deterministic throughout. Turning "Senior Data Analyst Grant Thornton Australia ·
Melbourne, VIC" into fields is find-and-copy work, and a model here would cost money,
add latency and occasionally invent a company.

    listings = scan(days=7)
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("jobapp.inbox")

# Boards that email listings. The query is theirs, not ours: he sets the alert on their
# site and they decide what matches.
SENDERS = (
    "linkedin.com", "naukri.com", "match.indeed.com", "indeed.com",
    "glassdoor.com", "instahyre.com", "cutshort.io", "hirist.com",
)

# Everything these senders email that is not a job. Without this the list fills with
# profile views and unread-message nags, and a list that is mostly noise gets ignored,
# which is the same as not having built it.
_NOISE = re.compile(
    r"(viewed|visited) your profile|new messages?|"
    r"job alert for .* has been created|invitation|connect(ion)?s?|"
    r"who's viewed|trending|newsletter|digest of|webinar|"
    r"congratulate|work anniversary|birthday|endorse|"
    r"password|security alert|sign-?in|verify your", re.I)

_LINKEDIN_JOB = re.compile(
    r'<a[^>]+href="([^"]*?/jobs/view/(\d+)[^"]*)"[^>]*>(.*?)</a>', re.S | re.I)
_INDEED_JOB = re.compile(
    r'<a[^>]+href="(https?://[^"]*indeed\.com/[^"]*?(?:jk|vjk)=([0-9a-f]{8,})[^"]*)"',
    re.S | re.I)

# "Senior Data Analyst at Grant Thornton" and "Associate Analyst - Remote @ Termgrid Inc."
_SUBJECT_SPLIT = re.compile(r"^(.{3,90}?)\s+(?:at|@)\s+(.{2,70})$", re.I)
_TRAILING_NOISE = re.compile(r"\s*(and \d+ more|\d+ new jobs?|apply now|easy apply)\s*$", re.I)


@dataclass
class Listing:
    """One job a board emailed him. Fields it could not read stay empty, never guessed."""
    source: str
    external_id: str
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    received: Optional[datetime] = None
    subject: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def label(self) -> str:
        """Title, and the employer only when the title does not already carry it.

        LinkedIn's anchor text is "Title Company", and the subject line is "Title at
        Company", so taking both produced "Senior Data Analyst Department of Justice and
        Community Safety, Victoria at Department of Justice...".
        """
        if not self.title:
            return self.company or self.url
        if not self.company:
            return self.title
        short = self.company.split(",")[0].strip().lower()
        return self.title if short and short in self.title.lower() else \
            f"{self.title} at {self.company}"

    def as_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "external_id": self.external_id, "url": self.url,
                "title": self.title, "company": self.company, "location": self.location,
                "received": self.received.isoformat() if self.received else None,
                "subject": self.subject, "label": self.label}


def is_noise(subject: str) -> bool:
    return bool(_NOISE.search(subject or ""))


def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(text.split())


def split_anchor(text: str) -> Dict[str, str]:
    """"Senior Data Analyst Grant Thornton Australia · Melbourne, VIC" to fields.

    The dot separates the place from everything else, reliably. Splitting the title from
    the company inside "everything else" is not reliable, so it is not attempted: the
    line is kept whole as the title and the company is left empty rather than invented.
    """
    text = _TRAILING_NOISE.sub("", _clean(text))
    if not text:
        return {}
    if "·" in text:
        left, _, right = text.partition("·")
        return {"title": left.strip(), "location": right.strip()}
    return {"title": text}


def from_subject(subject: str) -> Dict[str, str]:
    """A single-job email names the role and the employer in its subject line."""
    subject = _TRAILING_NOISE.sub("", _clean(subject))
    match = _SUBJECT_SPLIT.match(subject)
    if not match:
        return {}
    return {"title": match.group(1).strip(" -,"), "company": match.group(2).strip(" -,.")}


def listings_in(message: Any, body: str) -> List[Listing]:
    """Every job link in one email, with whatever the email says about each."""
    subject = getattr(message, "subject", "") or ""
    if is_noise(subject):
        return []

    received = getattr(message, "received", None)
    domain = getattr(message, "sender_domain", "") or ""
    from_subj = from_subject(subject)
    out: List[Listing] = []
    seen = set()

    for url, job_id, anchor in _LINKEDIN_JOB.findall(body or ""):
        if job_id in seen:
            continue
        seen.add(job_id)
        fields = split_anchor(anchor)
        out.append(Listing(
            source="linkedin", external_id=job_id,
            url=f"https://www.linkedin.com/jobs/view/{job_id}",
            title=fields.get("title", "") or from_subj.get("title", ""),
            company=from_subj.get("company", "") if len(out) == 0 else "",
            location=fields.get("location", ""),
            received=received, subject=subject,
        ))

    for url, job_key in _INDEED_JOB.findall(body or ""):
        if job_key in seen:
            continue
        seen.add(job_key)
        out.append(Listing(
            source="indeed", external_id=job_key, url=url,
            title=from_subj.get("title", ""), company=from_subj.get("company", ""),
            received=received, subject=subject,
        ))

    # An email that names a job in its subject but carries no readable link is still
    # worth listing. He can find it himself; the app's job is to not lose it.
    if not out and from_subj:
        out.append(Listing(
            source=domain.split(".")[0] or "email",
            external_id=f"subject:{abs(hash(subject)) % (10 ** 10)}",
            url="", title=from_subj["title"], company=from_subj["company"],
            received=received, subject=subject,
        ))
    return out


# Where he is willing to work. A listing outside all of these is not wrong, but it is
# worth saying out loud, because a board that knows where he lives will keep sending him
# jobs there whatever his alerts say.
TARGETS = ("india", "mumbai", "bengaluru", "bangalore", "pune", "hyderabad", "delhi",
           "gurgaon", "gurugram", "noida", "chennai", "dubai", "uae", "abu dhabi",
           "remote")


def off_target(listings: Sequence[Listing]) -> List[Listing]:
    """Listings outside every target market, which is a fault in the alert, not the job."""
    out = []
    for listing in listings:
        haystack = f"{listing.location} {listing.title}".lower()
        if listing.location and not any(place in haystack for place in TARGETS):
            out.append(listing)
    return out


def scan(days: int = 7, limit: int = 60, svc: Any = None) -> List[Listing]:
    """Every job any board emailed him recently, deduplicated, newest first."""
    from modules import gmail

    svc = svc or gmail.service()
    query = "from:(" + " OR ".join(SENDERS) + ")"
    messages = gmail.search(query, days=days, limit=limit, svc=svc)

    found: Dict[str, Listing] = {}
    for message in messages:
        if is_noise(message.subject):
            continue
        try:
            body = gmail.body_text(message.id, svc=svc)
        except Exception as exc:  # noqa: BLE001 - one unreadable mail must not stop the scan
            log.warning("inbox: could not read %s: %s", message.id, exc)
            continue
        for listing in listings_in(message, body):
            found.setdefault(listing.key, listing)

    out = sorted(found.values(), key=lambda l: (l.received or datetime.min), reverse=True)
    log.info("inbox: %d listing(s) from %d message(s) over %d day(s)",
             len(out), len(messages), days)
    return out
