"""View and edit the contact details the resume writer uses.

These live in their own table precisely so `seed_profile.py` cannot revert them. Edit
them here, or in the app once Section 0 has a screen. Editing profile_facts.json does
nothing after the first import, which is the point.

    python3 scripts/contact.py                                  # show everything
    python3 scripts/contact.py set phone "+91 98200 12345"
    python3 scripts/contact.py set address "12 Some Road, Mumbai 400001"
    python3 scripts/contact.py set location "Mumbai, India"
    python3 scripts/contact.py show 5                           # put it on the resume
    python3 scripts/contact.py hide 5                           # keep it, leave it off
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import get_db, init_db  # noqa: E402
from modules.contact import (KINDS, all_details, bootstrap, display_name,  # noqa: E402
                             resume_lines, set_detail, set_renders, warnings)


def show(db) -> None:
    rows = all_details(db, include_inactive=True)
    if not rows:
        print("no contact details. Run scripts/seed_profile.py first to import them.")
        return

    print(f"{'id':>3}  {'kind':<9} {'on resume':<10} value")
    print("-" * 74)
    for row in rows:
        state = "yes" if row.renders else "no"
        if not row.active:
            state = "removed"
        label = f"  ({row.label})" if row.label else ""
        print(f"{row.id:>3}  {row.kind:<9} {state:<10} {row.value}{label}")

    print(f"\nrenders as:  {display_name(db)}")
    print(f"             {' | '.join(resume_lines(db))}")

    for warning in warnings(db):
        print(f"\n  note: {warning}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command")

    p_set = sub.add_parser("set", help="update or create a detail")
    p_set.add_argument("kind", choices=("name",) + KINDS)
    p_set.add_argument("value")
    p_set.add_argument("--label", default=None, help="a note for yourself, not printed")
    p_set.add_argument("--on-resume", dest="renders", action="store_true", default=None)
    p_set.add_argument("--off-resume", dest="renders", action="store_false")

    for name in ("show", "hide"):
        p = sub.add_parser(name, help=f"{name} a detail on the resume")
        p.add_argument("id", type=int)

    args = ap.parse_args()

    init_db()
    db = next(get_db())
    try:
        bootstrap(db)
        if args.command == "set":
            row = set_detail(db, args.kind, args.value, args.label, args.renders)
            print(f"{row.kind} set to {row.value!r}"
                  f"{'' if row.renders else ', kept off the resume'}\n")
        elif args.command in ("show", "hide"):
            row = set_renders(db, args.id, args.command == "show")
            print(f"{row.kind} {row.value!r} is now "
                  f"{'on' if row.renders else 'off'} the resume\n")
        show(db)
        return 0
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
