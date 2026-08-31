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

from modules import keywords as keyword_tools
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


# A posting always names the role somewhere, even when the model hands back an empty
# title. The Marsh posting did exactly that, and an empty title costs three visible
# things: the headline under his name, the export filename, and the line the fit score
# reports against. Worth one regex rather than a re-ask.
_TITLE_LINE = re.compile(
    r"^(?:job\s*title|position|role|title)\s*[:\-]\s*(.{3,70})$", re.I | re.M)
_SEEKING = re.compile(
    r"\b(?:hiring|seeking|looking for|recruiting)\s+(?:an?\s+)?"
    r"((?:[A-Z][\w&/.-]*(?:\s+|,\s*)){1,5}(?:Analyst|Manager|Lead|Director|Engineer|"
    r"Specialist|Consultant|Associate|Officer|Head|Partner|Advisor|Executive))")


def title_from_text(jd_text: str) -> str:
    """Best effort at the role name when the model returns none."""
    text = jd_text or ""
    match = _TITLE_LINE.search(text)
    if match:
        return " ".join(match.group(1).split()).strip(" .-")

    match = _SEEKING.search(text)
    if match:
        return " ".join(match.group(1).split()).strip(" ,.-")

    # Failing that, the first short line that reads like a heading rather than a sentence.
    for line in text.splitlines()[:12]:
        line = " ".join(line.split())
        if 6 <= len(line) <= 70 and "." not in line and any(c.islower() for c in line):
            if re.search(r"\b(analyst|manager|lead|director|engineer|specialist|"
                         r"consultant|associate|officer|head|advisor|executive)\b",
                         line, re.I):
                return line
    return ""


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
    # Derived from where terms sit in the posting rather than from the model's own
    # must/nice split, which is not steady enough to be a scoring denominator.
    scored_must: List[str] = field(default_factory=list)
    scored_nice: List[str] = field(default_factory=list)

    @property
    def all_requirements(self) -> List[Requirement]:
        return self.must + self.nice

    def must_keywords(self) -> List[str]:
        """What the ATS gate scores against.

        Prefers the set derived from the posting's own structure. The model's per
        requirement keywords are the fallback, used when a posting has no headings to
        read, and they are the reason this exists: the same Wells Fargo description
        produced eighteen must-haves on one run and three on the next, so a resume passed
        or failed on which reading the model took that afternoon.
        """
        if self.scored_must:
            return list(self.scored_must)
        return [r.keyword for r in self.must if r.keyword]

    def nice_keywords(self) -> List[str]:
        if self.scored_nice:
            return list(self.scored_nice)
        return [r.keyword for r in self.nice if r.keyword]

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
        title=(str(data.get("title") or "").strip() or title_from_text(jd_text)),
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

    # A requirement can exist without being a searchable term, and conflating the two is
    # what made the ATS score unstable. One run returned "measurement and continuous
    # improvement" and "ai use case identification" as must-have keywords: phrases no
    # resume contains and no filter screens on, each one sitting in the denominator
    # pushing coverage toward the threshold that refuses the export. The requirement text
    # is kept and still shown; only its claim to be a keyword is dropped.
    dropped = []
    for requirement in result.all_requirements:
        if requirement.keyword and not keyword_tools.usable_keyword(requirement.keyword):
            dropped.append(requirement.keyword)
            requirement.keyword = ""

    # and the same test over the loose keyword list, deduplicated by meaning
    result.keywords = keyword_tools.sanitise(
        sorted(set(result.keywords)) + [r.keyword for r in result.all_requirements if r.keyword],
        limit=40,
    )

    # cap the must-have set. Beyond about a dozen, a job description is listing its
    # wishes rather than its filter, and every extra term makes the score noisier.
    kept = keyword_tools.sanitise([r.keyword for r in result.must if r.keyword])
    for requirement in result.must:
        if requirement.keyword and requirement.keyword.lower() not in kept:
            dropped.append(requirement.keyword)
            requirement.keyword = ""

    if dropped:
        log.info("extract: dropped %d unusable keyword(s): %s",
                 len(dropped), ", ".join(dropped[:8]))

    # Now the part that does not depend on the model's judgement at all. Every candidate
    # term is placed against the posting's own structure: mentioned in the required half
    # or only under "preferred", and how often. The posting does not change between runs.
    candidates = list(result.keywords) + [r.keyword for r in result.all_requirements
                                          if r.keyword]
    result.scored_must, result.scored_nice = keyword_tools.split_by_emphasis(
        jd_text, candidates)
    if result.scored_must:
        log.info("extract: %d must-have keyword(s) derived from the posting: %s",
                 len(result.scored_must), ", ".join(result.scored_must))
    else:
        log.warning("extract: no headings found to read emphasis from, falling back to "
                    "the model's own must/nice split for %r", result.title or "this job")

    if not result.must:
        log.warning(
            "extract found no must-have requirements for %r. The tailor stage will have "
            "little to aim at.", result.title or jd_text[:60]
        )
    return result
