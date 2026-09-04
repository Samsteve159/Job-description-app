"""The writer, run as a loop instead of a single shot.

What changed and why. The old flow made one tailoring call and shipped whatever survived
the gates. When a bullet was refused, for an invented number or a citation that did not
exist, it was simply dropped, and the requirement it was meant to answer went quietly
unanswered. The document that reached the page was always honest and was sometimes
missing the thing the posting cared about most, with nothing on screen to say so.

This runs the same call, then reads two signals the app already produces:

  keywords.unanswered   requirements from the posting with no bullet behind them. A term
                        on the skills line does not count, because a screener checks the
                        experience section against the requirement
  TailorResult.rejected what the gates refused, and the exact reason

and sends only those back for repair, up to a few rounds. Everything that passed is left
untouched, so a round costs seconds rather than re-running the document and trading one
regression for one improvement.

Two properties this must keep.

It cannot loosen a gate. `tailor.revise` runs every replacement through the same
`_validate` as the first pass. A round can produce a better sentence; it can never
produce permission.

It must be able to give up honestly. A requirement his record does not support has no
correct bullet, and the loop is written to stop asking rather than to keep pushing until
something gets through. Rounds that return nothing are the expected outcome on a genuine
gap, not a failure of the loop.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from modules import families, keywords
from modules.tailor import TailorResult, revise, tailor

log = logging.getLogger(__name__)

# Three was his call. It is also where the evidence points: a fault the model can fix it
# fixes on the first pass, and a requirement still unanswered after three attempts is
# almost always a real gap rather than a wording problem. Rounds past that spend time to
# confirm what round three already showed.
MAX_ROUNDS = 3


@dataclass
class Round:
    """What one pass actually changed, kept so the screen can show its working."""
    number: int
    unanswered_before: List[str] = field(default_factory=list)
    rejected_before: int = 0
    added: int = 0
    refused: int = 0
    seconds: float = 0.0


@dataclass
class AgentResult:
    result: Optional[TailorResult] = None
    family: str = ""
    rounds: List[Round] = field(default_factory=list)
    # Requirements still unanswered when the loop stopped. Reported, never hidden: this
    # is the honest output for something his record does not cover.
    still_unanswered: List[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(r.seconds for r in self.rounds)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family,
            "rounds": [
                {"number": r.number, "unanswered_before": r.unanswered_before,
                 "rejected_before": r.rejected_before, "added": r.added,
                 "refused": r.refused, "seconds": round(r.seconds, 1)}
                for r in self.rounds
            ],
            "still_unanswered": self.still_unanswered,
            "seconds": round(self.seconds, 1),
        }


def _gaps(extraction: Any, result: TailorResult) -> List[str]:
    return keywords.unanswered(extraction.must_keywords(), result.blocks)


def write(extraction: Any, facts: Sequence[Any], house_spec: str = "",
          max_rounds: int = MAX_ROUNDS) -> AgentResult:
    """Tailor, then repair what the posting asked for and the document does not answer."""
    out = AgentResult()

    # Which field this is, decided from the posting rather than guessed by a model, and
    # folded into the writing instruction for every call including the repairs.
    out.family = families.detect(
        title=getattr(extraction, "title", "") or "",
        keywords=list(extraction.must_keywords()) + list(getattr(extraction, "keywords", [])),
        jd_text=getattr(extraction, "jd_text", "") or "",
    )
    guidance = families.guidance(out.family)
    spec = (house_spec or "")
    if guidance:
        spec = spec + "\n\n" + guidance

    started = time.time()
    result = tailor(extraction, facts, house_spec=spec)
    first = Round(number=1, unanswered_before=_gaps(extraction, result),
                  rejected_before=len(result.rejected), added=len(result.blocks),
                  refused=len(result.rejected), seconds=time.time() - started)
    out.rounds.append(first)
    log.info("agent round 1: %d blocks, %d refused, %d requirement(s) unanswered",
             len(result.blocks), len(result.rejected), len(first.unanswered_before))

    for number in range(2, max_rounds + 1):
        unanswered = _gaps(extraction, result)
        rejected = list(result.rejected)
        if not unanswered and not rejected:
            log.info("agent: nothing left to fix after round %d", number - 1)
            break

        started = time.time()
        try:
            added, refused = revise(
                extraction, facts, unanswered=unanswered, rejected=rejected,
                house_spec=house_spec or "", family_guidance=guidance,
            )
        except Exception as exc:  # noqa: BLE001 - a failed repair must not lose the draft
            # The document from the previous round is real and sendable. Losing it
            # because an optional improvement failed would be the worse outcome, so this
            # stops the loop and keeps what it has.
            log.warning("agent round %d failed, keeping the previous draft: %s",
                        number, exc)
            break

        this = Round(number=number, unanswered_before=unanswered,
                     rejected_before=len(rejected), added=len(added),
                     refused=len(refused), seconds=time.time() - started)
        out.rounds.append(this)
        log.info("agent round %d: %d requirement(s) unanswered, %d added, %d refused",
                 number, len(unanswered), len(added), len(refused))

        if not added:
            # Nothing survived the gates. Asking again will not change that, because the
            # constraint is the record and not the wording.
            log.info("agent: round %d added nothing, the remaining gaps are real", number)
            break

        # The repairs join the document, and the ones the gates refused join the rejected
        # list so the screen can show them beside the reason, exactly as first-pass
        # refusals are shown.
        highest = max((b.order_index for b in result.blocks), default=0)
        for offset, block in enumerate(added, start=1):
            block.order_index = highest + offset
        result.blocks.extend(added)
        result.rejected = refused

    out.result = result
    out.still_unanswered = _gaps(extraction, result)
    log.info("agent: %d round(s) in %.1fs, %d requirement(s) still unanswered",
             len(out.rounds), out.seconds, len(out.still_unanswered))
    return out
