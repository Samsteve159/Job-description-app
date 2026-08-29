# Job Description App

A local desktop app for running a job search. You give it a job description, it produces a
tailored, ATS-safe resume where **every line traces back to something you actually did**.

Runs on your own machine. Your career history and generated documents never leave it.

> Status: in build. The writer pipeline, both gates, the fit score, the tracker and the
> web UI are implemented and tested. The interview brief is not built yet. Job scouting
> was dropped on purpose. See `TRACKER.md`.

## Why this exists

Most resume tailoring tools have the same failure mode: ask a model to match a job
description and it will happily write experience you do not have. It reads well, it matches
the keywords, and it falls apart in the first interview.

The other failure mode is quieter. A resume that is honest and well written still gets
filtered out before a human sees it, because an applicant tracking system could not extract
the contact block, or could not parse the dates, or did not find the words the job posting
actually used.

This app is built around those two problems. Two independent gates stand between a model
and the finished file, and neither of them is a prompt.

## The two gates

### Truth gate

Every generated block must cite the facts it drew on, by id, from a store you control.
`modules/tailor.py::_validate` then runs three checks that do not trust the model at all:

| Check | What it stops |
|---|---|
| Citation validation | Fact ids that do not exist are stripped. A block left with no surviving citation is blocked |
| Number validation | Every figure must appear in a cited fact. This is what stops `$19.3M` quietly becoming `$29.3M` |
| Scope validation | Configurable claims you must never make, for example team leadership if you work solo |

`modules/render_docx.py::gate` then refuses anything still graded `blocked`, and anything
graded `inferred` or `stretch` that a human has not accepted. It raises rather than
filtering, so a caller cannot skip it by accident.

Tailoring itself is deliberately aggressive. It reframes into the job's vocabulary, reorders
to what the posting weights, and claims adjacent skills where evidence supports it. The point
is not to be timid, it is to be aggressive **and** traceable.

### ATS gate

`modules/ats.py` does not inspect the file's markup. It **simulates the extraction** an
applicant tracking system performs, then scores the result out of 100.

| Check | Blocking | Why |
|---|---|---|
| No tables, text boxes, drawings, headers, footers | yes | Parsers flatten or drop these |
| Name, email and phone extract from the top | yes | The most common cause of a dropped application |
| At least three standard section headings | yes | A parser cannot segment the document without them |
| A parseable date range for every role | yes | Tenure is calculated from these |
| Must-have keyword coverage above 70% | yes | What the filter literally screens on |
| Nice-to-have keyword coverage | no | Free ranking points |
| Length | no | Warning only |

Neither gate uses a model, on purpose. A real parser is literal and stupid, so the checker
has to be too. An LLM would helpfully decide that "procurement analytics" satisfies a
requirement written as "spend analysis". The filter will not. And a fabrication check that
can be reasoned with is a fabrication check that can be talked around.

## Pipeline

```
fetch_jd   job URL or pasted text  ->  clean job description
extract    job description         ->  requirements, keywords, seniority, employer type
tailor     your facts + those      ->  graded blocks, each citing fact ids
  truth gate                           strips fabrications, blocks drifted numbers
render     accepted blocks         ->  ATS-safe .docx
  ATS gate                             simulates parsing, scores, can refuse export
```

## Model routing

Every stage names itself, and `config.STAGE_DEFAULTS` maps that name to a `provider:model`
string. Switching one stage is a single environment variable, and nothing else moves.

Primary routes run on [NVIDIA NIM](https://build.nvidia.com)'s free tier. A paid provider can
be configured as a fallback; when it fires, the log says so explicitly rather than billing
quietly.

Model ids in this repo were chosen by running `scripts/probe_models.py` against a live key,
not by reading a list. The first set, taken from a published guide, were all dead on arrival.
Several models that a NIM account's own `/models` endpoint lists return 404 when invoked.
Being listed is not the same as being callable.

## Setup

```bash
python3 -m pip install -r requirements.txt

cp .env.example .env                                   # add your NIM_API_KEY
cp data/profile_facts.example.json data/profile_facts.json
# edit profile_facts.json with your own career, then:
python3 scripts/seed_profile.py
```

`data/profile_facts.json` is the source of truth and is gitignored. The database is derived
from it, so edit the JSON and re-seed rather than touching SQLite. Entries marked
`"verified": false` are withheld from citation entirely until you confirm them.

## Commands

```bash
python3 tests/test_guards.py            # truth gate
python3 tests/test_ats.py               # ATS gate
python3 scripts/check_models.py         # verify every configured route resolves
python3 scripts/check_models.py --list  # what your key can actually reach
python3 scripts/probe_models.py         # rank candidate models on real work
```

Tests are plain scripts rather than pytest. They print a line per assertion and exit non-zero
on failure.

## What it will not do

- Invent experience. Every claim traces to a fact you entered
- Apply on your behalf. It prepares, you submit
- Find jobs for you. You bring the posting, as a URL or as pasted text
- Scrape anything

## Layout

```
config.py         environment, per-stage model routing
modules/llm.py    provider router, loud fallback
modules/extract.py, tailor.py, ats.py, render_docx.py, prompts.py
database/         SQLAlchemy models and session
scripts/          seeding, route validation, model probing
```

`CLAUDE.md` carries the working rules, `SPEC.md` the design, `DECISIONS.md` the reasoning,
`ERROR_LOG.md` the mistakes and what they taught.
