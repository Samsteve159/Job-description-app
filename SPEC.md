# SPEC

## What this is

A desktop app on Sameer's Mac for running an India job search. Career history and generated
documents never leave the machine. The only outbound traffic is LLM APIs, job APIs, and a
pull from the scout worker.

## Three sections

### 1. Scout Finds
The scout's output surface. A list, not a workflow.

Row: job link, short brief, fit score, gaps, source, posted date.
Actions: shortlist (hands the JD to Section 2), dismiss, clear queue.

Two writers, one table:
- pull from the VM worker, for jobs found while the Mac was off
- on-demand local scout, for fresh results while he is at the machine

Stages: `scout` -> `normalise` -> `dedupe` -> `score`. Dedupe is deterministic, not an LLM.

### 2. Resume & Cover Writer
Three ways in, all landing on the same pipeline:
- a **job URL**, which `fetch_jd` resolves to clean JD text
- **pasted JD text**
- a shortlisted row handed over from Scout Finds

Stages: `fetch` -> `extract` -> `tailor` -> `cover` -> `screening` -> `render` -> `ats`.

### The ATS gate

The point of the app is a resume that clears the filter and reaches a person. `modules/ats.py`
runs last and can refuse the export. It does not inspect markup, it **simulates the
extraction** an applicant tracking system performs, then scores it:

| Check | Blocking | Why |
|---|---|---|
| Format: tables, text boxes, drawings, headers, footers | yes | Parsers flatten or drop these |
| Contact block extracts: name, email, phone | yes | The most common cause of a dropped application |
| At least three standard section headings | yes | A parser needs them to segment the document |
| A parseable date range per role | yes | Tenure is computed from these |
| Must-have keyword coverage above 70% | yes | What the filter literally screens on |
| Nice-to-have keyword coverage | no | Free ranking points, not a gate |
| Length | no | Warning only |

Score is out of 100. `ats.gate()` raises `AtsBlocked` on any blocking failure, so a resume
that would not survive cannot be exported by mistake.

`fetch_jd` is deliberate about failure. Structured ATS hosts (Greenhouse, Lever, Workday,
SmartRecruiters) parse reliably. Auth-walled hosts, LinkedIn above all, will not, and the
module says so and asks for a paste rather than returning page furniture. A resume tailored
against navigation text is worse than no resume, and it fails silently.

Tailoring pushes hard on framing but never past the facts. Grading:

| Block cites | Grade | Behaviour |
|---|---|---|
| Facts directly | verified | renders |
| A reframing of a fact | inferred | amber, must be accepted |
| Adjacent skill with evidence | stretch | amber, must be accepted |
| Nothing | blocked | never renders |

### 3. Interview Brief (standalone)

Own nav item, own form, own table. Paste a job link or JD, get three things back:

1. **Questions they are likely to ask**, drawn from the JD
2. **Company details** worth knowing before you walk in
3. **What you should ask them**

That is the whole scope. No story matching, no resume tie-in, no prep scoring. Which is
also why it stays genuinely standalone: none of the three needs `ProfileFact`, so the
boundary costs nothing.

## The boundary

```
VM worker (always on)                 Mac (local app)
  scout, normalise, dedupe, score       Scout Finds, Writer, Interview Brief
  own unit, own DB, own hostname        ProfileFact, resumes, covers, briefs

  up:   search criteria only
  down: public job listings only
```

Career facts, salary expectations and generated documents never go up.
