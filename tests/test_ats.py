"""The ATS gate, tested against the failures that actually keep resumes from reaching people.

Every case here is a real-world ATS rejection mode, not a synthetic one:
  contact block not extractable   the single most common cause of a dropped application
  dates a parser cannot read      tenure is computed from these, so garbage dates distort it
  keyword floor                   what the filter literally screens on
  format hostility                tables and text boxes get flattened or dropped

    python3 tests/test_ats.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.ats import AtsBlocked, check, gate, simulate_parse  # noqa: E402
from modules.render_docx import ResumePayload, Role, render_resume  # noqa: E402

OUT = Path("data/output")
MUST = ["spend analysis", "sql", "unspsc"]

passed = failed = 0


def ok(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def build(name="Jane Doe", contact=None, roles=None, **kw) -> Path:
    payload = ResumePayload(
        name=name,
        contact=contact if contact is not None else ["jane.doe@example.com", "Mumbai, India"],
        summary=kw.get("summary", "Spend analysis, SQL and UNSPSC categorisation."),
        skills=kw.get("skills", ["Spend Analysis", "SQL", "UNSPSC"]),
        experience=roles if roles is not None else [
            Role("Data Analyst", "Purchasing Index", "Dec 2023 - Present",
                 bullets=["Ran spend analysis in SQL against UNSPSC categories."])],
        education=["Master of Business Analytics, University of Wollongong, 2023"],
    )
    return render_resume(payload, OUT / "_t.docx")


print("parser simulation")
p = simulate_parse("Jane Doe\njane.doe@example.com | +91 90000 00000 | Mumbai\n\nEXPERIENCE")
ok("name read from the first line", p.name == "Jane Doe", p.name)
ok("email extracted", p.email == "jane.doe@example.com", p.email)
ok("phone extracted", p.phone is not None, p.phone)

p = simulate_parse("Curriculum Vitae 2026\nsameer@x.com\n")
ok("a heading is not mistaken for a name", p.name is None or "Vitae" not in (p.name or ""), p.name)

for good in ["Dec 2023 - Present", "Apr 2018 - Oct 2021", "2018 - 2021", "03/2015 - 03/2018"]:
    ok(f"date range parsed: {good!r}", bool(simulate_parse(f"x\n{good}").date_ranges))
ok("prose date not parsed", not simulate_parse("x\nsometime last year").date_ranges)

print("\nblocking failures")
f = build(contact=["Mumbai, India"])
r = check(f, MUST, expect_roles=1)
ok("missing email blocks", any("no email" in b for b in r.blocking), r.blocking)
ok("report says BLOCKED", not r.passed)

f = build(roles=[Role("Analyst", "Co", "sometime last year", bullets=["Did spend analysis in SQL on UNSPSC."])])
r = check(f, MUST, expect_roles=1)
ok("unparseable dates block", any("dates:" in b for b in r.blocking), r.blocking)

f = build(summary="Generalist.", skills=["Excel"],
          roles=[Role("Analyst", "Co", "Dec 2023 - Present", bullets=["Worked on things."])])
r = check(f, MUST, expect_roles=1)
ok("keyword floor blocks", any("keywords:" in b for b in r.blocking), r.blocking)
ok("missing_must lists the gaps", set(r.missing_must()) == set(MUST), r.missing_must())

print("\npassing case")
f = build()
r = check(f, MUST, nice_keywords=["python"], expect_roles=1)
ok("well-formed resume passes", r.passed, r.blocking)
ok("score is high", r.score >= 85, r.score)
ok("all must-haves found", r.must_coverage == 1.0, r.must)
ok("missing nice-to-have warns but does not block",
   any("nice-to-have" in w for w in r.warnings) and r.passed, r.warnings)

print("\ncharacter hygiene")
# The first live run leaked U+2011 NON-BREAKING HYPHEN into four blocks and audit()
# passed the file clean, because it only screened for em and en dashes.
from modules.render_docx import audit, plain_text  # noqa: E402

ok("em dash becomes a comma", plain_text("cost\u2014recovery") == "cost, recovery",
   plain_text("cost\u2014recovery"))
ok("non-breaking hyphen becomes a hyphen", plain_text("AI\u2011agent") == "AI-agent",
   plain_text("AI\u2011agent"))
ok("curly apostrophe flattened", plain_text("client\u2019s") == "client's")
ok("non-breaking space flattened", plain_text("a\u00a0b") == "a b")
ok("ellipsis expanded", plain_text("wait\u2026") == "wait...")
ok("plain ASCII is left alone", plain_text("Spend analysis, SQL.") == "Spend analysis, SQL.")

f = build(summary="Ran AI\u2011agent spend analysis \u2014 in SQL against UNSPSC\u2026")
rendered = str(audit(f)["text"])
ok("dirty characters do not reach the page",
   all(ord(c) < 128 for c in rendered),
   [f"U+{ord(c):04X}" for c in rendered if ord(c) > 127])
ok("a normalised file still passes the audit", audit(f)["ok"], audit(f)["problems"])

print("\ngate")
try:
    gate(check(build(contact=["Mumbai"]), MUST, expect_roles=1))
    ok("gate raises on a blocked report", False, "no exception")
except AtsBlocked as exc:
    ok("gate raises on a blocked report", True)
    ok("the reason is in the message", "email" in str(exc), str(exc)[:60])

try:
    gate(check(build(), MUST, expect_roles=1))
    ok("gate allows a passing report", True)
except AtsBlocked as exc:
    ok("gate allows a passing report", False, str(exc)[:60])

(OUT / "_t.docx").unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
