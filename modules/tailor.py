"""Stage: facts + requirements -> graded resume blocks.

This is the stage that decides whether the whole app is useful or a liability, so the
safety does not live in the prompt. Prompts are advisory. These checks are not:

  1. Citation validation. The model is given real ProfileFact ids and must cite them.
     Any id it returns that does not exist is a fabricated citation. It gets stripped, and
     a block left with no surviving citation is forced to "blocked".

  2. Number validation. Every figure in a generated block must appear in at least one of
     the facts that block cites. This is what stops "$19.3M" quietly becoming "$29.3M", or
     "39 of 54 categories" becoming "49 of 54". A drifted number is the single most likely
     way this app could embarrass Sameer in an interview, and a model will do it casually.

  3. Headcount scan. He works solo. Any block claiming a team is blocked outright.

Only education and certifications skip tailoring entirely. They are reproduced verbatim
from facts, because there is nothing to gain by rewording a degree and everything to lose.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import config
from modules.llm import complete_json
from modules.prompts import ATS_CONTRACT, HOUSE_STYLE, NO_HEADCOUNT, TRUTH_CONTRACT
from modules.render_docx import ResumePayload, Role

log = logging.getLogger(__name__)

GRADES = ("verified", "inferred", "stretch", "blocked")
TAILORED_SECTIONS = ("summary", "skills", "experience")

# figures a block might drift on: money, percentages, multiples, plain counts
_NUMBER = re.compile(
    r"(?:[$₹£€]\s?\d[\d,]*(?:\.\d+)?\s*(?:[KMBkmb]n?)?)"      # $19.3M, ₹1,700
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


# ------------------------------------------------------------------------- validation

def _validate(block: Block, known: Dict[int, Any]) -> Tuple[Block, Optional[str]]:
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

def tailor(extraction: Any, facts: Sequence[Any]) -> TailorResult:
    """Produce graded blocks for one job. Nothing here trusts the model."""
    known = {f.id: f for f in facts}
    if not known:
        raise RuntimeError("No ProfileFact rows. Run scripts/seed_profile.py first.")

    citable = [f for f in facts if getattr(f, "verified", True)]
    if len(citable) < len(facts):
        log.info("%d unverified fact(s) withheld from tailoring", len(facts) - len(citable))

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
        f"MUST-HAVE KEYWORDS. Use these exact strings wherever a fact supports it. A\n"
        f"synonym does not match and scores zero:\n"
        f"  {', '.join(extraction.must_keywords()) or 'none stated'}\n\n"
        f"OTHER KEYWORDS WORTH HITTING IF TRUE:\n"
        f"  {', '.join(extraction.keywords) or 'none'}\n\n"
        f"FACTS (cite these by the id in square brackets):\n{_facts_block(citable)}"
    )

    data = complete_json("tailor", system=_SYSTEM, user=user, max_tokens=6000, temperature=0.3)
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
        checked, reason = _validate(block, known)
        (result.rejected.append((checked, reason)) if reason
         else result.blocks.append(checked))

    rendered = " ".join(b.text for b in result.blocks).lower()
    result.keyword_hits = {kw: kw in rendered for kw in extraction.must_keywords()}

    log.info("tailor: %s", result.summary_line())
    return result


def to_payload(
    result: TailorResult,
    facts: Sequence[Any],
    include_unaccepted: bool = False,
) -> ResumePayload:
    """Assemble accepted blocks plus verbatim facts into something renderable.

    Education, certifications and contact details are not tailored. They come straight from
    the facts, because rewording a degree gains nothing and risks everything.
    """
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
    skills_block = next((b for b in result.blocks if b.section == "skills" and usable(b)), None)
    skills = [s.strip() for s in skills_block.text.split("|")] if skills_block else []

    experience: List[Role] = []
    for role in roles:
        bullets = [
            b.text for b in sorted(
                (b for b in result.blocks
                 if b.section == "experience" and b.org == role.org and usable(b)),
                key=lambda b: b.order_index,
            )
        ]
        if not bullets:
            continue
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

    contact = [f.text for f in facts if f.kind == "contact"]
    name = next((f.text for f in facts if f.kind == "name"), "")

    return ResumePayload(
        name=name,
        contact=contact,
        summary=summary,
        skills=skills,
        experience=experience,
        education=verbatim("education"),
        certifications=verbatim("cert"),
    )
