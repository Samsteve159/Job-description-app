"""Stage: JD text -> structured requirements.

Cheap, low judgment, high volume. This is the stage NIM should handle comfortably.

The output is deliberately boring and typed. Everything downstream indexes into these
fields, so the parser normalises hard: missing keys become empty lists, junk grades become
defaults, and nothing here is ever allowed to raise an IndexError inside the tailor stage.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from modules.llm import complete_json

log = logging.getLogger(__name__)

ARCHETYPES = ("gcc", "consulting", "corporate", "startup", "unknown")
SENIORITIES = ("analyst", "senior analyst", "manager", "senior manager", "director", "unknown")

_SYSTEM = """You read job descriptions and return structured data. You do not editorialise,
you do not write prose, and you return JSON only with no preamble and no code fence.

Return exactly this shape:

{
  "title": "the role title as written",
  "company": "hiring company, or null if not stated",
  "location": "location as written, or null",
  "seniority": one of ["analyst","senior analyst","manager","senior manager","director","unknown"],
  "archetype": one of ["gcc","consulting","corporate","startup","unknown"],
  "must": [{"text": "requirement as written", "keyword": "the 1-3 word ATS term", "weight": 1.0}],
  "nice": [{"text": "...", "keyword": "...", "weight": 0.5}],
  "keywords": ["flat list of every term an ATS would scan for, lowercase"],
  "comp_hints": "any salary or CTC text, or null",
  "notes": "anything unusual worth a human knowing, one sentence, or null"
}

Rules:
- "must" is what the JD states as required. "nice" is preferred or desirable.
- weight is 0.0 to 1.0 and reflects how heavily the JD leans on that requirement.
- keyword must be the short form an applicant tracking system matches on, for example
  "spend analysis", "sql", "fp&a", not a full sentence.
- archetype: "gcc" for a global capability or shared services centre of a multinational,
  "consulting" for advisory and professional services firms, "corporate" for an operating
  company hiring in-house, "startup" for early stage.
- If the text you were given looks like navigation, a cookie banner, a login wall or an
  error page rather than a job description, return {"error": "not a job description"}."""


@dataclass
class Requirement:
    text: str
    keyword: str = ""
    weight: float = 1.0
    kind: str = "must"


@dataclass
class Extraction:
    title: str = ""
    company: Optional[str] = None
    location: Optional[str] = None
    seniority: str = "unknown"
    archetype: str = "unknown"
    must: List[Requirement] = field(default_factory=list)
    nice: List[Requirement] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    comp_hints: Optional[str] = None
    notes: Optional[str] = None
    jd_text: str = ""

    @property
    def all_requirements(self) -> List[Requirement]:
        return self.must + self.nice

    def must_keywords(self) -> List[str]:
        return [r.keyword for r in self.must if r.keyword]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Extraction":
        """Rebuild from what as_dict() stored. Reopening a package costs no model call."""
        data = dict(data or {})
        must = [Requirement(**r) for r in data.pop("must", []) or []]
        nice = [Requirement(**r) for r in data.pop("nice", []) or []]
        known = {f.name for f in fields(cls)}
        return cls(must=must, nice=nice,
                   **{k: v for k, v in data.items() if k in known})


class NotAJobDescription(ValueError):
    """Raised when the input is a login wall, error page or navigation rather than a JD."""


_MIN_JD_CHARS = 200


def _requirements(raw: Any, kind: str) -> List[Requirement]:
    """Normalise whatever the model returned into a clean list. Never raises."""
    out: List[Requirement] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            out.append(Requirement(text=item.strip(), keyword="", kind=kind))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            weight = float(item.get("weight", 1.0 if kind == "must" else 0.5))
        except (TypeError, ValueError):
            weight = 1.0 if kind == "must" else 0.5
        out.append(Requirement(
            text=text,
            keyword=str(item.get("keyword") or "").strip().lower(),
            weight=max(0.0, min(1.0, weight)),
            kind=kind,
        ))
    return out


def _one_of(value: Any, allowed: tuple, default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _looks_like_a_page_not_a_job(text: str) -> bool:
    lowered = text.lower()
    tells = ("sign in", "log in", "create an account", "cookie", "enable javascript",
             "page not found", "access denied", "captcha")
    hits = sum(1 for t in tells if t in lowered)
    return len(text) < _MIN_JD_CHARS or hits >= 3


def extract(jd_text: str) -> Extraction:
    """Parse a job description into structured requirements."""
    jd_text = (jd_text or "").strip()
    if not jd_text:
        raise NotAJobDescription("empty input")
    if _looks_like_a_page_not_a_job(jd_text):
        raise NotAJobDescription(
            "input looks like a login wall or error page, not a job description. "
            "Paste the description text instead."
        )

    data = complete_json(
        "extract",
        system=_SYSTEM,
        user=f"Job description:\n\n{jd_text[:20000]}",
        max_tokens=3000,
        temperature=0.1,
    )

    if isinstance(data, dict) and data.get("error"):
        raise NotAJobDescription(str(data["error"]))
    if not isinstance(data, dict):
        raise NotAJobDescription(f"extract returned {type(data).__name__}, expected an object")

    keywords = data.get("keywords")
    keywords = [str(k).strip().lower() for k in keywords if str(k).strip()] \
        if isinstance(keywords, list) else []

    result = Extraction(
        title=str(data.get("title") or "").strip(),
        company=(str(data["company"]).strip() if data.get("company") else None),
        location=(str(data["location"]).strip() if data.get("location") else None),
        seniority=_one_of(data.get("seniority"), SENIORITIES, "unknown"),
        archetype=_one_of(data.get("archetype"), ARCHETYPES, "unknown"),
        must=_requirements(data.get("must"), "must"),
        nice=_requirements(data.get("nice"), "nice"),
        keywords=sorted(set(keywords)),
        comp_hints=(str(data["comp_hints"]).strip() if data.get("comp_hints") else None),
        notes=(str(data["notes"]).strip() if data.get("notes") else None),
        jd_text=jd_text,
    )

    # keywords should be a superset of the per requirement keywords, whatever the model did
    merged = set(result.keywords)
    merged.update(r.keyword for r in result.all_requirements if r.keyword)
    result.keywords = sorted(merged)

    if not result.must:
        log.warning(
            "extract found no must-have requirements for %r. The tailor stage will have "
            "little to aim at.", result.title or jd_text[:60]
        )
    return result
