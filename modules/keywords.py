"""Deterministic keyword placement, because no model does it reliably.

The bake-off ran one job description through four models, repeatedly, and measured how
many of the job's must-have keywords ended up on the page. `gpt-oss-120b` swung 45 points
between runs. `claude-sonnet-5` swung 57, at four times the latency and real money per
call. The ATS gate's floor is 70%, so the same career record and the same job produced a
resume that cleared the filter or was never read by a person, at random.

That is not a model problem and a better prompt will not fix it. Asking a language model
to guarantee that eleven exact strings appear in its output is asking for a property that
sampling does not provide. So this module does it afterwards, with no model involved.

The rule it cannot break: **a keyword goes on the page only when a fact supports it.**
That is the same rule `tailor._validate` enforces, applied to a different failure. If
there is no evidence, the keyword stays missing and is reported as a genuine gap, which
is worth more than a resume that quietly claims it.

Three strengths of evidence, and they are graded differently on purpose:

  exact       the keyword is literally in a fact                     -> verified
  variant     the same word in another spelling or word form         -> verified
              ("spend analytics" against a fact saying "spend analysis")
  tokens      every significant word appears in one fact, apart      -> inferred
              ("data quality" against "improved the quality of the data")

The first two are the same claim written differently, so they render. The third is this
module asserting a relationship that a human should look at, so it needs a tick. It would
be easy to grade all three as verified and quietly gain twenty points of coverage. That
is the trade this app exists to refuse.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)

# A skills line has to stay readable by a person as well as a parser. Past roughly this
# many entries it stops reading as a summary and starts reading as keyword stuffing,
# which recruiters notice. Anything dropped is reported, never silently truncated.
MAX_SKILLS = 24

_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "in", "for", "with", "to", "on", "at",
    "by", "as", "from", "into", "using", "use", "strong", "hands", "years", "year",
}

# Word forms that mean the same thing in this domain. Checked before any stemming,
# because a stemmer that turns "analytics" and "analysis" into one root also turns
# "management" and "manager" into one, and this app must never claim to have managed
# anybody. Domain-specific and explicit beats general and surprising.
_EQUIVALENTS: List[Set[str]] = [
    {"analytics", "analysis", "analyses", "analytical", "analytic"},
    {"reporting", "report", "reports"},
    {"modelling", "modeling", "model", "models"},
    {"categorisation", "categorization", "categorised", "categorized", "categories",
     "category"},
    {"optimisation", "optimization", "optimised", "optimized"},
    {"visualisation", "visualization", "visualisations", "visualizations"},
    {"automation", "automated", "automating", "automate"},
    {"transformation", "transforming", "transform", "transformations"},
    {"validation", "validating", "validate", "validated"},
    {"reconciliation", "reconciling", "reconcile", "reconciled"},
    {"forecasting", "forecast", "forecasts", "forecasted"},
    {"governance", "governed", "governing"},
    {"procurement", "purchasing"},
    {"spend", "spending", "expenditure"},
    {"stakeholder", "stakeholders"},
    {"dashboard", "dashboards"},
    {"pipeline", "pipelines"},
    {"taxonomy", "taxonomies"},
    {"control", "controls", "controlled"},
    {"risk", "risks"},
    {"process", "processes"},
    {"requirement", "requirements"},
    {"metric", "metrics"},
]

_CANON: Dict[str, str] = {}
for _group in _EQUIVALENTS:
    _root = sorted(_group)[0]
    for _word in _group:
        _CANON[_word] = _root

_WORD = re.compile(r"[a-z0-9+#.]+")


@dataclass
class Evidence:
    keyword: str
    strength: str                    # exact | variant | tokens
    fact_ids: List[int] = field(default_factory=list)
    quote: str = ""                  # the supporting text, shown in review

    @property
    def grade(self) -> str:
        return "verified" if self.strength in ("exact", "variant") else "inferred"


def unsupported(text: str, wanted: Sequence[str], facts: Sequence[Any]) -> List[str]:
    """Terms the writing asserts that no fact supports.

    This closes a hole the citation guard cannot see. `tailor._validate` checks that a
    block cites a real fact id and that its numbers come from that fact. It does not
    check that the *skill* the sentence claims is one the fact demonstrates. So a bullet
    citing a genuine treasury fact can say "supporting liquidity risk management" and
    pass every guard, even though the word "risk" appears nowhere in the entire career
    record.

    That is not hypothetical. It is what made keyword coverage swing 45 points between
    runs: the high-scoring runs were the ones where the model invented more. Coverage was
    measuring fabrication and rewarding it.
    """
    lowered = (text or "").lower()
    joined = " ".join(tokens(text))
    out = []
    for term in wanted:
        term = (term or "").strip()
        if not term:
            continue
        wants = significant(term)
        present = term.lower() in lowered or (wants and " ".join(wants) in joined)
        if present and find_evidence(term, facts) is None:
            out.append(term)
    return out


@dataclass
class Placement:
    """What was added, what was already there, and what genuinely is not true."""
    added: List[Evidence] = field(default_factory=list)
    already_present: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)   # evidenced but over MAX_SKILLS
    unsupported: List[str] = field(default_factory=list)   # written, but nothing backs it

    def summary_line(self) -> str:
        verified = sum(1 for e in self.added if e.grade == "verified")
        line = (
            f"keywords: {len(self.already_present)} already placed, "
            f"{len(self.added)} added ({verified} verified, "
            f"{len(self.added) - verified} needing review), "
            f"{len(self.gaps)} genuine gap(s)"
        )
        if self.unsupported:
            line += f", {len(self.unsupported)} UNSUPPORTED CLAIM(S)"
        return line

    def as_dict(self) -> Dict[str, Any]:
        return {
            "added": [{"keyword": e.keyword, "strength": e.strength,
                       "grade": e.grade, "fact_ids": e.fact_ids, "quote": e.quote}
                      for e in self.added],
            "already_present": self.already_present,
            "gaps": self.gaps,
            "dropped": self.dropped,
            "unsupported": self.unsupported,
            "is_a_stretch": self.is_a_stretch,
            "summary": self.summary_line(),
        }

    @property
    def is_a_stretch(self) -> bool:
        """More of the job's requirements are missing from the record than are in it."""
        covered = len(self.already_present) + len(self.added)
        return len(self.gaps) > covered


def canonical(word: str) -> str:
    """One spelling per meaning. British and American, plural and singular."""
    word = word.lower()
    if word in _CANON:
        return _CANON[word]
    # -ise / -isation are the same word as -ize / -ization
    for british, american in (("isation", "ization"), ("ise", "ize"), ("yse", "yze")):
        if word.endswith(british):
            swapped = word[: -len(british)] + american
            if swapped in _CANON:
                return _CANON[swapped]
            word = swapped
            break
    if word in _CANON:
        return _CANON[word]
    # A trailing s is usually a plural, but "continuous" is not the plural of
    # "continuou" and "analysis" is not the plural of "analysi". Endings that are part
    # of the word, not a plural marker, are left alone.
    if (len(word) > 3 and word.endswith("s")
            and not word[-2:] in ("ss", "us", "is", "as", "os")):
        stem = word[:-1]
        return _CANON.get(stem, stem)
    return word


def tokens(text: str) -> List[str]:
    return [canonical(w) for w in _WORD.findall((text or "").lower())]


def significant(text: str) -> List[str]:
    """Tokens that carry the meaning. 'asset and liability management' loses 'and'."""
    return [t for t in tokens(text) if t not in _STOPWORDS and len(t) > 1]


def _fact_text(fact: Any) -> str:
    """Everything about a fact a keyword could legitimately match against."""
    parts = [getattr(fact, "text", "") or "", getattr(fact, "org", "") or ""]
    tags = getattr(fact, "tags", None) or []
    if isinstance(tags, (list, tuple)):
        parts.extend(str(t) for t in tags)
    metrics = getattr(fact, "metrics", None) or {}
    if isinstance(metrics, dict):
        parts.extend(f"{k} {v}" for k, v in metrics.items())
    return " ".join(parts)


def find_evidence(keyword: str, facts: Sequence[Any]) -> Optional[Evidence]:
    """Strongest evidence for one keyword, or None. Never guesses."""
    keyword = (keyword or "").strip()
    if not keyword:
        return None

    wanted = significant(keyword)
    if not wanted:
        return None
    normalised_kw = " ".join(wanted)

    best: Optional[Evidence] = None
    for fact in facts:
        if not getattr(fact, "verified", True):
            continue                       # unverified facts cannot support anything
        raw = _fact_text(fact)
        lowered = raw.lower()
        fid = getattr(fact, "id", None)
        if fid is None:
            continue

        if keyword.lower() in lowered:
            return Evidence(keyword, "exact", [fid], raw[:160])

        fact_tokens = tokens(raw)
        joined = " ".join(fact_tokens)
        if normalised_kw in joined:
            if best is None or best.strength == "tokens":
                best = Evidence(keyword, "variant", [fid], raw[:160])
            continue

        if all(w in fact_tokens for w in wanted) and best is None:
            best = Evidence(keyword, "tokens", [fid], raw[:160])

    return best


def place(existing_text: str, must: Sequence[str], nice: Sequence[str],
          facts: Sequence[Any], skills: Sequence[str] = ()) -> Placement:
    """Decide which missing keywords have earned a place on the page.

    `existing_text` is everything already written. A keyword already present is left
    alone: repeating it gains nothing and costs readability.
    """
    placement = Placement()
    present = " ".join(tokens(existing_text) + tokens(" ".join(skills)))
    current = list(skills)

    def already(keyword: str) -> bool:
        if keyword.lower() in (existing_text or "").lower():
            return True
        wanted = significant(keyword)
        return bool(wanted) and " ".join(wanted) in present

    for pool, is_must in ((must, True), (nice, False)):
        for keyword in pool:
            keyword = (keyword or "").strip()
            if not keyword:
                continue
            if already(keyword):
                if is_must:
                    placement.already_present.append(keyword)
                continue

            evidence = find_evidence(keyword, facts)
            if evidence is None:
                if is_must:
                    placement.gaps.append(keyword)
                continue

            if len(current) + len(placement.added) >= MAX_SKILLS:
                placement.dropped.append(keyword)
                continue
            placement.added.append(evidence)

    placement.unsupported = unsupported(existing_text, must, facts)
    if placement.unsupported:
        log.warning(
            "UNSUPPORTED CLAIMS: the writing asserts %s, and no fact supports any of "
            "them. Blocks containing them have been downgraded for review.",
            ", ".join(placement.unsupported),
        )

    if placement.dropped:
        log.warning("keywords: %d evidenced keyword(s) dropped at the %d skill cap: %s",
                    len(placement.dropped), MAX_SKILLS, ", ".join(placement.dropped))
    log.info("%s", placement.summary_line())
    return placement


def as_blocks(placement: Placement, block_factory: Any, start_index: int = 50) -> List[Any]:
    """Turn a Placement into resume blocks, split by whether a human must agree.

    Two blocks, not one, because they render differently. Verified additions are the same
    claim the fact already makes, so they go on the page. Token matches are this module
    asserting a relationship, so they wait for a tick. Merging them would smuggle the
    second past the gate on the first one's authority.
    """
    blocks = []
    for grade, index in (("verified", start_index), ("inferred", start_index + 1)):
        group = [e for e in placement.added if e.grade == grade]
        if not group:
            continue
        fact_ids: List[int] = []
        for evidence in group:
            for fid in evidence.fact_ids:
                if fid not in fact_ids:
                    fact_ids.append(fid)
        reason = (
            "the job's exact wording for something already in your record"
            if grade == "verified"
            else "your record covers these in different words. Check each before using"
        )
        blocks.append(block_factory(
            section="skills",
            text=" | ".join(e.keyword for e in group),
            fact_ids=fact_ids,
            grade=grade,
            rationale=f"Added by keyword placement: {reason}",
            order_index=index,
        ))
    return blocks


# ------------------------------------------------------------- keyword sanitising

# Phrases that are requirements but not search terms. A filter screens for "SQL", not
# for "demonstrated ability to build strong partnerships". Keeping these in the keyword
# set does two kinds of harm: they can never be satisfied, so they sit in the denominator
# forcing coverage down until a perfectly good resume is refused, and they crowd out the
# terms that would actually have been matched.
_NOT_A_KEYWORD = {
    "experience", "ability", "understanding", "knowledge", "skills", "expertise",
    "degree", "qualification", "background", "exposure", "familiarity", "judgment",
    "judgement", "collaboration", "communication", "partnership", "partnerships",
    "responsibility", "responsibilities", "requirement", "requirements", "initiative",
    "improvement", "measurement", "identification", "enablement", "adoption",
    "excellence", "leadership", "ownership", "delivery", "mindset", "practices",
}

MAX_KEYWORD_WORDS = 3
MAX_KEYWORD_CHARS = 42
MAX_MUST_KEYWORDS = 12


def usable_keyword(keyword: str) -> bool:
    """Is this something a person would actually write on a resume?

    `extract` lifts keywords out of the job description, and a job description contains
    headings. One run produced "measurement and continuous improvement" and "ai use case
    identification" as must-have keywords. No resume contains those strings, no filter
    screens on them, and every one of them pushed the coverage score down toward the
    threshold that refuses the export.
    """
    keyword = (keyword or "").strip()
    if not keyword or len(keyword) > MAX_KEYWORD_CHARS:
        return False
    # "5+ years", "6+" and the like describe a requirement's size, not its subject
    if re.search(r"\d", keyword) and not re.search(r"[a-z]{3}", keyword.lower()):
        return False
    words = significant(keyword)
    if not words or len(words) > MAX_KEYWORD_WORDS:
        return False
    # The head noun decides it. A real search term names a thing: a tool, a domain, a
    # role, a discipline. "liquidity risk management" and "product owner" end in a thing.
    # "ai use case identification" and "independent judgment" end in an abstraction, and
    # a phrase ending in an abstraction is a requirement being described, not a term
    # anybody types into a resume or a filter searches for.
    return words[-1] not in _NOT_A_KEYWORD


def sanitise(candidates: Sequence[str], limit: int = MAX_MUST_KEYWORDS) -> List[str]:
    """Keep the searchable ones, deduplicated by meaning, in the order given.

    Order is preserved rather than sorted, because `extract` returns must-haves roughly
    in the job's own order of emphasis and the cap should drop the least emphasised.
    """
    kept: List[str] = []
    seen: Set[str] = set()
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not usable_keyword(candidate):
            continue
        key = " ".join(significant(candidate))
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate.lower())
        if len(kept) >= limit:
            break
    return kept


# ------------------------------------------------- deriving must-haves from the text

# Headings a job description uses to separate what it insists on from what it would like.
# Matched against a whole line, because "required" appears inside prose constantly and
# only means something structural when it is a heading.
_REQUIRED_HEADS = re.compile(
    r"^\s*(?:required|requirements|minimum|basic|essential|must[\s-]?have|"
    r"qualifications|what you (?:will )?need|who you are|about you|responsibilities|"
    r"in this role|job expectations|key accountabilities|the role)\b",
    re.I | re.M,
)
_OPTIONAL_HEADS = re.compile(
    r"^\s*(?:nice[\s-]?to[\s-]?have|preferred|desired|desirable|bonus|advantageous|"
    r"good to have|pluses?|it would be great|additionally)\b",
    re.I | re.M,
)


def _sections(jd_text: str) -> Tuple[str, str]:
    """Split a job description into what it requires and what it would prefer.

    Crude on purpose: find the headings, cut at them, and treat everything before the
    first optional heading as required. A job description is not structured data and
    trying to parse it properly is how you end up with something that works on four
    postings and silently mangles the fifth.
    """
    text = jd_text or ""
    optional_starts = [m.start() for m in _OPTIONAL_HEADS.finditer(text)]
    if not optional_starts:
        return text, ""

    required_parts, optional_parts = [], []
    cursor = 0
    for start in optional_starts:
        required_parts.append(text[cursor:start])
        # an optional block runs until the next required heading, or the next optional one
        following = _REQUIRED_HEADS.search(text, start + 1)
        end = following.start() if following else len(text)
        for other in optional_starts:
            if start < other < end:
                end = other
        optional_parts.append(text[start:end])
        cursor = end
    required_parts.append(text[cursor:])
    return "\n".join(required_parts), "\n".join(optional_parts)


def _mentions(term: str, text: str) -> int:
    wanted = significant(term)
    if not wanted:
        return 0
    body = " ".join(tokens(text))
    needle = " ".join(wanted)
    return body.count(needle)


def split_by_emphasis(jd_text: str, candidates: Sequence[str],
                      limit: int = MAX_MUST_KEYWORDS) -> Tuple[List[str], List[str]]:
    """Decide must and nice from where a term sits in the posting, not from a model.

    `extract` classifies requirements itself, and it is not steady about it: the same
    Wells Fargo posting produced eighteen must-have requirements on one run and three on
    the next. Since the ATS gate scores coverage against that set, its denominator was
    moving between runs and a resume passed or failed on which reading the model happened
    to take.

    The posting does not move. A term in the required half is a must, a term that appears
    only under "preferred" is a nice-to-have, and how often it is repeated is how much
    the job is leaning on it. All three are readable from the text.
    """
    required, optional = _sections(jd_text)
    clean = sanitise(candidates, limit=200)

    scored = []
    for term in clean:
        in_required = _mentions(term, required)
        in_optional = _mentions(term, optional)
        if not in_required and not in_optional:
            continue
        scored.append((term, in_required, in_optional))

    # Required mentions first, then total weight. A term the posting repeats is one it
    # cares about, and a filter built from that posting will care about it too.
    #
    # Ties break alphabetically, NOT on the candidate list's order. Ordering by input
    # position let the model back in through the side door: it returns its keywords in a
    # different order each run, so equally-emphasised terms swapped places and a
    # different twelve survived the cap. The posting is the only thing allowed to decide.
    scored.sort(key=lambda row: (-row[1], -(row[1] + row[2]), row[0]))

    must = [term for term, req, _ in scored if req][:limit]
    nice = [term for term, req, opt in scored if not req and opt]
    return must, nice


# A frequency-only candidate list was tried here and deleted. Counting phrases in the
# posting is perfectly deterministic and perfectly useless: the top terms came back "ai",
# "data", "business", "lead", "act", "such", "teams", "wells". Word counts cannot tell
# that UNSPSC is a taxonomy and "such" is not. The model's domain sense is doing real
# work in naming candidates; what it could not be trusted with was deciding which of them
# the job insists on, and split_by_emphasis takes that job away from it.
