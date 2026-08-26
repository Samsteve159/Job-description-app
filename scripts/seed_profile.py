"""Load data/profile_facts.json into the ProfileFact table.

Idempotent: wipes and reloads, because the JSON file is the source of truth and hand-editing
it then re-running is the intended workflow.

    python3 scripts/seed_profile.py
    python3 scripts/seed_profile.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config                      # noqa: E402
from database.db import SessionLocal, init_db  # noqa: E402
from database.models import ProfileFact  # noqa: E402
from modules.contact import bootstrap        # noqa: E402

SEED_FILE = config.data_dir / "profile_facts.json"


def _row(entry: dict, parent_id=None, order=0) -> ProfileFact:
    return ProfileFact(
        kind=entry["kind"],
        parent_id=parent_id,
        text=entry["text"],
        tags=entry.get("tags", []),
        metrics=entry.get("metrics", {}),
        org=entry.get("org"),
        date_from=entry.get("date_from"),
        date_to=entry.get("date_to"),
        source=entry.get("source", "profile_facts.json"),
        verified=entry.get("verified", True),
        order_index=order,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SEED_FILE.exists():
        print(f"ERROR: {SEED_FILE} not found")
        return 1

    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    entries = payload["facts"]

    init_db()
    db = SessionLocal()
    try:
        existing = db.query(ProfileFact).count()
        if args.dry_run:
            print(f"DRY RUN: would replace {existing} rows")

        if not args.dry_run:
            db.query(ProfileFact).delete()
            db.flush()

        counts, unverified = {}, []
        for order, entry in enumerate(entries):
            parent = _row(entry, order=order)
            counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
            if not entry.get("verified", True):
                unverified.append(entry["text"][:70])

            if not args.dry_run:
                db.add(parent)
                db.flush()

            for child_order, child in enumerate(entry.get("children", [])):
                counts[child["kind"]] = counts.get(child["kind"], 0) + 1
                if not args.dry_run:
                    db.add(_row(child, parent_id=parent.id, order=child_order))

        if not args.dry_run:
            db.commit()
            # Imports name, email, phone and the rest into ContactDetail, but only on a
            # database that has none yet. After that the app owns them, and wiping
            # ProfileFact above must not take an edited phone number down with it.
            imported = bootstrap(db)
            if imported:
                print(f"  contact details imported: {imported} "
                      f"(edit them with scripts/contact.py, not this file)")

        total = sum(counts.values())
        print(f"{'would seed' if args.dry_run else 'seeded'} {total} facts:")
        for kind in sorted(counts):
            print(f"  {kind:12} {counts[kind]:3}")

        if unverified:
            print(f"\n{len(unverified)} fact(s) marked unverified. These CANNOT be cited")
            print("until you confirm them and set \"verified\": true:")
            for text in unverified:
                print(f"  - {text}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
