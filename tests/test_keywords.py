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

from modules.keywords import (_NOT_A_KEYWORD, MAX_SKILLS, _sections, canonical,  # noqa: E402
                              find_evidence, place, sanitise, significant,
                              split_by_emphasis, unsupported, usable_keyword)

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

print("\nreading emphasis out of the posting")
# extract classified must and nice itself and was not steady about it: the same Wells
# Fargo description produced eighteen must-have requirements on one run and three on the
# next. The ATS gate scores against that set, so its denominator was moving and a resume
# passed or failed on which reading the model happened to take. The posting does not move.
JD = """Manager, Procurement Analytics

Responsibilities
Own spend analysis across categories and build the reporting layer in Power BI.
Audit supplier categorisation against UNSPSC taxonomy.

Required
Advanced SQL for data transformation.
Hands-on experience with UNSPSC taxonomy.

Nice to have
Python for data pipelines.
Machine learning applied to spend classification.
"""
CANDIDATES = ["sql", "unspsc", "power bi", "python", "machine learning",
              "spend analysis", "kubernetes"]

required, optional = _sections(JD)
check("the optional half is found", "Nice to have" in optional, optional[:40])
check("and it is not also counted as required", "Nice to have" not in required)
check("required content stays in the required half", "Advanced SQL" in required)
check("optional content leaves the required half", "Python for data" not in required)

must, nice = split_by_emphasis(JD, CANDIDATES)
check("a term under Required is a must", "sql" in must, must)
check("a term in the responsibilities is a must", "spend analysis" in must, must)
check("a term only under Nice to have is not a must", "python" not in must, must)
check("and it lands in nice instead", "python" in nice, nice)
check("machine learning follows the same path", "machine learning" in nice, nice)
check("a term the posting never mentions appears nowhere",
      "kubernetes" not in must and "kubernetes" not in nice, (must, nice))
check("a term in both halves counts as required", "unspsc" in must, must)

again = split_by_emphasis(JD, CANDIDATES)
check("the same posting gives the same answer every time", again == (must, nice))
check("candidate order does not change the outcome",
      split_by_emphasis(JD, list(reversed(CANDIDATES)))[0] == must,
      split_by_emphasis(JD, list(reversed(CANDIDATES)))[0])

capped, _ = split_by_emphasis(JD, CANDIDATES, limit=2)
check("the cap keeps the most emphasised", len(capped) == 2, capped)

plain, plain_nice = split_by_emphasis("Just prose about SQL with no headings at all.",
                                      ["sql"])
check("a posting with no headings puts everything in must",
      plain == ["sql"] and plain_nice == [], (plain, plain_nice))

print("\nsoft skills are not search terms")
# A real posting asked for problem solving, multitasking and analytical skills. None can
# be evidenced from a career record, so all three sat in the denominator pushing the
# score down and then appeared under "genuine gaps", which read as the app announcing
# that a data analyst cannot solve problems or juggle priorities.
for soft in ("problem solving", "multitasking", "analytical skills", "presentation",
             "attention to detail", "strong communication skills",
             "ability to work in a fast paced environment", "excellent interpersonal skills",
             "adaptability", "teamwork", "positive attitude", "commercial acumen"):
    check(f"not a keyword: {soft!r}", not usable_keyword(soft))

# The blocklist was spelled plural and compared against canonical singulars, so "skills"
# never matched anything at all. This is the case that proves the wiring, not the words.
check("the blocklist is compared in canonical form",
      canonical("skills") in _NOT_A_KEYWORD and canonical("competencies") in _NOT_A_KEYWORD,
      canonical("skills"))

# The filter has to stay narrow. These name things a filter genuinely screens on, and
# blocking any of them would cost real coverage.
for real in ("sql", "power bi", "python", "data analytics", "data quality",
             "data management", "root cause analysis", "supplier master data",
             "liquidity risk management", "unspsc taxonomy", "product owner",
             "regulatory reporting", "financial services", "spend analytics"):
    check(f"still a keyword: {real!r}", usable_keyword(real))

print("\nthe company is not a skill")
# A Marsh posting produced "marsh" as a must-have keyword, and it appeared under genuine
# gaps: the app reporting that his record does not evidence the company he is applying
# to. No filter screens for its own name, and no gap closer can close it.
from modules.keywords import is_org_name  # noqa: E402

check("the employer's name is not a keyword", not usable_keyword("marsh", "Marsh"))
check("nor is it with the company written differently",
      not usable_keyword("marsh", "Marsh McLennan"))
check("org furniture is never a keyword",
      not usable_keyword("the team") and not usable_keyword("our client")
      and not usable_keyword("business division"))
check("a real term at the same company survives", usable_keyword("power bi", "Marsh"))
check("and so does one that merely contains the name",
      usable_keyword("marsh risk analytics", "Marsh"))
check("with no company given, nothing extra is blocked", usable_keyword("acme", None))
check("is_org_name says why", is_org_name("marsh", "Marsh") and not is_org_name("sql", "Marsh"))

print("\nproduct names people write two ways")
# A posting asked for "Chat GPT" against a record saying "ChatGPT". The word-level
# equivalence map cannot join those: one is two tokens, the other is one, and no amount
# of canonicalising a word settles a disagreement about where the space goes.
for a, b in [("chat gpt", "chatgpt"), ("Chat-GPT", "chatgpt"),
             ("power bi", "powerbi"), ("machine learning", "ml"),
             ("large language models", "llm"), ("natural language processing", "nlp"),
             ("sql server", "sqlserver"), ("co-pilot", "copilot")]:
    check(f"{a!r} and {b!r} are the same term",
          " ".join(significant(a)) == " ".join(significant(b)),
          (significant(a), significant(b)))

# It must join only what it is told to. Blindly closing spaces would make "data science"
# and "datascience" agree, and also "power" and "bi" agree with things they should not.
check("unrelated pairs are still different",
      " ".join(significant("data quality")) != " ".join(significant("data management")))
check("and a joined form does not swallow its neighbours",
      "chatgpt" in significant("we use chat gpt daily")
      and "daily" in significant("we use chat gpt daily"),
      significant("we use chat gpt daily"))

print("\nrequirements that describe the work rather than name it")
# From a real Deutsche Bank procurement posting. Every one of these was taken as a
# must-have keyword, none can appear on a resume, and each sat in the denominator and
# then in the list of things his record supposedly lacks.
for phrase in ("ai solutions", "insights generation", "cross functional",
               "generate fresh insights", "key deliverables", "ba da"):
    check(f"not a keyword: {phrase!r}", not usable_keyword(phrase))

# "Strong BA-DA skills" became "ba da". Two initials the posting coined out of two other
# acronyms, matchable by nothing. But a single short token is a real term.
for short in ("ai", "ml", "bi", "sql"):
    check(f"still a keyword: {short!r}", usable_keyword(short))

# The same posting's genuine terms, which the filter must not touch.
for real in ("ai architect", "procurement data", "regulatory reporting",
             "data visualisation", "dashboard automation", "vendor spend"):
    check(f"still a keyword: {real!r}", usable_keyword(real))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
