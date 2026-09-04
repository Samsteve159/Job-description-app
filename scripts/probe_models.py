"""Probe candidate NIM models on the workload this app actually runs.

Written because the model IDs taken from a published guide were all dead on arrival:
410 Gone or 404. The catalogue moves faster than anything written about it, so pick from
evidence gathered against the live key rather than from a list somebody published.

Runs candidates in parallel with a short per-model timeout, because a single hung model
must not take the whole probe down.

    python3 scripts/probe_models.py                 # extract-shaped task
    python3 scripts/probe_models.py --task tailor   # harder: cite and self-grade
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from config import config  # noqa: E402

TIMEOUT = 45.0

CANDIDATES = [
    # Re-picked 3 Sep 2026, the morning openai/gpt-oss-120b went 410 Gone. Everything
    # here was on the key's own /models listing that day, which is a necessary and not a
    # sufficient condition: the listing has offered 404s before, so the probe still
    # sends real work before anything is routed to it.
    "deepseek-ai/deepseek-v4-pro-0813",
    "deepseek-ai/deepseek-v4-flash-0731",
    "moonshotai/kimi-k3",
    "moonshotai/kimi-k2.6",
    "minimaxai/minimax-m3",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "mistralai/mistral-large-2-instruct",
    "mistralai/mistral-nemotron",
    "openai/gpt-oss-20b",
]

JD = """Manager, Procurement Analytics. Mumbai, hybrid. A global capability centre of a
multinational. Required: 6+ years in spend analytics or procurement analytics, advanced SQL,
Power BI or Tableau, hands-on with UNSPSC taxonomy and supplier master data management.
Nice to have: Python, FP&A exposure, experience presenting savings cases at CFO level.
Reporting to the Director of Procurement Excellence. CTC 35-45 LPA."""

TASKS = {
    "extract": (
        'Return JSON only. No prose, no code fence. Exact shape: '
        '{"title":str,"seniority":str,"archetype":str,'
        '"must":[{"text":str,"keyword":str}],"keywords":[str]}',
        f"Job description:\n{JD}",
        ("title", "must", "keywords"),
    ),
    "tailor": (
        'You tailor a career record to a job. Return JSON only, no prose, no code fence. '
        'Shape: {"bullets":[{"text":str,"fact_ids":[int],"grade":"verified"|"inferred"|"stretch"}]}. '
        'Every bullet MUST cite at least one fact id from the FACTS given. Never invent a '
        'number. If you cannot cite a fact, omit the bullet.',
        f"""JOB:\n{JD}\n\nFACTS:
[12] Scaled a $12.5M unmatched-spend finding into a ~$40M five-year value case.
[14] Audits supplier categorisation against UNSPSC across 98.4% of categorised spend.
[20] Built treasury dashboards used for regional reporting.
[31] Skill: SQL
[32] Skill: Power BI

Write 3 bullets for this job.""",
        ("bullets",),
    ),
}


def probe(model: str, system: str, user: str, required: tuple) -> dict:
    t0 = time.time()
    try:
        resp = httpx.post(
            f"{config.nim_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.nim_api_key}"},
            json={"model": model,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "max_tokens": 1500, "temperature": 0.0},
            timeout=httpx.Timeout(TIMEOUT, connect=10.0),
        )
        dt = time.time() - t0
        if resp.status_code >= 400:
            body = resp.text[:120].replace("\n", " ")
            return {"model": model, "secs": dt, "state": "DEAD",
                    "note": f"HTTP {resp.status_code} {body[:60]}"}
        raw = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001 - a hung model must not kill the probe
        return {"model": model, "secs": time.time() - t0, "state": "ERR",
                "note": type(exc).__name__}

    body = raw
    if "```" in body:
        parts = body.split("```")
        if len(parts) > 1:
            body = parts[1]
            if body.lower().startswith("json"):
                body = body[4:]
    i, j = body.find("{"), body.rfind("}")
    if i == -1 or j <= i:
        return {"model": model, "secs": dt, "state": "NO-JSON",
                "note": raw[:55].replace("\n", " ")}
    try:
        data = json.loads(body[i:j + 1])
    except json.JSONDecodeError as exc:
        return {"model": model, "secs": dt, "state": "BAD-JSON", "note": str(exc)[:55]}

    missing = [k for k in required if k not in data]
    if missing:
        return {"model": model, "secs": dt, "state": "SHAPE",
                "note": f"missing {missing}"}

    if "bullets" in data:
        bullets = data["bullets"] or []
        cited = sum(1 for b in bullets if isinstance(b, dict) and b.get("fact_ids"))
        note = f"{len(bullets)} bullets, {cited} cited"
        if raw.count("—") or raw.count("–"):
            note += ", EM DASH"
    else:
        note = (f"{len(data.get('must', []))} musts, "
                f"{len(data.get('keywords', []))} keywords, "
                f"sen={str(data.get('seniority'))[:14]}")
    return {"model": model, "secs": dt, "state": "OK", "note": note}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="extract", choices=sorted(TASKS))
    args = parser.parse_args()

    if not config.nim_api_key:
        print("NIM_API_KEY is not set.")
        return 1

    system, user, required = TASKS[args.task]
    print(f"task={args.task}  candidates={len(CANDIDATES)}  timeout={TIMEOUT:.0f}s\n")
    print(f"{'model':46} {'secs':>5}  {'state':<9} note")
    print("-" * 104)

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe, m, system, user, required): m for m in CANDIDATES}
        for fut in as_completed(futures):
            results.append(fut.result())

    for r in sorted(results, key=lambda r: (r["state"] != "OK", r["secs"])):
        print(f"{r['model']:46} {r['secs']:5.1f}  {r['state']:<9} {r['note'][:44]}")

    ok = [r for r in results if r["state"] == "OK"]
    print(f"\n{len(ok)} of {len(CANDIDATES)} usable for {args.task}.")
    if ok:
        print(f"fastest usable: {min(ok, key=lambda r: r['secs'])['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
