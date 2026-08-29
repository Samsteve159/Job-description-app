# SPEC

## What this is

A desktop app on Sameer's Mac for running an India job search. Career history and generated
documents never leave the machine. The only outbound traffic is LLM API calls and, when he
gives it a URL, one fetch of that job posting.

Finding the jobs is not the app's job. He does that himself on LinkedIn and Naukri, which no
job API reaches. The app starts at the point where he has a posting in front of him.

## Two sections

### 1. Resume & Cover Writer
Two ways in, both landing on the same pipeline:
- a **job URL**, which `fetch_jd` resolves to clean JD text
- **pasted JD text**

Stages: `fetch` -> `extract` -> `tailor` -> `cover` -> `render` -> `ats`.

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

### 2. Interview Brief (standalone)

Own nav item, own form, own table. Paste a job link or JD, get three things back:

1. **Questions they are likely to ask**, drawn from the JD
2. **Company details** worth knowing before you walk in
3. **What you should ask them**

That is the whole scope. No story matching, no resume tie-in, no prep scoring. Which is
also why it stays genuinely standalone: none of the three needs `ProfileFact`, so the
boundary costs nothing.

## The boundary

Everything runs on the Mac. There is no second machine, no worker, no inbound port and no
tunnel. `main.py` binds `127.0.0.1` and warns if `HOST` is ever set to anything else, because
that bind is the only thing standing in for a login.

Career facts, salary expectations and generated documents never leave the machine.
