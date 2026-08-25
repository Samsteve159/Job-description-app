"""Shared prompt blocks.

HOUSE_STYLE comes from an earlier production system, where it was added after a live smoke
test showed the model defaulting to em dash density no human writes at. It is the single most
reused block in this codebase.

Worth knowing: the rule names the exact character, which means the prompt contains the
character it bans. That is deliberate, because naming it is what makes the rule unambiguous.
If em dashes ever leak through anyway, removing those two parentheticals is the first thing
to try.
"""
from __future__ import annotations

HOUSE_STYLE = """PUNCTUATION RULES (strict, no exceptions):
- NEVER use em dashes (—) or en dashes (–). Em dashes are an AI-writing tell.
- Use periods, commas, colons, or semicolons instead. If a sentence reads like it wants an em dash, split it into two sentences.
- Do not use em dashes even for emphasis, asides, or list separators.

BANNED PHRASES (naming the specific failure works; "sound natural" does not):
- "game-changer", "leverage synergies", "in today's fast-paced world", "it's more important than ever"
- "passionate about", "results-driven", "proven track record", "dynamic professional"
- "spearheaded", "orchestrated" as filler verbs where "built", "ran" or "led" is true
Be specific, human, and worth reading."""


TRUTH_CONTRACT = """TRUTHFULNESS CONTRACT (non-negotiable):

You are given a set of numbered FACTS about the candidate. Those facts are the only
material you may draw on. You may:
  - reframe a fact into the vocabulary the job description uses
  - reorder and reweight facts so the ones the job cares about lead
  - combine two facts into one line where both are cited
  - claim an adjacent skill ONLY where a fact provides genuine supporting evidence

You may NOT:
  - invent an employer, job title, date, qualification, tool or metric
  - change a number, a date, or the name of an organisation
  - imply seniority, headcount or ownership the facts do not support

Every block you output MUST carry the fact ids it draws on. Grade each block yourself:
  "verified"  restates cited facts in the job's language
  "inferred"  reframes a fact meaningfully beyond how it was written
  "stretch"   claims an adjacent skill from supporting evidence

If you cannot support a block with at least one fact id, do not write the block. Omit it.
A short honest resume beats a long one with a claim that fails verification."""


NO_HEADCOUNT = """SCOPE CONSTRAINT: the candidate works solo end to end. Never write "led a
team", "managed a team", "mentored" or any headcount claim. Frame scope as ownership and
range instead: owning an analysis end to end, from raw data through to the recommendation."""


ATS_CONTRACT = """ATS CONTRACT (this resume is read by a machine before any human sees it):

The filter that screens this document is literal. It does not understand meaning, only
string matches. Write accordingly.

- Use the EXACT keyword strings the job description used, wherever a fact genuinely supports
  it. "Spend analysis" does not match a requirement written as "spend analytics". If the job
  says "spend analytics", write "spend analytics". A synonym scores zero.
- Work the must-have terms into the summary and the bullets naturally. A skills list alone is
  weaker than a term that also appears in context.
- Never invent a keyword you cannot support with a fact. A missing keyword costs you ranking.
  A false one costs you the offer. Leave it out and it will be flagged as a gap instead.
- Plain characters only. No em dashes, en dashes, bullets glyphs, arrows, or symbols inside
  the text. They corrupt on extraction.
- Write dates only as "Mon YYYY" with a plain hyphen between them, for example
  "Apr 2018 - Oct 2021". Anything else fails to parse and distorts calculated tenure.
- Spell out an acronym once with the short form in brackets, for example
  "Financial Planning and Analysis (FP&A)", so both forms match.
- Past tense, one achievement per bullet, outcome first."""
