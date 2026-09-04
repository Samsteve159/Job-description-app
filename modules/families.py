"""What a resume has to prove, which is different in each field he applies to.

The pipeline used to write the same way for every posting. It read the requirements,
matched them to facts and produced correct, flat bullets. Correct is not the bar. A
consulting reader and a data reader are looking for different evidence about the same
piece of work, and a bullet that satisfies one reads as beside the point to the other.

The same real project, described for two readers:

  data          "Rebuilt a 40,000 line category taxonomy in SQL, cutting unmatched
                 spend from 22% to under 2% across 44 categories"
  consulting    "Found $12.5M of unmatched spend at a national retailer and built the
                 five-year value case that took it to the CFO"

Neither is a stretch and neither is the other one reworded. One leads with the mechanism
and the scale of the data, because that is what a data reader probes. The other leads
with the finding and whether anyone acted on it, because that is what a consulting reader
probes. Getting this wrong is the difference between a document that is accurate and one
that is persuasive, and only the second gets read twice.

Detection is deterministic. A model asked to classify a posting is one more thing that
can be wrong on a run, and the signal is sitting in the title and the required terms.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Sequence, Tuple

log = logging.getLogger(__name__)

# Ordered. The first family whose markers win the count is the one used, and ties break
# toward the earlier entry, so the more specific families are listed before the general
# ones. "data" would otherwise absorb most of "ai", since every AI posting also asks for
# SQL and Python.
FAMILIES: Tuple[str, ...] = ("ai", "consulting", "finance", "data", "tech")

_MARKERS: Dict[str, Tuple[str, ...]] = {
    "ai": (
        "machine learning", "ml", "llm", "large language model", "generative ai", "genai",
        "nlp", "natural language", "predictive model", "predictive modelling",
        "predictive modeling", "model training", "prompt", "rag", "computer vision",
        "data science", "mlops", "pytorch", "tensorflow", "scikit",
    ),
    "consulting": (
        "consulting", "advisory", "client engagement", "engagement manager", "stakeholder",
        "recommendation", "value case", "business case", "transformation", "diagnostic",
        "workshop", "client facing", "professional services", "practice",
    ),
    "finance": (
        "fp&a", "financial planning", "treasury", "reconciliation", "variance",
        "month end", "forecasting", "budget", "audit", "controls", "compliance",
        "general ledger", "working capital", "cash flow", "p&l", "financial reporting",
        "risk", "regulatory reporting", "financial process", "financial control",
        "cost centre", "cost center", "management information", "mi", "capex", "opex",
        "profitability", "expense", "revenue", "invoice", "accrual", "settlement",
    ),
    "data": (
        "sql", "power bi", "powerbi", "tableau", "etl", "data warehouse", "dashboard",
        "data model", "data quality", "data governance", "python", "analytics",
        "reporting", "pipeline", "master data", "taxonomy", "spend analysis",
    ),
    "tech": (
        "software", "engineering", "api", "platform", "cloud", "aws", "azure", "devops",
        "architecture", "microservice", "backend", "frontend", "product manager",
        "agile", "scrum", "release",
    ),
}

# What each reader is actually checking for, and therefore what a bullet has to carry to
# count. Written as instructions to a writer rather than as a description of the field,
# because the writer is what reads them.
_GUIDANCE: Dict[str, str] = {
    "ai": (
        "This reader wants to know what the model did, how it was judged, and whether it "
        "reached anybody. Name the technique and the tool. Say what it was measured "
        "against and by how much it moved. Distinguish what shipped and is in use from "
        "what was a prototype, because that distinction is the first question asked and "
        "blurring it reads as overclaiming. Where the work was using a model rather than "
        "building one, say that plainly. Applied use is a real and current skill and it "
        "is not model development, and a reader who catches the conflation stops "
        "believing the rest of the page."
    ),
    "consulting": (
        "This reader wants the problem, the recommendation, and whether anyone acted on "
        "it. Lead with the finding and its size, not the method that produced it. Name "
        "who the recommendation went to and how senior they were, because scope here is "
        "measured in whose decision it changed. Say whether it was adopted. An analysis "
        "nobody acted on is a smaller claim than one that moved a budget, and the "
        "difference is what this reader is scanning for. Client organisations are "
        "described and never named."
    ),
    "finance": (
        "This reader wants materiality, control and accuracy. Give every figure its "
        "denominator: a variance means nothing without the base it moved against. Name "
        "the reporting line and its frequency, because a monthly pack and an ad hoc "
        "analysis are different jobs. Where the work was a control or a reconciliation, "
        "say what it caught and what it prevented. Precision in the wording is itself "
        "the signal to this reader: an approximate number reads as an approximate "
        "process."
    ),
    "data": (
        "This reader wants the mechanism and the scale. Name the tool, name the volume, "
        "and say what the output was used for. Rows, categories, entities, run time, "
        "refresh frequency: those are the numbers that establish size here, and without "
        "one a bullet reads as a small piece of work. Say who consumed the output, "
        "because a dashboard nobody opened is not a delivery. Own the whole path where "
        "it is true, from raw data through to the decision, since end to end ownership "
        "is the thing that separates candidates at this level."
    ),
    "tech": (
        "This reader wants ownership and what changed as a result. Say what was built, "
        "what it replaced, and what it made possible that was not possible before. Name "
        "the systems it touched. Where the work was integrating or configuring rather "
        "than building, say so in those words, because this reader will find out and the "
        "correction is expensive."
    ),
}


# A marker matches its ordinary word endings too. Exact matching read a banking posting
# asking for "budgetary management" and "financial processes" as a data job, because
# "budget" and "process" both failed on the character after them. The alternative was an
# ever growing list of every form of every word, which is the same bug deferred.
_ENDINGS = r"(?:s|es|ing|ed|ary|al|ics)?"


def _score(family: str, hay: str) -> int:
    return sum(
        1 for term in _MARKERS[family]
        if re.search(r"(?<![\w-])" + re.escape(term) + _ENDINGS + r"(?![\w-])", hay)
    )


def detect(title: str = "", keywords: Sequence[str] = (), jd_text: str = "") -> str:
    """Which field this posting belongs to.

    The title counts three times. A posting for a data analyst inside a bank mentions
    finance terms all the way down and is still a data job, and the title is the one part
    of a posting that is about the role rather than the environment it sits in.
    """
    title_hay = f" {(title or '').lower()} "
    body_hay = " " + " ".join(
        [(k or "").lower() for k in keywords] + [(jd_text or "").lower()]
    ) + " "

    scores = {f: _score(f, body_hay) + 3 * _score(f, title_hay) for f in FAMILIES}
    best = max(FAMILIES, key=lambda f: scores[f])
    if scores[best] == 0:
        return "data"  # his own centre of gravity, and the safest default framing
    log.info("family: %s (scores %s)", best,
             ", ".join(f"{f}={scores[f]}" for f in FAMILIES if scores[f]))
    return best


def guidance(family: str) -> str:
    """The block that goes into the writing prompt for this field."""
    body = _GUIDANCE.get(family)
    if not body:
        return ""
    return (
        f"WRITING FOR A {family.upper()} READER. You have placed people in this field for "
        f"years and you know what its screeners actually check.\n\n{body}"
    )


def all_families() -> List[str]:
    return list(FAMILIES)
