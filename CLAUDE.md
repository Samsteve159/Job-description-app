# Job_App — working notes for Claude

A local macOS app that runs Sameer Iyer's India job search. Three sections, one of which is
deliberately isolated from the other two.

**Read `TRACKER.md` first** for current status and what is blocked.
Then `SPEC.md` for what it does and `DECISIONS.md` for why it is shaped this way.
Patterns are borrowed from an earlier system of Sameer's (FastAPI + SQLite + APScheduler
+ LLM + human approval gate). That project is not a dependency and is not read at runtime.

## Committing. Do this without being asked.

**At the end of every session, and whenever Sameer says pause, stop or we are done:
stage, commit and push.** He has standing authorisation for this, so do not ask first.

```bash
git add -A
git commit -m "<what changed and why, not a file list>"
git push
```

Remote is `git@github.com:Samsteve159/Job-description-app.git` (private). SSH is already
working. Commit identity is set locally to the GitHub noreply address, so his real email
stays out of the log. Do not change it to the global config.

**Always scan before committing.** The repo is private now, but `data/profile_facts.json`
holds his email, phone and career figures, and `.env` holds a live key. Both are gitignored;
verify rather than trust:

```bash
git diff --cached --name-only | grep -xE "\.env|data/profile_facts\.json|data/search_criteria\.json|data/job_app\.db"
git grep --cached -nE "nvapi-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}"
```

Either returning a hit means stop and fix before committing.

Write the commit message as prose explaining the change and the reasoning. `git diff` already
lists the files.

## Scope boundary

**This directory is the whole project.** Do not read from, write to, or reference anything
outside `Job_App/`. If the app needs a fact about Sameer it goes in `data/profile_facts.json`.
That file is this project's own copy, on purpose, so nothing here depends on work that
lives elsewhere.

## Hard rules

1. **Every claim cites a fact.** A block citing nothing is blocked at render, not just
   discouraged in a prompt. See `modules/render_docx.py::gate` and
   `modules/tailor.py::_validate`. Do not route around it.
   Tailoring itself is meant to be aggressive: reframe into the JD's vocabulary, reorder to
   what the job weights, claim adjacent skills where evidence supports it. Per-job tweaking
   is the product, not a risk to be managed.
2. **No headcount claims.** Sameer works solo end to end. Never "led a team", "managed",
   "mentored". Frame scope as ownership and range. Enforced in `modules/prompts.py`.
3. **No em dashes.** Anywhere. In generated output, in this repo's docs, in comments.
   `HOUSE_STYLE` in `modules/prompts.py` carries the rule into every prompt.
4. **Client names withheld.** Descriptors only: "a national QSR chain", "an ASX-listed
   agribusiness", "a top-5 Australian distributor".
5. **Never assume a model id exists.** Every id first configured from a published guide was
   dead on contact, and several models the account's own /models endpoint lists return 404
   when invoked. Pick with `scripts/probe_models.py`, confirm with `scripts/check_models.py`,
   and record what you found. Listing is not the same as callable.
6. **There is no scout and no VM worker.** Jobs arrive as a pasted JD or a URL, nothing else.
   Do not add job-board adapters or a second machine without asking. Dropped 29 Aug 2026;
   the reasoning is in `DECISIONS.md`.

## Environment

- **Python 3.9.6.** Put `from __future__ import annotations` at the top of every module.
  `list[str]` and `X | Y` in annotations are fine with it; at runtime they are not.
- `openai` is not installed and is not needed. NIM is OpenAI-compatible and called over
  `httpx` in `modules/llm.py`.
- Provider keys are read at import but **not required** at import. Seeding, rendering and
  the ATS audit all work with no keys at all. `modules/llm.py` raises at call time instead.

## Cost

Every primary stage runs on NVIDIA NIM's free tier via `NIM_API_KEY`. The fallback is
`anthropic:claude-sonnet-5`, which is a PAID call and fires only when a NIM route fails.
`modules/llm.py` logs it at WARNING with the literal string `*** PAID CALL ***` so it can
never bill quietly. If fallbacks start appearing in the log, fix the NIM route rather than
letting it run on Claude.

## Flags

`STRICT_NUMBERS` (default true) blocks any figure not present in the facts a block cites.
It only fires on invented numbers. Sameer's call whether it stays on; do not argue it again.

`config.describe()` prints one greppable line with the full flag and routing state.

## Layout

```
config.py              env, per-stage LLM routing, fail fast on structure not credentials
database/models.py     two table families that must not touch (see SPEC.md)
modules/llm.py         provider router. complete() and complete_json()
modules/prompts.py     HOUSE_STYLE, TRUTH_CONTRACT, NO_HEADCOUNT
modules/extract.py     JD text to a typed Extraction
modules/tailor.py      facts + requirements to graded blocks, then to_payload()
modules/render_docx.py the truth gate, ATS-safe writer, and audit()
modules/ats.py         the ATS gate. simulate_parse(), check(), gate()
data/profile_facts.json  the career source of truth. Hand-edit, then re-seed
scripts/seed_profile.py  loads that JSON into ProfileFact
scripts/check_models.py  verify routes resolve. --list shows what the key can reach
scripts/probe_models.py  rank candidate models on real work. --task extract|tailor
tests/test_guards.py     truthfulness guards. A plain script, not pytest
tests/test_ats.py        the ATS gate, against real rejection modes
```

## Conventions carried over from an earlier production system

- Forms beat parsers. If intent arrives as prose you regex, the fix is a form field.
- The screen is the notification. Do not email what the UI already shows.
- Every scheduled job needs a manual trigger too. A fixed cron silently drops late input.
- Log the flags, not just the events. `config.describe()` prints one greppable line.
- Soft deletes. Nothing is hard deleted; status columns carry the state.
