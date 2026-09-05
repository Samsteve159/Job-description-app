# ERROR_LOG

Incidents and their root causes. Newest first.

## 2026-09-05: three must-have keywords could never go green

The pills beside each requirement on the review screen were coloured by membership in
`extraction.must_keywords()`, the scored keyword list. The pills themselves sit beside
`extraction.must` and display `r.keyword`. Those are two different vocabularies produced
by two different parts of extract.

On a real posting, three of the six keywords on screen, "timely decisions", "risk
management" and "risk and controls", were absent from the scored list, so they showed red
permanently no matter what the document said. Two of them were written in full in the
draft. A false gap is worse than no signal, because it sends him looking for evidence to
close something that was never open.

Fixed by deciding the colour from the document text rather than from which list a term
came from, and by building the set from the keywords actually on screen.

The same screen also had one colour doing two jobs. A keyword was red both when nothing
in the record supported it and when it was written in a block he had not ticked yet. The
first is a gap and the second is one click. There are three states now.

## 2026-09-05: every server restart for two days was a silent no-op

Restarts were being issued as `pkill -f "python3 main.py"`. On macOS the process shows up
as `.../Python.app/Contents/MacOS/Python main.py`, so the pattern never matched. The old
process kept port 8100, each new one failed to bind and exited without a sound, and every
live check for two days was reading code from the previously running build.

The test suite was never affected, since it builds its own app object, and it was the
tests that were right each time the browser disagreed with them. Match on `main.py`
alone, and check the process start time before trusting anything a live page says.

## 2026-09-03: the model every main stage ran on reached end of life mid-session

`openai/gpt-oss-120b` stopped answering at 08:00 UTC and began returning 410 Gone with
the end-of-life date in the body. Every primary stage was routed to it, so every stage
fell through to the paid Anthropic fallback in a single session. Nothing warned in
advance; the first sign was a run of `*** PAID CALL ***` lines in the log.

The replacement was picked the way the last one was, by sending real work rather than
reading a list. Of the fourteen ids on the key's own /models response that day, six
returned 404 when actually invoked and two more timed out. Two survived the tailor task.

This is the third time a listing has disagreed with reality, so the rule holds: a model
that appears in /models is a candidate, not a route.

## 2026-09-03: the fallback burned its whole budget thinking and returned nothing

With the primary route dead, tailor fell through to `anthropic:claude-sonnet-5` and
failed there too, with `empty response`. That message was the actual defect: it named a
symptom shared by three unrelated causes, so it pointed nowhere.

Making the error say why took one edit and produced the answer immediately:

    stop_reason=max_tokens blocks=thinking in=11786 out=12000 of max_tokens=12000

Extended thinking is adaptive and its tokens come out of the same `max_tokens` budget as
the answer. On a small prompt it is not used and nothing looks wrong. On the tailor
prompt, which is roughly 19k of system instructions before the uploaded house spec is
added, the model spent all 12,000 tokens reasoning and emitted no text block at all.

Two things fixed:

1. Thinking is now explicitly disabled on the Anthropic path. The budget could not simply
   be raised: past a certain size the SDK requires streaming. With thinking off the same
   call answers in 2,480 tokens.
2. Both optional Anthropic parameters, `temperature` and `thinking`, now drop themselves
   and remember the model when a 400 says they are unsupported. Previously only
   `temperature` did, and a model refusing the other would have taken the safety net down.

The general lesson is about the error text, not the parameter. An exception that names a
symptom instead of a cause costs more than the bug does.

## 2026-08-25: every model id taken from a published guide was dead

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

## 2026-08-25: probe died on the first model that hung

The first version of the probe caught only LLMError, so an httpx.ReadTimeout on candidate
one killed the whole run and produced nothing for the other thirteen.

Fix: catch every exception per candidate, run candidates in parallel with a short per-model
timeout, and report the timeout as a result rather than an abort.

## 2026-08-25: check_models reported success having verified nothing

With no provider key set, every route was skipped and the script still printed
"All configured routes resolve". A green light meaning "I checked nothing" is exactly the
failure the script exists to prevent.

Fix: track what was actually verified. If nothing was, say NOTHING VERIFIED and exit 1.

## 2026-08-25: config required provider keys to do offline work

`Config.__post_init__` used `_require_if` for `NIM_API_KEY`, so `scripts/seed_profile.py`
crashed at import on a machine with no keys, despite touching no API.

Fix: provider keys are read but not required at import. `modules/llm.py` checks them at call
time and raises a clear message. Structural config (malformed routes) still fails fast,
because that is a bug rather than a missing credential.
