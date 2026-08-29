# PROGRESS

> **Paused 25 Aug 2026.** See `TRACKER.md` for overall status across both phases.

## Built

Backbone only. No section logic yet, by design.

- `config.py` — env, per-stage LLM routing, `describe()` one-line flag state,
  `missing_keys()`. Fails fast on malformed routes, not on absent credentials
- `database/models.py` — all seven tables, with the Section 3 boundary documented in place
- `database/db.py` — engine, `SessionLocal`, `init_db`
- `modules/llm.py` — provider router. `complete()`, `complete_json()`, `health()`.
  NIM over httpx, Anthropic over the SDK, loud fallback
- `modules/prompts.py` — `HOUSE_STYLE` (em dash and cliché rules, lifted verbatim),
  `TRUTH_CONTRACT`, `NO_HEADCOUNT`
- `modules/render_docx.py` — `gate()`, ATS-safe writer, `audit()`, `keyword_coverage()`
- `data/profile_facts.json` — 63 career facts, seeded and verified
- `scripts/seed_profile.py` — idempotent loader
- `modules/extract.py` — JD to typed `Extraction`. Normalises hard so nothing downstream
  can hit an IndexError. Detects login walls and refuses them
- `modules/tailor.py` — facts plus requirements to graded `Block`s, then `to_payload()`.
  Three hard guards run on every block, independent of the prompt
- `tests/test_guards.py` — 20 assertions covering all three guards, no API key needed

## Verified working

```
gate blocks blocked / unaccepted stretch / empty fact_ids   pass
gate allows verified + cited                                pass
render_resume -> audit: ATS clean, no problems              pass
all five section headings found by the parser               pass
seed_profile: 58 facts (4 roles, 20 bullets, 23 skills,
              3 education, 2 certs, 1 publication, 5 answers)
```

## Next, in order

1. `modules/fetch_jd.py` — job URL to clean JD text. No key needed, testable now
2. Run `extract` and `tailor` against real JDs once the NIM key lands. The code is written
   and the guards are tested; what is unproven is prompt behaviour, which cannot be
   reasoned about without running it
3. `scripts/bakeoff.py` — same JD through NIM and Claude, side by side
4. `main.py` and `webapp/` — local server, three-section nav, Writer wired in
5. `run.command` — double-clickable launcher
8. Cover letter, screening answers
9. Interview Brief

## Waiting on Sameer

- **NIM API key** (build.nvidia.com) into `.env` as `NIM_API_KEY`. Every guard and the
  renderer work without it. `extract` and `tailor` are written but unrun
- Four unverified facts in `data/profile_facts.json`, excluded from citation until
  confirmed: notice period, expected CTC, reason for moving back to India, and the phone
  number, which is currently an Australian mobile
