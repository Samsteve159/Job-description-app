# TRACKER

Status for the Job App. Start here.

**Updated 26 Aug 2026.** The writer pipeline has now run live, end to end, against a real
job description. Two defects it exposed are fixed. The web app is not built, so there is no
local link yet.

Scope note: this directory is the whole project. It does not read from, write to, or depend
on anything outside `Job_App/`. The LinkedIn profile work is finished and lives elsewhere.

---

## Free to run, with a paid safety net

`NIM_API_KEY` from build.nvidia.com drives everything. Free, email only, no card.
`ANTHROPIC_API_KEY` is the fallback and only bills when a NIM route fails. **Not yet set.**

Model ids below are what `config.STAGE_DEFAULTS` actually resolves to. They were chosen by
running `scripts/probe_models.py` against the live key, not from a published list.

| Stage | Model | Why |
|---|---|---|
| `score` | `minimaxai/minimax-m3` | Highest volume. Fastest usable model on the probe |
| `extract` | `minimaxai/minimax-m3` | 2.6s. Got seniority, all must-haves and 15 keywords right |
| `tailor` | `openai/gpt-oss-120b` | 3.2s. Fastest model that held the citation shape on the hard task |
| `cover` | `openai/gpt-oss-120b` | Prose quality still untested. Bake off before trusting it |
| `screening` | `minimaxai/minimax-m3` | Short answers |
| `brief` | `minimaxai/minimax-m3` | Question generation from a JD |
| fallback | `anthropic:claude-sonnet-5` | **Paid.** Fires only on a NIM failure. Logged at WARNING with the word PAID |

`nvidia/nemotron-3-super-120b-a12b` is configured as `NIM_BIG`, unused, kept as a bake-off
candidate.

`normalise` is not a stage. Mapping a job board's JSON onto our `Find` record is
find-and-copy work, so it becomes plain Python when the adapters land.

---

## Slices

| # | Slice | Status |
|---|---|---|
| 0 | Backbone: config, models, db, LLM router, prompts, renderer | done, tested |
| 1 | Writer core: `extract`, `tailor`, truth guards, ATS gate | done, run live |
| 2 | `fetch_jd`: job URL to clean JD text | **done, run against real postings** |
| 3 | App shell: modules, dashboard, tracker, `run.command` | **done. It opens in a browser** |
| 4 | Scout adapters and Scout Finds | blocked on Adzuna / RapidAPI keys |
| 5 | VM worker and sync | blocked on SSH + tunnel hostname |
| 6 | Cover letter and screening answers | not started |
| 7 | Interview Brief | not started |
| 8 | Gmail application scan | **blocked on a Google Cloud OAuth client** |
| 9 | Post Writer module | blocked on a port-or-rebuild decision |

## Running it

```bash
./run.command          # or: python3 main.py
```

Serves on `http://127.0.0.1:8100`. The loopback bind is the security boundary, which is
why there is no login. `main.py` warns if HOST is ever set to anything else.

Two modules, Job App and Post Writer, with a switcher in the header. Job App lands on a
dashboard: applications sent, how many are live, how many reached a human, how far they
got. Packages sit below it. Errors are modals rather than a strip at the top of the page,
because an export being refused is not something to find out later.

Light and dark follow the OS by default with an explicit override that survives a reload,
applied before first paint so a dark-mode user never sees a white flash.

## The bake-off, and what it settled

`scripts/bakeoff.py` runs one JD through several models repeatedly and measures keyword
coverage, spread across runs, fabricated citations, drifted numbers, wrong tenure and
non-ASCII output. It disables the fallback first, because measuring a model with a safety
net armed measures the safety net.

| Model | Mean coverage | Spread | Secs | Notes |
|---|---|---|---|---|
| `nim:openai/gpt-oss-120b` | 61%, then 100% | 45%, then 0% | 20s | Fast. Inconsistent between sessions |
| `nim:minimaxai/minimax-m3` | 100% on its one completed run | n/a | 38s | **Rate limited, HTTP 429 on the free tier** |
| `nim:nvidia/nemotron-3-super-120b-a12b` | n/a | n/a | n/a | Unparseable JSON on every run |
| `anthropic:claude-sonnet-5` | 64% | 57% | 86s | Swings as much as the free model, at four times the latency |

**The finding that matters: the instability is prompt-side, not model-side.** Claude, at
real money per call, swings 57 points on the same measure. Paying more would not have
fixed it. Keyword placement needs a deterministic pass. That is the next writer job.

Two bugs found on the way:

- `max_tokens=6000` truncated Claude mid-JSON on two runs in three, which presents as a
  model that cannot follow a schema. It was a budget, not a capability. Now 12000
- `temperature` is deprecated for `claude-sonnet-5`, which returns 400 at any value but
  the default. That took the fallback down on its first real health check. `modules/llm.py`
  now learns which models reject it and retries without it once

## What is proven

```
tests/test_ats.py        29      the ATS gate against real rejection modes
tests/test_contact.py    23      contact details, and surviving a re-seed
tests/test_fetch_jd.py   30      refusing login walls and bot checks
tests/test_guards.py     34      citations, numbers, tenure, headcount, the render gate
tests/test_tracker.py    28      counting, the high-water mark, silence
tests/test_webapp.py     40      every screen, and that a screen cannot skip a gate
                        ---
                        184 passing
```

The webapp suite's most important case: accept a reaching block, build, untick it, and
the download closes again. Without that, unticking left the passing file on disk and the
download route served it, because that route checks the file rather than the choices.

## What the first live run exposed, and what was done about it

**1. Non-ASCII punctuation reached the page.** The model emitted U+2011 NON-BREAKING HYPHEN
in four blocks. `audit()` screened only for em and en dashes, so the file passed clean. A
parser can mangle it and a keyword filter comparing `AI<U+2011>agent` to `ai agent` scores zero.
Fixed: `render_docx.plain_text()` normalises fifteen characters at write time, and `audit()`
now reports any non-ASCII character rather than two specific ones.

**2. Tenure claims were completely unguarded.** The run described nine years of history as
"6+ years", against a posting asking for 5+. Every existing guard passed it, because
`_NUMBER` matches currency and percentages and a bare year count is neither.
Fixed: `tailor.experience_years()` computes the figure from the role dates, summing across
roles so a study gap is not claimed as experience and overlapping roles are not counted
twice. It is passed into the prompt so the model never estimates, and `_validate` blocks any
claim more than a year off. It runs before the drift check and exempts its own digits, so a
correct "10 years" is not misread as an invented number.

The second run said "10 years of experience". Computed figure is 9.8.

## What is still unproven

- **Keyword placement.** The live run put 57% of must-have terms on the page against a 70%
  floor, so the export was refused. Some gaps are real (product owner, backlog management).
  Some are the model choosing its own words for something you have done (corporate treasury,
  data validation). Worth a bake-off before deciding it is a model problem or a prompt one
- **Run-to-run stability.** Three runs of the same JD produced materially different bullets
  and coverage between 57% and 88%. That variance is the strongest argument for a bake-off
- **Cover letter prose.** Never generated

---

## Blocked on Sameer

| | Blocks |
|---|---|
| **Google Cloud project + OAuth client for Gmail** | Counting applications automatically. Only he can create it |
| Adzuna app id + key, RapidAPI key (both free tier) | Scout Finds |
| SSH access + spare tunnel hostname | The always-on VM worker |
| Port the existing Post Writer in, or rebuild it here | Module two |

### Three unverified facts

In `data/profile_facts.json`, excluded from citation until confirmed and flipped to
`"verified": true`. Every live run withheld them.

1. **Notice period**, and whether November 2026 availability still holds
2. **Expected CTC** range in INR. Currently literal `PLACEHOLDER` text
3. **Reason for moving back to India**, in his own words

The phone number is settled: it stays Australian, and `modules/contact.warnings()`
mentions it without blocking.

## Settled decisions

Full rationale in `DECISIONS.md`. The ones most likely to be re-litigated:

- **Tailoring is aggressive.** Reframes into the JD's vocabulary, reorders to what the job
  weights, claims adjacent skills with evidence. Per-job tweaking is the product
- **`STRICT_NUMBERS` is a flag, default on.** Blocks figures not present in cited facts.
  Only fires on invented numbers. Set false in `.env` to disable
- **Interview Brief does three things only**: likely questions, company details, what to ask
  them. No story matching, no resume tie-in. That is why it stays standalone
- **Free primaries, paid fallback.** Every stage runs on NIM. Claude catches failures and
  announces itself in the log when it does
- **No auto-apply.** Nothing is submitted without you reading it
- **APIs only, no scraping.** LinkedIn returns HTTP 999 to automation
- **No agents.** Every stage is a single call

---

## Session log

| Date | What happened |
|---|---|
| 25 Aug 2026 | Backbone built. `extract` and `tailor` written, 20 guard tests passing. Model routing researched and corrected against the live catalogue. Moved into the project root. Paused |
| 26 Aug 2026 (later) | App shell: two modules, dashboard, tracker, modal errors, light and dark. `fetch_jd` done. Bake-off run, which found the variance is prompt-side not model-side. Tests 63 to 184 |
| 26 Aug 2026 | First live run, Wells Fargo Lead Treasury Analyst JD. `scripts/run_job.py` added to drive the whole pipeline from one command. Two defects found and fixed: non-ASCII punctuation reaching the page, and tenure claims going unchecked. Tests 41 to 63 |
