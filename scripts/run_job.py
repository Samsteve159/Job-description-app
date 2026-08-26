"""Run one job description end to end: extract, tailor, gate, render, score.

This is the whole writer pipeline in one command, and it is how a run is verified
before the web UI exists. It prints what each stage decided rather than only the
result, because the interesting failures are in the middle: a model that invents a
citation, or grades a reach as verified, is only visible if you print the grading.

Nothing here is auto-accepted. Blocks graded inferred or stretch stay unaccepted, so
the first render shows what survives on verified content alone. Use --accept-all to
see the aggressive version, then decide block by block.

    python3 scripts/run_job.py data/jds/some_job.txt
    python3 scripts/run_job.py data/jds/some_job.txt --accept-all
    python3 scripts/run_job.py data/jds/some_job.txt --no-render   # stop before the docx
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config  # noqa: E402
from database.db import get_db, init_db  # noqa: E402
from database.models import ProfileFact  # noqa: E402
from modules.ats import AtsBlocked, check, gate as ats_gate  # noqa: E402
from modules.extract import NotAJobDescription, extract  # noqa: E402
from modules.render_docx import BlockedContentError, render_resume  # noqa: E402
from modules.tailor import tailor, to_payload  # noqa: E402

RULE = "-" * 78


def banner(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jd", type=Path, help="a file containing the job description text")
    ap.add_argument("--accept-all", action="store_true",
                    help="accept every inferred and stretch block, the aggressive version")
    ap.add_argument("--no-render", action="store_true", help="stop before writing the docx")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if not args.jd.exists():
        print(f"no such file: {args.jd}")
        return 1

    print(config.describe())
    jd_text = args.jd.read_text(encoding="utf-8")

    # ----------------------------------------------------------------- extract
    banner("EXTRACT")
    try:
        ex = extract(jd_text)
    except NotAJobDescription as exc:
        print(f"not a job description: {exc}")
        return 1

    print(f"title      {ex.title}")
    print(f"company    {ex.company}")
    print(f"location   {ex.location}")
    print(f"seniority  {ex.seniority}")
    print(f"archetype  {ex.archetype}")
    print(f"\nmust-have ({len(ex.must)}):")
    for r in ex.must:
        print(f"  ({r.weight:.1f}) {r.text[:66]}"
              + (f"   [{r.keyword}]" if r.keyword else ""))
    print(f"\nnice-to-have ({len(ex.nice)}):")
    for r in ex.nice:
        print(f"        {r.text[:66]}" + (f"   [{r.keyword}]" if r.keyword else ""))
    print(f"\nkeywords   {', '.join(ex.keywords)}")
    if ex.comp_hints:
        print(f"comp       {ex.comp_hints}")

    # ------------------------------------------------------------------ tailor
    banner("TAILOR")
    init_db()
    db = next(get_db())
    try:
        facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
        print(f"{len(facts)} facts loaded, "
              f"{sum(1 for f in facts if not f.verified)} unverified and withheld\n")
        result = tailor(ex, facts)

        print(result.summary_line())

        if result.rejected:
            print(f"\nREJECTED BY THE TRUTH GATE ({len(result.rejected)}):")
            for block, why in result.rejected:
                print(f"  [{why}]\n    {block.text[:100]}")

        print("\nblocks:")
        for i, b in enumerate(result.blocks):
            mark = {"verified": "ok ", "inferred": "~~ ", "stretch": "!! ",
                    "blocked": "XX "}.get(b.grade, "?? ")
            print(f"  {i:>2} {mark}{b.section:<10} cites {b.fact_ids}")
            print(f"       {b.text}")

        missing = [k for k, hit in result.keyword_hits.items() if not hit]
        if missing:
            print(f"\nmust-have keywords not placed: {', '.join(missing)}")

        if args.accept_all:
            for b in result.blocks:
                if b.grade in ("inferred", "stretch"):
                    b.accepted = True
            print(f"\n--accept-all: accepted {len(result.needs_review)} reaching block(s)")

        if args.no_render:
            return 0

        # ------------------------------------------------------------- render
        banner("RENDER")
        payload = to_payload(result, facts)
        out = args.out or (config.base_dir / "data" / "output" /
                           f"{args.jd.stem}.docx")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            path = render_resume(payload, out)
        except BlockedContentError as exc:
            print(f"TRUTH GATE REFUSED THE RENDER\n  {exc}")
            return 2
        print(f"wrote {path}")

        # ---------------------------------------------------------- ats gate
        banner("ATS GATE")
        report = check(
            path,
            must_keywords=ex.must_keywords(),
            nice_keywords=[r.keyword for r in ex.nice if r.keyword],
            # roles that actually reached the page, not every role on file. A role
            # whose bullets were all dropped is not missing a date, it is not there
            expect_roles=len(payload.experience),
            expect_phone=True,
        )
        print(report.summary())
        print(f"\nparsed  name={report.parsed.name!r} email={report.parsed.email!r} "
              f"phone={report.parsed.phone!r}")
        print(f"        sections={report.parsed.sections}")
        print(f"        dates={report.parsed.date_ranges}")
        for b in report.blocking:
            print(f"  BLOCK   {b}")
        for w in report.warnings:
            print(f"  warn    {w}")
        if report.missing_must():
            print(f"\n  missing must-have keywords: {', '.join(report.missing_must())}")

        try:
            ats_gate(report)
        except AtsBlocked:
            print("\nExport refused. Fix the blocking items above.")
            return 3
        print("\nExport allowed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
