"""Contact details, and the one property the whole table exists for.

Contact details used to be ProfileFact rows. seed_profile.py wipes and reloads
ProfileFact, because data/profile_facts.json is the source of truth for the career
record. That is exactly wrong for a phone number the app lets you edit: the next re-seed
puts the old one back, silently, and you find out when a recruiter calls the wrong
country. So the survival test below is the real one. The rest is scaffolding.

    python3 tests/test_contact.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database.models import Base, ContactDetail, ProfileFact  # noqa: E402
from modules.contact import (all_details, bootstrap, display_name,  # noqa: E402
                             infer_kind, resume_lines, set_detail, set_renders, warnings)

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def fresh_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_facts(db):
    """What scripts/seed_profile.py writes: name and contact rows from the JSON."""
    rows = [
        ProfileFact(kind="name", text="Jane Doe", order_index=0),
        ProfileFact(kind="contact", text="jane.doe@example.com", order_index=1),
        ProfileFact(kind="contact", text="linkedin.com/in/jane-doe-analytics", order_index=2),
        ProfileFact(kind="contact", text="Mumbai, India", order_index=3),
        ProfileFact(kind="contact", text="+61 477 542 567", order_index=4),
    ]
    for row in rows:
        db.add(row)
    db.commit()


print("classification")
for text, want in [
    ("jane.doe@example.com", "email"),
    ("linkedin.com/in/jane-doe-analytics", "link"),
    ("https://github.com/someone", "link"),
    ("+61 477 542 567", "phone"),
    ("+91 98200 12345", "phone"),
    ("Mumbai, India", "location"),
]:
    check(f"{text[:34]!r} reads as {want}", infer_kind(text) == want, infer_kind(text))

print("\nbootstrap")
db = fresh_db()
seed_facts(db)
check("imports every contact fact", bootstrap(db) == 5, bootstrap(db))
check("a second call is a no-op", bootstrap(db) == 0)
check("name is separated from the contact line", display_name(db) == "Jane Doe")
check("contact line is in resume order",
      resume_lines(db) == ["jane.doe@example.com", "+61 477 542 567",
                           "linkedin.com/in/jane-doe-analytics", "Mumbai, India"],
      resume_lines(db))

print("\nsurviving a re-seed")
# This is the property the table exists for.
set_detail(db, "phone", "+91 98200 12345")
check("the edit takes", "+91 98200 12345" in resume_lines(db), resume_lines(db))

db.query(ProfileFact).delete()          # exactly what seed_profile.py does
db.commit()
seed_facts(db)                          # and reloads the old number from the JSON
bootstrap(db)                           # startup calls this every time

check("a re-seed does not revert the edited phone",
      "+91 98200 12345" in resume_lines(db) and "+61 477 542 567" not in resume_lines(db),
      resume_lines(db))
check("the re-seed did not duplicate anything",
      len(all_details(db)) == 5, [(d.kind, d.value) for d in all_details(db)])

print("\naddress")
row = set_detail(db, "address", "12 Some Road, Mumbai 400001")
check("address is stored", row.value == "12 Some Road, Mumbai 400001")
check("address is withheld from the resume by default",
      "12 Some Road, Mumbai 400001" not in resume_lines(db), resume_lines(db))
set_renders(db, row.id, True)
check("and can be put on it deliberately",
      "12 Some Road, Mumbai 400001" in resume_lines(db), resume_lines(db))
set_renders(db, row.id, False)

print("\nrejections and warnings")
try:
    set_detail(db, "phone", "   ")
    check("an empty value is rejected", False, "no exception")
except ValueError:
    check("an empty value is rejected", True)
try:
    set_detail(db, "favourite_colour", "green")
    check("an unknown kind is rejected", False, "no exception")
except ValueError:
    check("an unknown kind is rejected", True)

check("an Indian number raises no warning",
      not any("not an Indian number" in w for w in warnings(db)), warnings(db))
set_detail(db, "phone", "+61 477 542 567")
check("a foreign number warns but does not block",
      any("not an Indian number" in w for w in warnings(db)), warnings(db))

db.query(ContactDetail).filter(ContactDetail.kind == "email").delete()
db.commit()
check("a missing email is called out",
      any("no email" in w for w in warnings(db)), warnings(db))

empty = fresh_db()
check("no details gives no name rather than an error", display_name(empty) == "")
check("no details gives an empty contact line", resume_lines(empty) == [])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
