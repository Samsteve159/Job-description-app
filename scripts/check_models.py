"""Validate that every configured route actually resolves before you rely on it.

Free NIM models can be deprecated at short notice. Without this check a dead route shows
up as a silent fallback to Claude, which looks like the system working while quietly
costing money and changing the output. Run it after any .env change.

    python3 scripts/check_models.py
    python3 scripts/check_models.py --list   # what the account actually offers
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from config import config, is_thinking_model  # noqa: E402
from modules.llm import LLMError, _PROVIDERS, _split_route  # noqa: E402

OK, BAD, SKIP = "  ok  ", " FAIL ", " skip "


def list_models() -> int:
    if not config.nim_api_key:
        print("NIM_API_KEY is not set, cannot list.")
        return 1
    resp = httpx.get(
        f"{config.nim_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {config.nim_api_key}"},
        timeout=30.0,
    )
    if resp.status_code >= 400:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return 1
    ids = sorted(m.get("id", "") for m in resp.json().get("data", []))
    print(f"{len(ids)} models available to this key:\n")
    for i in ids:
        print(f"  {i}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list all available models")
    args = parser.parse_args()
    if args.list:
        return list_models()

    missing = config.missing_keys()
    if missing:
        print(f"Missing keys: {', '.join(missing)}")
        print("Set them in .env, then run this again.\n")

    routes = dict(config.routes)
    routes["(fallback)"] = config.fallback

    failures = 0
    checked = set()
    print(f"{'stage':12} {'route':42} result")
    print("-" * 78)

    for stage, route in routes.items():
        provider, model = _split_route(route)
        key = config.nim_api_key if provider == "nim" else config.anthropic_api_key
        if not key:
            print(f"{stage:12} {route:42}{SKIP} no key for {provider}")
            continue

        if route in checked:
            print(f"{stage:12} {route:42}{OK} (already checked)")
            continue

        try:
            _PROVIDERS[provider](model, "Reply with the single word OK.", "ping", 16, 0.0)
            note = " thinking model, temperature clamped to 0" if is_thinking_model(model) else ""
            print(f"{stage:12} {route:42}{OK}{note}")
            checked.add(route)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            reason = str(exc)
            print(f"{stage:12} {route:42}{BAD} {reason[:90]}")
            if "404" in reason or "not found" in reason.lower():
                print(f"{'':12} model may have been deprecated. "
                      f"Run --list to see what is available.")

    print()
    if failures:
        print(f"{failures} route(s) failed. Fix them in .env before running any stage,")
        print("otherwise they will fall back to Claude silently on every call.")
        return 1
    if not checked:
        # Never report success on a run that verified nothing. A green light that means
        # "I checked nothing" is the exact failure this script exists to prevent.
        print("NOTHING VERIFIED. No provider key was available, so no route was tested.")
        print("Add NIM_API_KEY to .env and run this again.")
        return 1
    print(f"All {len(checked)} distinct route(s) resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
