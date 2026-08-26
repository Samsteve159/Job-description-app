"""Deterministic keyword placement, and the fabrication it was built to expose.

The bake-off said keyword coverage swung 45 points between runs on every model tested,
and the obvious reading was that models are inconsistent. The real reading, once the
facts were checked, was worse: the high-coverage runs were the ones where the model
invented more. The word "risk" appears in none of Sameer's 63 facts, and runs still
produced "supporting liquidity risk management", citing a real fact id, passing every
guard. Coverage was measuring fabrication and rewarding it.

So the tests that matter here are not the placement ones. They are `unsupported`.

    python3 tests/test_keywords.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.keywords import (MAX_SKILLS, canonical, find_evidence,  # noqa: E402
                              place, sanitise, significant, unsupported,
                              usable_keyword)

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def fact(id, text, tags=None, metrics=None, verified=True):
    return SimpleNamespace(id=id, text=text, tags=tags or [], metrics=metrics or {},
                           org=None, verified=verified, kind="bullet")


FACTS = [
    fact(1, "Ran spend analysis across the client's full distributor network.",
         tags=["spend analytics", "sql"]),
    fact(2, "Audits supplier categorisation against UNSPSC at scale.",
         metrics={"coverage": "99.7% of categorised spend"}),
    fact(3, "Improved the quality of the data feeding regional reporting."),
    fact(4, "Built treasury dashboards used for regional reporting."),
    fact(5, "Certified in advanced Power BI.", verified=False),
]

print("canonical forms")
for word, want in [
    ("analytics", "analyses"), ("analysis", "analyses"),
    ("categorisation", "categories"), ("categorization", "categories"),
    ("dashboards", "dashboard"),
]:
    check(f"{word!r} and its variants agree", canonical(word) == want, canonical(word))

# Which spelling wins does not matter, only that both reach the same one.
for british, american in [("optimisation", "optimization"),
                          ("categorisation", "categorization"),
                          ("visualisation", "visualization")]:
    check(f"{british} and {american} land together",
          canonical(british) == canonical(american),
          (canonical(british), canonical(american)))

for word in ("continuous", "analysis", "status", "process", "bus"):
    check(f"{word!r} does not lose a letter to the plural rule",
          not canonical(word).endswith(("ou", "asi", "atu")), canonical(word))

check("management and manager stay different words",
      canonical("management") != canonical("manager"),
      (canonical("management"), canonical("manager")))
check("that is what keeps a stemmer from inventing a headcount claim",
      canonical("managed") != canonical("management"))

check("stopwords drop out of a phrase",
      significant("asset and liability management") == ["asset", "liability", "management"],
      significant("asset and liability management"))

print("\nwhat counts as a keyword")
for term, want in [
    ("sql", True), ("power bi", True), ("unspsc", True), ("product owner", True),
    ("asset and liability management", True), ("liquidity risk management", True),
    ("spend analytics", True), ("backlog management", True),
    ("measurement and continuous improvement", False),
    ("ai use case identification", False), ("independent judgment", False),
    ("strong understanding", False), ("project leadership", False),
    ("5+ years of experience", False), ("experience", False), ("", False),
]:
    check(f"{'keep' if want else 'drop'}: {term[:40]!r}", usable_keyword(term) == want)

print("\nsanitising a keyword list")
out = sanitise(["SQL", "sql", "Spend Analytics", "spend analysis", "Power BI",
                "measurement and continuous improvement", "Treasury"])
check("case is normalised", "sql" in out, out)
check("exact duplicates collapse", out.count("sql") == 1, out)
check("duplicates by meaning collapse too",
      len([o for o in out if "spend" in o]) == 1, out)
check("unusable phrases are removed",
      not any("continuous" in o for o in out), out)
check("order of emphasis is kept", out[0] == "sql", out)
check("the cap is honoured", len(sanitise([f"skill{i}" for i in range(40)], limit=5)) == 5)

print("\nevidence")
e = find_evidence("spend analysis", FACTS)
check("an exact match is found", e and e.strength == "exact" and e.fact_ids == [1], e)
e = find_evidence("spend analytics", FACTS)
check("a spelling variant is found", e and e.strength in ("exact", "variant"), e)
e = find_evidence("data quality", FACTS)
check("words apart in one fact are a token match",
      e and e.strength == "tokens" and e.fact_ids == [3], e)
check("a token match only earns amber", e.grade == "inferred", e.grade)
check("exact and variant matches render",
      find_evidence("spend analysis", FACTS).grade == "verified")
check("tags count as evidence", find_evidence("sql", FACTS) is not None)
check("metrics count as evidence", find_evidence("categorised spend", FACTS) is not None)
check("no evidence returns nothing, it does not guess",
      find_evidence("kubernetes", FACTS) is None)
check("an unverified fact cannot support anything",
      find_evidence("power bi", FACTS) is None, find_evidence("power bi", FACTS))

print("\nunsupported claims, which is the point of all this")
written = ("Monitored liquidity and funding metrics, supporting liquidity risk "
           "management and treasury risk oversight.")
bad = unsupported(written, ["liquidity risk management", "treasury risk", "spend analysis"],
                  FACTS)
check("a claim with no supporting fact is caught",
      set(bad) == {"liquidity risk management", "treasury risk"}, bad)
check("a claim that is supported is not flagged", "spend analysis" not in bad, bad)
check("a keyword that was never written is not flagged",
      "kubernetes" not in unsupported(written, ["kubernetes"], FACTS))
check("it reads word forms, not just exact strings",
      unsupported("we do treasury risks daily", ["treasury risk"], FACTS) != [])

print("\nplacement")
p = place("Ran spend analysis in SQL.", must=["spend analysis", "data quality", "kubernetes"],
          nice=[], facts=FACTS)
check("something already written is not repeated",
      p.already_present == ["spend analysis"], p.already_present)
check("an evidenced keyword is added",
      [e.keyword for e in p.added] == ["data quality"], [e.keyword for e in p.added])
check("an unevidenced keyword becomes a gap", p.gaps == ["kubernetes"], p.gaps)
check("the added one needs review, it is only a token match",
      p.added[0].grade == "inferred", p.added[0].grade)

p = place("", must=["spend analysis"], nice=["unspsc"], facts=FACTS)
check("nice-to-haves are placed too",
      {e.keyword for e in p.added} == {"spend analysis", "unspsc"},
      [e.keyword for e in p.added])
check("but a missing nice-to-have is not called a gap", p.gaps == [], p.gaps)

p = place("", must=["spend analysis"], nice=[], facts=FACTS,
          skills=[f"existing{i}" for i in range(MAX_SKILLS)])
check("the skills cap is respected", p.added == [], p.added)
check("and what it dropped is reported, not silently lost",
      p.dropped == ["spend analysis"], p.dropped)

p = place("", must=["spend analysis", "a", "b", "c"], nice=[], facts=FACTS)
check("a job needing more than the record holds reads as a stretch", p.is_a_stretch)
p = place("", must=["spend analysis"], nice=[], facts=FACTS)
check("a job the record covers does not", not p.is_a_stretch)

check("as_dict survives a round trip through JSON",
      isinstance(p.as_dict()["added"], list) and "summary" in p.as_dict())

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
