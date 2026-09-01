"""Gap closer: the override, and the one shape it is allowed to take.

The truth gate exists to stop a *model* inventing experience. It was never meant to stop
*he* recording true things about his own career, and it had started to. The word
"risk" appears in none of his 63 facts, so every risk keyword reads as a gap, on a record
belonging to somebody who ran multi-banking treasury operations at an energy company for
three and a half years. The likeliest reading is that the record under-describes the work,
not that the work never happened.

So the override runs in the only direction that is honest: it does not let a keyword onto
the page unevidenced, it asks him a question and turns his answer into evidence. The model
writes the question. He writes the answer. Nothing is saved that he has not typed or
edited himself, and every fact created this way is stamped so it can be found again.

**Closeability is computed, not asked of a model.** A model asked "could he plausibly
claim this?" will say yes to almost anything, which is the failure this whole app is built
against. Adjacency to his existing record is arithmetic:

  likely      the keyword's words already appear across several of his facts
  maybe       one fact touches it
  unlikely    nothing in the record is near it

`unlikely` gets no question and no draft. "Product owner" against a record with no product
work is not a gap to close, it is a job that does not fit, and the useful thing to tell
him is that rather than a prompt inviting him to stretch.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config import config
from modules import keywords as kw
from modules.llm import LLMError, complete_json
from modules.prompts import HOUSE_STYLE, TRUTH_CONTRACT

log = logging.getLogger(__name__)

SEED_FILE = config.base_dir / "data" / "profile_facts.json"

# how many of his facts a keyword's words have to touch before it is worth asking about
LIKELY_TOUCHES = 2

_OPTIONS_SYSTEM = f"""You turn a gap in someone's CV into a short list of statements they
can tick, so that closing it costs a click rather than an evening of writing.

{TRUTH_CONTRACT}

{HOUSE_STYLE}

They are shown a term a job wants that their record does not currently contain, and the
parts of their record that sit closest to it. Write the two to four things a person with
that record might truthfully have done, phrased as finished sentences in their voice.

Rules that matter more than being helpful:
- Every option must be a MODEST extension of a fact you were shown, not a new career.
  Each one names the fact it extends
- NEVER invent a number, a percentage, a client, a system name or a scale. A statement
  they tick becomes a permanent fact in their record, and an invented figure discovered
  in an interview discredits the whole document
- Options must differ from each other in substance, not in wording. Three ways of saying
  the same sentence is one option
- If nothing in the record is genuinely near the term, return an empty list. Offering
  someone a plausible sentence about work they have not done is the one thing this must
  never do
- Written as a statement of fact, first person implied, no hedging. "Ran X" not "Have
  experience in X"

Return JSON only, no prose, no code fence:
{{"options": [{{"text": "one specific thing they did", "fact_ids": [12],
               "why": "six words on what this evidences"}}]}}
"""

_SYSTEM = f"""You help someone describe work they have already done, in language a job
description would recognise. You are writing a QUESTION, never a claim.

{TRUTH_CONTRACT}

{HOUSE_STYLE}

The person is being asked about a term a job wants that their CV does not currently
contain. You are shown the parts of their record that sit closest to it.

Rules that matter more than being helpful:
- Ask about what they DID. Never suggest what they might say
- If the nearby facts do not really touch the term, say so in `honest_read` and set
  `worth_asking` to false. A question that invites someone to stretch is worse than no
  question
- The draft is a SHAPE, not a claim: a sentence with the specifics left blank for them to
  fill in. Never invent a number, a system name, a client or a scale
- One question. Specific enough to answer in a sentence, not "tell me about risk"

Return JSON only, no prose, no code fence:
{{"worth_asking": true|false,
  "question": "one specific question about what they actually did",
  "why": "one line on why this job cares about it",
  "draft": "a sentence shape with ... where their specifics go",
  "honest_read": "one line: what the record does and does not show"}}"""


@dataclass
class Suggestion:
    keyword: str
    closeability: str                       # likely | maybe | unlikely
    nearby: List[Dict[str, Any]] = field(default_factory=list)
    question: str = ""
    why: str = ""
    draft: str = ""
    honest_read: str = ""
    worth_asking: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "keyword": self.keyword, "closeability": self.closeability,
            "nearby": self.nearby, "question": self.question, "why": self.why,
            "draft": self.draft, "honest_read": self.honest_read,
            "worth_asking": self.worth_asking,
        }


def adjacency(keyword: str, facts: Sequence[Any]) -> List[Any]:
    """Facts whose words overlap the keyword's. Deterministic, no model, no opinion."""
    wanted = set(kw.significant(keyword))
    if not wanted:
        return []
    scored = []
    for fact in facts:
        if not getattr(fact, "verified", True):
            continue
        overlap = wanted & set(kw.tokens(_text_of(fact)))
        if overlap:
            scored.append((len(overlap), len(getattr(fact, "text", "") or ""), fact))
    # most overlap first, then the longer fact, which carries more for him to react to
    scored.sort(key=lambda row: (-row[0], -row[1]))
    return [row[2] for row in scored]


def _text_of(fact: Any) -> str:
    tags = getattr(fact, "tags", None) or []
    return " ".join([
        getattr(fact, "text", "") or "",
        getattr(fact, "org", "") or "",
        " ".join(str(t) for t in tags) if isinstance(tags, (list, tuple)) else "",
    ])


def closeability(keyword: str, facts: Sequence[Any]) -> str:
    near = adjacency(keyword, facts)
    if len(near) >= LIKELY_TOUCHES:
        return "likely"
    return "maybe" if near else "unlikely"


@dataclass
class Option:
    """One tickable statement. Ticking it makes it a permanent, verified fact."""
    text: str
    fact_ids: List[int] = field(default_factory=list)
    why: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "fact_ids": self.fact_ids, "why": self.why}


_DIGITS = re.compile(r"\d[\d,.]*%?")


def _figures_are_his(text: str, basis: Sequence[Any]) -> bool:
    """No number may appear in an option that is not already in the fact it extends.

    The free-text box never needed this: he typed his own numbers. A tickable statement
    is different. It is one click from becoming a verified fact, and a plausible figure
    is exactly what a model produces when asked to write a sentence about work it can
    only see the outline of.
    """
    source = " ".join((getattr(f, "text", "") or "") + " " +
                      " ".join(str(v) for v in (getattr(f, "metrics", None) or {}).values())
                      for f in basis)
    known = set(_DIGITS.findall(source))
    return all(figure in known for figure in _DIGITS.findall(text or ""))


def options(keyword: str, facts: Sequence[Any], role: str = "",
            limit: int = 4) -> List[Option]:
    """Statements he can tick to close one gap. Empty when nothing is genuinely near."""
    near = adjacency(keyword, facts)[:5]
    if not near or closeability(keyword, facts) == "unlikely":
        return []

    by_id = {f.id: f for f in facts}
    listed = "\n".join(f"[{f.id}] {(f.text or '')[:220]}" for f in near)
    user = (f"TERM THE JOB WANTS: {keyword}\n"
            f"ROLE BEING APPLIED FOR: {role or 'unspecified'}\n\n"
            f"CLOSEST THINGS ALREADY IN THEIR RECORD:\n{listed}\n\n"
            f"Write the options.")
    try:
        data = complete_json("gaps", system=_OPTIONS_SYSTEM, user=user,
                             max_tokens=1200, temperature=0.2)
    except Exception as exc:  # noqa: BLE001 - a gap without options is not a failure
        log.warning("gaps: could not draft options for %r: %s", keyword, exc)
        return []

    raw = (data or {}).get("options") if isinstance(data, dict) else None
    out: List[Option] = []
    for item in (raw or [])[: limit * 2]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not (15 <= len(text) <= 260):
            continue
        ids = [i for i in (item.get("fact_ids") or []) if i in by_id]
        basis = [by_id[i] for i in ids] or near
        if not _figures_are_his(text, basis):
            log.warning("gaps: dropped an option for %r, it invented a figure: %r",
                        keyword, text[:70])
            continue
        if any(o.text.lower() == text.lower() for o in out):
            continue
        out.append(Option(text=text, fact_ids=ids,
                          why=" ".join(str(item.get("why") or "").split())[:90]))
        if len(out) >= limit:
            break

    log.info("gaps: %d option(s) for %r", len(out), keyword)
    return out


def suggest_one(keyword: str, facts: Sequence[Any], role: str = "") -> Suggestion:
    """One gap, with a question if the record is anywhere near it."""
    near = adjacency(keyword, facts)[:5]
    grade = closeability(keyword, facts)
    nearby = [{"id": f.id, "text": (f.text or "")[:220], "org": getattr(f, "org", None)}
              for f in near]

    if grade == "unlikely":
        # The old wording read "that is not a gap to close, it is a genuine part of the
        # job you have not done", which is a claim about him that this function is in no
        # position to make. It knows one thing: no fact on file is near the term. A
        # record that never uses the word "insights" is not a person who has never
        # produced one, and saying otherwise from a token search is both wrong and
        # insulting. State the finding, and leave the conclusion to him.
        return Suggestion(
            keyword=keyword, closeability=grade, nearby=[],
            worth_asking=False,
            honest_read=("Nothing on file uses these words or anything close to them. "
                         "Either it is genuinely outside what you have done, or you have "
                         "done it and the record has never said so. Only you can tell "
                         "which."),
        )

    listed = "\n".join(f"[{n['id']}] {n['text']}" for n in nearby) or "nothing close"
    user = (
        f"TERM THE JOB WANTS: {keyword}\n"
        f"ROLE BEING APPLIED FOR: {role or 'unspecified'}\n\n"
        f"CLOSEST THINGS ALREADY IN THEIR RECORD:\n{listed}\n\n"
        f"Ask them one question about what they actually did."
    )
    try:
        data = complete_json("gaps", system=_SYSTEM, user=user,
                             max_tokens=900, temperature=0.2)
    except (LLMError, RuntimeError) as exc:
        log.warning("gap suggestion failed for %r: %s", keyword, exc)
        return Suggestion(keyword=keyword, closeability=grade, nearby=nearby,
                          worth_asking=False,
                          honest_read="Could not reach the model to draft a question.")

    if not isinstance(data, dict):
        return Suggestion(keyword=keyword, closeability=grade, nearby=nearby,
                          worth_asking=False, honest_read="No usable answer from the model.")

    return Suggestion(
        keyword=keyword,
        closeability=grade,
        nearby=nearby,
        worth_asking=bool(data.get("worth_asking")),
        question=str(data.get("question") or "").strip(),
        why=str(data.get("why") or "").strip(),
        draft=str(data.get("draft") or "").strip(),
        honest_read=str(data.get("honest_read") or "").strip(),
    )


def suggest(gaps: Sequence[str], facts: Sequence[Any], role: str = "",
            limit: int = 6) -> List[Suggestion]:
    """Rank gaps by how closeable they are, then ask about the closeable ones.

    Ranked before it is capped, so the cap drops the ones he could do least about rather
    than whichever the job happened to list last.
    """
    order = {"likely": 0, "maybe": 1, "unlikely": 2}
    ranked = sorted(dict.fromkeys(gaps), key=lambda g: order[closeability(g, facts)])

    out: List[Suggestion] = []
    for keyword in ranked[:limit]:
        out.append(suggest_one(keyword, facts, role))
    dropped = len(ranked) - len(out)
    if dropped > 0:
        log.info("gap closer: %d further gap(s) not asked about this round", dropped)
    return out


# --------------------------------------------------------------- writing it back

def add_fact(text: str, *, parent_org: Optional[str] = None, tags: Optional[List[str]] = None,
             kind: str = "bullet", source: str = "gap closer",
             seed_file: Optional[Path] = None) -> Dict[str, Any]:
    """Append a fact he has attested to, to the JSON, which is the source of truth.

    Written to the file rather than only to SQLite because `seed_profile.py` wipes and
    reloads ProfileFact. A fact that existed only in the database would survive until the
    next re-seed and then vanish, which is the same trap contact details fell into.

    Marked `source` so anything added this way can be found later, and `verified` true
    because he typed it. That flag records who vouched for a fact, and he is allowed to
    vouch for his own career.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("a fact cannot be empty")
    # The floor is there to stop a one-word answer to "what have you done", which is a
    # claim with nothing in it. A skill is not that kind of statement: "SQL" and "Rust"
    # are complete, and holding them to twelve characters refused the shortest true
    # things on the page.
    floor = 2 if kind in ("skill", "cert") else 12
    if len(text) < floor:
        raise ValueError("that is too short to be a fact. Say what you did"
                         if floor > 2 else "a fact needs at least two characters")

    path = seed_file or SEED_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload["facts"]

    entry = {
        "kind": kind,
        "text": text,
        "tags": tags or [],
        "source": source,
        "verified": True,
    }

    if parent_org:
        for candidate in entries:
            if candidate.get("kind") == "role" and candidate.get("org") == parent_org:
                candidate.setdefault("children", []).append(entry)
                break
        else:
            raise ValueError(f"no role on file for {parent_org!r}")
    else:
        entries.append(entry)

    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    log.info("fact added to %s under %s: %r", path.name, parent_org or "top level", text[:70])
    return entry


def roles(facts: Sequence[Any]) -> List[str]:
    """Employers a new fact can be attached to, in the order they appear on the resume."""
    seen: List[str] = []
    for fact in sorted(facts, key=lambda f: getattr(f, "order_index", 0)):
        if getattr(fact, "kind", None) == "role" and fact.org and fact.org not in seen:
            seen.append(fact.org)
    return seen


def save_answer(db: Any, text: str, *, org: Optional[str] = None,
                tags: Optional[List[str]] = None, keyword: Optional[str] = None) -> Any:
    """Write an attested fact to the JSON and into the live database.

    Both, in that order. The JSON is the source of truth and survives a re-seed; the row
    is what makes the fact usable in the next tailoring run without restarting anything.
    If the file write fails, nothing is inserted, so the two cannot drift apart.
    """
    from database.models import ProfileFact       # imported here to keep gaps importable

    tags = list(tags or [])
    if keyword and keyword.lower() not in [t.lower() for t in tags]:
        tags.append(keyword.lower())

    add_fact(text, parent_org=org, tags=tags, kind="bullet", source="gap closer")

    parent = None
    if org:
        parent = db.query(ProfileFact).filter(
            ProfileFact.kind == "role", ProfileFact.org == org).first()

    highest = db.query(ProfileFact).order_by(ProfileFact.order_index.desc()).first()
    row = ProfileFact(
        kind="bullet",
        parent_id=parent.id if parent else None,
        org=org,
        text=text.strip(),
        tags=tags,
        metrics={},
        source="gap closer",
        verified=True,
        order_index=(highest.order_index + 1) if highest else 0,
    )
    db.add(row)
    db.commit()
    log.info("attested fact saved: #%d under %s", row.id, org or "no role")
    return row
