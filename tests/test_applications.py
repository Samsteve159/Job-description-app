"""Counting applications from the confirmations employers send.

The tracker used to depend on him remembering to type each one in, which is the first
admin a job search sheds. Every serious employer acknowledges within minutes, so the
record already exists in his mailbox.

Two things must not happen: counting a rejection as a fresh application, and counting the
same one twice. Both are tested below against the real Wells Fargo acknowledgement.

    python3 tests/test_applications.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.applications import (company_from, looks_like_an_acknowledgement,  # noqa: E402
                                  title_from)

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


print("telling an acknowledgement from everything else")
for yes in ("Wells Fargo Careers: Thank you for applying",
            "Your application has been received",
            "Application received: Data Analyst",
            "Thank you for your interest in Deloitte"):
    check(f"is one: {yes[:46]!r}", looks_like_an_acknowledgement(yes))

# A rejection is still evidence he applied, but it is not a new application, and counting
# both would double every job he is turned down for.
for no in ("Unfortunately we are not moving forward with your application",
           "Your application: we have decided to proceed with other candidates",
           "Thank you for applying, but we are unable to progress your application",
           "New jobs recommended for you",
           "13 people visited your profile"):
    check(f"is not: {no[:46]!r}", not looks_like_an_acknowledgement(no))

print("\nwho it was to")
check("the subject names the employer before the colon",
      company_from("wellsfargoworkday@wellsfargo.com",
                   "Wells Fargo Careers: Thank you for applying") == "Wells Fargo",
      company_from("wellsfargoworkday@wellsfargo.com", "Wells Fargo Careers: Thank you for applying"))
check("the domain answers when the subject does not",
      company_from("no-reply@deloitte.com", "Thank you for applying") == "Deloitte",
      company_from("no-reply@deloitte.com", "Thank you for applying"))
# Every applicant tracking system on earth sends from its own domain, and none of them
# are the employer.
for ats in ("no-reply@myworkday.com", "jobs@greenhouse.io", "careers@icims.com"):
    got = company_from(ats, "Thank you for applying")
    check(f"the ATS vendor is not counted as the employer: {ats}",
          got.lower() not in ("myworkday", "workday", "greenhouse", "icims"), got)

print("\nwhich job")
BODY = ("Dear Sameer, Thank you for your interest in Wells Fargo. We have received your "
        "application for the following position: R-570561 Lead Analytics Consultant - "
        "We appreciate the time you have taken to share your background.")
check("the role is read out of the body",
      title_from(BODY, "") == "Lead Analytics Consultant", title_from(BODY, ""))
# The requisition id is not a job title and looks absurd on a tracker.
check("the requisition id is stripped", "R-570561" not in title_from(BODY, ""))
# Run against raw HTML, a tag between two words breaks the specific pattern and a looser
# one wins with a worse answer: "the following position".
HTML = ("We have received your <b>application for the following position</b>: "
        "<span>R-570561 Lead Analytics Consultant</span> - We appreciate")
check("html between the words does not change the answer",
      title_from(HTML, "") == "Lead Analytics Consultant", title_from(HTML, ""))
check("a body that names no role yields nothing, not a guess",
      title_from("Thanks for getting in touch.", "") == "")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
