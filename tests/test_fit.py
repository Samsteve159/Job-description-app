"""The fit score, and every way a score like this flatters the person reading it.

A number nobody argues with is a number nobody should trust, so the tests here are mostly
about the score being *low* when it should be. Four ways this one lied during its first
hour, all now fixed and all covered below:

  it counted keywords the model had typed as evidence, which is the exact number this
  app proved unreliable, and scored a product-owner role at 75 on a record with no
  product work anywhere in it

  it awarded 14 of 20 for "document survivability" before any document existed, handing
  every fresh application the same fourteen free points

  it matched location on the word "india", so Bengaluru counted as home for someone in
  Mumbai

  it weighted every keyword equally, so four generic terms outvoted the one in the job title

    python3 tests/test_fit.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import fit  # noqa: E402
from modules.keywords import weights  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def fact(id, text, tags=None, kind="bullet", verified=True, **kw):
    return SimpleNamespace(id=id, text=text, tags=tags or [], metrics={}, org=kw.get("org"),
                           kind=kind, verified=verified, order_index=0,
                           date_from=kw.get("date_from"), date_to=kw.get("date_to"))


FACTS = [
    fact(1, "Treasury Analyst", kind="role", org="Puma", date_from="Apr 2016",
         date_to="Oct 2021"),
    fact(2, "Data Analyst", kind="role", org="PI", date_from="Dec 2021", date_to="Present"),
    fact(3, "Ran spend analysis in SQL across the distributor network.",
         tags=["spend analytics"]),
    fact(4, "Built treasury dashboards for regional reporting."),
]

JD = """Manager, Procurement Analytics
Mumbai, India.

Responsibilities
Own spend analysis across categories. Spend analysis is the core of the role.
Build reporting in Power BI.

Required
6+ years of experience. Advanced SQL.
"""


def extraction(**kw):
    base = dict(title="Manager, Procurement Analytics", company="Acme",
                location="Mumbai, India", seniority="manager", archetype="corporate",
                jd_text=JD, scored_must=["spend analysis", "sql"], scored_nice=[],
                must=[], nice=[], keywords=[])
    base.update(kw)
    ns = SimpleNamespace(**base)
    ns.must_keywords = lambda: list(ns.scored_must)
    ns.nice_keywords = lambda: list(ns.scored_nice)
    return ns


print("evidence is counted against the facts, never against the draft")
# The placement says these terms are all over the writing. They are not in the record.
lying = {"already_present": ["kubernetes", "terraform"], "added": [], "gaps": [],
         "unsupported": []}
f = fit.assess(extraction(scored_must=["kubernetes", "terraform"]), lying, FACTS)
evidence = f.components[0]
check("terms the draft claims but no fact backs score zero",
      evidence.points == 0, evidence.points)
check("and the band reflects it", f.band == fit.RED, (f.score, f.band))

f = fit.assess(extraction(), {}, FACTS)
check("terms the facts do support score full", f.components[0].points == 40,
      f.components[0].points)

print("\nan unbuilt document neither helps nor hurts")
f_none = fit.assess(extraction(), {}, FACTS, ats_report=None, location="Mumbai, India")
doc = [c for c in f_none.components if c.name == "Document survivability"][0]
check("it scores nothing rather than a default", doc.points == 0, doc.points)
check("and it is excluded from the total, not counted as a loss",
      f_none.score >= 90, f_none.score)
check("scoring only what is known keeps the ceiling reachable",
      f_none.score > fit.GREEN_FLOOR, f_none.score)

good_doc = SimpleNamespace(passed=True, score=96)
f_doc = fit.assess(extraction(), {}, FACTS, ats_report=good_doc)
check("a real document is scored once it exists",
      [c for c in f_doc.components if c.name == "Document survivability"][0].points > 0)

bad_doc = SimpleNamespace(passed=False, score=30)
f_bad = fit.assess(extraction(), {}, FACTS, ats_report=bad_doc)
check("a document that will not parse pulls the score down",
      f_bad.score < f_doc.score, (f_bad.score, f_doc.score))
check("and it says so", any("parser" in a for a in f_bad.advice), f_bad.advice)

print("\nlocation matches on the city, not the country")
f = fit.assess(extraction(location="Bengaluru, Karnataka, India"), {}, FACTS,
               location="Mumbai, India")
loc = [c for c in f.components if c.name == "Location"][0]
check("another Indian city is not home", loc.points < 10, loc.points)
check("but it is still the right country", loc.points >= 5, loc.points)
check("the reason names relocation", "relocation" in loc.detail, loc.detail)

f = fit.assess(extraction(location="Mumbai, India. Hybrid"), {}, FACTS,
               location="Mumbai, India")
check("his own city scores full",
      [c for c in f.components if c.name == "Location"][0].points == 10)

f = fit.assess(extraction(location="London, United Kingdom"), {}, FACTS,
               location="Mumbai, India")
check("a country he is not moving to scores low",
      [c for c in f.components if c.name == "Location"][0].points <= 3)

print("\nkeywords are weighted by what the posting leans on")
w = weights(JD, "Manager, Procurement Analytics", ["spend analysis", "sql", "power bi"])
check("a repeated term outweighs a mentioned one",
      w["spend analysis"] > w["power bi"], w)
check("repetition is damped, not linear", w["spend analysis"] < 20, w)

titled = weights(JD, "Spend Analysis Manager", ["spend analysis", "sql"])
check("a term in the job title is weighted far above the rest",
      titled["spend analysis"] > titled["sql"] * 3, titled)
check("and the same term is worth less when it is not in the title",
      titled["spend analysis"] > w["spend analysis"] * 3, (titled, w))

print("\na heavy gap is named even when the score is high")
heavy_jd = (JD + chr(10) + "Product owner experience essential. The product owner "
            "runs the backlog and the product owner sets priorities.")
f = fit.assess(extraction(title="Product Owner, Analytics",
                          scored_must=["spend analysis", "sql", "product owner"],
                          jd_text=heavy_jd),
               {}, FACTS, location="Mumbai, India")
check("the headline does not claim full coverage",
      "covers what they asked for" not in f.headline, f.headline)
check("it names the missing thing instead",
      "product owner" in f.headline.lower(), f.headline)
check("and the advice repeats it",
      any("product owner" in a.lower() for a in f.advice), f.advice)

# The same case in amber. Amber alone says "some work needed" without saying which work.
amber = fit.assess(extraction(title="Product Owner, Analytics",
                              scored_must=["product owner", "kubernetes", "sql"],
                              jd_text=heavy_jd),
                   {}, FACTS, location="Bengaluru, India")
check("an amber score names its heaviest gap too",
      amber.band == fit.AMBER and "product owner" in amber.headline.lower(),
      (amber.score, amber.band, amber.headline))

print("\nfabricated claims are deducted, never counted as coverage")
clean = fit.assess(extraction(), {"unsupported": []}, FACTS)
faked = fit.assess(extraction(), {"unsupported": ["risk management", "agile"]}, FACTS)
check("claiming things nothing backs lowers the score",
      faked.score < clean.score, (faked.score, clean.score))
check("the penalty appears as its own line",
      any(c.name == "Claims nothing backs" for c in faked.components))
check("and it warns regardless of the number",
      any("does not support" in a for a in faked.advice), faked.advice)

print("\nbands")
check("a high score is green", fit.Fit(score=85).band == fit.GREEN)
check("the middle is amber", fit.Fit(score=55).band == fit.AMBER)
check("a low score is red", fit.Fit(score=20).band == fit.RED)
check("the score never leaves 0 to 100",
      0 <= fit.assess(extraction(scored_must=["kubernetes"]),
                      {"unsupported": ["a", "b", "c", "d"]}, FACTS).score <= 100)

print("\nplacement arrives as an object or a dict, and both work")
from modules.keywords import Placement  # noqa: E402
as_object = fit.assess(extraction(), Placement(), FACTS)
as_dict = fit.assess(extraction(), {}, FACTS)
check("an empty Placement object does not break it", as_object.score == as_dict.score,
      (as_object.score, as_dict.score))
check("None works too", fit.assess(extraction(), None, FACTS).score == as_dict.score)
check("as_dict round trips", "components" in as_dict.as_dict())

print("\nthe verdict, which is the half you read first")
from modules.fit import Fit  # noqa: E402

check("green says apply", Fit(score=88).call == "Apply", Fit(score=88).call)
check("amber leaves it to him", Fit(score=55).call == "Your call", Fit(score=55).call)
check("red says do not", Fit(score=22).call == "Skip it", Fit(score=22).call)
check("the boundary at 70 is green", Fit(score=70).call == "Apply")
check("the boundary at 40 is not red", Fit(score=40).call == "Your call")
check("39 is red", Fit(score=39).call == "Skip it")

# A verdict that cannot say why is worth much less than one that can.
named = Fit(score=75, weakest="supplier master data")
check("green names what is still missing", "supplier master data" in named.because)
check("and still says apply", named.call == "Apply")
weak = Fit(score=52, weakest="model governance")
check("amber names the thing it hinges on", "model governance" in weak.because)
low = Fit(score=30, weakest="model governance")
check("red names it too", "model governance" in low.because)
check("with nothing to name it still gives a reason",
      len(Fit(score=30).because) > 20, Fit(score=30).because)

d = Fit(score=80, weakest="sql").as_dict()
check("the verdict survives into the stored package",
      d["call"] == "Apply" and "sql" in d["because"] and d["weakest"] == "sql", d)

print("\nsurvivability, before there is a document to measure")
from modules.fit import _projection  # noqa: E402

MUST = ["sql", "power bi", "unspsc taxonomy", "spend analytics", "fp&a", "supplier master data"]
SUP = ["sql", "power bi", "unspsc taxonomy", "spend analytics", "fp&a"]
PENDING = {"added": [{"keyword": "fp&a", "grade": "inferred"},
                     {"keyword": "unspsc taxonomy", "grade": "inferred"}]}

line = _projection(MUST, SUP, PENDING)
check("it says what building it would score", "of 20" in line, line)
check("it names the terms behind the amber lines",
      "fp&a" in line and "unspsc taxonomy" in line, line)
check("it quantifies what ticking them is worth", "points" in line, line)
check("ticking can only help", "roughly 18" in line and "About 16" in line, line)

settled = _projection(MUST, SUP, {"added": []})
check("with nothing pending it recommends nothing",
      "Ticking" not in settled and "5 of 6" in settled, settled)

# The structural 65 is earned by the renderer, so even zero keyword coverage is not zero.
none_hit = _projection(MUST, [], {"added": []})
check("no coverage still scores the structural half", "13 of 20" in none_hit, none_hit)
# 19, not 20. The last point is nice-to-have coverage, which the projection does not
# assume, because promising a point it might not earn is the one direction this must
# never err in.
check("full must coverage stops short of the ceiling",
      "19 of 20" in _projection(MUST, MUST, {"added": []}),
      _projection(MUST, MUST, {"added": []}))
check("no must-haves is said plainly, not divided by zero",
      "Nothing to measure" in _projection([], [], {}))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
