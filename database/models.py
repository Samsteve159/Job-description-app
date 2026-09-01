"""Data model.

Two families of tables that must never touch each other:

  Sections 1 and 2 (Resume & Cover Writer, and the application tracker)
      AlertListing, ContactDetail, DesignSpec, DismissedAlert, ProfileFact,
      Requirement, SyncState, Package, GeneratedBlock,
      Application

  Section 3 (Interview Brief) -- fully standalone
      Brief

Section 3 holds no foreign key into anything above and reads none of it. That boundary is
enforced by tests/test_independence.py, not just by intent.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ------------------------------------------------------------------- contact details

class ContactDetail(Base):
    """Name, email, phone and address. Edited in the app, never seeded.

    These began life as ProfileFact rows, which was wrong the moment they became
    editable: seed_profile.py wipes and reloads ProfileFact because the JSON file is the
    source of truth for the career record. Contact details are the opposite. The app is
    the source of truth for them, so a re-seed must not be able to revert a phone number
    Sameer changed last week. Different lifecycle, different table.

    `renders` exists because not every detail belongs on the page. A full street address
    is worth storing for application forms and worth leaving off a resume, where city and
    country is the convention and the rest is only a privacy cost.
    """
    __tablename__ = "contact_details"

    id = Column(Integer, primary_key=True)
    # name | email | phone | location | address | link
    kind = Column(String(32), nullable=False, index=True)
    label = Column(String(64), nullable=True)     # "mobile", "india", shown in the UI only
    value = Column(Text, nullable=False)
    renders = Column(Boolean, default=True)       # goes in the resume contact line
    order_index = Column(Integer, default=0)      # left to right on the page
    active = Column(Boolean, default=True)        # soft delete
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --------------------------------------------------------------- career source of truth

class ProfileFact(Base):
    """Every fact about Sameer's career, stored once.

    The tailor stage may only select and rephrase from these rows. Any generated block
    that cites no fact is blocked from rendering. This table is the reason the app can
    push hard on framing without drifting into claims that fail a background check.
    """
    __tablename__ = "profile_facts"

    id = Column(Integer, primary_key=True)
    # role | bullet | skill | education | cert | metric | answer
    kind = Column(String(32), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("profile_facts.id"), nullable=True)
    text = Column(Text, nullable=False)
    tags = Column(JSON, default=list)        # ["spend analytics", "sql", "ai"]
    metrics = Column(JSON, default=dict)     # {"value": "19.3M", "currency": "AUD"}
    # where this fact came from, so it can be re-verified later
    source = Column(String(128), nullable=True)
    # employer / institution context, for role and bullet rows
    org = Column(String(256), nullable=True)
    date_from = Column(String(32), nullable=True)   # "Apr 2018"
    date_to = Column(String(32), nullable=True)     # "Oct 2021" | "Present"
    verified = Column(Boolean, default=True)
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


# ----------------------------------------------------------------- section 2: writer

class DismissedAlert(Base):
    """A job alert he has cleared away.

    Kept keyed by source and the board's own job id, which is stable across the several
    emails a board sends about the same job.
    """
    __tablename__ = "dismissed_alerts"

    id = Column(Integer, primary_key=True)
    key = Column(String(128), nullable=False, unique=True, index=True)
    label = Column(Text, nullable=True)          # kept so a mistake is recoverable
    created_at = Column(DateTime, default=datetime.utcnow)


class AlertListing(Base):
    """A job an alert email carried, stored rather than re-read.

    Reading Gmail took nine seconds on eighteen messages, because each one needs its body
    fetched to find the links. Paying that on every visit to the screen makes the screen
    something he stops opening. Pulled on demand instead, and kept.
    """
    __tablename__ = "alert_listings"

    id = Column(Integer, primary_key=True)
    key = Column(String(128), nullable=False, unique=True, index=True)
    source = Column(String(32), nullable=False)
    external_id = Column(String(128), nullable=False)
    url = Column(Text, default="")
    title = Column(Text, default="")
    company = Column(String(256), default="")
    location = Column(String(256), default="")
    subject = Column(Text, default="")
    received = Column(DateTime, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)


class SyncState(Base):
    """When each feed was last pulled, so the screen can say so rather than imply it."""
    __tablename__ = "sync_state"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    last_run = Column(DateTime, nullable=True)
    note = Column(Text, default="")


class DesignSpec(Base):
    """A house style for the resume or the cover letter, in his words.

    Design conventions move. What an ATS forgives, what an LLM screener flags as
    machine-written, how long a document should be at his band: all of it changed inside
    a year. Hard-coding that into prompts means editing Python to change a house rule, so
    it lives here instead and he replaces it by uploading a new one.

    Only one spec of each kind is active. Superseded ones are kept, not deleted, because
    a change that makes the output worse has to be reversible.
    """
    __tablename__ = "design_specs"

    id = Column(Integer, primary_key=True)
    kind = Column(String(16), nullable=False, index=True)      # resume | cover
    name = Column(String(200), nullable=False)
    text = Column(Text, nullable=False)
    rules = Column(JSON, default=dict)        # what was read out of it deterministically
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    kind = Column(String(8), default="must")             # must | nice
    text = Column(Text, nullable=False)
    keyword = Column(String(128), nullable=True)
    weight = Column(Float, default=1.0)


class Package(Base):
    """One generated application bundle: resume + cover + screening answers."""
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    # the posting itself, pasted or fetched from a URL. Always the source
    job_text = Column(Text, nullable=False)
    # sha256 of the posting text. Analysing the same job twice must not produce a
    # different keyword set, or closing a gap and rebuilding could move the score for
    # reasons that have nothing to do with the gap being closed
    jd_hash = Column(String(64), nullable=True, index=True)
    company = Column(String(256), nullable=True)
    title = Column(String(256), nullable=True)
    # the whole Extraction, so reopening a package costs no model call
    extraction = Column(JSON, default=dict)
    # what keyword placement decided: what it added, what has no support, what is a
    # genuine gap. Stored because it is the most useful thing on the review screen and
    # recomputing it would mean re-running the stage that produced the text
    placement = Column(JSON, default=dict)
    # the fit score and the components behind it, so the number can always be argued with
    fit = Column(JSON, default=dict)
    # the drafted cover letter: paragraphs, what each cites, and what was refused
    cover = Column(JSON, default=dict)
    resume_path = Column(Text, nullable=True)
    cover_path = Column(Text, nullable=True)
    screening = Column(JSON, default=dict)
    # draft | ready | exported
    status = Column(String(16), default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)


class GeneratedBlock(Base):
    """A single piece of generated resume content, graded by how far it reaches.

    grade:
      verified  cites ProfileFact rows directly            -> renders
      inferred  reframes a fact into the JD's language     -> amber, needs accept
      stretch   adjacent-skill claim with real evidence    -> amber, needs accept
      blocked   cites nothing                              -> never renders

    render_docx refuses to emit any block that is blocked, or that is inferred/stretch
    and not yet accepted.
    """
    __tablename__ = "generated_blocks"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False, index=True)
    # summary | skills | experience | education | certifications
    section = Column(String(32), nullable=False)
    org = Column(String(256), nullable=True)
    text = Column(Text, nullable=False)
    fact_ids = Column(JSON, default=list)
    grade = Column(String(16), default="blocked", index=True)
    rationale = Column(Text, nullable=True)   # why the model reached, shown in review
    accepted = Column(Boolean, default=False)
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------------------------------------------- application tracker

class Application(Base):
    """One job actually applied for, and what happened next.

    Deliberately separate from Package. A Package is something this app wrote; an
    Application is something Sameer sent. Most applications will never have a Package
    (Easy Apply, a form on a company site, a recruiter email) and a Package can exist for
    a job never applied to. Joining them into one row would force every real application
    to carry an empty resume record, and every draft to look like an application.

    `source` records how the app learned about it, because that governs how much to
    trust the row. A manual entry is authoritative. An inbox match is a guess with a
    message id attached so it can be re-checked.
    """
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)

    company = Column(String(256), nullable=True)
    title = Column(String(256), nullable=True)
    url = Column(Text, nullable=True)
    # manual | gmail | easy_apply | external | package
    source = Column(String(16), default="manual", index=True)
    # applied | acknowledged | screening | interview | offer | rejected | ghosted
    status = Column(String(16), default="applied", index=True)
    # the furthest stage ever reached. Status alone loses it: an interview that ends in
    # a rejection reads as "rejected", and a funnel built from that shows a candidate
    # who was never interviewed. The high-water mark is what the funnel counts.
    furthest_status = Column(String(16), default="applied")
    applied_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_event_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    # set when a row came from an inbox scan, so the same email is never counted twice
    external_ref = Column(String(256), nullable=True, index=True)
    confidence = Column(Float, nullable=True)     # 0-1, only meaningful for scanned rows
    active = Column(Boolean, default=True)        # soft delete
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------------------------- section 3: interview brief (standalone)

class Brief(Base):
    """Interview brief. Deliberately isolated.

    No foreign key into finds, packages or profile_facts. The context the model needs is
    supplied per brief and stored here, so this section can be deleted wholesale without
    touching Sections 1 and 2.
    """
    __tablename__ = "briefs"

    id = Column(Integer, primary_key=True)
    label = Column(String(256), nullable=True)     # "Accenture - Spend Analytics Mgr"
    job_text = Column(Text, nullable=False)
    context_text = Column(Text, nullable=True)     # whatever background he pastes in
    content = Column(Text, nullable=True)          # the generated brief
    created_at = Column(DateTime, default=datetime.utcnow)
