# TRACKER

Status for the Job App. Start here.

**Paused 25 Aug 2026.** Backbone and the writer modules are built and tested. Blocked on a
NIM key for anything that makes a live model call.

Scope note: this directory is the whole project. It does not read from, write to, or depend
on anything outside `Job_App/`. The LinkedIn profile work is finished and lives elsewhere.

---

## Free to run, with a paid safety net

`NIM_API_KEY` from build.nvidia.com drives everything. Free, email only, no card.
`ANTHROPIC_API_KEY` is the fallback and only bills when a NIM route fails.

| Stage | Model | Why |
|---|---|---|
| `score` | `deepseek-ai/deepseek-v4-flash` | Highest volume. 284B MoE, ~1M context, best latency/quality |
| `extract` | `deepseek-ai/deepseek-v4-flash` | Long JDs fit whole |
| `tailor` | `deepseek-ai/deepseek-v4-pro` | 89/100 reasoning. Self-grading is reasoning work. Thinking model, temperature clamped to 0 |
| `cover` | `moonshotai/kimi-k2.6` | 87/100, strong prose |
| `screening` | `deepseek-ai/deepseek-v4-flash` | Short answers |
| `brief` | `deepseek-ai/deepseek-v4-flash` | Question generation from a JD |
| fallback | `anthropic:claude-sonnet-5` | **Paid.** Fires only when a NIM route fails. Logged at WARNING with the word PAID |

`normalise` is not a stage. Mapping a job board's JSON onto our `Find` record is
find-and-copy work, so it becomes plain Python when the adapters land.

Every primary route is free. The fallback is Claude, which is paid, and only fires when a
NIM route errors or returns junk. When that happens the log says `FALLBACK USED *** PAID
CALL ***` naming the stage, so it can never bill you quietly.

---

## Slices

| # | Slice | Status |
|---|---|---|
| 0 | Backbone: config, models, db, LLM router, prompts, renderer | ✅ done, tested |
| 1 | Writer core: `extract`, `tailor`, truth guards, ATS gate | ⏸ **written, unrun** |
| 2 | `fetch_jd` — job URL to clean JD text | ☐ needs no key |
| 3 | App shell: `main.py`, `run.command`, three-section nav | ☐ |
| 4 | Scout adapters and Scout Finds | ☐ needs Adzuna / RapidAPI keys |
| 5 | VM worker and sync | ☐ needs SSH + tunnel hostname |
| 6 | Cover letter and screening answers | ☐ |
| 7 | Interview Brief | ☐ |

## What is proven

```
tests/test_guards.py                          20 passed, 0 failed
  fabricated fact id rejected, invented id stripped, real id survives
  drifted money figure blocked, drifted count blocked
  figures from fact text and from fact metrics both allowed
  headcount claims blocked, ownership language allowed
  render gate stops blocked / unaccepted inferred / unaccepted stretch
  payload excludes unaccepted reframings

tests/test_ats.py                             21 passed, 0 failed
  contact block, dates, keyword floor and format all block correctly
  well-formed resume scores 100/100 and exports
  broken resume scores 28/100 and export is refused

render_resume -> audit()                      ATS clean, all five headings parsed
scripts/seed_profile.py                       63 facts loaded
scripts/check_models.py                       refuses to report success on an unverified run
```

## What is unproven

`extract` and `tailor` have never made a live call. What cannot be reasoned about without
running it is prompt behaviour: whether the routed model returns the JSON shape reliably,
cites honestly, and grades its own reach sensibly.

`scripts/bakeoff.py` settles it by running the same JD through two NIM models side by side.

---

## Blocked on Sameer

| | Blocks |
|---|---|
| **NIM API key** into `.env` | Slice 1 verification and everything after |
| Adzuna app id + key, RapidAPI key (both free tier) | Slice 4 only |
| SSH access + spare tunnel hostname | Slice 5 only |

### Four unverified facts

In `data/profile_facts.json`, excluded from citation until confirmed and flipped to
`"verified": true`:

1. **Notice period**, and whether November 2026 availability still holds
2. **Expected CTC** range in INR
3. **Reason for moving back to India**, in your own words. First thing every Indian
   recruiter asks, so it belongs in the answer bank in your phrasing
4. **Phone number** is an Australian mobile. Indian recruiters call

---

## Settled decisions

Full rationale in `DECISIONS.md`. The ones most likely to be re-litigated:

- **Tailoring is aggressive.** Reframes into the JD's vocabulary, reorders to what the job
  weights, claims adjacent skills with evidence. Per-job tweaking is the product
- **`STRICT_NUMBERS` is a flag, default on.** Blocks figures not present in cited facts.
  Only fires on invented numbers. Set false in `.env` to disable
- **Interview Brief does three things only**: likely questions, company details, what to ask
  them. No story matching, no resume tie-in. That is why it stays standalone
- **Free primaries, paid fallback.** Every stage runs on NIM. Claude catches failures and announces itself in the log when it does
- **No auto-apply.** Nothing is submitted without you reading it
- **APIs only, no scraping.** LinkedIn returns HTTP 999 to automation
- **No agents.** Every stage is a single call. Tool calling works on the free tier, it is
  just not needed here

---

## Session log

| Date | What happened |
|---|---|
| 25 Aug 2026 | Backbone built. `extract` and `tailor` written, 20 guard tests passing. Model routing researched and corrected to the 2026 catalogue. Moved into the project root. Paused |
