"""How likely this application is to be shortlisted, and why.

A single number is worse than no number if it cannot be argued with. So this returns
components, each with its own points, its own ceiling and a sentence saying what it read.
The score is the sum. Nothing here is a model's opinion: every part is counted off the
posting, the career record, and the rendered document.

**Evidence coverage dominates on purpose.** What actually decides a shortlist is whether
the person has done the thing, and everything else is rounding: a beautifully parsed
document describing the wrong career does not get called. So it carries 40 of the 100
points, and it counts keywords a fact can *support*, not keywords the model managed to
type. Those are different numbers, and the gap between them is the one this app exists
to close.

The honesty penalty is the part worth defending. If the writing claims skills nothing in
the record backs, the fit is being manufactured rather than described, and the score goes
DOWN rather than up. Every other resume tool in existence would count those as coverage.

This is a heuristic, not a prediction. It is calibrated to be useful for choosing between
jobs, not to be a probability. `band()` is deliberately coarse for the same reason: the
difference between 61 and 64 is noise, the difference between red and green is not.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from modules import keywords as kw

log = logging.getLogger(__name__)

RED, AMBER, GREEN = "red", "amber", "green"
AMBER_FLOOR, GREEN_FLOOR = 40, 70

# Ordered, so "manager" against "analyst" is a reach and the reverse is a step down.
LADDER = ("analyst", "senior analyst", "manager", "senior manager", "director")

_YEARS_WANTED = re.compile(r"(\d{1,2})\s*\+?\s*years?", re.I)

_REMOTE = re.compile(r"\b(remote|work from home|anywhere in india)\b", re.I)


@dataclass
class Component:
    name: str
    points: float
    ceiling: float
    detail: str
    good: bool = True

    @property
    def percent(self) -> int:
        return int(round(100 * self.points / self.ceiling)) if self.ceiling else 0


# The call, in the two words you want before reading anything else. A score is a
# comparison and a verdict is a decision, and on an evening with nine tabs open the
# decision is the useful half. Deliberately only three: a scale with more rungs than
# this invites deliberation, which is the thing it exists to save you.
VERDICTS = {
    GREEN: ("Apply", "Your record covers what they screen on."),
    AMBER: ("Your call", "Applicable, but you would be arguing for parts of it."),
    RED: ("Skip it", "The gaps are in what the job is about, not the wording."),
}


@dataclass
class Fit:
    score: int = 0
    components: List[Component] = field(default_factory=list)
    headline: str = ""
    advice: List[str] = field(default_factory=list)
    # what is missing that the posting weights most, so the verdict can name it
    weakest: str = ""

    @property
    def band(self) -> str:
        if self.score >= GREEN_FLOOR:
            return GREEN
        return AMBER if self.score >= AMBER_FLOOR else RED

    @property
    def call(self) -> str:
        return VERDICTS[self.band][0]

    @property
    def because(self) -> str:
        """One line, and it names the specific thing wherever there is one to name."""
        if self.weakest:
            if self.band == GREEN:
                return (f"Strong overall, though nothing shows {self.weakest}. "
                        f"Apply if you can speak to it.")
            if self.band == AMBER:
                return (f"Hinges on {self.weakest}, which your record does not show. "
                        f"Worth it only if you can argue it.")
            return f"Nothing shows {self.weakest}, and the posting leans on it hardest."
        return VERDICTS[self.band][1]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score, "band": self.band, "headline": self.headline,
            "advice": self.advice, "call": self.call, "because": self.because,
            "weakest": self.weakest,
            "components": [{"name": c.name, "points": round(c.points, 1),
                            "ceiling": c.ceiling, "percent": c.percent,
                            "detail": c.detail, "good": c.good}
                           for c in self.components],
        }


def _required_years(jd_text: str) -> Optional[int]:
    """The largest "N+ years" the posting asks for. Postings repeat it; take the highest."""
    found = [int(m.group(1)) for m in _YEARS_WANTED.finditer(jd_text or "")]
    found = [n for n in found if 1 <= n <= 25]
    return max(found) if found else None


def _seniority_gap(target: str, years: Optional[float]) -> int:
    """Rungs between the role and where his experience puts him. Negative is a step down."""
    if target not in LADDER or years is None:
        return 0
    if years < 3:
        his = "analyst"
    elif years < 6:
        his = "senior analyst"
    elif years < 10:
        his = "manager"
    elif years < 15:
        his = "senior manager"
    else:
        his = "director"
    return LADDER.index(target) - LADDER.index(his)


# The parser's scorecard, from modules/ats.py. Everything except keyword coverage is
# earned by the renderer: one column, no tables, a contact block at the top, standard
# headings, dates in a shape a machine can read. Those 65 points are not in question by
# the time a document exists, which is what makes the remaining 30 predictable.
_ATS_STRUCTURAL = 65
_ATS_MUST_WEIGHT = 30


def _projection(must: Sequence[str], supported: Sequence[str],
                report: Dict[str, Any]) -> str:
    """What building it would score, and which choice of his moves the number."""
    if not must:
        return "not built yet. Nothing to measure coverage against"

    pending = [e.get("keyword") for e in (report.get("added") or [])
               if isinstance(e, dict) and e.get("grade") != "verified"]
    pending = [k for k in pending if k]

    covered = len(supported)
    now = covered - len(pending)
    total = len(must)

    def points(hit: int) -> float:
        return 20.0 * (_ATS_STRUCTURAL + _ATS_MUST_WEIGHT * (hit / total)) / 100.0

    base = points(max(now, 0))
    if not pending:
        return (f"not built yet. On what is accepted it should score about "
                f"{int(round(base))} of 20, with {covered} of {total} of their terms "
                f"on the page")

    full = points(covered)
    gain = full - base
    named = ", ".join(pending[:3]) + ("..." if len(pending) > 3 else "")
    return (f"not built yet. About {int(round(base))} of 20 as things stand. Ticking the "
            f"amber lines that carry {named} takes it to roughly {int(round(full))}, "
            f"worth about {gain:.1f} points, because keyword coverage is 30% of what "
            f"the parser scores")


def assess(extraction: Any, placement: Any, facts: Sequence[Any],
           ats_report: Any = None, location: str = "") -> Fit:
    """Score one application. Every argument is optional except the first two."""
    fit = Fit()
    add = fit.components.append

    must = list(extraction.must_keywords())

    # Placement arrives as an object from tailor and as a dict from the database. One
    # normalisation at the top rather than a getattr-or-get dance at every use: the dance
    # looked defensive and was not, because an empty list is falsy, so a Placement with no
    # gaps fell through to calling .get() on an object and the whole score vanished.
    if placement is None:
        report = {}
    elif hasattr(placement, "as_dict"):
        report = placement.as_dict()
    else:
        report = dict(placement)

    unsupported = list(report.get("unsupported") or [])

    # 1. evidence coverage, 40 points. The one that actually decides a shortlist.
    #
    # Counted against the FACTS, not against what the draft happens to say. The first
    # version of this counted `already_present`, meaning terms the model had typed, and
    # scored the Wells Fargo posting 75 and green on a record with no risk, product or
    # agile experience anywhere in it. That is the precise failure this app exists to
    # prevent, reintroduced by the thing meant to summarise it.
    supported = [term for term in must if kw.find_evidence(term, facts) is not None]
    evidenced = len(supported)
    unbacked = [term for term in must if term not in supported]

    # Weighted, not counted. A term in the title is the job; a term mentioned once in a
    # sentence is context. Flat counting scored a product-owner role at 72 on a record
    # with no product work, because eight easy terms outvoted the four that defined it.
    term_weights = kw.weights(getattr(extraction, "jd_text", "") or "",
                              getattr(extraction, "title", "") or "", must)
    total_weight = sum(term_weights.values())
    ratio = ((sum(term_weights.get(t, 1.0) for t in supported) / total_weight)
             if total_weight else 0.0)
    heavy_missing = sorted(unbacked, key=lambda t: -term_weights.get(t, 1.0))
    add(Component(
        "Evidence for what they asked for", 40 * ratio, 40,
        f"{evidenced} of {len(must)} must-have terms trace to something you have actually "
        f"done, {int(round(100 * ratio))}% by how much this posting leans on each"
        + (f". Nothing evidences {', '.join(heavy_missing[:3])}, which this posting "
           f"leans on hardest" if heavy_missing else ""),
        good=ratio >= 0.6,
    ))

    # 2. seniority, 20 points
    years = None
    try:
        from modules.tailor import experience_years
        years = experience_years(facts)
    except Exception:  # noqa: BLE001 - a score must not depend on an import succeeding
        pass

    rungs = _seniority_gap(getattr(extraction, "seniority", "unknown"), years)
    if rungs <= -2:
        pts, note = 8.0, "well below your level. Likely to read as overqualified"
    elif rungs == -1:
        pts, note = 15.0, "a step down from where your experience sits"
    elif rungs == 0:
        pts, note = 20.0, "pitched at your level"
    elif rungs == 1:
        pts, note = 12.0, "one rung above you, which is a normal stretch"
    else:
        pts, note = 4.0, "two or more rungs above you. Rarely shortlisted"
    add(Component("Seniority", pts, 20,
                  f"{getattr(extraction, 'seniority', 'unknown')} role, {note}",
                  good=pts >= 12))

    # 3. years asked for, 10 points
    wanted = _required_years(getattr(extraction, "jd_text", "") or "")
    if wanted is None or years is None:
        add(Component("Years of experience", 7.0, 10,
                      "the posting does not name a number", good=True))
    elif years >= wanted:
        add(Component("Years of experience", 10.0, 10,
                      f"they want {wanted}+, you have {years:.0f}", good=True))
    elif years >= wanted - 2:
        add(Component("Years of experience", 6.0, 10,
                      f"they want {wanted}+, you have {years:.0f}. Close enough to argue",
                      good=True))
    else:
        add(Component("Years of experience", 1.0, 10,
                      f"they want {wanted}+, you have {years:.0f}. A hard filter for many",
                      good=False))

    # 4. will the document survive the filter, 20 points
    if ats_report is None:
        # Scored out of nothing, so it contributes nothing and is excluded from the
        # denominator rather than handed a default. Awarding 14 of 20 for a document that
        # does not exist inflated every fresh score by the same fourteen points, which is
        # how a job needing product ownership he has never done came out green.
        #
        # Zero points, but not zero to say. "Build the resume and this counts" is a fact
        # about the app rather than advice about the job, and the parser's arithmetic is
        # known in advance: the structural half is earned by the renderer, and keyword
        # coverage is the only part a decision of his moves. So project it, and name the
        # decision.
        add(Component("Document survivability", 0.0, 0,
                      _projection(must, supported, report), good=True))
    else:
        passed = getattr(ats_report, "passed", False)
        raw = float(getattr(ats_report, "score", 0))
        add(Component("Document survivability", (raw / 100.0) * 20, 20,
                      f"the resume scores {int(raw)}/100 at the parser"
                      + ("" if passed else " and is currently refused"),
                      good=passed))

    # 5. location. Flagged, never scored.
    #
    # Where a job is says nothing about whether he can do it, and scoring it mixed two
    # unlike things into one number: a role he is perfect for in the wrong city came out
    # lower than a role he is weak on down the road. Whether to move is his decision and
    # it has nothing to do with fit. Ceiling zero, so it reports and contributes nothing.
    job_place = (getattr(extraction, "location", "") or "").lower()
    home = (location or "").lower()
    if not job_place:
        note, good = "the posting does not say where", True
    elif _REMOTE.search(job_place):
        note, good = "remote", True
    elif home and home.split(",")[0].strip() and home.split(",")[0].strip() in job_place:
        # the CITY, not the country. Matching on "india" scored Bengaluru as home.
        note, good = f"{job_place}, where you are", True
    elif "india" in job_place:
        note, good = f"{job_place}. Right country, wrong city, so a move is on the table", True
    else:
        note, good = f"{job_place}, outside your target", False
    add(Component("Location", 0.0, 0, note, good=good))

    # 6. the honesty penalty. Deducted, never awarded.
    if unsupported:
        penalty = min(15.0, 5.0 * len(unsupported))
        add(Component(
            "Claims nothing backs", -penalty, 0,
            f"the draft asserts {', '.join(unsupported[:3])}, which no fact supports. "
            f"That is not fit, it is the fit being manufactured",
            good=False,
        ))

    # Scored against what is actually known. Components still pending are left out of the
    # denominator, so an unbuilt resume neither helps nor hurts. Penalties come off the
    # scaled result, because a fabricated claim is bad in proportion to the whole score
    # and not in proportion to how many components happen to have run.
    scoring = [c for c in fit.components if c.ceiling > 0]
    penalties = sum(-c.points for c in fit.components if c.ceiling == 0)
    possible = sum(c.ceiling for c in scoring) or 1
    earned = sum(c.points for c in scoring)
    fit.score = max(0, min(100, int(round(100.0 * earned / possible - penalties))))

    # A high score with a heavy term missing is the interesting case, and the one a
    # headline can most easily lie about. He can score green on the Wells Fargo posting
    # by being strong on treasury and AI while having no product ownership at all, and
    # "your record covers what they asked for" would be false in the way that matters.
    heaviest = heavy_missing[0] if heavy_missing else None
    heavy_share = (term_weights.get(heaviest, 0) / total_weight) if heaviest and total_weight else 0

    if heaviest and heavy_share >= 0.08:
        fit.weakest = heaviest

    if heavy_share >= 0.10 and fit.band != RED:
        # Named in amber as well as green. Amber alone says "some work needed" without
        # saying which work, and the whole value of the number is knowing what moves it.
        opener = ("Strong on most of it, but" if fit.band == GREEN
                  else "Applicable, but")
        fit.headline = (
            f"{opener} nothing in your record shows {heaviest}, and this posting leans "
            f"on it hard. Worth applying if you can speak to it."
        )
    elif fit.band == GREEN:
        fit.headline = "Worth applying to properly. Your record covers what they asked for."
    elif fit.band == AMBER:
        fit.headline = ("Applicable, with work. Some of what they want is not in your "
                        "record yet.")
    else:
        fit.headline = ("A long shot. The gaps are in the things the job is actually "
                        "about, not the wording.")

    if unbacked:
        fit.advice.append(
            f"Nothing in your record evidences {', '.join(heavy_missing[:3])}. "
            f"If you have actually done any of them, close the gap and this moves."
        )
    if unsupported:
        fit.advice.append(
            "The draft is claiming things your record does not support. Fix that before "
            "sending, whatever the score says."
        )
    if ats_report is not None and not getattr(ats_report, "passed", True):
        fit.advice.append("The resume will not clear the parser yet. See the ATS check.")
    if fit.band == RED and not unbacked:
        fit.advice.append("Nothing here is fixable by writing. Spend the evening elsewhere.")

    log.info("fit: %d/100 (%s) for %r", fit.score, fit.band,
             getattr(extraction, "title", "") or "this job")
    return fit
