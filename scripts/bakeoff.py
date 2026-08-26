"""Run the same job through several models and measure what actually differs.

Written because three runs of one job description on the configured model produced
must-have keyword coverage of 57%, 83% and 88%. Same input, same settings. That spread
decides whether a resume clears a keyword filter or is never read by a person, and no
amount of reading one sample tells you whether it is the model or the prompt.

So this measures, over repeated runs:

  coverage    must-have keywords placed. What the filter screens on
  spread      best minus worst across runs. A model that is right on average and wrong
              on Tuesday is not usable for something you send once
  honesty     citations to facts that do not exist, and numbers not in the cited facts
  reach       how much it grades verified rather than admitting it reframed
  tenure      years claimed against the years the role dates actually total
  dirt        non-ASCII characters, which the renderer normalises and should not have to

The fallback is disabled for the duration. Otherwise a NIM model that fails gets quietly
served by Claude and scored as though it had answered, which would make the paid model
look identical to whichever free one happened to break.

    python3 scripts/bakeoff.py data/jds/some_job.txt
    python3 scripts/bakeoff.py data/jds/some_job.txt --runs 5
    python3 scripts/bakeoff.py data/jds/some_job.txt --models nim:openai/gpt-oss-120b
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import NIM_BIG, NIM_FAST, NIM_MAIN, config  # noqa: E402
from database.db import get_db, init_db  # noqa: E402
from database.models import ProfileFact  # noqa: E402
from modules.extract import extract  # noqa: E402
from modules.render_docx import PUNCTUATION_MAP  # noqa: E402
from modules.tailor import _tenure_claims, experience_years, tailor  # noqa: E402

DEFAULT_MODELS = [
    f"nim:{NIM_MAIN}",
    f"nim:{NIM_FAST}",
    f"nim:{NIM_BIG}",
    "anthropic:claude-sonnet-5",
]


def measure(extraction, facts, actual_years) -> Dict:
    started = time.time()
    result = tailor(extraction, facts)
    elapsed = time.time() - started

    text = " ".join(b.text for b in result.blocks)
    dirt = sorted({c for c in text if ord(c) > 127})
    grades = {g: sum(1 for b in result.blocks if b.grade == g)
              for g in ("verified", "inferred", "stretch")}

    # A rejection is the guard working, but it is also the model having tried it on.
    fabricated = sum(1 for _, why in result.rejected if "cites no real fact" in why)
    drifted = sum(1 for _, why in result.rejected if "not in cited facts" in why)
    tenure_wrong = sum(
        1 for b in result.blocks for claimed in _tenure_claims(b.text)
        if actual_years and abs(claimed - actual_years) > 1.0
    )

    return {
        "secs": elapsed,
        "coverage": result.coverage,
        "blocks": len(result.blocks),
        "rejected": len(result.rejected),
        "fabricated": fabricated,
        "drifted": drifted,
        "tenure_wrong": tenure_wrong,
        "verified": grades["verified"],
        "reaching": grades["inferred"] + grades["stretch"],
        "dirt": len(dirt),
        "dirt_chars": dirt[:4],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jd", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

    if not args.jd.exists():
        print(f"no such file: {args.jd}")
        return 1

    init_db()
    db = next(get_db())
    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    if not facts:
        print("No career facts. Run scripts/seed_profile.py first.")
        return 1
    actual_years = experience_years(facts)

    print(f"extracting {args.jd.name} once, so every model scores the same requirements")
    extraction = extract(args.jd.read_text(encoding="utf-8"))
    print(f"  {extraction.title} at {extraction.company}: "
          f"{len(extraction.must)} must-haves, {len(extraction.must_keywords())} keywords")
    print(f"  role dates total {actual_years} years\n")

    original_route = config.routes["tailor"]
    original_fallback = config.fallback
    # config is a frozen dataclass, deliberately, so that nothing in the app can quietly
    # repoint a route at runtime. This script has to, because measuring a model while a
    # fallback is armed measures the fallback whenever the model fails. Bypassing the
    # freeze here is honest about being a diagnostic, and the finally block puts it back.
    _set = lambda name, value: object.__setattr__(config, name, value)  # noqa: E731
    _set("fallback", "")
    paid = [m for m in args.models if m.split(":", 1)[0] in ("anthropic",)]
    if paid:
        print(f"  note: {', '.join(paid)} is a PAID route. "
              f"{args.runs} run(s) each.\n")

    results: Dict[str, List[Dict]] = {}
    try:
        for model in args.models:
            config.routes["tailor"] = model
            runs: List[Dict] = []
            for index in range(args.runs):
                try:
                    runs.append(measure(extraction, facts, actual_years))
                    print(f"  {model:<38} run {index + 1}/{args.runs}  "
                          f"coverage {runs[-1]['coverage']:.0f}%  "
                          f"{runs[-1]['secs']:.1f}s")
                except Exception as exc:  # noqa: BLE001 - one dead model must not end the run
                    print(f"  {model:<38} run {index + 1}/{args.runs}  "
                          f"FAILED {type(exc).__name__}: {str(exc)[:60]}")
            if runs:
                results[model] = runs
    finally:
        config.routes["tailor"] = original_route
        _set("fallback", original_fallback)
        db.close()

    if not results:
        print("\nNothing completed. Nothing to compare.")
        return 1

    print(f"\n{'model':<38} {'cover':>6} {'spread':>7} {'secs':>6} "
          f"{'blocks':>7} {'reach':>6} {'bad':>4} {'dirt':>5}")
    print("-" * 88)
    for model, runs in results.items():
        cov = [r["coverage"] for r in runs]
        bad = sum(r["fabricated"] + r["drifted"] + r["tenure_wrong"] for r in runs)
        print(
            f"{model:<38} {statistics.mean(cov):>5.0f}% "
            f"{max(cov) - min(cov):>6.0f}% "
            f"{statistics.mean(r['secs'] for r in runs):>5.1f}s "
            f"{statistics.mean(r['blocks'] for r in runs):>7.1f} "
            f"{statistics.mean(r['reaching'] for r in runs):>6.1f} "
            f"{bad:>4} {sum(r['dirt'] for r in runs):>5}"
        )

    print("\ncover   mean must-have keyword coverage. The ATS floor is 70%")
    print("spread  best run minus worst. High means you cannot trust one run")
    print("bad     fabricated citations + drifted numbers + wrong tenure, all runs")
    print("dirt    non-ASCII characters the renderer had to normalise away")

    ranked = sorted(
        results.items(),
        key=lambda kv: (
            -statistics.mean(r["coverage"] for r in kv[1]),
            max(r["coverage"] for r in kv[1]) - min(r["coverage"] for r in kv[1]),
        ),
    )
    best = ranked[0]
    print(f"\nhighest mean coverage: {best[0]}")
    steadiest = min(results.items(),
                    key=lambda kv: max(r["coverage"] for r in kv[1])
                    - min(r["coverage"] for r in kv[1]))
    print(f"steadiest across runs:  {steadiest[0]}")
    if best[0] != steadiest[0]:
        print("These disagree. For something you send once, steady beats high.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
