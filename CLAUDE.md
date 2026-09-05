# Job_App, working notes for Claude

A local macOS app that runs the user's India job search. Three sections, one of which is
deliberately isolated from the other two.

**Read `TRACKER.md` first** for current status and what is blocked.
Then `SPEC.md` for what it does and `DECISIONS.md` for why it is shaped this way.
Patterns are borrowed from an earlier system of his (FastAPI + SQLite + APScheduler
+ LLM + human approval gate). That project is not a dependency and is not read at runtime.

## Committing. Do this without being asked.

**At the end of every session, and whenever he says pause, stop or we are done:
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

## Replies

**Keep them short.** Answer the question, say what changed, stop. No recaps of what he
just asked, no summaries of work he watched happen, no bullet lists restating the same
point three ways. If something needs a long explanation, that is a sign it needs a
decision from him, so ask the one question instead of writing the essay.

## What may not go in a tracked file

**Write every doc, comment and commit message as though the repo were public.** It is
private today. Private is a setting, and a setting is one click and one careless invite
from being something else. Nothing here is worth the bet.

Out of the docs entirely:

- **His name.** "the user", "he", "his". A repo full of career figures is one search away
  from being about a specific person, and the code does not need to know who he is
- **Client names**, and the figures attached to them. `$19.3M` and `$7.35M` are real
  findings from real engagements belonging to his employer. Where a doc needs a number to
  illustrate a rule, invent one
- **Employers he has worked for or applied to.** "a US bank", "the treasury role". The
  point being made is never about which company it was
- **Contact details.** Email, phone, address. They live in the database and in
  `data/profile_facts.json`, both gitignored, and nowhere else
- **Anything from `Assets/`**, which holds unredacted client names

Two files are expected to match that scan and are excluded from it. This one, because a
scan for a name has to contain the name. And `desktop/build_app.sh`, whose bundle
identifier carries his name and must keep it: macOS keys the Full Disk Access grant to
that string, so renaming it costs him another trip through System Settings and buys
nothing, since a bundle id is never published anywhere.

None of this applies to `data/`. That is where his real record belongs and every file in
it that carries one is gitignored. The rule is about what gets committed.

Before committing, the scan below covers credentials. This one covers him:

```bash
git grep --cached -niE "sameer|nando|nufarm|5ways|19\.3M|7\.35M" \
  | grep -vE "^CLAUDE.md:|^desktop/build_app.sh:"
```

## Restarting the local server

`pkill -f "python3 main.py"` does not match it. macOS runs it as
`.../Python.app/Contents/MacOS/Python main.py`, so the pattern misses, the old process
keeps port 8100, and the replacement exits without binding. Two days of live checks were
read off a stale build that way while the tests, which build their own app object, were
right the whole time.

```bash
pkill -f "main.py"; sleep 2; nohup python3 main.py >> ~/Library/Logs/JobApp.log 2>&1 &
ps -ax | grep "[m]ain.py"     # confirm the start time before trusting a live page
```

## Scope boundary

**This directory is the whole project.** Do not read from, write to, or reference anything
outside `Job_App/`. If the app needs a fact about him it goes in `data/profile_facts.json`.
That file is this project's own copy, on purpose, so nothing here depends on work that
lives elsewhere.

## Hard rules

1. **Every claim cites a fact.** A block citing nothing is blocked at render, not just
   discouraged in a prompt. See `modules/render_docx.py::gate` and
   `modules/tailor.py::_validate`. Do not route around it.
   Tailoring itself is meant to be aggressive: reframe into the JD's vocabulary, reorder to
   what the job weights, claim adjacent skills where evidence supports it. Per-job tweaking
   is the product, not a risk to be managed.
2. **No headcount claims.** He works solo end to end. Never "led a team", "managed",
   "mentored". Frame scope as ownership and range. Enforced in `modules/prompts.py`.
3. **No em dashes.** Anywhere. In generated output, in this repo's docs, in comments.
   `HOUSE_STYLE` in `modules/prompts.py` carries the rule into every prompt.
4. **Client names withheld.** Descriptors only: "a national QSR chain", "an ASX-listed
   agribusiness", "a top-5 Australian distributor".
5. **Never assume a model id exists.** Every id first configured from a published guide was
   dead on contact, and several models the account's own /models endpoint lists return 404
   when invoked. Pick with `scripts/probe_models.py`, confirm with `scripts/check_models.py`,
   and record what you found. Listing is not the same as callable. This has now happened
   three times, most recently when the model every primary stage ran on went 410 Gone
   mid-session with no warning. Assume it will happen again and re-probe rather than
   guessing a replacement.
6. **What may never be claimed lives in `modules/house.py`, and it is enforced.** Two
   masters degrees and specifically not an MBA, because a background check verifies the
   exact title. No experience in HR, workforce, field operations, marketplace, mobility,
   quick commerce, logistics or FMCG, none of which appear on the record. The ban is on
   the claim and not on the word: pricing freight keeps "logistics" and "six years in
   logistics" is blocked. Do not widen it to bare nouns; that was the first version and
   it deleted true bullets.
7. **Style rules and truth rules get opposite treatment.** `house.natural_language` reports
   and never blocks, because a stiff sentence is not a lie. `house.claims_a_degree` and
   `house.claims_a_domain` block at `tailor._validate`. Do not move either across the line.
8. **Justified body text is settled.** He asked for it and overrode the advice against it.
   Headings, role titles and date lines stay left aligned. Do not raise it again.
9. **The writer is a loop, and the loop may never loosen a gate.** `modules/agent.py`
   tailors, reads which requirements no bullet answers and which blocks the gates
   refused, and sends only those back for repair. Every replacement goes through the same
   `tailor._validate`. A round can produce a better sentence; it can never produce
   permission. A round that returns nothing is the correct outcome on a real gap, so do
   not "fix" the loop by making it retry harder.
10. **There is no scout and no VM worker.** Jobs arrive as a pasted JD or a URL, nothing else.
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

Extended thinking is disabled on the Anthropic path. Its tokens come out of the same
`max_tokens` budget as the answer, and on the tailor prompt it spent all of it reasoning
and returned no text at all. Raising the budget is not the fix: past a certain size the
SDK requires streaming. See `ERROR_LOG.md`.

`STRICT_NUMBERS` (default true) blocks any figure not present in the facts a block cites.
It only fires on invented numbers. His call whether it stays on; do not argue it again.

`config.describe()` prints one greppable line with the full flag and routing state.

## Layout

```
config.py              env, per-stage LLM routing, fail fast on structure not credentials
database/models.py     two table families that must not touch (see SPEC.md)
modules/llm.py         provider router. complete() and complete_json()
modules/prompts.py     HOUSE_STYLE, TRUTH_CONTRACT, NO_HEADCOUNT, NEVER_CLAIM
modules/house.py       what is true about him. Degrees, unworked domains, writing tells
modules/design.py      the specs he uploads. How a resume is written, not what is true
modules/extract.py     JD text to a typed Extraction
modules/tailor.py      facts + requirements to graded blocks, then to_payload()
modules/agent.py       the writing loop. Repairs what the posting asked for and the
                       draft does not answer. Cannot loosen a gate
modules/families.py    which field a posting is in, and what that reader screens on
modules/render_docx.py the truth gate, ATS-safe writer, and audit()
modules/ats.py         the ATS gate. simulate_parse(), check(), gate()
data/profile_facts.json  the career source of truth. Hand-edit, then re-seed
scripts/seed_profile.py  loads that JSON into ProfileFact
scripts/check_models.py  verify routes resolve. --list shows what the key can reach
scripts/probe_models.py  rank candidate models on real work. --task extract|tailor
tests/test_guards.py     truthfulness guards. A plain script, not pytest
tests/test_ats.py        the ATS gate, against real rejection modes
tests/test_house.py      his standing rules. Mostly negative cases, which are the point
```

## Conventions carried over from an earlier production system

- Forms beat parsers. If intent arrives as prose you regex, the fix is a form field.
- The screen is the notification. Do not email what the UI already shows.
- Every scheduled job needs a manual trigger too. A fixed cron silently drops late input.
- Log the flags, not just the events. `config.describe()` prints one greppable line.
- Soft deletes. Nothing is hard deleted; status columns carry the state.
