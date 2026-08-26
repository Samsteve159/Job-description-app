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
| 1 | Writer core: `extract`, `tailor`, truth guards, ATS gate | **done, run live** |
| 2 | `fetch_jd`: job URL to clean JD text | next. Needs no key |
| 3 | App shell: `main.py`, `run.command`, three-section nav | not started. **This is what a local link needs** |
| 4 | Scout adapters and Scout Finds | blocked on Adzuna / RapidAPI keys |
| 5 | VM worker and sync | blocked on SSH + tunnel hostname |
| 6 | Cover letter and screening answers | not started |
| 7 | Interview Brief | not started |

## What is proven

```
tests/test_guards.py                          34 passed, 0 failed
tests/test_ats.py                             29 passed, 0 failed

scripts/run_job.py, live, Wells Fargo JD:
  extract   title, company, location, seniority and archetype all correct
            12 must-haves, 14 nice-to-haves, 42 keywords
  tailor    12 to 13 blocks, 0 rejected, citations all real
  render    ATS-safe docx, five headings, contact block extracts
  ATS gate  blocked the export on keyword coverage, which is the gate working
```

Both money figures the model produced (`$19.3M`, `$120M`) traced to the metrics of the facts
it cited. The citation and number guards behave on real output the way they behave on
fixtures.

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
| Anthropic key | The fallback, and the bake-off that would settle model choice |
| Adzuna app id + key, RapidAPI key (both free tier) | Slice 4 only |
| SSH access + spare tunnel hostname | Slice 5 only |

### Four unverified facts

In `data/profile_facts.json`, excluded from citation until confirmed and flipped to
`"verified": true`. The live run withheld all four.

1. **Notice period**, and whether November 2026 availability still holds
2. **Expected CTC** range in INR
3. **Reason for moving back to India**, in your own words. First thing every Indian
   recruiter asks, so it belongs in the answer bank in your phrasing
4. **Phone number** is an Australian mobile, and it is currently rendering on the resume.
   Indian recruiters call

---

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
| 26 Aug 2026 | First live run, Wells Fargo Lead Treasury Analyst JD. `scripts/run_job.py` added to drive the whole pipeline from one command. Two defects found and fixed: non-ASCII punctuation reaching the page, and tenure claims going unchecked. Tests 41 to 63 |
