# ERROR_LOG

Incidents and their root causes. Newest first.

## 2026-08-25 — every model id taken from a published guide was dead

The routing defaults were picked from a 2026 blog post that named deepseek-v4-flash,
deepseek-v4-pro and kimi-k2.6. On the first live run all six routes failed: HTTP 410 Gone
for the deepseek ids, 404 for kimi.

Two lessons, both now baked in:

1. **The catalogue moves faster than anything written about it.** Pick model ids from
   `scripts/check_models.py --list` against the live key, never from a published list.
2. **Being listed is not the same as being callable.** `writer/palmyra-fin-70b-32k`,
   `mistralai/mistral-large-2-instruct` and `moonshotai/kimi-k2.6` all appear in the
   account's own /models response and all return 404 when invoked. Only a real request
   proves a route works, which is why `scripts/probe_models.py` sends actual work rather
   than checking a manifest.

## 2026-08-25 — probe died on the first model that hung

The first version of the probe caught only LLMError, so an httpx.ReadTimeout on candidate
one killed the whole run and produced nothing for the other thirteen.

Fix: catch every exception per candidate, run candidates in parallel with a short per-model
timeout, and report the timeout as a result rather than an abort.

## 2026-08-25 — check_models reported success having verified nothing

With no provider key set, every route was skipped and the script still printed
"All configured routes resolve". A green light meaning "I checked nothing" is exactly the
failure the script exists to prevent.

Fix: track what was actually verified. If nothing was, say NOTHING VERIFIED and exit 1.

## 2026-08-25 — config required provider keys to do offline work

`Config.__post_init__` used `_require_if` for `NIM_API_KEY`, so `scripts/seed_profile.py`
crashed at import on a machine with no keys, despite touching no API.

Fix: provider keys are read but not required at import. `modules/llm.py` checks them at call
time and raises a clear message. Structural config (malformed routes) still fails fast,
because that is a bug rather than a missing credential.
