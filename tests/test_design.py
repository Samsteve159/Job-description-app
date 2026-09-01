"""House specs: his own writing rules, applied to the generated documents.

The risk here is a spec that reads too greedily. A style guide is mostly structural
advice, and a naive reader turns "Required: Experience, Education, Skills" into a list of
banned words that blocks every resume the app will ever write. So the extraction is
narrow, there is a list of words it may never ban, and both are tested.

    python3 tests/test_design.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import design  # noqa: E402
from modules.design import banned_words, read_rules, violations  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


SPEC = """
## 3. Hard structural constraints

**Forbidden:**
- Tables of any kind
- Multi-column layouts and sidebars
- Images, logos, icons

**Required:**
- Single column, top to bottom
- Standard section headings: Summary, Experience, Education, Skills

## 7. Experience

**Banned constructions:**
- "Responsible for", "Duties included", "Tasked with", "Helped with"
- Any bullet describing a duty rather than an outcome

## 9. Anti-patterns

- Filler adjectives: comprehensive, robust, cutting-edge, seamless, innovative
- Filler verbs: leverage, spearhead, orchestrate, drive, utilise

**Banned openings:** "Results-driven professional", "Highly motivated", "Seasoned"
"""

print("reading a spec")
banned = banned_words(SPEC)
for word in ("robust", "cutting-edge", "leverage", "spearhead", "responsible for",
             "highly motivated", "seasoned"):
    check(f"banned: {word!r}", word in banned, banned)

print("\nand not reading too much")
# The first version read every Forbidden block and came back with "tables of any kind",
# "single column", "experience" and "skills". Banning the word Experience would have
# blocked every resume the app writes.
for structural in ("tables of any kind", "single column", "top to bottom",
                   "multi-column layouts and sidebars"):
    check(f"structural rule is not a banned word: {structural!r}",
          structural not in banned, banned)
for required in ("summary", "experience", "education", "skills", "certifications"):
    check(f"a required section is never banned: {required!r}", required not in banned)

print("\ncatching it in the draft")
rules = read_rules(SPEC)
check("a clean bullet passes",
      violations("Scaled a $4.5M finding into a $60M five-year value case.", rules) == [])
check("a banned adjective is caught",
      "robust" in violations("Built robust pipelines.", rules))
# "leverage" as written let "leveraged" through, which is the same word doing the same job.
check("a banned verb is caught in every form it takes",
      "leverage" in violations("Leveraged the platform.", rules))
check("and in the gerund",
      "spearhead" in violations("Spearheading the migration.", rules))
check("a banned phrase is caught",
      "responsible for" in violations("Responsible for reporting.", rules))
check("a phrase is not inflected, because inflecting a phrase is guesswork",
      violations("Responsibility for reporting.", rules) == [])
check("no rules means no violations", violations("Anything at all.", None) == [])
check("empty rules means no violations", violations("Anything at all.", {}) == [])

print("\nthe real spec he wrote")
real = Path(__file__).resolve().parent.parent.parent / "resume_build_spec.md"
if real.exists():
    found = read_rules(real.read_text())["banned"]
    check("it yields a usable number of terms, not none and not everything",
          10 <= len(found) <= 40, len(found))
    check("including its filler verbs", "orchestrate" in found, found)
    check("and none of the sections it requires",
          not ({"experience", "skills", "education"} & set(found)), found)
else:
    print("  skip  the spec file is not on this machine")

print("\nrefusing what it cannot use")
class FakeDB:
    def query(self, *a, **k): raise AssertionError("should not reach the database")

for bad, why in ((""[:], "empty"), ("too short", "short")):
    try:
        design.save(FakeDB(), "resume", "x", bad)
        check(f"a {why} spec is refused", False, "no exception")
    except (ValueError, AssertionError) as exc:
        check(f"a {why} spec is refused", isinstance(exc, ValueError), str(exc)[:50])

try:
    design.save(FakeDB(), "poster", "x", "y" * 300)
    check("an unknown kind is refused", False)
except (ValueError, AssertionError) as exc:
    check("an unknown kind is refused", isinstance(exc, ValueError), str(exc)[:50])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
