# DECISIONS

Recorded because "not built" and "broken" look identical from outside.

| Decision | Choice | Why |
|---|---|---|
| Surface | Desktop, local only | Career history and salary expectations stay on the machine |
| Users | Single user | No tenancy, no sign-up, no billing |
| Sections | Three | Scout Finds, Writer, Interview Brief |
| Interview Brief | Fully standalone | Own input, own table, shares nothing. Enforced by test |
| Job sourcing | APIs only | LinkedIn returns HTTP 999 to automation. Scraping is out |
| Autonomy | Prepare only | Nothing auto-applies. He reads everything before it goes out |
| Tailoring | Aggressive, per job | Reframes to the JD's vocabulary, reorders, claims adjacent skills with evidence. Reaches are flagged for acceptance |
| Number checking | `STRICT_NUMBERS`, default on | Blocks figures absent from cited facts. A flag, not a policy |
| Interview Brief scope | Three things | Likely questions, company details, what to ask them. Nothing else |
| Fabrication | Not built | Background verification is standard at his target employers |
| Format | ATS-safe single-column docx | Built with python-docx, audited on every render |
| Models | NIM first, per-stage override | Cost. Claude is the fallback and the bake-off comparator |
| Always-on scout | Worker on the existing VM | A local app cannot run while the Mac is off |
| Front end | Server-rendered Jinja2 | No bundler. One user, a handful of screens. A build step buys nothing |

## Decisions that look odd and are deliberate

**Provider keys are not required at import.** Seeding the profile, rendering a docx and
running the ATS audit touch no API. Requiring a key to do offline work was the first bug
this project had, and it was fixed by moving credential checks to call time in
`modules/llm.py`.

**`complete_json` is forgiving.** Open models fence and preamble far more than Claude does,
and the router defaults to NIM. A strict parser would break half the stages on formatting
alone, so it tries the fenced block, the raw string, then the outermost braces.

**Fallback logs a WARNING naming the stage.** Learned the hard way on an earlier system: a silent fallback is
indistinguishable from a system quietly producing worse output for weeks.

**The gate lives in the renderer, not the UI.** The UI is not the last line of defence.
`render_docx.gate` raises rather than filtering, so a caller cannot skip it by accident.

**The em dash rule keeps its parenthetical examples.** The rule names the exact character,
which is what makes it unambiguous, even though that means the prompt contains the character
it bans. If em dashes ever leak through anyway, removing those two parentheticals is the
first thing to try.

**`fetch_jd` fails loudly rather than returning partial text.** A half-extracted page is
worse than an obvious error, because the tailor stage will happily build a resume against
navigation and cookie banners and nothing downstream will notice. LinkedIn job URLs return
HTTP 999 to any automated fetch and always will. The module detects that class of host and
asks for a paste instead. Driving a logged-in Chrome would defeat the auth wall and may get
added later; paste always works, so it is not blocking.

**`STRICT_NUMBERS` is a flag rather than a hard rule.** It catches a model turning $19.3M
into $29.3M, which is an accuracy bug rather than a stylistic one. It only ever fires on a
figure that is not in the cited facts, so it cannot block a true claim. Default on, and
switchable in `.env`. Sameer's call.

**Interview Brief was scoped down deliberately.** An earlier version included story
matching against the career record. That would have quietly required `ProfileFact` and
broken the standalone boundary. The three-item scope is both simpler and more consistent.

**No agents. Every stage is a single call.** The original idea was agents per pipeline
stage. Dropped, because an agent earns its place only when the model must decide its own
next step or reach outside itself, and no stage here does. `score`, `extract`, `cover` and
`screening` are one input and one structured output. `normalise`, `dedupe` and the guards
are deterministic code. A loop would add cost and failure modes for nothing.

Worth recording for whoever revisits this: all four NIM models tested do support tool
calling, so agents are technically possible on the free tier. That was verified, not
assumed. The reason not to build them is that they are unnecessary, not that they are
unavailable.

## Not built

- Fabricated experience of any kind
- Gap closer (honest answers for genuine gaps, plus a learn-next list). Gap data already
  exists in scoring, so this is a small addition when wanted
- Application tracker. `Find.status` covers shortlist and dismiss only
- Auto-apply, scraping, multi-user
- Any coupling between Interview Brief and Sections 1 or 2
