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


# The advisory half of modules/house.py. Every rule below is also checked in code after
# the model has answered, because a prompt is a request and these are not requests. It is
# still worth stating: a model told why a rule exists breaks it less often than one that
# meets the rule only as a rejection.
NEVER_CLAIM = """WHAT MAY NEVER BE CLAIMED, whatever the posting asks for:

QUALIFICATIONS. Two masters degrees, in Business Analytics and in International Business.
Not an MBA, and the substitution is not a shorthand: background checks verify the exact
title and a mismatch costs the offer rather than the interview. No PhD, no CFA, no CPA,
no chartered accountancy, no PMP, no Six Sigma belt.

DOMAINS WITH NO ENTRY ON THE RECORD. HR, workforce, headcount and attrition data. Field
operations. Marketplace, mobility and quick commerce. Logistics and FMCG. This bans the
CLAIM, not the word: pricing freight is real work and keeps the word logistics, while
"six years in logistics" is a sentence about somebody else. Never write "experience in",
"background in" or "years in" against any of them.

SCOPE. He mentors and he does not manage. On the treasury centralisation his own public
profile says he was part of the team, so this says the same. Promoting an individual
contributor to a lead is the one failure worse than a rejection.

THE SOURCE MUST STATE THE OUTCOME. "Worked on X" is not "delivered X, which achieved Y".
Where a fact records the activity and not the result, write the activity. Do not supply
the result.

If a requirement genuinely cannot be met from the record, the correct output is a strong
document that fails that requirement. Not one that fakes it."""


# Style, as opposed to truth. Checked afterwards too, but only ever reported: a stiff
# sentence is a fault to fix and never a reason to refuse a document.
NATURAL_LANGUAGE = """HOW IT SHOULD READ:

Vary bullet length. Uniform bullets are the clearest tell that a document was generated,
because no real career produces that rhythm.

Plain verbs. Not leverage, spearhead, orchestrate, utilise, facilitate or empower. Not
comprehensive, robust, cutting-edge, seamless, innovative, holistic or best-in-class.
Each of those is what a sentence reaches for when it has nothing specific to say.

No triads. Three items in a list, three times in a row, reads as filler.

Not every bullet opens with a power verb. Some sentences start with the thing that
happened, or with when it happened, which is how people actually write."""


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
