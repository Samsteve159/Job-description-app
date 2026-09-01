"""fetch_jd, tested on the thing it exists to prevent.

Parsing HTML is the easy half. The half that matters is refusing to return a login wall,
a cookie interstitial or a bot check as though it were a job description, because
`extract` will happily turn any of those into a confident and entirely fictional set of
requirements, and nothing downstream would notice.

    python3 tests/test_fetch_jd.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.fetch_jd import (blocked_reason, clean, looks_like_a_job,  # noqa: E402
                              normalise_url, _densest_block, _Text)

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


REAL_JD = (
    "We are looking for a Manager, Procurement Analytics to join our Mumbai team. "
    "About the role: you will own spend analytics end to end. "
    "Responsibilities include building dashboards, running category analysis and "
    "presenting savings cases to senior stakeholders. "
    "Qualifications: 6+ years of experience in spend analytics, advanced SQL, and "
    "hands-on work with UNSPSC taxonomy. Requirements also include Power BI. "
    "What you get: a hybrid working pattern and a strong benefits package. "
)

print("host blocking")
for url, want in [
    ("https://www.linkedin.com/jobs/view/1", True),
    ("LINKEDIN.COM/jobs/1", True),
    ("http://in.linkedin.com/jobs/2", True),
    ("naukri.com/job/456", True),
    ("https://www.glassdoor.com/job/1", True),
    ("https://careers.northwindbank.com/job/1", False),
    ("notlinkedin.com/x", False),
]:
    got = blocked_reason(url) is not None
    check(f"{'blocked' if want else 'allowed'}: {url[:38]}", got == want, got)

check("linkedin's reason names HTTP 999",
      "999" in (blocked_reason("linkedin.com/jobs/1") or ""))
check("a scheme is added before the host is read",
      normalise_url("naukri.com/x") == "https://naukri.com/x")
check("an existing scheme is left alone",
      normalise_url("http://x.com") == "http://x.com")
check("empty stays empty rather than becoming https://", normalise_url("") == "")

print("\nrefusing walls")
check("a real posting is accepted", looks_like_a_job(REAL_JD)[0], looks_like_a_job(REAL_JD))
for wall in [
    "Please sign in to continue to view this job. " * 20,
    "Verify you are human before continuing to the job posting page. " * 20,
    "Access denied. You do not have permission to view this job listing page. " * 20,
    "Please enable JavaScript to view this job posting on our careers site. " * 20,
]:
    ok, why = looks_like_a_job(wall)
    check(f"refused: {wall[:38].strip()!r}", not ok, why)

ok, why = looks_like_a_job("Short.")
check("too-short input is refused", not ok, why)
ok, why = looks_like_a_job("The weather in Mumbai is humid this week. " * 30)
check("prose with no job markers is refused", not ok, why)

print("\nextraction")
html = """<html><head><title>Manager, Procurement Analytics</title>
<style>.x{color:red}</style><script>var a=1;</script></head>
<body><nav>Home Jobs About Contact</nav>
<div><p>%s</p></div>
<footer>Copyright 2026. Privacy. Terms.</footer></body></html>""" % REAL_JD
parser = _Text()
parser.feed(html)
text = parser.text()
check("title captured", parser.title == "Manager, Procurement Analytics", parser.title)
check("script contents dropped", "var a=1" not in text, text[:60])
check("style contents dropped", "color:red" not in text, text[:60])
check("nav dropped", "Home Jobs About" not in text, text[:60])
check("the description survived", "spend analytics end to end" in text)

dense = _densest_block("Home\nJobs\n" + REAL_JD + "\nPrivacy\nTerms\nCookies")
check("boilerplate trimmed from the dense block",
      "Privacy" not in dense and "spend analytics" in dense, dense[:70])

print("\ncleaning")
check("carriage returns normalised", "\r" not in clean("a\r\nb"))
check("entities unescaped", clean("R&amp;D") == "R&D", clean("R&amp;D"))
check("non-breaking spaces flattened", clean("a\xa0b") == "a b", repr(clean("a\xa0b")))
check("blank line runs collapsed", clean("a\n\n\n\nb") == "a\n\nb", repr(clean("a\n\n\n\nb")))
check("clean is safe on empty input", clean("") == "")
check("clean is safe on None", clean(None) == "")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
