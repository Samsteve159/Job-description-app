"""The tracker, and the counting decisions the dashboard depends on.

Counting applications is where a dashboard quietly starts lying. The two that matter:

  a rejection after an interview is still an interview that happened, so the funnel
  reads the furthest stage reached and not the current status

  silence is not the same as still being in the running, so anything untouched for
  long enough is separated out rather than left inflating the live count

    python3 tests/test_tracker.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="jobapp-tracker-")) / "t.db")

from database.db import get_db, init_db  # noqa: E402
from database.models import Application  # noqa: E402
from modules import tracker  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


init_db()
db = next(get_db())
NOW = datetime(2026, 8, 26, 12, 0)

print("logging")
a = tracker.log_application(db, title="Treasury Analyst", company="Wells Fargo",
                            source="manual")
check("an application is logged", a.id is not None and a.status == "applied")
check("it starts at the first stage", a.furthest_status == "applied", a.furthest_status)

again = tracker.log_application(db, title="Dup", company="X", source="gmail",
                                external_ref="msg-1")
same = tracker.log_application(db, title="Dup", company="X", source="gmail",
                               external_ref="msg-1")
check("an inbox rescan cannot double count", again.id == same.id, (again.id, same.id))
check("two rows exist, not three", len(tracker.all_applications(db)) == 2,
      len(tracker.all_applications(db)))

try:
    tracker.log_application(db, title="Y", source="carrier_pigeon")
    check("an unknown source is refused", False, "no exception")
except ValueError:
    check("an unknown source is refused", True)

print("\nthe high-water mark")
tracker.set_status(db, a.id, "interview")
check("status moves", a.status == "interview")
check("furthest follows it", a.furthest_status == "interview", a.furthest_status)

tracker.set_status(db, a.id, "rejected")
check("a rejection is the current status", a.status == "rejected")
check("but the interview is not forgotten", a.furthest_status == "interview",
      a.furthest_status)
check("the funnel still counts the interview",
      [s for s in tracker.stats(db)["funnel"] if s["key"] == "interview"][0]["count"] == 1,
      tracker.stats(db)["funnel"])

tracker.set_status(db, a.id, "ghosted")
check("silence does not move the mark either", a.furthest_status == "interview",
      a.furthest_status)

for bad in ("hired", "", "APPLIED"):
    try:
        tracker.set_status(db, a.id, bad)
        check(f"status {bad!r} is refused", False, "no exception")
    except ValueError:
        check(f"status {bad!r} is refused", True)

print("\nsilence")
fresh = Application(title="Fresh", status="applied", applied_at=NOW - timedelta(days=3))
old = Application(title="Old", status="applied", applied_at=NOW - timedelta(days=90))
moving = Application(title="Moving", status="interview",
                     applied_at=NOW - timedelta(days=90))
check("a recent application is not stale", not tracker.is_stale(fresh, NOW))
check("an untouched old one is stale", tracker.is_stale(old, NOW))
check("an old one still moving is not stale", not tracker.is_stale(moving, NOW))

old.last_event_at = NOW - timedelta(days=2)
check("recent movement resets the clock", not tracker.is_stale(old, NOW))

print("\nstats")
db.query(Application).delete()
db.commit()
for index in range(5):
    row = tracker.log_application(db, title=f"Role {index}", company="Co", source="manual")
    row.applied_at = NOW - timedelta(days=index * 2)
db.commit()
rows = tracker.all_applications(db)
tracker.set_status(db, rows[0].id, "interview")
tracker.set_status(db, rows[1].id, "rejected")

s = tracker.stats(db, now=NOW)
check("total counts everything sent", s["total"] == 5, s["total"])
check("the funnel starts at the total", s["funnel"][0]["count"] == 5, s["funnel"])
check("one reached interview",
      [f for f in s["funnel"] if f["key"] == "interview"][0]["count"] == 1, s["funnel"])
check("a rejection is not live", s["live"] == 4, s["live"])
check("response rate is a percentage of everything sent",
      abs(s["response_rate"] - 20.0) < 0.01, s["response_rate"])
check("eight weeks of history are returned", len(s["weeks"]) == 8, len(s["weeks"]))
check("this week is counted", s["this_week"] == 4, s["this_week"])

print("\nsoft delete")
tracker.remove(db, rows[0].id)
check("a removed application leaves the list",
      len(tracker.all_applications(db)) == 4, len(tracker.all_applications(db)))
check("but the row survives",
      len(tracker.all_applications(db, include_inactive=True)) == 5)
check("and it stops counting", tracker.stats(db, now=NOW)["total"] == 4)

db.close()
print("\nwhat a package's status should say")
# A package knows it produced a document. It does not know he then sent it, and the
# confirmation proving he did arrives by email with no reference back to the package. So
# the two are matched on what they share: the employer and the role.
from types import SimpleNamespace  # noqa: E402

def pkg(company, title, status):
    return SimpleNamespace(company=company, title=title, status=status)

keys = {("wells fargo", "lead analytics consultant"), ("wells fargo", "")}
check("a package he applied to reads applied, not exported",
      tracker.display_status(pkg("Wells Fargo", "Lead Analytics Consultant", "exported"),
                             keys) == "applied")
check("applied outranks whatever the package thinks it is",
      tracker.display_status(pkg("Wells Fargo", "Lead Analytics Consultant", "draft"),
                             keys) == "applied")
# Employers and this app word titles differently. Having applied there at all is the
# stronger signal, and the alternative is a package sitting at exported for ever.
check("a different wording at the same employer still counts",
      tracker.display_status(pkg("Wells Fargo", "Lead Analytics Consultant II", "exported"),
                             keys) == "applied")
check("a different employer does not",
      tracker.display_status(pkg("Deloitte", "Lead Analytics Consultant", "exported"),
                             keys) == "exported")
check("a package with no company keeps its own status",
      tracker.display_status(pkg(None, "Analyst", "draft"), keys) == "draft")
check("and an empty tracker changes nothing",
      tracker.display_status(pkg("Wells Fargo", "Lead", "exported"), set()) == "exported")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
