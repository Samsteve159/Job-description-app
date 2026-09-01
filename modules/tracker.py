"""The application tracker, and the numbers the dashboard puts on the wall.

Counting applications sounds trivial until you decide what counts. A draft resume is not
an application. A job saved and never sent is not an application. The same role applied to
twice, once through Easy Apply and once on the company site, is one application with two
attempts, not two. So counting lives here rather than being spelled out in a template,
where it would quietly diverge from whatever the tracker page shows.

STATUSES is ordered by progress, which is what makes a funnel possible. `ghosted` sits
outside that order: it is an absence of an outcome, not a stage, and treating it as a
terminal negative would flatter the numbers by turning silence into a decision.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func

from database.models import Application, Package

log = logging.getLogger(__name__)

# ordered by how far through a process the application has got
STATUSES = ("applied", "acknowledged", "screening", "interview", "offer", "rejected")
GHOSTED = "ghosted"
ALL_STATUSES = STATUSES + (GHOSTED,)

LIVE = ("applied", "acknowledged", "screening", "interview")
GOOD = ("screening", "interview", "offer")

SOURCES = ("manual", "package", "easy_apply", "external", "gmail")

STATUS_LABELS = {
    "applied": "Applied",
    "acknowledged": "Acknowledged",
    "screening": "Screening call",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    GHOSTED: "No response",
}

# after this long with no movement, an application is treated as silence rather than
# as still live. Recruiters do not send a rejection, they simply stop.
GHOST_AFTER_DAYS = 30


def log_application(db: Any, *, company: Optional[str] = None, title: Optional[str] = None,
                    url: Optional[str] = None, source: str = "manual",
                    package_id: Optional[int] = None,
                    external_ref: Optional[str] = None, notes: Optional[str] = None,
                    confidence: Optional[float] = None,
                    applied_at: Optional[datetime] = None) -> Application:
    """Record an application. Idempotent on external_ref, so a rescan cannot double count."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}. Expected one of: {', '.join(SOURCES)}")

    if external_ref:
        existing = db.query(Application).filter(
            Application.external_ref == external_ref).first()
        if existing:
            return existing

    row = Application(
        company=(company or "").strip() or None,
        title=(title or "").strip() or None,
        url=(url or "").strip() or None,
        source=source, package_id=package_id,
        external_ref=external_ref, notes=notes, confidence=confidence,
        applied_at=applied_at or datetime.utcnow(),
        status="applied",
    )
    db.add(row)
    db.commit()
    log.info("application logged: %s at %s via %s", row.title, row.company, source)
    return row


def set_status(db: Any, application_id: int, status: str) -> Application:
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    row = db.get(Application, application_id)
    if row is None:
        raise ValueError(f"no application with id {application_id}")
    row.status = status
    row.furthest_status = _furthest(row.furthest_status, status)
    row.last_event_at = datetime.utcnow()
    db.commit()
    return row


def _furthest(current: Optional[str], candidate: str) -> str:
    """High-water mark. Rejection and silence never move it, they only end the story."""
    ranked = [s for s in (current, candidate) if s in STATUSES and s != "rejected"]
    if not ranked:
        return current or "applied"
    return max(ranked, key=STATUSES.index)


def remove(db: Any, application_id: int) -> None:
    """Soft delete. Nothing is hard deleted, per the app's conventions."""
    row = db.get(Application, application_id)
    if row is not None:
        row.active = False
        db.commit()


def applied_keys(db: Any) -> set:
    """Company and title of everything he has actually applied to, normalised.

    A package knows it produced a document. It does not know he then sent it, and the
    confirmation that proves he did arrives by email with no reference back. So the two
    are matched on what they have in common: who the job was with and what it was called.
    """
    keys = set()
    for row in db.query(Application).filter(Application.active.is_(True)).all():
        company = (row.company or "").strip().lower()
        title = (row.title or "").strip().lower()
        if company:
            keys.add((company, title))
            keys.add((company, ""))          # applied there, title unread or worded differently
    return keys


def display_status(package: Any, keys: set) -> str:
    """What to show on the list. `applied` outranks anything the package knows itself."""
    company = (getattr(package, "company", "") or "").strip().lower()
    title = (getattr(package, "title", "") or "").strip().lower()
    if company and ((company, title) in keys or (company, "") in keys):
        return "applied"
    return getattr(package, "status", "") or "draft"


def all_applications(db: Any, include_inactive: bool = False) -> List[Application]:
    query = db.query(Application)
    if not include_inactive:
        query = query.filter(Application.active.is_(True))
    return query.order_by(Application.applied_at.desc()).all()


def is_stale(row: Application, now: Optional[datetime] = None) -> bool:
    """No movement for long enough that silence is the outcome."""
    if row.status not in ("applied", "acknowledged"):
        return False
    now = now or datetime.utcnow()
    last = row.last_event_at or row.applied_at or row.created_at
    return bool(last and (now - last) > timedelta(days=GHOST_AFTER_DAYS))


def stats(db: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the dashboard shows, computed once so the page cannot disagree with itself."""
    now = now or datetime.utcnow()
    rows = all_applications(db)

    by_status = {s: 0 for s in ALL_STATUSES}
    by_source = {s: 0 for s in SOURCES}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_source[row.source] = by_source.get(row.source, 0) + 1

    total = len(rows)
    live = sum(1 for r in rows if r.status in LIVE and not is_stale(r, now))
    stale = sum(1 for r in rows if is_stale(r, now))
    reached_human = sum(1 for r in rows if (r.furthest_status or "applied") in GOOD)

    week_ago = now - timedelta(days=7)
    this_week = sum(1 for r in rows if r.applied_at and r.applied_at >= week_ago)

    # eight weeks of counts, oldest first, for the sparkline
    weeks: List[Dict[str, Any]] = []
    for index in range(7, -1, -1):
        start = now - timedelta(days=7 * (index + 1))
        end = now - timedelta(days=7 * index)
        count = sum(1 for r in rows if r.applied_at and start <= r.applied_at < end)
        weeks.append({"label": end.strftime("%d %b"), "count": count})

    drafts = db.query(func.count(Package.id)).filter(Package.status == "draft").scalar() or 0
    ready = db.query(func.count(Package.id)).filter(Package.status != "draft").scalar() or 0

    return {
        "total": total,
        "live": live,
        "stale": stale,
        "this_week": this_week,
        "reached_human": reached_human,
        "response_rate": (100.0 * reached_human / total) if total else 0.0,
        "by_status": by_status,
        "by_source": by_source,
        "weeks": weeks,
        "peak_week": max((w["count"] for w in weeks), default=0),
        "drafts": drafts,
        "packages_built": ready,
        "funnel": [
            {"key": s, "label": STATUS_LABELS[s],
             "count": sum(1 for r in rows if _at_least(r, s))}
            for s in ("applied", "screening", "interview", "offer")
        ],
    }


def _at_least(row: Application, stage: str) -> bool:
    """Has this application ever reached the given stage?

    Reads furthest_status, not status. A rejection after an interview is still an
    interview that happened, and a funnel built from the current status would show a
    candidate who never got one. Everything reached "applied" by definition.
    """
    if stage == "applied":
        return True
    reached = row.furthest_status or "applied"
    if reached not in STATUSES or stage not in STATUSES:
        return False
    return STATUSES.index(reached) >= STATUSES.index(stage)
