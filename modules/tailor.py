"""Stage: facts + requirements -> graded resume blocks.

This is the stage that decides whether the whole app is useful or a liability, so the
safety does not live in the prompt. Prompts are advisory. These checks are not:

  1. Citation validation. The model is given real ProfileFact ids and must cite them.
     Any id it returns that does not exist is a fabricated citation. It gets stripped, and
     a block left with no surviving citation is forced to "blocked".

  2. Number validation. Every figure in a generated block must appear in at least one of
     the facts that block cites. This is what stops "$4.5M" quietly becoming "$5.4M", or
     "39 of 54 categories" becoming "49 of 54". A drifted number is the single most likely
     way this app could embarrass he in an interview, and a model will do it casually.

  3. Headcount scan. He works solo. Any block claiming a team is blocked outright.

Only education and certifications skip tailoring entirely. They are reproduced verbatim
from facts, because there is nothing to gain by rewording a degree and everything to lose.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import config
from modules import keywords
from modules import house
from modules.llm import complete_json
from modules.prompts import (ATS_CONTRACT, HOUSE_STYLE, NATURAL_LANGUAGE,
                            NEVER_CLAIM, NO_HEADCOUNT, TRUTH_CONTRACT)
from modules.render_docx import ResumePayload, Role

log = logging.getLogger(__name__)

GRADES = ("verified", "inferred", "stretch", "blocked")
TAILORED_SECTIONS = ("summary", "skills", "experience")

# figures a block might drift on: money, percentages, multiples, plain counts
_NUMBER = re.compile(
    r"(?:[$₹£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:[KMBkmb]n?)?)"      # $4.5M, ₹1,700
    r"|(?:\d[\d,]*(?:\.\d+)?\s*%)"                              # 99.7%
    r"|(?:\b\d[\d,]*(?:\.\d+)?\s*(?:[KMBkmb]n?)\b)"             # 120M
    r"|(?:\b\d{2,}\b)"                                          # 87, 54
)
_HEADCOUNT = re.compile(
    r"\b(led|managed|mentored|supervised|headed)\s+(a\s+)?(team|group|squad|people|analysts)\b"
    r"|\bteam of\b|\bdirect reports?\b|\bline manage",
    re.IGNORECASE,
)

_SYSTEM = f"""You tailor an existing career record to a specific job description. You are not
writing a new candidate. You are choosing which true things to say, in which order, in the
words the job description uses.

{TRUTH_CONTRACT}

{ATS_CONTRACT}

{NO_HEADCOUNT}

{NEVER_CLAIM}

{NATURAL_LANGUAGE}

{HOUSE_STYLE}

Return JSON only, no preamble and no code fence:

{{
  "summary": {{"text": "3 to 4 lines, first person implied, no 'I am a'", "fact_ids": [1,2]}},
  "skills": {{"items": ["Spend Analysis", "SQL"], "fact_ids": [30,31]}},
  "experience": [
    {{"org": "exact org name from the facts",
      "bullets": [
        {{"text": "one achievement, past tense, leading with the outcome",
          "fact_ids": [12],
          "grade": "verified" | "inferred" | "stretch",
          "rationale": "one short line: why this grade, and what the job called it"}}
      ]}}
  ]
}}

Hard requirements:
- Every bullet MUST carry at least one fact_id, drawn from the numbered FACTS you are given.
  A bullet you cannot cite is a bullet you must not write. Omit it.
- Never alter a number, a date or an organisation name. Copy figures exactly as the fact
  states them.
- Order orgs newest first. Order bullets within an org by how much this job cares.
- Aim for 4 to 6 bullets on the most recent role, fewer on older ones. Skip an org entirely
  if nothing about it is relevant to this job.
- "skills" should be the subset of skill facts this job actually asks for, in the job's own
  wording where the fact supports it."""


@dataclass
class Block:
    section: str
    text: str
    fact_ids: List[int] = field(default_factory=list)
    grade: str = "blocked"
    rationale: Optional[str] = None
    org: Optional[str] = None
    order_index: int = 0
    accepted: bool = False

    @property
    def needs_review(self) -> bool:
        return self.grade in {"inferred", "stretch"}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section, "text": self.text, "fact_ids": self.fact_ids,
            "grade": self.grade, "rationale": self.rationale, "org": self.org,
            "order_index": self.order_index, "accepted": self.accepted,
        }


@dataclass
class TailorResult:
    blocks: List[Block] = field(default_factory=list)
    rejected: List[Tuple[Block, str]] = field(default_factory=list)
    keyword_hits: Dict[str, bool] = field(default_factory=dict)
    placement: Optional[Any] = None      # keywords.Placement, set by tailor()

    @property
    def needs_review(self) -> List[Block]:
        return [b for b in self.blocks if b.needs_review]

    @property
    def coverage(self) -> float:
        if not self.keyword_hits:
            return 0.0
        return 100.0 * sum(self.keyword_hits.values()) / len(self.keyword_hits)

    def summary_line(self) -> str:
        by_grade = {g: sum(1 for b in self.blocks if b.grade == g) for g in GRADES}
        return (
            f"{len(self.blocks)} blocks "
            f"(verified={by_grade['verified']} inferred={by_grade['inferred']} "
            f"stretch={by_grade['stretch']}), {len(self.rejected)} rejected, "
            f"must-have keyword coverage {self.coverage:.0f}%"
        )


# --------------------------------------------------------------------------- fact prep

def _facts_block(facts: Sequence[Any]) -> str:
    """Render facts as a numbered list the model can cite by real database id."""
    lines: List[str] = []
    by_parent: Dict[Optional[int], List[Any]] = {}
    for f in facts:
        by_parent.setdefault(f.parent_id, []).append(f)

    for fact in sorted(
        (f for f in facts if f.parent_id is None),
        key=lambda f: (f.kind != "role", f.order_index),
    ):
        head = f"[{fact.id}] ({fact.kind})"
        if fact.org:
            head += f" {fact.org} |"
        head += f" {fact.text}"
        if fact.date_from:
            head += f" | {fact.date_from} - {fact.date_to or 'Present'}"
        if fact.tags:
            head += f" | tags: {', '.join(fact.tags)}"
        lines.append(head)
        for child in sorted(by_parent.get(fact.id, []), key=lambda f: f.order_index):
            tags = f" | tags: {', '.join(child.tags)}" if child.tags else ""
            lines.append(f"    [{child.id}] {child.text}{tags}")
    return "\n".join(lines)


def _numbers(text: str) -> List[str]:
    return [re.sub(r"[\s,]", "", m.group(0)).lower() for m in _NUMBER.finditer(text or "")]


# A tenure claim is the first thing a recruiter filters on, and _NUMBER does not see it:
# it matches currency and percentages, so "6+ years" and "nine years" both pass unchecked.
# The first live run understated nine years as "6+ years" against a job asking for 5+.
# Wrong in the direction that costs an interview, and invisible to every existing guard.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
# Deliberately narrow. It must read as a claim about how long he has worked, so a
# "3 year programme" or a "five-year value case" is left alone. A false positive here
# blocks a summary, which is expensive, so the phrase has to earn the match.
_TENURE_CONTEXT = (
    r"(?:\s*(?:'|\u2019)?s?\s*"
    r"(?:of\s+"
    r"|(?:professional\s+|hands.on\s+|combined\s+)?"
    r"(?:experience|expertise|background|career|delivering|working|spanning|across|in)\b))"
)
_TENURE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_WORD_NUMBERS) + r")\s*\+?\s*(?:years?|yrs?)\b"
    r"(?=" + _TENURE_CONTEXT + r")"
    r"(?![^.]{0,24}\b(?:ago|old)\b)",
    re.I,
)

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec")

# how far a claim may sit from the computed figure before it is treated as wrong
TENURE_TOLERANCE_YEARS = 1.0


def _as_months(value: Optional[str], today_months: int) -> Optional[int]:
    """'Apr 2018' or 'Present' to a month count. None when unparseable."""
    if not value:
        return None
    text = value.strip().lower()
    if text in ("present", "current", "now", "ongoing"):
        return today_months
    match = re.search(r"([a-z]{3})[a-z]*\.?\s*(\d{4})", text)
    if match and match.group(1) in _MONTHS:
        return int(match.group(2)) * 12 + _MONTHS.index(match.group(1))
    match = re.search(r"\b(\d{4})\b", text)
    return int(match.group(1)) * 12 if match else None


def experience_years(facts: Sequence[Any], today: Optional[date] = None) -> Optional[float]:
    """Years actually worked, summed across roles, merging any overlap.

    Summed rather than measured end to end, because a study gap is not experience and
    claiming it as such is the kind of thing a background check turns up.
    """
    today = today or date.today()
    now_months = today.year * 12 + (today.month - 1)

    spans = []
    for fact in facts:
        if getattr(fact, "kind", None) != "role":
            continue
        start = _as_months(getattr(fact, "date_from", None), now_months)
        end = _as_months(getattr(fact, "date_to", None), now_months)
        if start is None:
            continue
        spans.append((start, min(end if end is not None else now_months, now_months)))

    if not spans:
        return None

    total, cursor = 0, None
    for start, end in sorted(spans):
        if end <= start:
            continue
        if cursor is not None and start < cursor:
            start = cursor          # overlapping roles are not counted twice
        if end > start:
            total += end - start
            cursor = end
    return round(total / 12.0, 1)


def _tenure_claims(text: str) -> List[float]:
    out = []
    for match in _TENURE.finditer(text or ""):
        token = match.group(1).lower()
        out.append(float(_WORD_NUMBERS[token] if token in _WORD_NUMBERS else int(token)))
    return out


# ------------------------------------------------------------------------- validation

def _validate(block: Block, known: Dict[int, Any],
              actual_years: Optional[float] = None) -> Tuple[Block, Optional[str]]:
    """Apply the three hard checks. Returns the block and a rejection reason, or None."""
    # 1. citations must exist
    real = [i for i in block.fact_ids if i in known]
    invented = [i for i in block.fact_ids if i not in known]
    if invented:
        log.warning(
            "FABRICATED CITATION: block cited fact ids %s which do not exist. Text: %r",
            invented, block.text[:90],
        )
    block.fact_ids = real
    if not real:
        block.grade = "blocked"
        return block, "cites no real fact"

    # 2. numbers must come from the cited facts. config.strict_numbers can turn this off.
    if not config.strict_numbers:
        if block.grade not in GRADES or block.grade == "blocked":
            block.grade = "inferred"
        return block, None
    cited_text = " ".join(
        (known[i].text or "") + " " + " ".join(str(v) for v in (known[i].metrics or {}).values())
        for i in real
    )
    allowed = set(_numbers(cited_text))

    # 2a. Tenure is checked against the role dates rather than the cited text, because a
    # summary citing a role fact can still get the years wrong in either direction. It
    # runs before the drift check and exempts its own digits on success: with 9.8 years
    # on the clock "10 years" is the honest phrasing, and _NUMBER would otherwise read
    # that 10 as an invented figure and block a true statement.
    if actual_years is not None:
        for claimed in _tenure_claims(block.text):
            if abs(claimed - actual_years) > TENURE_TOLERANCE_YEARS:
                block.grade = "blocked"
                log.warning(
                    "TENURE DRIFT: claimed %.0f years, roles total %.1f. Text: %r",
                    claimed, actual_years, block.text[:90],
                )
                return block, (f"claims {claimed:.0f} years of experience, "
                               f"the role dates total {actual_years:.1f}")
            allowed.add(str(int(claimed)))

    drifted = [n for n in _numbers(block.text) if n not in allowed]
    if drifted:
        block.grade = "blocked"
        log.warning(
            "NUMBER DRIFT: %s not present in cited facts %s. Text: %r",
            drifted, real, block.text[:90],
        )
        return block, f"number(s) {drifted} not in cited facts"

    # 3. no headcount claims
    if _HEADCOUNT.search(block.text):
        block.grade = "blocked"
        return block, "headcount or team-leadership claim"

    # 4. no qualification he does not hold. Blocked rather than downgraded, because a
    # degree is the one claim a background check verifies exactly and the substitution a
    # model reaches for is always the more famous one: two masters become an MBA.
    degrees = house.claims_a_degree(block.text)
    if degrees:
        block.grade = "blocked"
        log.warning("INVENTED QUALIFICATION: %s. Text: %r", degrees, block.text[:90])
        return block, "claims a qualification not held: " + ", ".join(degrees)

    # 5. no experience in a domain he has never worked in. Matched only inside a frame
    # that asserts experience, so a bullet that prices freight keeps the word logistics
    # and a bullet claiming years in logistics does not.
    domains = house.claims_a_domain(block.text)
    if domains:
        block.grade = "blocked"
        log.warning("UNWORKED DOMAIN: %s. Text: %r",
                    [d for d, _ in domains], block.text[:90])
        return block, ("claims experience in " + ", ".join(sorted({d for d, _ in domains}))
                       + ", which is not on the record")

    if block.grade not in GRADES or block.grade == "blocked":
        block.grade = "inferred"
    return block, None


def _coerce_bullets(raw: Any, org: str, known: Dict[int, Any]) -> List[Block]:
    out: List[Block] = []
    if not isinstance(raw, list):
        return out
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        ids = item.get("fact_ids") or []
        ids = [int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()]
        out.append(Block(
            section="experience", org=org, text=text, fact_ids=ids,
            grade=str(item.get("grade") or "inferred").strip().lower(),
            rationale=(str(item["rationale"]).strip() if item.get("rationale") else None),
            order_index=index,
        ))
    return out


# ------------------------------------------------------------------------------ public

def _roles_block(facts: Sequence[Any]) -> str:
    """Every employer, newest first, with how many bullets each is worth.

    The counts follow the convention that relevance decays: the reader knows it, and a
    uniform five bullets per role across fifteen years reads as padding.
    """
    # By date, not by string. Sorting "Mar 2015" against "Dec 2023" alphabetically put
    # March first, so the newest role was given one bullet and the oldest six.
    def started(fact: Any) -> int:
        return _as_months(getattr(fact, "date_from", None), 0) or 0

    roles = sorted((f for f in facts if getattr(f, "kind", "") == "role"),
                   key=started, reverse=True)
    wanted = (6, 4, 3)
    lines = []
    for index, role in enumerate(roles):
        count = wanted[index] if index < len(wanted) else 1
        dates = f"{role.date_from or '?'} to {role.date_to or 'Present'}"
        lines.append(f"- {role.org} ({dates}): {role.text}. Write {count} bullet"
                     f"{'' if count == 1 else 's'}.")
    return "\n".join(lines) or "- none on record"


def tailor(extraction: Any, facts: Sequence[Any],
           house_spec: str = "") -> TailorResult:
    """Produce graded blocks for one job. Nothing here trusts the model."""
    known = {f.id: f for f in facts}
    if not known:
        raise RuntimeError("No ProfileFact rows. Run scripts/seed_profile.py first.")

    citable = [f for f in facts if getattr(f, "verified", True)]
    if len(citable) < len(facts):
        log.info("%d unverified fact(s) withheld from tailoring", len(facts) - len(citable))

    # Computed, not estimated. Left to itself the model guesses from the role list and
    # gets it wrong, which is how a nine year record went out claiming "6+ years".
    actual_years = experience_years(facts)

    must = "\n".join(
        f"- ({r.weight:.1f}) {r.text}" + (f"  [keyword: {r.keyword}]" if r.keyword else "")
        for r in extraction.must
    ) or "- none stated"
    nice = "\n".join(f"- {r.text}" for r in extraction.nice) or "- none stated"

    user = (
        f"TARGET ROLE: {extraction.title or 'unspecified'}\n"
        f"COMPANY: {extraction.company or 'unspecified'}\n"
        f"SENIORITY: {extraction.seniority}\nEMPLOYER TYPE: {extraction.archetype}\n\n"
        f"MUST HAVE:\n{must}\n\nNICE TO HAVE:\n{nice}\n\n"
        + (f"YEARS OF EXPERIENCE: {actual_years:.0f}. This figure is computed from the\n"
           f"role dates. If you state years of experience, state this number. Do not\n"
           f"estimate your own and do not round it down.\n\n"
           if actual_years is not None else "")
        +
        f"MUST-HAVE KEYWORDS. Use these exact strings wherever a fact supports it. A\n"
        f"synonym does not match and scores zero:\n"
        f"  {', '.join(extraction.must_keywords()) or 'none stated'}\n\n"
        f"OTHER KEYWORDS WORTH HITTING IF TRUE:\n"
        f"  {', '.join(extraction.keywords) or 'none'}\n\n"
        f"ANSWER THE CHECKLIST, NOT THE BRIEF. For each must-have above, decide which\n"
        f"bullet is meant to satisfy it, and make that bullet say so in the posting's own\n"
        f"words. A requirement with no bullet assigned to it is a failed screen. An\n"
        f"outcome the reader admires does not answer a question they did not ask: a\n"
        f"five-year value case is a fine result and no evidence at all of programme\n"
        f"management.\n\n"
        f"USE THEIR VOCABULARY WHERE THE WORK IS REAL. If the posting names a method or a\n"
        f"metric, Lean, root cause, input and output metrics, cycle time, and he has\n"
        f"actually done that thing under another name, describe it in theirs. This is\n"
        f"where most of the value is. If he has not done it, leave the term out\n"
        f"completely. Never carry a term he cannot back.\n\n"
        f"FRAME FOR THE ROLE BEING APPLIED FOR. An analyst bullet describes a deliverable:\n"
        f"built, analysed, automated. A manager bullet describes ownership of an outcome\n"
        f"across people he does not manage: the scope owned, who was influenced, the\n"
        f"metric and how far it moved, over what period. The same real work supports\n"
        f"either. Only the framing differs, and the framing is what is being read. This\n"
        f"NEVER means promoting him: he has no reports, and inventing scope is the one\n"
        f"failure worse than a rejection.\n\n"
        f"ROLES. Write bullets for EVERY one of these, not only the most recent. A\n"
        f"resume missing a job reads as a gap being hidden, and the reader assumes the\n"
        f"worst case. Weight by recency: the current role carries the argument, older\n"
        f"ones establish the arc.\n{_roles_block(citable)}\n\n"
        f"FACTS (cite these by the id in square brackets):\n{_facts_block(citable)}"
    )

    # 6000 truncated Claude's output mid-JSON on two runs in three during the bake-off,
    # which surfaces as "did not return parseable JSON" and looks like a model that
    # cannot follow a schema. It was a budget, not a capability. Claude writes more
    # blocks than the NIM models for the same input, so the ceiling has to fit the most
    # verbose model on the list, not the one currently routed.
    data = complete_json("tailor", system=_SYSTEM + (house_spec or ""), user=user,
                         max_tokens=12000, temperature=0.3)
    if not isinstance(data, dict):
        raise RuntimeError(f"tailor returned {type(data).__name__}, expected an object")

    candidates: List[Block] = []

    summary = data.get("summary")
    if isinstance(summary, dict) and str(summary.get("text") or "").strip():
        ids = [int(i) for i in (summary.get("fact_ids") or []) if str(i).isdigit()]
        candidates.append(Block("summary", str(summary["text"]).strip(), ids, "inferred"))

    skills = data.get("skills")
    if isinstance(skills, dict):
        items = [str(s).strip() for s in (skills.get("items") or []) if str(s).strip()]
        if items:
            ids = [int(i) for i in (skills.get("fact_ids") or []) if str(i).isdigit()]
            candidates.append(Block("skills", " | ".join(items), ids, "verified"))

    for org_index, entry in enumerate(data.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        org = str(entry.get("org") or "").strip()
        for block in _coerce_bullets(entry.get("bullets"), org, known):
            block.order_index += org_index * 100
            candidates.append(block)

    result = TailorResult()
    for block in candidates:
        checked, reason = _validate(block, known, actual_years)
        (result.rejected.append((checked, reason)) if reason
         else result.blocks.append(checked))

    # Deterministic keyword placement. The model does this unreliably enough that
    # coverage swung 45 to 57 points across runs on every model tested, which decides
    # whether the resume is read by a person. See modules/keywords.py.
    written = " ".join(b.text for b in result.blocks)
    current_skills = [
        s.strip()
        for b in result.blocks if b.section == "skills"
        for s in b.text.split("|") if s.strip()
    ]
    result.placement = keywords.place(
        written,
        must=extraction.must_keywords(),
        nice=extraction.nice_keywords(),
        facts=citable,
        skills=current_skills,
    )
    # A block asserting a skill nothing in the record supports is downgraded rather than
    # blocked. Blocking would be wrong: the sentence may still be a fair reframing of the
    # fact it cites, and this app's whole position is that aggressive reframing is the
    # product. But it stops rendering unsupervised. He looks at it and decides.
    if result.placement.unsupported:
        for block in result.blocks:
            claimed = [
                term for term in result.placement.unsupported
                if term.lower() in block.text.lower()
                or " ".join(keywords.significant(term)) in " ".join(keywords.tokens(block.text))
            ]
            if not claimed:
                continue
            note = ("no fact supports " + ", ".join(f"{c!r}" for c in claimed[:3]))
            block.rationale = f"{block.rationale}. {note}" if block.rationale else note.capitalize()
            if block.grade == "verified":
                block.grade = "inferred"
                log.warning("downgraded to inferred, %s: %r", note, block.text[:70])

    for block in keywords.as_blocks(result.placement, Block):
        checked, reason = _validate(block, known, actual_years)
        if reason:
            log.warning("keyword placement block rejected by the gate: %s", reason)
            result.rejected.append((checked, reason))
        else:
            result.blocks.append(checked)

    rendered = " ".join(b.text for b in result.blocks).lower()
    result.keyword_hits = {kw: kw in rendered for kw in extraction.must_keywords()}

    log.info("tailor: %s", result.summary_line())
    return result


def _study_entry(facts: Sequence[Any], start: int, end: int) -> Optional[Role]:
    """A dated entry for a study period, to sit in the experience run.

    Better than moving the education section above experience, which was the first fix
    and reads as an odd ordering. A reader who meets the gap and its explanation in the
    same glance never forms the doubt, and the section order stays conventional.
    """
    degrees = [f for f in facts if getattr(f, "kind", "") == "education"]
    if not degrees:
        return None

    def year(months_value: int) -> int:
        return months_value // 12

    names = []
    for degree in degrees:
        text = (getattr(degree, "text", "") or "").strip()
        if text and text not in names:
            names.append(text)
    if not names:
        return None

    org = next((getattr(d, "org", "") for d in degrees if getattr(d, "org", "")), "")
    return Role(
        title="Full-time postgraduate study" if len(names) > 1 else names[0],
        org=org,
        dates=f"{_month_name(start)} - {_month_name(end)}",
        location="",
        bullets=[],
        is_study=True,
    )


def _month_name(months_value: int) -> str:
    return f"{_MONTHS[months_value % 12].title()} {months_value // 12}"


def _insert_study(experience: List[Role], entry: Role, roles: Sequence[Any],
                  gap) -> List[Role]:
    """Put the study entry between the two roles it sits between, newest first."""
    by_org = {}
    today = date.today().year * 12 + date.today().month - 1
    for role in roles:
        by_org[role.org] = _as_months(getattr(role, "date_from", None), today)

    out = []
    placed = False
    for role in experience:
        start = by_org.get(role.org)
        if not placed and start is not None and start <= gap[0]:
            out.append(entry)
            placed = True
        out.append(role)
    if not placed:
        out.append(entry)
    return out


def _gap_between(roles: Sequence[Any], months: int = 6):
    """The first break long enough for a reader to notice, as (start, end) in months."""
    today = date.today().year * 12 + date.today().month - 1
    spans = []
    for role in roles:
        begin = _as_months(getattr(role, "date_from", None), today)
        finish = _as_months(getattr(role, "date_to", None), today)
        if begin is not None:
            spans.append((begin, finish if finish is not None else today))
    spans.sort()
    for i in range(len(spans) - 1):
        if spans[i + 1][0] - spans[i][1] >= months:
            return spans[i][1], spans[i + 1][0]
    return None


def _has_study_gap(roles: Sequence[Any], months: int = 6) -> bool:
    """Is there a break between roles long enough for a reader to notice?

    Six months. Below that a reader assumes notice periods and a holiday; above it they
    start filling the silence in themselves, and they fill it unfavourably. Where the
    break is study, the education section explains it, but only if it is somewhere the
    eye reaches before the doubt forms.
    """
    today = date.today().year * 12 + date.today().month - 1
    spans = []
    for role in roles:
        start = _as_months(getattr(role, "date_from", None), today)
        end = _as_months(getattr(role, "date_to", None), today)
        if start is not None:
            spans.append((start, end if end is not None else today))
    spans.sort()
    return any(spans[i + 1][0] - spans[i][1] >= months for i in range(len(spans) - 1))


def _relevance_of(text: str, terms: Sequence[str]) -> int:
    """How many of the job's own must-have terms one bullet carries."""
    if not terms:
        return 0
    lowered = " ".join(keywords.tokens(text or ""))
    hits = 0
    for term in terms:
        wanted = keywords.significant(term)
        if wanted and " ".join(wanted) in lowered:
            hits += 1
    return hits


def to_payload(
    result: TailorResult,
    facts: Sequence[Any],
    include_unaccepted: bool = False,
    contact: Optional[Sequence[str]] = None,
    name: Optional[str] = None,
    headline: Optional[str] = None,
    wanted_terms: Optional[Sequence[str]] = None,
) -> ResumePayload:
    """Assemble accepted blocks plus verbatim facts into something renderable.

    Education, certifications and contact details are not tailored. They come straight from
    the facts, because rewording a degree gains nothing and risks everything.
    """
    wanted_terms = list(wanted_terms or [])

    def usable(block: Block) -> bool:
        if block.grade == "blocked":
            return False
        return True if block.grade == "verified" else (block.accepted or include_unaccepted)

    by_id = {f.id: f for f in facts}
    roles = sorted(
        (f for f in facts if f.kind == "role"), key=lambda f: f.order_index
    )
    role_by_org = {r.org: r for r in roles if r.org}

    summary = next((b.text for b in result.blocks if b.section == "summary" and usable(b)), "")
    # Every usable skills block, not just the first. Keyword placement adds a second and
    # sometimes a third, graded separately, and taking only the first would silently drop
    # whichever ones a human had just agreed to.
    skills: List[str] = []
    seen_skills = set()
    for block in sorted((b for b in result.blocks if b.section == "skills" and usable(b)),
                        key=lambda b: b.order_index):
        for item in block.text.split("|"):
            item = item.strip()
            key = item.lower()
            if item and key not in seen_skills:
                seen_skills.add(key)
                skills.append(item)

    experience: List[Role] = []
    for role in roles:
        mine = [b for b in result.blocks
                if b.section == "experience" and b.org == role.org and usable(b)]
        # Ordered by how much of what this job screens on each bullet actually carries.
        # A review found the two bullets mapping to the posting's own named priorities
        # sitting third and fourth in a block of six, because the model had ordered them
        # by dollar size. A reader gives the block seconds, and the first two lines are
        # the ones that get read.
        bullets = [b.text for b in sorted(
            mine, key=lambda b: (-_relevance_of(b.text, wanted_terms), b.order_index))]
        # A role with no bullets still appears, with its title and dates. Dropping it
        # deleted Halcyon Energy and HDFC Bank from a resume outright, which does not read
        # as brevity: it reads as a four-year hole the reader fills in unfavourably.
        # Title and dates only is a legitimate treatment for an older role. Silence is
        # not.
        experience.append(Role(
            title=role.text,
            org=role.org or "",
            dates=f"{role.date_from} - {role.date_to or 'Present'}",
            bullets=bullets,
        ))

    def verbatim(kind: str) -> List[str]:
        out = []
        for f in sorted((f for f in facts if f.kind == kind), key=lambda f: f.order_index):
            line = f.text
            if f.org:
                line += f", {f.org}"
            if f.date_to or f.date_from:
                line += f", {f.date_to or f.date_from}"
            out.append(line)
        return out

    # Contact details come from ContactDetail when the caller supplies them, because the
    # app owns those and a re-seed must not revert them. Falling back to the facts keeps
    # the old path working for a database that predates the table.
    if contact is None:
        contact = [f.text for f in facts if f.kind == "contact"]
    if not name:
        name = next((f.text for f in facts if f.kind == "name"), "")

    # A study period goes into the experience run at the point where the gap is.
    gap = _gap_between(roles)
    if gap:
        entry = _study_entry(facts, gap[0], gap[1])
        if entry:
            experience = _insert_study(experience, entry, roles, gap)

    return ResumePayload(
        name=name,
        headline=(headline or "").strip(),
        skill_groups=keywords.group_skills(skills),
        contact=contact,
        summary=summary,
        skills=skills,
        experience=experience,
        education=verbatim("education"),
        certifications=verbatim("cert"),
    )


# --------------------------------------------------------------------- the revision pass

_REVISE_SYSTEM = """You are fixing specific faults in a resume that is otherwise written.

You are given requirements from the posting that no bullet currently answers, and bullets
that were refused by the checks. Write replacements for those and nothing else. Do not
rewrite the document. Do not restate what already works.

Return JSON only, no preamble and no code fence:

{"bullets": [{"org": "the employer this belongs under", "text": "the bullet",
              "fact_ids": [1, 2], "grade": "verified|inferred|stretch",
              "answers": "the requirement it is meant to satisfy"}]}

THE RULE THAT MATTERS MOST HERE. You may return fewer bullets than there are
requirements, and for a requirement no fact supports you must return nothing at all. A
requirement left unanswered is reported to the candidate as a genuine gap, which is
useful and true. A requirement answered by a sentence the record does not support is
caught by the same checks that refused the last attempt, so it wastes a round and, if it
somehow passed, would be the one failure worse than a rejection. When you are unsure
whether a fact supports a claim, leave the claim out.

Every bullet cites the fact ids it draws on. A bullet citing nothing is discarded."""


def revise(extraction: Any, facts: Sequence[Any], unanswered: Sequence[str],
           rejected: Sequence[Tuple[Any, str]], house_spec: str = "",
           family_guidance: str = "") -> Tuple[List[Block], List[Tuple[Block, str]]]:
    """One focused repair pass. Returns (accepted blocks, rejected blocks with reasons).

    Deliberately small. The first design re-ran the whole tailor call each round, which
    on the free route meant three minutes per round and produced a different document
    every time, so improvements in one part arrived alongside regressions in another.
    Sending only the faults keeps a round to seconds and leaves everything that already
    passed exactly as it was.
    """
    known = {f.id: f for f in facts}
    citable = [f for f in facts if getattr(f, "verified", True)]
    if not unanswered and not rejected:
        return [], []

    parts = []
    if unanswered:
        parts.append(
            "REQUIREMENTS WITH NO BULLET ANSWERING THEM. Write one bullet for each that "
            "the facts genuinely support, and skip the rest:\n"
            + "\n".join(f"- {term}" for term in unanswered)
        )
    if rejected:
        # The reason matters more than the text. A block refused for an invented number
        # needs the number removed, not a different sentence.
        lines = []
        for block, reason in rejected[:8]:
            lines.append(f"- REFUSED ({reason}): {getattr(block, 'text', '')[:200]}")
        parts.append(
            "BULLETS THE CHECKS REFUSED. Each reason is exact. Fix that specific fault, "
            "or leave the bullet out if it cannot be fixed honestly:\n" + "\n".join(lines)
        )

    user = (
        f"TARGET ROLE: {extraction.title or 'unspecified'}\n\n"
        + "\n\n".join(parts)
        + f"\n\nROLES:\n{_roles_block(citable)}"
        + f"\n\nFACTS (cite these by the id in square brackets):\n{_facts_block(citable)}"
    )

    system = _REVISE_SYSTEM
    if family_guidance:
        system += "\n\n" + family_guidance
    if house_spec:
        system += house_spec

    data = complete_json("tailor", system=system, user=user,
                         max_tokens=6000, temperature=0.3)
    if not isinstance(data, dict):
        raise RuntimeError(f"revise returned {type(data).__name__}, expected an object")

    actual_years = experience_years(facts)
    accepted: List[Block] = []
    refused: List[Tuple[Block, str]] = []
    for entry in data.get("bullets") or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        ids = [int(i) for i in (entry.get("fact_ids") or []) if str(i).isdigit()]
        grade = str(entry.get("grade") or "inferred").strip().lower()
        block = Block("experience", text, ids,
                      grade if grade in GRADES else "inferred",
                      org=str(entry.get("org") or "").strip() or None)
        # The same gates, unchanged. A revision is not a second chance at the truth
        # rules, only at the wording, and routing around them here would undo the point
        # of having them raise rather than filter.
        checked, reason = _validate(block, known, actual_years)
        (refused.append((checked, reason)) if reason else accepted.append(checked))

    return accepted, refused
