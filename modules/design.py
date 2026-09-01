"""House style for the generated documents, supplied by him rather than hard-coded.

Resume convention moves. Inside a year, single-page advice became entry-level advice,
keyword repetition went from free to penalised, and an LLM screener joined the parser and
the human as a reader with different tastes from either. A prompt with those rules baked
in means editing Python to change a house rule, and Python is not where a style guide
belongs.

So he uploads a spec and it becomes part of the instruction the writer gets. Two things
happen to it:

  the whole text     goes into the prompt for the stage it governs, because a rule with
                     its reasoning attached is followed better than the rule alone, and
                     the specs he writes carry their reasoning
  banned wording     is lifted out and enforced in code afterwards, because a prompt is
                     advisory and every list of forbidden words in this app has eventually
                     been ignored by some model on some run

The truth gates are untouched by any of this. A spec can say how to write; it cannot
authorise a claim, and nothing here can reach past `tailor._validate`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("jobapp.design")

KINDS = ("resume", "cover")
MAX_CHARS = 40_000

# Pulled out of the spec's own prose. These are the two lists it calls "banned", and they
# are worth enforcing rather than requesting: an adjective like "cutting-edge" is exactly
# what a model reaches for when it has nothing specific to say.
#
# Narrowly. The first version read every "Forbidden:" block and came back with "tables of
# any kind", "single column", "experience" and "skills": structural rules and, in two
# cases, sections the document is required to have. Banning the word "Experience" would
# have blocked every resume it ever wrote. Only lists that are explicitly about wording
# are read.
# "Banned" is about wording in both his specs; "Forbidden" is about structure. Matching
# the first and not the second is what keeps section headings out of the ban list.
# The label must open a line. Matching "banned" anywhere pulled in prose like "not on the
# banned list in section 5" and turned "per section 6" into a forbidden phrase.
_LABEL = (r"^\s*[-*•]?\s*\*{0,2}"
          r"(?:banned[a-z ]{0,20}|filler (?:adjectives?|verbs?))\*{0,2}\s*:\*{0,2}")
_BANNED_BLOCK = re.compile(_LABEL + r"[^\n]*\n((?:\s*[-*\u2022][^\n]*\n?)+)", re.I | re.M)
_INLINE_LIST = re.compile(_LABEL + r"[ \t]*([^\n]+)", re.I | re.M)

# A quoted string in a spec is a literal phrase to refuse. Unquoted items are single
# words or short constructions, and anything longer than that is prose describing a rule.
_QUOTED = re.compile(r"[\"\u201c]([^\"\u201d]{3,80})[\"\u201d]")

# A word the document cannot do without, whatever a spec seems to say about it.
# Cross-references and fragments of prose, which a list of phrases will always pick up
# some of and which are never actually forbidden wording.
_JUNK = re.compile(
    r"\bsection\b|\bspec\b|^\W*$|^\d|\bapplies\b|\babsent\b"
    r"|^(?:its|the|and|any|a|an|of|to|in|or)$", re.I)

_NEVER_BAN = {
    "summary", "experience", "education", "skills", "certifications", "projects",
    "contact", "name", "dates", "single column", "top to bottom", "whole-document",
    "skills and experience", "percentages", "images", "icons", "logos",
}


def _terms(fragment: str) -> List[str]:
    fragment = fragment or ""

    # Quoted first. His cover spec bans whole closings, "Thank you for your time and
    # consideration", which no word-count rule would ever have let through.
    quoted = [q.strip().lower() for q in _QUOTED.findall(fragment)]
    if quoted:
        return [q for q in quoted
                if 3 <= len(q) <= 80 and q not in _NEVER_BAN and not _JUNK.search(q)]

    out = []
    for piece in re.split(r"[,\n\u2022\u00b7]|\s{2,}", fragment):
        piece = piece.strip().strip(' -*."\u201c\u201d\'`').strip()
        # a list of banned words is words, not sentences
        piece = piece.lower()
        if not (2 <= len(piece) <= 40) or piece.endswith(":") or len(piece.split()) > 4:
            continue
        if piece in _NEVER_BAN or _JUNK.search(piece):
            continue
        out.append(piece)
    return out


def banned_words(text: str) -> List[str]:
    """Every word or phrase the spec calls banned, deduplicated, order preserved."""
    found: List[str] = []
    for match in _BANNED_BLOCK.finditer(text or ""):
        found.extend(_terms(match.group(1)))
    for match in _INLINE_LIST.finditer(text or ""):
        found.extend(_terms(match.group(1)))

    seen, out = set(), []
    for term in found:
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out


def read_rules(text: str) -> Dict[str, Any]:
    """The parts of a spec that code can enforce, rather than ask for."""
    return {
        "banned": banned_words(text),
        "chars": len(text or ""),
    }


def violations(draft: str, rules: Optional[Dict[str, Any]]) -> List[str]:
    """Banned wording that survived into the draft. Advisory in the prompt, checked here."""
    if not rules:
        return []
    lowered = (draft or "").lower()
    hits = []
    for term in rules.get("banned") or []:
        # A single banned word is banned in every form it takes. Matching "leverage"
        # exactly let "leveraged" through, which is the same word doing the same damage.
        # Phrases are matched as written, because inflecting a phrase is guesswork.
        pattern = (re.escape(term) + r"\w*" if " " not in term else re.escape(term))
        if re.search(r"\b" + pattern, lowered):
            hits.append(term)
    return hits


def active(db: Any, kind: str) -> Optional[Any]:
    from database.models import DesignSpec
    if kind not in KINDS:
        raise ValueError(f"unknown spec kind {kind!r}. Expected one of: {', '.join(KINDS)}")
    return (db.query(DesignSpec)
            .filter(DesignSpec.kind == kind, DesignSpec.active.is_(True))
            .order_by(DesignSpec.created_at.desc()).first())


def save(db: Any, kind: str, name: str, text: str) -> Any:
    """Store a spec and make it the one in force. The previous one is kept, not deleted."""
    from database.models import DesignSpec

    if kind not in KINDS:
        raise ValueError(f"unknown spec kind {kind!r}")
    text = (text or "").strip()
    if len(text) < 200:
        raise ValueError("that is too short to be a spec. Two hundred characters minimum")
    if len(text) > MAX_CHARS:
        raise ValueError(f"specs are capped at {MAX_CHARS:,} characters")

    for old in db.query(DesignSpec).filter(DesignSpec.kind == kind,
                                           DesignSpec.active.is_(True)).all():
        old.active = False

    row = DesignSpec(kind=kind, name=name or f"{kind} spec", text=text,
                     rules=read_rules(text), active=True)
    db.add(row)
    db.commit()
    log.info("design: %s spec %r active, %d banned term(s)",
             kind, row.name, len(row.rules.get("banned") or []))
    return row


def instruction(db: Any, kind: str) -> str:
    """The block that goes into the prompt, or nothing if he has not supplied one."""
    spec = active(db, kind)
    if not spec:
        return ""
    return (
        "\n\n=== HOUSE SPEC. These are the rules this document is judged against. ===\n"
        "Follow them. Where they conflict with your own habits, the spec wins. Where they\n"
        "conflict with the truth rules above, the truth rules win and you say nothing\n"
        "rather than inventing something that satisfies the spec.\n\n"
        f"{spec.text.strip()}\n"
        "=== end house spec ===\n"
    )
