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

from modules.tailor import Block, TailorResult, _validate, to_payload  # noqa: E402
from modules.render_docx import gate, BlockedContentError               # noqa: E402


def fact(id, text, metrics=None, kind="bullet", **kw):
    return SimpleNamespace(
        id=id, text=text, metrics=metrics or {}, kind=kind, tags=[], org=kw.get("org"),
        parent_id=kw.get("parent_id"), order_index=kw.get("order_index", 0),
        date_from=kw.get("date_from"), date_to=kw.get("date_to"), verified=True,
    )


KNOWN = {f.id: f for f in [
    fact(12, "Scaled a $12.5M unmatched-spend finding into a ~$80M five-year value case.",
         {"finding": "$12.5M", "value_case": "~$80M / 5 years"}),
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

b, reason = _validate(Block("experience", "Scaled a $12.5M finding into a ~$80M case.", [12], "verified"), KNOWN)
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
    SimpleNamespace(id=1, kind="name", text="Sameer Iyer", org=None, order_index=0,
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
      payload.name == "Sameer Iyer" and payload.contact == ["a@b.com"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
