"""The writing loop, and the field framing it writes with.

Runs with the model stubbed. What is being checked is the control flow, which is where
the risk actually is: a loop that keeps asking until something gets through would defeat
every gate underneath it, and a loop that stops too early leaves the posting's main
requirement unanswered with nothing on screen to say so.

    python3 tests/test_agent.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import agent, families  # noqa: E402
import modules.tailor as tailor_mod  # noqa: E402
from modules.tailor import Block, TailorResult  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def fact(id, text, **kw):
    return SimpleNamespace(id=id, text=text, metrics={}, kind=kw.get("kind", "bullet"),
                           tags=[], org=kw.get("org"), parent_id=None, order_index=0,
                           date_from=kw.get("date_from"), date_to=kw.get("date_to"),
                           verified=True)


FACTS = [
    fact(1, "Ran spend analysis in SQL across 44 categories.", org="An advisory firm"),
    fact(2, "Built Power BI dashboards used by client finance teams.", org="An advisory firm"),
]


class FakeExtraction:
    def __init__(self, must, title="Spend Analyst", jd_text=""):
        self._must = must
        self.title = title
        self.jd_text = jd_text
        self.keywords = list(must)
        self.company = "Acme"
        self.seniority = "manager"
        self.archetype = "corporate"
        self.must = []
        self.nice = []

    def must_keywords(self):
        return list(self._must)

    def nice_keywords(self):
        return []


print("which field the posting is in")
CASES = [
    ("Manager, Procurement Analytics", ["spend analysis", "sql", "power bi"], "data"),
    ("Machine Learning Engineer", ["machine learning", "pytorch", "model training"], "ai"),
    ("Senior Consultant", ["client engagement", "advisory", "business case"], "consulting"),
    ("Treasury Analyst", ["reconciliation", "cash flow", "month end"], "finance"),
    ("Platform Engineer", ["aws", "microservice", "devops"], "tech"),
    # A bank's business manager role, which read as data until markers were allowed to
    # match ordinary word endings: "budgetary" missed "budget" and "processes" missed
    # "process", so the only terms scoring were the two data ones.
    ("Assistant Vice President, Business Manager",
     ["data governance", "data quality", "budgetary management", "financial processes"],
     "finance"),
]
for title, kws, expected in CASES:
    got = families.detect(title=title, keywords=kws)
    check(f"{title} reads as {expected}", got == expected, f"got {got}")

check("an empty posting falls back rather than guessing",
      families.detect() == "data")
check("a marker matches its ordinary endings",
      families.detect(title="Analyst", keywords=["budgetary control", "accruals"]) == "finance",
      families.detect(title="Analyst", keywords=["budgetary control", "accruals"]))
check("every field has writing guidance",
      all(families.guidance(f) for f in families.all_families()))
check("guidance names the reader it is for",
      "CONSULTING READER" in families.guidance("consulting"))
check("and the ai guidance draws the line it has to",
      "prototype" in families.guidance("ai").lower()
      and "not model development" in families.guidance("ai").lower())


print("\nthe loop stops when there is nothing left to fix")
ex = FakeExtraction(["spend analysis", "sql"])

clean = TailorResult(blocks=[
    Block("experience", "Ran spend analysis in SQL across 44 categories.", [1], "verified",
          org="An advisory firm"),
])
calls = {"tailor": 0, "revise": 0}


def fake_tailor(extraction, facts, house_spec=""):
    calls["tailor"] += 1
    return clean


def fake_revise(*a, **kw):
    calls["revise"] += 1
    return [], []


real_tailor, real_revise = agent.tailor, agent.revise
agent.tailor, agent.revise = fake_tailor, fake_revise
try:
    run = agent.write(ex, FACTS)
    check("a clean first draft runs one round", len(run.rounds) == 1, str(run.as_dict()))
    check("and never calls the repair pass", calls["revise"] == 0)
    check("nothing is left unanswered", run.still_unanswered == [], run.still_unanswered)
    check("the field is recorded", run.family == "data", run.family)

    print("\nit repairs what the first draft missed")
    calls["revise"] = 0
    def fake_tailor_gappy(extraction, facts, house_spec=""):
        # A fresh result per call. The loop appends repairs to the object it is given,
        # so a shared fixture would arrive at the next case already repaired.
        return TailorResult(blocks=[
            Block("experience", "Built dashboards used by client finance teams.", [2],
                  "verified", org="An advisory firm"),
        ])

    def fake_revise_fixes(extraction, facts, unanswered, rejected, house_spec="",
                          family_guidance=""):
        calls["revise"] += 1
        return [Block("experience", "Ran spend analysis in SQL across 44 categories.",
                      [1], "verified", org="An advisory firm")], []

    agent.tailor, agent.revise = fake_tailor_gappy, fake_revise_fixes
    run = agent.write(ex, FACTS)
    check("it goes back for the missing requirement", calls["revise"] >= 1)
    check("the repair joins the document", len(run.result.blocks) == 2,
          [b.text for b in run.result.blocks])
    check("and the gap closes", run.still_unanswered == [], run.still_unanswered)
    check("the round is recorded with what it was given",
          run.rounds[1].unanswered_before and run.rounds[1].added == 1,
          str(run.as_dict()))

    print("\nit gives up rather than pushing until something gets through")
    calls["revise"] = 0

    def fake_revise_finds_nothing(extraction, facts, unanswered, rejected, house_spec="",
                                  family_guidance=""):
        calls["revise"] += 1
        return [], []

    agent.tailor, agent.revise = fake_tailor_gappy, fake_revise_finds_nothing
    run = agent.write(ex, FACTS)
    check("one repair attempt, not three", calls["revise"] == 1, str(calls["revise"]))
    check("the requirement is reported as still unanswered",
          "spend analysis" in run.still_unanswered, run.still_unanswered)
    check("and the honest draft survives", len(run.result.blocks) == 1)

    print("\na failed repair never costs the draft")
    def fake_revise_explodes(*a, **kw):
        raise RuntimeError("the model fell over")

    agent.tailor, agent.revise = fake_tailor_gappy, fake_revise_explodes
    run = agent.write(ex, FACTS)
    check("the draft from the previous round is kept",
          run.result is not None and len(run.result.blocks) == 1)
    check("and the gap is still reported honestly",
          "spend analysis" in run.still_unanswered)

    print("\nthe round cap holds")
    rounds_run = {"n": 0}

    def fake_revise_always_adds(extraction, facts, unanswered, rejected, house_spec="",
                               family_guidance=""):
        rounds_run["n"] += 1
        # Adds something every time without ever closing the gap, which is the shape a
        # runaway loop would take.
        return [Block("experience", f"Another bullet {rounds_run['n']}.", [2],
                      "verified", org="An advisory firm")], []

    agent.tailor, agent.revise = fake_tailor_gappy, fake_revise_always_adds
    run = agent.write(ex, FACTS)
    check("it stops at three rounds", len(run.rounds) == 3, str(len(run.rounds)))
    check("which is two repair passes", rounds_run["n"] == 2, str(rounds_run["n"]))
finally:
    agent.tailor, agent.revise = real_tailor, real_revise


print("\nthe repair pass cannot loosen a gate")
src = Path("modules/tailor.py").read_text()
revise_src = src[src.index("def revise("):]
check("every replacement goes through _validate", "_validate(block, known" in revise_src)
check("and the prompt tells it to return nothing rather than invent",
      "must return nothing at all" in src)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
