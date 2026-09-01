"""The cover letter, and the fabrication a resume never had to worry about.

A resume makes claims about him, and the truth gate already covers those. A cover letter
makes claims about *them* as well, and no citation check would ever notice: a paragraph
can cite a perfectly real fact about his SQL work while opening with "I have long admired
your commitment to innovation", which invents a fact about a company and a feeling about
it in one sentence.

So the tests that matter here are `unverifiable_company_claims`. The rest is the resume's
machinery reused, plus a cliche filter, because every model reaches for "I am writing to
apply" and a prompt saying "do not" is a suggestion.

    python3 tests/test_cover.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.cover import (CLICHES, MAX_WORDS, MIN_WORDS, CoverLetter,  # noqa: E402
                           found_cliches, unverifiable_company_claims)
from modules.render_docx import (BlockedContentError, CoverPayload,  # noqa: E402
                                 audit, render_cover)
from modules.tailor import Block  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


JD = ("Manager, Procurement Analytics at Acme. We run spend analytics for a global "
      "procurement organisation and are rebuilding our reporting layer in Power BI. "
      "Required: advanced SQL, UNSPSC taxonomy, supplier master data.")

print("cliches, which every model reaches for")
for text, expect in [
    ("I am writing to apply for the role of Manager.", True),
    ("I am passionate about data and a proven team player.", True),
    ("I would like to apply for this position.", True),
    ("Thank you for considering my application.", True),
    ("Closed a $1.8M reporting variance and rebuilt the reconciliation logic.", False),
    ("I rebuilt the reconciliation so the variance could not recur.", False),
]:
    hit = bool(found_cliches(text))
    check(f"{'caught' if expect else 'allowed'}: {text[:46]!r}", hit == expect,
          found_cliches(text))

check("the list covers the openings that mark a letter as one of forty",
      "i am writing to apply" in CLICHES and "hit the ground running" in CLICHES)
check("case and spacing do not hide one",
      found_cliches("I  Am   Writing To Apply for this") != [])

print("\nclaims about the employer")
for text, expect in [
    ("I have long admired your company's leadership in digital transformation.", True),
    ("Your organisation's reputation for innovation is world-class.", True),
    ("I am drawn to your team's culture of excellence.", True),
    ("Your posting describes rebuilding the reporting layer in Power BI, which is "
     "exactly the work I have done.", False),
    ("Your requirement for UNSPSC taxonomy and supplier master data matches my work.",
     False),
    ("I built the SQL models behind a $60M value case.", False),
    ("I closed a reporting variance of $1.8M.", False),
]:
    flagged = bool(unverifiable_company_claims(text, JD))
    check(f"{'flagged' if expect else 'allowed'}: {text[:50]!r}", flagged == expect,
          unverifiable_company_claims(text, JD))

check("praise grounded in the posting survives",
      not unverifiable_company_claims(
          "Your team runs spend analytics for a global procurement organisation.", JD))
check("praise grounded in nothing does not",
      unverifiable_company_claims(
          "Your firm is a renowned leader with a world-class brand.", JD) != [])
check("an empty string is safe", unverifiable_company_claims("", JD) == [])
check("a missing posting does not crash it",
      unverifiable_company_claims("Your company is wonderful and innovative.", "") != [])

print("\ncounting words, and when to complain about them")
letter = CoverLetter(paragraphs=[
    Block(section="cover", text="one two three four five", grade="verified"),
    Block(section="cover", text="six seven eight", grade="inferred", accepted=False),
    Block(section="cover", text="nine ten", grade="blocked"),
])
check("what renders counts only what is verified or ticked",
      letter.word_count == 5, letter.word_count)
check("the draft count includes what is still amber",
      letter.draft_word_count == 8, letter.draft_word_count)
check("neither counts what was blocked",
      "nine" not in " ".join(p.text for p in letter.usable))

letter.paragraphs[1].accepted = True
check("ticking a paragraph moves it into what renders",
      letter.word_count == 8, letter.word_count)
check("needs_review lists only the reaching ones",
      len(letter.needs_review) == 1, letter.needs_review)
check("bounds are the ones a reader actually tolerates",
      MIN_WORDS >= 60 and MAX_WORDS <= 400, (MIN_WORDS, MAX_WORDS))

print("\nrendering")
out = Path(tempfile.mkdtemp(prefix="jobapp-cover-")) / "c.docx"
payload = CoverPayload(
    name="Jane Doe", contact=["a@b.com", "Mumbai, India"],
    role="Manager, Procurement Analytics", company="Acme",
    date_line="27 August 2026",
    paragraphs=["Closed a $1.8M reporting variance, and rebuilt the reconciliation "
                "logic so it could not recur.",
                "Your posting describes the same problem."])
path = render_cover(payload, out)
report = audit(path)
check("the letter is ATS clean", report["ok"], report["problems"])
text = str(report["text"])
check("his name is at the top", text.split(chr(10))[0] == "Jane Doe", text[:40])
check("the contact line survives", "a@b.com" in text)
check("the role and company are named", "Manager, Procurement Analytics" in text
      and "Acme" in text)
check("the greeting and sign off are there",
      "Dear Hiring Manager," in text and "Kind regards," in text)
check("both paragraphs are present", "1.8M" in text and "same problem" in text)
check("nothing non-ASCII reaches the page",
      all(ord(c) < 128 for c in text),
      [f"U+{ord(c):04X}" for c in text if ord(c) > 127])

dirty = CoverPayload(name="X", contact=[],
                     paragraphs=["An AI‑agent pipeline — built well…"])
report = audit(render_cover(dirty, out))
check("dirty punctuation is normalised on the way in",
      all(ord(c) < 128 for c in str(report["text"])))

for empty in ([], [""], ["   "]):
    try:
        render_cover(CoverPayload(name="X", contact=[], paragraphs=empty), out)
        check(f"an empty letter is refused ({empty})", False, "no exception")
    except BlockedContentError:
        check(f"an empty letter is refused ({empty!r})", True)

out.unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
