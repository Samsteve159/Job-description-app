"""His standing rules about himself, enforced rather than requested.

The interesting half of this file is the negatives. A guard that blocks every mention of
a word is easy and useless: he prices freight, so a bullet about logistics spend is real
work and has to survive, while "six years in logistics" is a sentence about a different
person. Most of what follows checks that the second is caught and the first is not.

    python3 tests/test_house.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import house  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


print("qualifications he does not hold")
for text in ["MBA from a top school", "Completed an M.B.A.",
             "Master of Business Administration", "PhD in economics",
             "CFA charterholder", "Certified PMP"]:
    check(f"blocked: {text[:34]}", bool(house.claims_a_degree(text)), text)

for text in ["Master of Business Analytics", "Master of International Business",
             "Built the analytics model",
             "Presented the savings case to the CPA firm's audit lead"]:
    # The last one names somebody else's qualification. The guard is about what the
    # document claims for him, and "the CPA firm" claims nothing, so it is a known and
    # accepted false positive: the cost is one blocked bullet he can rephrase, against
    # the cost of a degree claim reaching a background check.
    hits = house.claims_a_degree(text)
    if "CPA" in text:
        check("a third party's qualification is caught too, and that is the safe error",
              bool(hits), "expected the cautious reading")
    else:
        check(f"allowed: {text[:38]}", not hits, str(hits))


print("\ndomains with no entry on the record")
CLAIMS = [
    "Six years of experience in logistics and freight",
    "Deep knowledge of FMCG distribution",
    "Background in people analytics",
    "Experience with workforce planning across the group",
    "Worked in quick commerce for two years",
    "Specialised in marketplace operations",
]
for text in CLAIMS:
    hits = house.claims_a_domain(text)
    check(f"blocked: {text[:38]}", bool(hits), text)

ALLOWED = [
    "Reduced logistics spend by a fifth across three depots",
    "Rebuilt the freight category taxonomy covering 40,000 line items",
    "Analysed FMCG suppliers inside the wider spend base",
    "Built the reporting pack the HR team also consumed",
    "Priced a marketplace fee schedule as one of nine categories",
    "Delivered the supply chain spend cube on schedule",
]
for text in ALLOWED:
    hits = house.claims_a_domain(text)
    check(f"allowed: {text[:38]}", not hits, str(hits))

print("\na skills line needs no frame")
check("listing a domain as a skill is the claim",
      house.skills_claim_a_domain(["SQL", "People Analytics", "Power BI"]))
check("and an ordinary skills line passes",
      not house.skills_claim_a_domain(["SQL", "Power BI", "Spend Analysis", "UNSPSC"]))

print("\nflagging a posting rather than blocking it")
jd = ("People Analytics Manager. You will own workforce planning, attrition modelling "
      "and headcount forecasting for the group HR function.")
check("a people analytics posting is flagged",
      "HR and workforce" in house.off_target_domain(jd))
check("a procurement posting is not",
      not house.off_target_domain(
          "Manager, Procurement Analytics. Spend analysis, UNSPSC taxonomy, supplier "
          "master data, SQL and Power BI."))
check("one passing mention is not a domain",
      not house.off_target_domain(
          "Finance analyst. You will partner with HR on one reporting line."),
      "a single mention should read as a passing reference")

print("\nwriting tells")
uniform = ["Built the model and shipped it", "Wrote the queries and ran them",
           "Made the deck and gave the talk", "Ran the numbers and sent them"]
notes = house.natural_language(uniform)
check("uniform bullet length is called out", any("Uniform" in n or "uniform" in n
                                                 for n in notes), str(notes))

varied = [
    "Rebuilt a 40,000 line spend taxonomy that had drifted over four years, then held it",
    "Found the variance",
    "Wrote the SQL behind the monthly pack and cut its run time from an hour to nine minutes",
]
check("varied bullets pass", not any("uniform" in n.lower()
                                     for n in house.natural_language(varied)),
      str(house.natural_language(varied)))

check("filler is caught",
      "leveraged" in " ".join(house.natural_language(
          ["Leveraged a robust framework to drive outcomes across the business",
           "Short one here", "A third bullet of a quite different length again"])))

check("triads are counted",
      house.triads(["Owned discovery, delivery, and handover of the build"]) == 1)
check("an ordinary sentence is not a triad",
      house.triads(["Owned the build from discovery through to handover"]) == 0)

check("fewer than three bullets is not a sample",
      house.natural_language(["Short", "Also short"]) == [])

print("\nthe withheld line")
check("says both things it needs to",
      "withheld" in house.WITHHELD_LINE.lower()
      and "verified" in house.WITHHELD_LINE.lower())
check("and carries no em dash", "—" not in house.WITHHELD_LINE)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
