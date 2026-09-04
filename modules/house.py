"""His standing rules about himself, enforced in code rather than asked for in a prompt.

Separate from `design.py` on purpose. That module holds the specs he uploads, which say
how a resume should be written in general and change as convention changes. This one
holds what is true and not true about him, which does not change between jobs and must
not be re-decided by a model on every run.

Three rules govern what goes in here.

  It is about him, not about resumes.   "Vary bullet length" is a writing rule and lives
                                        in a spec. "He does not hold an MBA" is a fact
                                        about a person and lives here.
  It is checkable.                      A rule that cannot be tested is a prompt line.
                                        Everything below is matched against real output.
  It states the outcome of an argument  he has already settled, so that nothing here gets
                                        re-litigated on a later run by a model that finds
                                        the reasoning persuasive.

Nothing here can authorise a claim. It only ever removes or downgrades one, so a bug in
this file makes the writing more cautious and never less true.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Sequence, Tuple

log = logging.getLogger(__name__)

# The line that goes on every document that names a client engagement. His employer's
# client names are confidential, and a reader who sees a descriptor with no explanation
# reads it as vagueness rather than as discretion.
WITHHELD_LINE = "Client names withheld. Figures verified against source data."


# --------------------------------------------------------------------------- degrees

# Background checks verify titles, so a degree is the one claim where an approximation
# is treated as a lie. He holds two masters and neither of them is an MBA, which is the
# specific substitution a model reaches for because it is the more famous qualification.
HELD_DEGREES = (
    "Master of Business Analytics",
    "Master of International Business",
)

_NOT_HELD = re.compile(
    r"\b(?:"
    r"m\.?b\.?a\.?|master of business administration"
    r"|ph\.?d\.?|doctorate|doctoral"
    r"|c\.?f\.?a\.?|c\.?p\.?a\.?|chartered accountant"
    r"|six sigma black belt|pmp certified|certified pmp"
    r")\b",
    re.I,
)


def claims_a_degree(text: str) -> List[str]:
    """Qualifications asserted in the writing that he does not hold."""
    return sorted({m.group(0).strip() for m in _NOT_HELD.finditer(text or "")})


# --------------------------------------------------------------------------- domains

# Domains he has not worked in. The ban is on claiming experience in them, not on the
# words themselves, and the distinction matters more than it looks.
#
# A spend analyst prices freight, so "reduced logistics spend by a fifth" is true work
# and must survive. "Six years in logistics" is a different sentence about a different
# person. So each domain is matched only inside a frame that asserts experience, and the
# bare noun passes untouched. Same reasoning as `_NOT_ALONE` in keywords.py, arrived at
# the same way: the first version banned the noun and would have deleted real bullets.
UNWORKED_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "HR and workforce": (
        "hr", "human resources", "people analytics", "workforce analytics",
        "workforce planning", "headcount planning", "attrition", "talent analytics",
        "employee engagement", "recruitment analytics", "hr data", "hris",
    ),
    "field operations": (
        "field operations", "field ops", "on-ground operations", "last mile",
        "warehouse operations", "depot operations",
    ),
    "marketplace and mobility": (
        "marketplace", "two-sided marketplace", "mobility", "ride hailing",
        "quick commerce", "q-commerce", "gig economy",
    ),
    "logistics and FMCG": (
        "logistics", "supply chain operations", "fmcg", "consumer packaged goods", "cpg",
        "route planning", "fleet operations",
    ),
}

# The frames that turn a noun into a claim of experience. Anything outside these is the
# word doing ordinary work in a sentence about something he did do.
_CLAIM_FRAMES = (
    r"(?:\d+\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience\s+)?(?:in|across|within)\s+)",
    r"(?:experien(?:ce|ced)\s+(?:in|with|across)\s+)",
    r"(?:background\s+in\s+)",
    r"(?:expertise\s+in\s+)",
    r"(?:specialis(?:t|ed|ing)\s+in\s+)",
    r"(?:worked\s+(?:in|across|with)\s+)",
    r"(?:domain\s+knowledge\s+(?:in|of)\s+)",
    r"(?:deep\s+(?:knowledge|understanding)\s+of\s+)",
    r"(?:\bin\s+the\s+)",
)

# The alternation has to be bracketed. Without the outer group the | binds looser than
# the concatenation, so the trailing capture attaches to the last frame only and the
# other eight silently match nothing. Every positive case in the test file failed and
# every negative one passed, which is the shape a permissive bug always takes.
_FRAME = re.compile(
    r"(?:" + "|".join(_CLAIM_FRAMES) + r")([a-z][a-z /&-]{2,40})", re.I
)


def claims_a_domain(text: str) -> List[Tuple[str, str]]:
    """Domains the writing asserts experience in that he has never worked in.

    Returns (domain, the phrase that asserted it). Empty when the word appears without
    a claim attached to it, which is the common and legitimate case.
    """
    hits: List[Tuple[str, str]] = []
    for match in _FRAME.finditer(text or ""):
        tail = (match.group(match.lastindex or 1) or "").lower()
        for domain, terms in UNWORKED_DOMAINS.items():
            for term in terms:
                if re.search(r"\b" + re.escape(term) + r"\b", tail):
                    hits.append((domain, match.group(0).strip()))
                    break
            else:
                continue
            break
    return hits


def skills_claim_a_domain(skills: Sequence[str]) -> List[Tuple[str, str]]:
    """A skills line needs no frame. Listing a domain there is the claim."""
    hits = []
    for skill in skills or []:
        low = (skill or "").strip().lower()
        for domain, terms in UNWORKED_DOMAINS.items():
            if any(low == term or low.startswith(term + " ") or low.endswith(" " + term)
                   for term in terms):
                hits.append((domain, skill))
                break
    return hits


def off_target_domain(job_text: str, keywords: Sequence[str] = ()) -> List[str]:
    """Which of his no-go domains a posting is in.

    Not a block. He may still want to read it, and the app does not get to decide what he
    applies for. It is a flag, so that a red score comes with the reason attached rather
    than looking like the writer having a bad day.
    """
    hay = " ".join([job_text or ""] + [k or "" for k in keywords]).lower()
    found = []
    for domain, terms in UNWORKED_DOMAINS.items():
        weight = sum(1 for t in terms if re.search(r"\b" + re.escape(t) + r"\b", hay))
        # One mention is a passing reference. Two is what the job is about.
        if weight >= 2:
            found.append(domain)
    return found


# ------------------------------------------------------------------- writing tells

# Filler that survives every prompt telling a model not to use it, which is the reason
# it is checked here instead. Each of these is what a sentence reaches for when it has
# nothing specific to say.
FILLER_ADJECTIVES = (
    "comprehensive", "robust", "cutting-edge", "seamless", "innovative", "holistic",
    "best-in-class", "world-class", "state-of-the-art", "bespoke", "strategic-minded",
    "results-driven", "highly motivated", "seasoned", "dynamic", "proven track record",
)
FILLER_VERBS = (
    "leverage", "leveraged", "leveraging", "spearhead", "spearheaded", "orchestrate",
    "orchestrated", "utilise", "utilised", "utilize", "utilized", "facilitate",
    "facilitated", "empower", "empowered", "synergise", "synergize",
)
_FILLER = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in FILLER_ADJECTIVES + FILLER_VERBS) + r")\b",
    re.I,
)


def filler(text: str) -> List[str]:
    return sorted({m.group(0).lower() for m in _FILLER.finditer(text or "")})


# A triad is three items in a list, and three of them in a row is the single clearest
# tell that a document was generated. Real careers do not produce that rhythm.
_TRIAD = re.compile(r"\b[\w-]+(?:\s+[\w-]+){0,2},\s+[\w-]+(?:\s+[\w-]+){0,2},?\s+and\s+[\w-]+")


def triads(bullets: Sequence[str]) -> int:
    return sum(1 for b in bullets if _TRIAD.search(b or ""))


_OPENING = re.compile(r"^\s*([A-Za-z]+)")


def natural_language(bullets: Sequence[str]) -> List[str]:
    """The tells that a set of bullets was written by a machine rather than a person.

    Advisory. These are style faults, not untruths, so they are surfaced for him to read
    and never used to block a document. A CV that is a little stiff still gets sent; a CV
    that claims a degree does not.
    """
    live = [b for b in bullets if (b or "").strip()]
    if len(live) < 3:
        return []

    notes = []

    lengths = [len(b.split()) for b in live]
    spread = max(lengths) - min(lengths)
    if spread <= 4:
        notes.append(
            f"every bullet is {min(lengths)} to {max(lengths)} words. Uniform length is a "
            f"tell; no real career produces it"
        )

    openings = [(_OPENING.match(b).group(1) or "").lower() for b in live if _OPENING.match(b)]
    repeated = {w for w in openings if openings.count(w) > 2}
    if repeated:
        notes.append("more than two bullets open with " + ", ".join(sorted(repeated)))

    count = triads(live)
    if count >= 2:
        notes.append(f"{count} bullets are built as a list of three")

    seen = filler(" ".join(live))
    if seen:
        notes.append("filler wording: " + ", ".join(seen))

    return notes
