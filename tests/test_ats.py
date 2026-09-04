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
        headline=kw.get("headline", ""),
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

p = simulate_parse("Curriculum Vitae 2026\njane@x.com\n")
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

print("\nthe two things a filter and a recruiter both read first")
# A parser weights the job title, and a recruiter reads the filename before opening
# anything. Neither was being written: the document had no title line and every export
# was called "package-18.docx".
from modules.render_docx import export_name  # noqa: E402

f = build(headline="Lead Treasury Analyst")
lines = str(audit(f)["text"]).splitlines()
ok("the name is still the first line a parser sees", lines[0].strip() == "Jane Doe", lines[0])
ok("their job title sits directly under it", lines[1].strip() == "Lead Treasury Analyst", lines[1])
ok("contact still follows, so extraction is unaffected", "@" in lines[2], lines[2])

r = check(f, MUST, expect_roles=1)
ok("a headline does not break the parser simulation", r.passed, r.blocking)
ok("the name is not confused with the headline",
   simulate_parse("\n".join(lines)).name == "Jane Doe",
   simulate_parse("\n".join(lines)).name)

f2 = build()
ok("no headline given, none written",
   str(audit(f2)["text"]).splitlines()[1].strip().startswith("jane.doe@"),
   str(audit(f2)["text"]).splitlines()[1])

ok("the file on disk is named for a person and a role",
   export_name("Jane Doe", "Lead Treasury Analyst", 18)
   == "Jane Doe - Lead Treasury Analyst (18).docx",
   export_name("Jane Doe", "Lead Treasury Analyst", 18))
ok("the download drops the id, which means nothing to a recruiter",
   export_name("Jane Doe", "Lead Treasury Analyst")
   == "Jane Doe - Lead Treasury Analyst.docx",
   export_name("Jane Doe", "Lead Treasury Analyst"))
ok("a cover letter says so in the name",
   export_name("Jane Doe", "Analyst", 3, kind="Cover Letter")
   == "Jane Doe - Analyst - Cover Letter (3).docx",
   export_name("Jane Doe", "Analyst", 3, kind="Cover Letter"))
ok("characters a filesystem or a mail client would choke on are dropped",
   export_name("Jane Doe", "Sr. Analyst / FP&A (Mumbai)", 4)
   == "Jane Doe - Sr. Analyst FP&A Mumbai (4).docx",
   export_name("Jane Doe", "Sr. Analyst / FP&A (Mumbai)", 4))
ok("a missing title still gives a usable name",
   export_name("Jane Doe", "", 9) == "Jane Doe (9).docx",
   export_name("Jane Doe", "", 9))
ok("a very long title is trimmed rather than refused",
   len(export_name("Jane Doe", "Manager " * 30, 1)) < 100,
   len(export_name("Jane Doe", "Manager " * 30, 1)))

print("\nthe shape of the document, after reviewing one he wrote himself")
from modules.render_docx import Role as R  # noqa: E402
from modules.keywords import group_skills  # noqa: E402

# Three labelled rows, not one run of twenty-four terms. This is where a filter looks
# hardest and where a human's eye lands second, and a single pipe-separated line is
# unreadable to one and undifferentiated to the other.
rows = dict(group_skills(["SQL", "Power BI", "stakeholder management",
                          "root cause analysis", "Python", "cost reduction"]))
ok("skills are grouped by what they are", len(rows) == 3, list(rows))
ok("tools land in the technical row", "SQL" in rows["Data & Technical"], rows)
ok("methods land in the process row",
   "root cause analysis" in rows["Program & Process"], rows)
ok("people work lands in its own row",
   "stakeholder management" in rows["Stakeholder & Delivery"], rows)
# Losing a real skill to a tidy layout would be the wrong trade.
kept = sum(len(v) for v in rows.values())
ok("nothing is dropped to make the rows tidy", kept == 6, kept)
ok("an unmatched term still appears",
   any("Esperanto" in v for v in dict(group_skills(["Esperanto"])).values()),
   group_skills(["Esperanto"]))

# A study period sits in the experience run as a dated entry. Moving the education
# section above experience was the first attempt and reads as an odd ordering; a reader
# who meets the gap and its reason in one glance never forms the doubt.
payload = ResumePayload(
    name="Jane Doe", contact=["jane@x.com"],
    summary="Spend analysis, SQL and UNSPSC categorisation.",
    skill_groups=[("Data & Technical", ["SQL", "UNSPSC"])],
    experience=[
        R("Data Analyst", "Now Co", "Dec 2023 - Present", bullets=["Ran spend analysis in SQL against UNSPSC."]),
        R("Full-time postgraduate study", "A University", "Oct 2021 - Dec 2023", is_study=True),
        R("Treasury Analyst", "Then Co", "Apr 2018 - Oct 2021", bullets=["Built treasury dashboards."]),
    ],
    education=["Master of Business Analytics, A University, 2023"])
path = render_resume(payload, OUT / "_shape.docx")
text = str(audit(path)["text"])
lines = [l.strip() for l in text.splitlines() if l.strip()]

ok("the study entry is in the experience run",
   "Full-time postgraduate study" in text)
ok("and sits between the two roles it explains",
   lines.index("Data Analyst | Now Co")
   < lines.index("Full-time postgraduate study | A University")
   < lines.index("Treasury Analyst | Then Co"))
ok("education still has its own section at the end",
   lines.index("EDUCATION") > lines.index("PROFESSIONAL EXPERIENCE"))
ok("the skills row is labelled", "Data & Technical: SQL" in text, text[:200])
# The middle dot his own PDF uses reads better and is refused here, because a parser that
# mangles it turns a whole skills row into one unmatched token.
ok("and separated with something a parser cannot mangle",
   all(ord(c) < 128 for c in text), [c for c in text if ord(c) > 127][:4])
ok("headings match the convention a parser knows",
   all(h in text for h in ("SUMMARY", "CORE SKILLS", "PROFESSIONAL EXPERIENCE")))

report = check(path, ["sql", "unspsc"], expect_roles=0)
ok("and the whole thing still clears the parser", report.passed, report.blocking)
(OUT / "_shape.docx").unlink(missing_ok=True)

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

print("\nhis own formatting rules")

import zipfile  # noqa: E402
from docx import Document as _Doc  # noqa: E402
from modules import house  # noqa: E402
from modules.render_docx import audit as _audit  # noqa: E402

path = build(summary="Rebuilt the taxonomy for a national retail client using SQL.")
with zipfile.ZipFile(path) as zf:
    doc_xml = zf.read("word/document.xml").decode()
ok("body text is justified", 'w:val="both"' in doc_xml,
   "he asked for this explicitly and overrode the advice against it")
ok("and the document still clears the parser", _audit(path)["ok"])
ok("a client descriptor pulls in the withheld line",
   house.WITHHELD_LINE in _audit(path)["text"])

plain = build(summary="Wrote SQL against the spend cube.",
              roles=[Role("Data Analyst", "Purchasing Index", "Dec 2023 - Present",
                          bullets=["Ran spend analysis in SQL."])])
ok("and a document naming no engagement does not carry it",
   house.WITHHELD_LINE not in _audit(plain)["text"])

# The footer rule used to be "never". It is now "not contact details", because a name and
# a page number are already in the body and losing them costs nothing.
long_bullet = ("Rebuilt the category taxonomy across 40,000 line items and held it "
               "through two quarterly refreshes without a break in the series. ")
two_pager = build(roles=[Role("Data Analyst", "Purchasing Index", "Dec 2023 - Present",
                              bullets=[long_bullet] * 34)])
with zipfile.ZipFile(two_pager) as zf:
    footers = [n for n in zf.namelist()
               if n.startswith("word/footer") and n.endswith(".xml")]
    footer_xml = zf.read(footers[0]).decode() if footers else ""
ok("a two-pager gets a footer", bool(footers))
ok("it carries the name and a live page number",
   "Jane Doe" in footer_xml and "PAGE" in footer_xml, footer_xml[:80])
ok("and that footer is not held against it", _audit(two_pager)["ok"],
   str(_audit(two_pager)["problems"]))

one_pager = build(roles=[Role("Data Analyst", "Purchasing Index", "Dec 2023 - Present",
                              bullets=["Ran spend analysis in SQL."])])
with zipfile.ZipFile(one_pager) as zf:
    ok("a one-pager gets none",
       not any(n.startswith("word/footer") and n.endswith(".xml") for n in zf.namelist()))

# What the blanket ban was actually protecting, which must still be caught.
doc = _Doc()
doc.add_paragraph("Body")
doc.sections[0].footer.paragraphs[0].text = "jane@example.com  +61 400 000 000"
leaky = OUT / "_leak.docx"
doc.save(str(leaky))
report = _audit(leaky)
ok("contact details in a footer are still refused", not report["ok"])
ok("and the message names what would be lost",
   any("contact details" in p for p in report["problems"]), str(report["problems"]))
leaky.unlink(missing_ok=True)

(OUT / "_t.docx").unlink(missing_ok=True)
print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
