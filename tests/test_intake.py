"""Reading a CV or notes file into the career record.

This is the one place a fabrication could be laundered into a source of truth. Every
claim in every generated document cites a fact, and the truth gate checks that the
citation exists, not that the fact is true. So the fact itself has to be his words, and
the tests that matter here are the ones proving nothing gets rewritten on the way in.

    python3 tests/test_intake.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import intake  # noqa: E402
from modules.intake import Candidate, UnreadableFile, propose, read  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


CV = """# Jane Doe
he@example.com | +61 400 000 000 | linkedin.com/in/someone
Page 1 of 2

## Experience
Dec 2023 - Present
- Built a spend categorisation pipeline covering 98.4% of categorised spend
- Delivered a $4.5M unmatched-spend finding into a five-year value case
- Automated reconciliation across four source systems

## Skills
SQL | Power BI | Python | UNSPSC taxonomy

## Certifications
- Certified Supply Chain Professional, APICS, 2024

## Education
Master of Business Analytics, University of Wollongong, 2023
"""

print("reading a file")
props = propose(CV)
texts = [c.text for c in props]

check("contact lines are not facts", not any("@example.com" in t for t in texts), texts[:3])
check("page numbers are not facts", not any(t.strip().startswith("Page 1") for t in texts))
check("a bare date range is not a fact", "Dec 2023 - Present" not in texts, texts)
check("headings are not facts", not any(t.lower() == "experience" for t in texts))

check("a real bullet survives",
      any("spend categorisation pipeline" in t for t in texts), texts)
check("bullet markers are stripped", not any(t.startswith("-") for t in texts), texts[:4])

print("\nclassifying")
kinds = {c.text: c.kind for c in props}
check("an achievement reads as a bullet",
      kinds.get("Automated reconciliation across four source systems") == "bullet")
check("a certification reads as a cert",
      any(k == "cert" for t, k in kinds.items() if "APICS" in t), kinds)
check("a degree reads as education",
      any(k == "education" for t, k in kinds.items() if "Wollongong" in t), kinds)
check("a skills line is split into separate skills",
      "SQL" in texts and "Power BI" in texts, [t for t in texts if len(t) < 20])
check("and each is a skill",
      all(kinds.get(s) == "skill" for s in ("SQL", "Power BI", "Python")), kinds)

# "Power BI" matched a credential pattern and came back as a certification, because the
# pattern listed product names. His own heading is the better evidence.
check("a tool under a Skills heading is not a certification",
      kinds.get("Power BI") == "skill", kinds.get("Power BI"))
check("but a real credential still is, wherever it sits",
      any(k == "cert" for t, k in kinds.items() if "Certified" in t), kinds)

print("\nnothing is rewritten")
# The whole safety argument. A model asked to tidy would turn "supported" into "led".
source_lines = {l.strip().lstrip("-* ").strip() for l in CV.splitlines()}
for c in props:
    if c.kind == "skill" and len(c.text) < 20:
        continue     # split out of a list line, still verbatim within it
    check(f"verbatim: {c.text[:38]!r}", c.text in " ".join(source_lines) or
          any(c.text == s for s in source_lines), c.text)

print("\nre-importing the same file")
class Fact:
    def __init__(self, text): self.text = text

existing = [Fact("Built a spend categorisation pipeline covering 98.4% of categorised spend")]
again = propose(CV, existing)
dup = [c for c in again if not c.is_new]
check("a fact already on record is flagged", len(dup) >= 1, [c.text[:40] for c in dup])
check("and is off by default", all(c.duplicate_of for c in dup))
check("the rest still come through", any(c.is_new for c in again))

twice = propose(CV + CV)
new_twice = [c for c in twice if c.is_new]
check("the same line twice in one file is caught once",
      len(new_twice) == len([c for c in propose(CV) if c.is_new]),
      (len(new_twice), len(propose(CV))))

print("\nrefusing files it cannot read")
for name, why in [("cv.docx", "format"), ("cv.rtf", "format")]:
    p = Path("data/output") / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    try:
        read(p)
        check(f"{name} is refused", False, "no exception")
    except UnreadableFile as exc:
        check(f"{name} is refused", "format" in str(exc) or "not a format" in str(exc), str(exc))
    p.unlink()

try:
    read(Path("data/output/nope.pdf"))
    check("a missing file is refused", False)
except UnreadableFile:
    check("a missing file is refused", True)

md = Path("data/output/_notes.md")
md.write_text("## Projects\n- Rebuilt the forecasting model end to end in Python\n")
check("markdown is read", "forecasting model" in read(md))
check("and proposes from it", any("forecasting model" in c.text for c in propose(read(md))))
md.unlink()

print("\nwriting is opt in")
check("accept takes only what it is given", intake.accept([]) == 0)

# A skill is a complete statement at three characters. The twelve-character floor was
# written for gap answers, where one word is a claim with nothing in it, and it silently
# refused the shortest true things on a CV.
import json  # noqa: E402
from modules.gaps import add_fact  # noqa: E402

seed = Path("data/output/_seed.json")
seed.write_text(json.dumps({"facts": []}))
add_fact("Rust", kind="skill", seed_file=seed)
check("a three-letter skill is allowed",
      json.loads(seed.read_text())["facts"][0]["text"] == "Rust")
for bad_kind, value in (("bullet", "Did stuff"), ("education", "BSc")):
    try:
        add_fact(value, kind=bad_kind, seed_file=seed)
        check(f"a short {bad_kind} is still refused", False, value)
    except ValueError:
        check(f"a short {bad_kind} is still refused", True)
seed.unlink()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
