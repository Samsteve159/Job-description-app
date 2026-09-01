"""The safety logic in modules/tailor.py, tested without any API key.

These are the checks that stop the app becoming a liability, so they are tested against
fixtures rather than against a live model. A model that behaves today can drift tomorrow;
these assertions do not.

    python3 tests/test_guards.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.tailor import (Block, TailorResult, _tenure_claims,  # noqa: E402
                            _validate, experience_years, to_payload)
from modules.render_docx import gate, BlockedContentError               # noqa: E402


def fact(id, text, metrics=None, kind="bullet", **kw):
    return SimpleNamespace(
        id=id, text=text, metrics=metrics or {}, kind=kind, tags=[], org=kw.get("org"),
        parent_id=kw.get("parent_id"), order_index=kw.get("order_index", 0),
        date_from=kw.get("date_from"), date_to=kw.get("date_to"), verified=True,
    )


KNOWN = {f.id: f for f in [
    fact(12, "Scaled a $12.5M unmatched-spend finding into a ~$40M five-year value case.",
         {"finding": "$12.5M", "value_case": "~$40M / 5 years"}),
    fact(14, "Audits supplier categorisation across 98.4% of categorised spend.",
         {"coverage": "98.4% of categorised spend", "categories": "31 of 44"}),
    fact(20, "Built treasury dashboards used for regional reporting."),
]}

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


print("citation validation")
b, reason = _validate(Block("experience", "Something plausible.", [999], "verified"), KNOWN)
check("fabricated fact id is rejected", reason is not None and b.grade == "blocked", reason)

b, reason = _validate(Block("experience", "Scaled a $12.5M finding.", [12, 999], "verified"), KNOWN)
check("invented id stripped, real id survives", reason is None and b.fact_ids == [12], b.fact_ids)

b, reason = _validate(Block("experience", "No citation at all.", [], "verified"), KNOWN)
check("empty fact_ids is rejected", reason is not None and b.grade == "blocked", reason)

print("\nnumber drift")
b, reason = _validate(Block("experience", "Scaled a $22.5M unmatched-spend finding.", [12], "verified"), KNOWN)
check("drifted money figure is blocked", reason is not None and b.grade == "blocked", reason)

b, reason = _validate(Block("experience", "Scaled a $12.5M finding into a ~$40M case.", [12], "verified"), KNOWN)
check("exact money figures pass", reason is None, reason)

b, reason = _validate(Block("experience", "Coverage across 98.4% of categorised spend.", [14], "verified"), KNOWN)
check("percentage from fact text passes", reason is None, reason)

b, reason = _validate(Block("experience", "Covered 41 of 44 categories.", [14], "verified"), KNOWN)
check("drifted count is blocked", reason is not None, reason)

b, reason = _validate(Block("experience", "Audited 31 of 44 categories.", [14], "verified"), KNOWN)
check("count present in fact metrics passes", reason is None, reason)

print("\nheadcount")
for claim in ["Led a team of four analysts.", "Managed a team across two regions.",
              "Had three direct reports."]:
    b, reason = _validate(Block("experience", claim, [20], "verified"), KNOWN)
    check(f"blocked: {claim!r}", reason is not None and b.grade == "blocked", reason)

b, reason = _validate(Block("experience", "Owned the analysis end to end.", [20], "verified"), KNOWN)
check("ownership language allowed", reason is None, reason)

print("\ntenure")
# The first live run against a real job description understated nine years of history
# as "6+ years", against a posting asking for 5+. Every existing guard passed it:
# _NUMBER matches currency and percentages, so a bare year count was invisible.
from datetime import date  # noqa: E402

roles = [
    SimpleNamespace(kind="role", date_from="Mar 2015", date_to="Mar 2018"),
    SimpleNamespace(kind="role", date_from="Apr 2018", date_to="Oct 2021"),
    SimpleNamespace(kind="role", date_from="Dec 2023", date_to="Present"),
]
years = experience_years(roles, today=date(2026, 8, 26))
check("years summed across roles, study gap excluded", years == 9.2, years)
check("an end-to-end span would have overstated it", years < 11.5, years)

overlap = [
    SimpleNamespace(kind="role", date_from="Jan 2020", date_to="Jan 2024"),
    SimpleNamespace(kind="role", date_from="Jan 2022", date_to="Jan 2023"),
]
check("overlapping roles are not double counted",
      experience_years(overlap, today=date(2026, 8, 26)) == 4.0,
      experience_years(overlap, today=date(2026, 8, 26)))
check("no roles gives no figure rather than zero", experience_years([]) is None)

for text, want in [
    ("with 6+ years delivering corporate treasury", [6.0]),
    ("nine years of banking and treasury experience", [9.0]),
    ("10 years' experience in FP&A", [10.0]),
    ("5 years ago", []),
    ("a 3 year programme", []),
    ("a ~$60M five-year value case", []),
]:
    check(f"tenure read from {text[:34]!r}", _tenure_claims(text) == want, _tenure_claims(text))

b, reason = _validate(
    Block("summary", "Treasury professional with 6+ years of experience.", [20], "inferred"),
    KNOWN, actual_years=9.8)
check("understated tenure is blocked", reason is not None and b.grade == "blocked", reason)

b, reason = _validate(
    Block("summary", "Treasury professional with 15 years of experience.", [20], "inferred"),
    KNOWN, actual_years=9.8)
check("overstated tenure is blocked", reason is not None and b.grade == "blocked", reason)

b, reason = _validate(
    Block("summary", "Treasury professional with 10 years of experience.", [20], "inferred"),
    KNOWN, actual_years=9.8)
check("a tenure claim within tolerance passes", reason is None, reason)

b, reason = _validate(
    Block("experience", "Built treasury dashboards.", [20], "verified"), KNOWN, actual_years=None)
check("no computed figure means no tenure check", reason is None, reason)

print("\nrender gate")
for label, block in [
    ("blocked grade", {"text": "x", "grade": "blocked", "fact_ids": [1], "accepted": True}),
    ("unaccepted inferred", {"text": "x", "grade": "inferred", "fact_ids": [1], "accepted": False}),
    ("unaccepted stretch", {"text": "x", "grade": "stretch", "fact_ids": [1], "accepted": False}),
    ("no fact_ids", {"text": "x", "grade": "verified", "fact_ids": [], "accepted": True}),
]:
    try:
        gate([block]); check(f"gate stops {label}", False, "allowed through")
    except BlockedContentError:
        check(f"gate stops {label}", True)

try:
    gate([{"text": "x", "grade": "inferred", "fact_ids": [1], "accepted": True}])
    check("gate allows accepted inferred", True)
except BlockedContentError as exc:
    check("gate allows accepted inferred", False, str(exc))

print("\npayload assembly")
facts = [
    SimpleNamespace(id=1, kind="name", text="Jane Doe", org=None, order_index=0,
                    date_from=None, date_to=None, parent_id=None, tags=[], metrics={}, verified=True),
    SimpleNamespace(id=2, kind="contact", text="a@b.com", org=None, order_index=0,
                    date_from=None, date_to=None, parent_id=None, tags=[], metrics={}, verified=True),
    SimpleNamespace(id=3, kind="role", text="Data Analyst", org="PI", order_index=0,
                    date_from="Dec 2023", date_to="Present", parent_id=None, tags=[], metrics={}, verified=True),
]
res = TailorResult(blocks=[
    Block("summary", "Nine years across finance and data.", [1], "verified"),
    Block("experience", "An accepted reframing.", [3], "inferred", org="PI", accepted=True),
    Block("experience", "An unaccepted reframing.", [3], "inferred", org="PI", accepted=False),
])
payload = to_payload(res, facts)
bullets = payload.experience[0].bullets if payload.experience else []
check("accepted inferred bullet included", "An accepted reframing." in bullets, bullets)
check("unaccepted inferred bullet excluded", "An unaccepted reframing." not in bullets, bullets)
check("name and contact pulled from facts",
      payload.name == "Jane Doe" and payload.contact == ["a@b.com"])

print("\nwhen the model returns no job title")
# A real Marsh posting came back with an empty title. That costs three visible things:
# the title line under his name, which an ATS weights, the export filename, and the role
# the fit score reports against. The posting names the job somewhere regardless.
from modules.extract import title_from_text  # noqa: E402

check("a labelled title line is read",
      title_from_text("Job Title: Lead Analyst, Treasury Risk\nLocation: Mumbai")
      == "Lead Analyst, Treasury Risk",
      title_from_text("Job Title: Lead Analyst, Treasury Risk\nLocation: Mumbai"))
check("a heading at the top is read",
      title_from_text("Manager, Procurement Analytics\nMumbai, hybrid. A GCC.")
      == "Manager, Procurement Analytics")
check("hiring language is read",
      title_from_text("We are hiring a Senior Data Analyst to support the UK business.")
      == "Senior Data Analyst")
check("it invents nothing when the posting names no role",
      title_from_text("Some prose with no role in it at all whatsoever.") == "")
check("and nothing from an empty posting", title_from_text("") == "")

print("\nno job disappears from the document")
# A real build came out with only the current employer on it. Tailor had written bullets
# for one role, and to_payload dropped every role with no bullets, so Halcyon Energy and
# HDFC Bank were deleted outright. Four years of banking and treasury became a hole the
# reader fills in unfavourably, and unexplained gaps are a documented rejection trigger.
from modules.tailor import _roles_block, to_payload, TailorResult, Block  # noqa: E402


class R:
    def __init__(self, i, org, text, kind="role", frm=None, to=None):
        self.id, self.org, self.text, self.kind = i, org, text, kind
        self.date_from, self.date_to = frm, to
        self.order_index, self.tags, self.metrics = i, [], {}
        self.verified = True


ROLES = [R(1, "Now Co", "Analyst", frm="Dec 2023"),
         R(2, "Then Co", "Treasury Analyst", frm="Apr 2018", to="Oct 2021"),
         R(3, "Long Ago Co", "Assistant Manager", frm="Mar 2015", to="Mar 2018")]

only_one = TailorResult(blocks=[
    Block(section="experience", text="Did a thing worth 4.5M.", fact_ids=[1],
          grade="verified", org="Now Co", accepted=True, order_index=0)])
payload = to_payload(only_one, ROLES, contact=["a@b.com"], name="X")
orgs = [role.org for role in payload.experience]
check("every employer is on the page", orgs == ["Now Co", "Then Co", "Long Ago Co"], orgs)
check("the one with bullets keeps them",
      len(payload.experience[0].bullets) == 1)
check("the ones without still carry their dates",
      payload.experience[1].dates == "Apr 2018 - Oct 2021", payload.experience[1].dates)
check("and say nothing rather than something invented",
      payload.experience[1].bullets == [])

block = _roles_block(ROLES)
check("the model is told to cover every role", block.count("\n- ") == 2, block)
# Sorting "Mar 2015" against "Dec 2023" as strings put March first, so the newest role
# was asked for one bullet and the oldest six.
check("newest first, by date and not by month name",
      block.index("Now Co") < block.index("Then Co") < block.index("Long Ago Co"), block)
check("and the current role carries the most",
      "Write 6 bullets" in block.split("\n")[0], block.split("\n")[0])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
