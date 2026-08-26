"""The gap closer, and the direction the override is allowed to run in.

The truth gate stops a model inventing experience. It had also started stopping Sameer
from recording true things about his own career: "risk" appears in none of his facts, so
every risk keyword reads as a gap, on the record of somebody who ran multi-banking
treasury operations for three and a half years.

The override therefore runs one way only. It never lets a keyword onto the page
unevidenced. It asks a question, and what he types becomes the evidence. So the tests
that matter are the ones proving it cannot be turned around: closeability is arithmetic
rather than a model's opinion, a term nothing sits near gets no question at all, and an
answer is written to the JSON that survives a re-seed rather than to a row that does not.

No model calls. `suggest_one` is exercised through its deterministic half.

    python3 tests/test_gaps.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import gaps  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def fact(id, text, org=None, tags=None, kind="bullet", verified=True, order=0):
    return SimpleNamespace(id=id, text=text, org=org, tags=tags or [], kind=kind,
                           verified=verified, order_index=order, metrics={})


FACTS = [
    fact(1, "Treasury Analyst (Multi-Banking Operations)", org="Puma Energy",
         kind="role", tags=["treasury", "banking operations"], order=1),
    fact(2, "Tracked e-banking and treasury metrics across a multi-country bank "
            "account structure, feeding funding strategy.", org="Puma Energy"),
    fact(3, "Built treasury dashboards used for regional reporting.", org="Puma Energy"),
    fact(4, "Ran spend analysis across the distributor network.", org="Purchasing Index",
         tags=["spend analytics"]),
    fact(5, "Data Analyst", org="Purchasing Index", kind="role", order=0),
    fact(6, "Certified in treasury risk management.", verified=False),
]

print("adjacency, which is arithmetic and not an opinion")
near = gaps.adjacency("treasury risk", FACTS)
check("facts sharing a word are found", len(near) >= 3, len(near))
check("the most overlapping fact comes first",
      "treasury" in (near[0].text or "").lower() or near[0].org == "Puma Energy", near[0].text)
check("an unverified fact never counts as adjacent",
      all(f.id != 6 for f in near), [f.id for f in near])
check("a term with nothing near it finds nothing",
      gaps.adjacency("kubernetes", FACTS) == [], gaps.adjacency("kubernetes", FACTS))
check("an empty term finds nothing", gaps.adjacency("", FACTS) == [])

print("\ncloseability")
check("a term the record touches often is likely",
      gaps.closeability("treasury risk", FACTS) == "likely",
      gaps.closeability("treasury risk", FACTS))
check("a term one fact touches is a maybe",
      gaps.closeability("spend forecasting", FACTS) == "maybe",
      gaps.closeability("spend forecasting", FACTS))
check("a term nothing touches is unlikely",
      gaps.closeability("kubernetes", FACTS) == "unlikely")
check("it is computed from the record, so it cannot be talked up",
      gaps.closeability("agile ceremonies", FACTS) == "unlikely")

print("\nrefusing to ask")
s = gaps.suggest_one("kubernetes", FACTS)
check("an unlikely term gets no question", s.question == "", s.question)
check("and no draft to react to", s.draft == "", s.draft)
check("it says so plainly instead", "not a gap to close" in s.honest_read, s.honest_read)
check("and is not marked worth asking", not s.worth_asking)
check("no model call is made for it", s.nearby == [], s.nearby)

print("\nranking")
order = []
for term in ("kubernetes", "treasury risk", "spend forecasting"):
    order.append((term, gaps.closeability(term, FACTS)))
ranked = sorted(order, key=lambda kv: {"likely": 0, "maybe": 1, "unlikely": 2}[kv[1]])
check("closeable gaps rank above unclosable ones",
      ranked[0][0] == "treasury risk" and ranked[-1][0] == "kubernetes", ranked)

print("\nroles a fact can attach to")
check("roles come back in resume order",
      gaps.roles(FACTS) == ["Purchasing Index", "Puma Energy"], gaps.roles(FACTS))

print("\nwriting an answer back to the source of truth")
tmp = Path(tempfile.mkdtemp(prefix="jobapp-gaps-")) / "facts.json"
tmp.write_text(json.dumps({
    "meta": {"owner": "test"},
    "facts": [
        {"kind": "role", "org": "Puma Energy", "text": "Treasury Analyst",
         "children": [{"kind": "bullet", "text": "An existing bullet."}]},
        {"kind": "skill", "text": "SQL"},
    ],
}), encoding="utf-8")

gaps.add_fact("Monitored FX exposure across the multi-country account structure.",
              parent_org="Puma Energy", tags=["treasury risk"], seed_file=tmp)
saved = json.loads(tmp.read_text(encoding="utf-8"))
role = saved["facts"][0]
check("it lands under the right employer", len(role["children"]) == 2, role["children"])
check("the existing bullets are untouched",
      role["children"][0]["text"] == "An existing bullet.")
check("it is marked verified, because he typed it",
      role["children"][1]["verified"] is True)
check("and stamped so it can be found again",
      role["children"][1]["source"] == "gap closer")
check("the keyword is carried as a tag",
      "treasury risk" in role["children"][1]["tags"])

gaps.add_fact("A standalone thing he did.", seed_file=tmp)
saved = json.loads(tmp.read_text(encoding="utf-8"))
check("a fact with no employer goes to the top level", len(saved["facts"]) == 3)
check("meta survives the rewrite", saved["meta"]["owner"] == "test")

for bad, why in [("", "empty"), ("   ", "whitespace"), ("short", "too short")]:
    try:
        gaps.add_fact(bad, seed_file=tmp)
        check(f"{why} input is refused", False, "no exception")
    except ValueError:
        check(f"{why} input is refused", True)

try:
    gaps.add_fact("A real enough sentence here.", parent_org="Nowhere Ltd", seed_file=tmp)
    check("an unknown employer is refused", False, "no exception")
except ValueError:
    check("an unknown employer is refused", True)

before = tmp.read_text(encoding="utf-8")
try:
    gaps.add_fact("x", seed_file=tmp)
except ValueError:
    pass
check("a refused write leaves the file untouched",
      tmp.read_text(encoding="utf-8") == before)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
