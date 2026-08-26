"""Contact details: the one part of the profile the app owns rather than the JSON file.

The career record flows one way, from data/profile_facts.json into ProfileFact, and
seed_profile.py wipes and reloads because the file is the source of truth. Contact details
run the other way. Sameer edits them in the app, so the database is the source of truth and
a re-seed must not be able to revert them.

That is the whole reason ContactDetail is a separate table. It is not tidiness. Leaving a
phone number in ProfileFact means the next `python3 scripts/seed_profile.py` silently puts
the old one back, and the first you learn of it is a recruiter calling the wrong country.

KINDS is ordered, and that order is the order the details print on the resume. Name is
handled separately because it is the document title, not part of the contact line.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from database.models import ContactDetail, ProfileFact

log = logging.getLogger(__name__)

# resume convention, left to right. Address is stored but withheld from the page by
# default: a street address on a resume is a privacy cost that buys nothing, while
# application forms ask for it constantly and it is worth having somewhere.
KINDS = ("email", "phone", "link", "location", "address")
DEFAULT_HIDDEN = ("address",)

_PLACEHOLDER_MARKERS = ("example.com", "your.name", "555", "xxx", "placeholder")


def _looks_like_a_link(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in ("linkedin.com", "github.com", "http", "www."))


def infer_kind(text: str) -> str:
    """Classify a bare contact string. Used only when importing existing rows."""
    if "@" in text and "." in text:
        return "email"
    if _looks_like_a_link(text):
        return "link"
    digits = sum(c.isdigit() for c in text)
    if digits >= 8 and digits >= len(text.replace(" ", "")) // 2:
        return "phone"
    return "location"


def bootstrap(db: Any, facts: Optional[Sequence[Any]] = None) -> int:
    """Populate the table once, from whatever contact facts already exist.

    Returns the number of rows created. Does nothing if the table is already populated,
    so it is safe to call on every startup and safe to call twice.
    """
    if db.query(ContactDetail).count():
        return 0

    if facts is None:
        facts = db.query(ProfileFact).filter(
            ProfileFact.kind.in_(("name", "contact"))
        ).order_by(ProfileFact.order_index).all()

    created = 0
    for fact in facts:
        text = (getattr(fact, "text", "") or "").strip()
        if not text or getattr(fact, "kind", None) not in ("name", "contact"):
            continue
        kind = "name" if fact.kind == "name" else infer_kind(text)
        db.add(ContactDetail(
            kind=kind,
            value=text,
            renders=kind not in DEFAULT_HIDDEN,
            order_index=KINDS.index(kind) if kind in KINDS else 99,
        ))
        created += 1

    if created:
        db.commit()
        log.info("contact details bootstrapped: %d row(s) imported", created)
    return created


def all_details(db: Any, include_inactive: bool = False) -> List[ContactDetail]:
    query = db.query(ContactDetail)
    if not include_inactive:
        query = query.filter(ContactDetail.active.is_(True))
    return query.order_by(ContactDetail.order_index, ContactDetail.id).all()


def display_name(db: Any) -> str:
    row = db.query(ContactDetail).filter(
        ContactDetail.kind == "name", ContactDetail.active.is_(True)
    ).first()
    return row.value if row else ""


def resume_lines(db: Any) -> List[str]:
    """The contact line, in resume order. Only details marked to render."""
    rows = [
        d for d in all_details(db)
        if d.kind != "name" and d.renders and (d.value or "").strip()
    ]
    return [d.value.strip() for d in rows]


def set_detail(db: Any, kind: str, value: str, label: Optional[str] = None,
               renders: Optional[bool] = None) -> ContactDetail:
    """Update the first detail of this kind, or create one. Commits."""
    kind = kind.strip().lower()
    value = (value or "").strip()
    if not value:
        raise ValueError("a contact detail cannot be empty. Hide it instead")
    if kind != "name" and kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}. Expected one of: name, {', '.join(KINDS)}")

    row = db.query(ContactDetail).filter(
        ContactDetail.kind == kind, ContactDetail.active.is_(True)
    ).order_by(ContactDetail.order_index, ContactDetail.id).first()

    if row is None:
        row = ContactDetail(
            kind=kind, value=value,
            renders=(kind not in DEFAULT_HIDDEN) if renders is None else renders,
            order_index=KINDS.index(kind) if kind in KINDS else 99,
        )
        db.add(row)
    else:
        row.value = value
        if renders is not None:
            row.renders = renders
    if label is not None:
        row.label = label
    db.commit()
    return row


def set_renders(db: Any, detail_id: int, renders: bool) -> ContactDetail:
    row = db.get(ContactDetail, detail_id)
    if row is None:
        raise ValueError(f"no contact detail with id {detail_id}")
    row.renders = renders
    db.commit()
    return row


def warnings(db: Any) -> List[str]:
    """Things worth telling him before a resume goes out. Never blocking.

    The ATS gate already refuses a document whose contact block will not parse. This is
    the softer question of whether the details are the right ones for the job market he
    is applying into, which no parser can answer.
    """
    out = []
    rows = all_details(db)
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row.kind, []).append(row)

    if not display_name(db):
        out.append("no name set. The resume will render without one")
    if "email" not in by_kind:
        out.append("no email set. An ATS drops applications it cannot extract an email from")
    if "phone" not in by_kind:
        out.append("no phone set. Indian recruiters call before they email")

    for row in rows:
        if any(m in row.value.lower() for m in _PLACEHOLDER_MARKERS):
            out.append(f"{row.kind} still looks like placeholder text: {row.value!r}")

    for row in by_kind.get("phone", []):
        if row.renders and not row.value.replace(" ", "").startswith("+91"):
            out.append(
                f"phone {row.value!r} is not an Indian number. It renders on the resume "
                f"as is, which is fine if intended"
            )
    return out
