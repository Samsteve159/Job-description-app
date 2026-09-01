"""Cover letters, under the same rules as the resume plus one the resume never needed.

A cover letter makes two kinds of claim, and only one of them is about him.

The first kind is career claims, and those go through the machinery that already exists:
every paragraph cites fact ids, the citations are checked against the store, numbers must
appear in a cited fact, headcount is refused. `tailor._validate` does all of it and is
reused verbatim rather than reimplemented, because a second copy of a safety check is a
second copy to forget to update.

The second kind is claims about *them*. "I have long admired Northwind Bank's leadership in
digital treasury" invents a fact about a company and a feeling about it in one sentence,
and no citation guard would notice: the paragraph can cite a perfectly real fact about his
SQL work while opening with a fabrication. So company claims are checked against the job
description text. If the posting did not say it, he does not know it.

That check is why this module exists at all rather than being three lines in tailor.py.

The other thing worth defending is length. A cover letter is read in about twenty seconds
by somebody who has forty of them, and the ones that work are short, specific and lead
with evidence. MAX_WORDS is enforced rather than requested, because "keep it brief" in a
prompt is a suggestion and models are not brief.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from modules import keywords as kw
from modules.llm import complete_json
from modules.prompts import HOUSE_STYLE, NO_HEADCOUNT, TRUTH_CONTRACT
from modules.tailor import Block, _validate, experience_years

log = logging.getLogger(__name__)

MAX_WORDS = 300
MIN_WORDS = 90
MAX_PARAGRAPHS = 4

# Openings and phrases that mark a letter as one of forty. Checked, not just discouraged,
# because every model reaches for them and the prompt saying "do not" is not enough.
CLICHES = (
    "i am writing to apply",
    "i am writing to express",
    "please find attached",
    "please find enclosed",
    "i would like to apply",
    "i am excited to apply",
    "thank you for considering my application",
    "i believe i am the perfect",
    "i am the ideal candidate",
    "i am passionate about",
    "i have always been passionate",
    "dynamic and results-driven",
    "proven track record of success",
    "wealth of experience",
    "hit the ground running",
    "think outside the box",
    "team player",
    "hard-working individual",
    "i look forward to hearing from you at your earliest convenience",
)

# Sentences that say something about the employer rather than about him. If a paragraph
# contains one of these shapes, whatever it asserts has to be traceable to the posting.
_ABOUT_THEM = re.compile(
    r"\b(?:your|their|the)\s+(?:company|firm|organisation|organization|team|business|"
    r"mission|culture|values|reputation|growth|leadership|history|brand|products?|"
    r"platform|approach|success|innovation|commitment)\b",
    re.I,
)
_ADMIRATION = re.compile(
    r"\b(?:i (?:have )?(?:long )?(?:admired|followed|respected)|"
    r"impressed by|drawn to|excited by|inspired by|passionate about|"
    r"leader in|leading|pioneer|world.class|best.in.class|renowned|prestigious)\b",
    re.I,
)


@dataclass
class CoverLetter:
    greeting: str = "Dear Hiring Manager,"
    paragraphs: List[Block] = field(default_factory=list)
    sign_off: str = "Kind regards,"
    rejected: List[Tuple[Block, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def usable(self) -> List[Block]:
        return [p for p in self.paragraphs
                if p.grade == "verified" or (p.grade != "blocked" and p.accepted)]

    @property
    def word_count(self) -> int:
        """Words in what would actually render, given what has been accepted so far."""
        return len(" ".join(p.text for p in self.usable).split())

    @property
    def draft_word_count(self) -> int:
        """Words in everything that survived the checks, accepted or not.

        Length is judged on this at draft time. Judging it on `word_count` warned that a
        fresh letter was too thin, when the only reason it was thin is that nothing had
        been ticked yet: the reaching paragraphs are excluded until he agrees to them.
        """
        return len(" ".join(p.text for p in self.paragraphs
                            if p.grade != "blocked").split())

    @property
    def needs_review(self) -> List[Block]:
        return [p for p in self.paragraphs if p.needs_review]

    def summary_line(self) -> str:
        return (f"{len(self.paragraphs)} paragraph(s), {len(self.rejected)} rejected, "
                f"{len(self.needs_review)} needing review, "
                f"{self.word_count} words in what would render")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "greeting": self.greeting, "sign_off": self.sign_off,
            "paragraphs": [p.as_dict() for p in self.paragraphs],
            "rejected": [{"text": b.text, "why": why} for b, why in self.rejected],
            "warnings": self.warnings,
        }


def found_cliches(text: str) -> List[str]:
    lowered = " ".join((text or "").lower().split())
    return [c for c in CLICHES if c in lowered]


def unverifiable_company_claims(text: str, jd_text: str) -> List[str]:
    """Sentences about the employer that the posting does not support.

    Deliberately blunt. A sentence that talks about them and reaches for admiration has
    to be grounded in something the posting actually said, and the test for that is
    whether its content words appear in the job description. Praise assembled out of
    nothing is the most common thing in a cover letter and the easiest to catch.
    """
    jd_tokens = set(kw.tokens(jd_text or ""))
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        if not sentence.strip():
            continue
        if not (_ABOUT_THEM.search(sentence) or _ADMIRATION.search(sentence)):
            continue
        content = [w for w in kw.significant(sentence) if len(w) > 3]
        if not content:
            continue
        grounded = sum(1 for w in content if w in jd_tokens)
        if grounded / len(content) < 0.5:
            out.append(sentence.strip())
    return out


_SYSTEM = f"""You write a short cover letter for one specific job.

{TRUTH_CONTRACT}

{NO_HEADCOUNT}

{HOUSE_STYLE}

WHAT MAKES THIS ONE WORTH READING. It is read in twenty seconds by somebody holding forty
of them. So:
- Open with the strongest piece of evidence, not with an introduction. Never begin with
  "I am writing to apply"
- Every paragraph earns its place by saying something the resume cannot: why this
  particular work matters for this particular job
- Be specific. A number, a system, a named outcome, all cited
- Do not restate the resume. Do not list skills
- Do not flatter the company. If you want to reference them, reference something the job
  description actually says. You know nothing else about them
- Say plainly what you would do in the role
- {MIN_WORDS} to {MAX_WORDS} words total, across at most {MAX_PARAGRAPHS} paragraphs

BANNED, these mark a letter as one of forty:
"I am writing to apply", "passionate about", "proven track record", "team player",
"wealth of experience", "hit the ground running", "the perfect candidate",
"at your earliest convenience".

CITATIONS. Every paragraph carries fact_ids for the facts it draws on. A paragraph that
draws on nothing gets an empty list and will be refused. Never cite an id you were not
given.

GRADE each paragraph:
  verified  states what the facts state
  inferred  reframes a fact into this job's language
  stretch   claims something adjacent, with evidence

Return JSON only, no prose, no code fence:
{{"greeting": "Dear Hiring Manager,",
  "paragraphs": [{{"text": "...", "fact_ids": [1,2], "grade": "verified"}}],
  "sign_off": "Kind regards,"}}"""


def write(extraction: Any, facts: Sequence[Any],
          resume_blocks: Optional[Sequence[Any]] = None,
          house_spec: str = "") -> CoverLetter:
    """Draft a cover letter for one job. Nothing here trusts the model."""
    known = {f.id: f for f in facts}
    if not known:
        raise RuntimeError("No ProfileFact rows. Run scripts/seed_profile.py first.")

    citable = [f for f in facts if getattr(f, "verified", True)]
    actual_years = experience_years(facts)

    must = "\n".join(f"- {r.text}" for r in extraction.must[:10]) or "- none stated"
    already = ""
    if resume_blocks:
        already = "\n".join(f"- {b.text}" for b in list(resume_blocks)[:8])

    user = (
        f"ROLE: {extraction.title or 'unspecified'}\n"
        f"COMPANY: {extraction.company or 'unspecified'}\n"
        f"SENIORITY: {extraction.seniority}\nEMPLOYER TYPE: {extraction.archetype}\n\n"
        f"WHAT THE JOB ASKS FOR:\n{must}\n\n"
        + (f"WHAT THE RESUME ALREADY SAYS. Do not repeat these, build on them:\n"
           f"{already}\n\n" if already else "")
        + (f"YEARS OF EXPERIENCE: {actual_years:.0f}. Computed from the role dates. If you\n"
           f"state a number of years, state this one.\n\n" if actual_years else "")
        + f"THE POSTING ITSELF, which is the only thing you know about this employer:\n"
          f"{(getattr(extraction, 'jd_text', '') or '')[:6000]}\n\n"
        + f"FACTS (cite these by the id in square brackets):\n{_facts_for(citable)}"
    )

    data = complete_json("cover", system=_SYSTEM + (house_spec or ""), user=user,
                         max_tokens=4000, temperature=0.4)
    if not isinstance(data, dict):
        raise RuntimeError(f"cover returned {type(data).__name__}, expected an object")

    letter = CoverLetter()
    greeting = str(data.get("greeting") or "").strip()
    if greeting:
        letter.greeting = greeting
    sign_off = str(data.get("sign_off") or "").strip()
    if sign_off:
        letter.sign_off = sign_off

    jd_text = getattr(extraction, "jd_text", "") or ""
    raw = data.get("paragraphs")
    raw = raw if isinstance(raw, list) else []

    for index, item in enumerate(raw[:MAX_PARAGRAPHS]):
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            continue
        ids = [int(i) for i in (item.get("fact_ids") or [])
               if isinstance(i, (int, str)) and str(i).isdigit()]
        block = Block(section="cover", text=text, fact_ids=ids,
                      grade=str(item.get("grade") or "inferred").strip().lower(),
                      order_index=index)

        # the resume's guards, verbatim: citations, numbers, headcount, tenure
        checked, reason = _validate(block, known, actual_years)
        if reason:
            letter.rejected.append((checked, reason))
            continue

        stale = found_cliches(text)
        if stale:
            checked.grade = "blocked"
            letter.rejected.append((checked, f"cliche: {stale[0]!r}"))
            log.warning("cover paragraph refused for a cliche %r", stale[0])
            continue

        invented = unverifiable_company_claims(text, jd_text)
        if invented:
            checked.grade = "blocked"
            letter.rejected.append(
                (checked, "says something about the employer the posting does not"))
            log.warning("INVENTED COMPANY CLAIM: %r", invented[0][:90])
            continue

        letter.paragraphs.append(checked)

    words = letter.draft_word_count
    if words > MAX_WORDS:
        letter.warnings.append(
            f"{words} words across the whole draft. Over {MAX_WORDS} and it stops "
            f"being read, so leave a paragraph unticked")
    elif words and words < MIN_WORDS:
        letter.warnings.append(
            f"{words} words is thin even with everything accepted. It says too little")
    if not letter.paragraphs:
        letter.warnings.append("Nothing survived the checks. Try again.")

    log.info("cover: %s", letter.summary_line())
    return letter


def _facts_for(facts: Sequence[Any]) -> str:
    lines = []
    for fact in facts:
        if getattr(fact, "kind", "") in ("contact", "name"):
            continue
        org = f" ({fact.org})" if getattr(fact, "org", None) else ""
        lines.append(f"[{fact.id}]{org} {fact.text}")
    return "\n".join(lines)
