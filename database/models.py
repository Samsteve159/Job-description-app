"""Data model.

Two families of tables that must never touch each other:

  Sections 1 and 2 (Scout Finds, Resume & Cover Writer)
      ProfileFact, Find, Requirement, Package, GeneratedBlock

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


# ------------------------------------------------------------------ section 1: finds

class Find(Base):
    """A scouted job. Written by the local scout and by the VM worker pull."""
    __tablename__ = "finds"

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False)          # adzuna | jsearch | jooble
    external_id = Column(String(256), nullable=True)
    dedupe_key = Column(String(64), nullable=False, index=True)
    company = Column(String(256), nullable=True)
    title = Column(String(256), nullable=False)
    location = Column(String(256), nullable=True)
    url = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    brief = Column(Text, nullable=True)                  # why this is a fit, 2-3 lines
    fit_score = Column(Float, nullable=True)             # 0-100
    gaps = Column(JSON, default=list)
    posted_at = Column(DateTime, nullable=True)
    origin = Column(String(8), default="local")          # local | vm
    # new | shortlisted | dismissed
    status = Column(String(16), default="new", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ----------------------------------------------------------------- section 2: writer

class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True)
    find_id = Column(Integer, ForeignKey("finds.id"), nullable=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    kind = Column(String(8), default="must")             # must | nice
    text = Column(Text, nullable=False)
    keyword = Column(String(128), nullable=True)
    weight = Column(Float, default=1.0)


class Package(Base):
    """One generated application bundle: resume + cover + screening answers."""
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    find_id = Column(Integer, ForeignKey("finds.id"), nullable=True)
    # a pasted JD has no Find, so the raw text lives here
    job_text = Column(Text, nullable=False)
    company = Column(String(256), nullable=True)
    title = Column(String(256), nullable=True)
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
