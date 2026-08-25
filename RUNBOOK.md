# RUNBOOK

## First run

```bash
cd "Job_App"
python3 -m pip install -r requirements.txt
cp .env.example .env          # already done; fill in NIM_API_KEY when you have it
python3 scripts/seed_profile.py
```

## Everyday

```bash
python3 scripts/seed_profile.py            # after editing data/profile_facts.json
python3 scripts/seed_profile.py --dry-run  # see what would change first
python3 -c "import sys; sys.path.insert(0,'.'); from config import config; print(config.describe())"
```

That last line prints one greppable line with the full flag and routing state. When
something behaves oddly, read it before anything else.

## Running the tests

```bash
python3 tests/test_guards.py     # 20 assertions, no API key needed
```

## Turning the number check off

```bash
# in .env
STRICT_NUMBERS=false
```

Blocks figures that are not present in the facts a block cites. Only fires on invented
numbers, so leaving it on costs nothing on true claims.

## Checking keys

```bash
python3 -c "import sys; sys.path.insert(0,'.'); from config import config; print(config.missing_keys() or 'all present')"
```

## Editing the career facts

`data/profile_facts.json` is the source of truth, not the database and not the old CV.
Edit it, then re-seed. The seeder wipes and reloads, which is intended.

Anything with `"verified": false` is excluded from citation until you confirm it and flip
the flag. Three entries are currently in that state.

## Auditing a generated resume

```python
from modules.render_docx import audit, keyword_coverage
a = audit("data/output/whatever.docx")
print(a["ok"], a["problems"], a["headings_found"])
print(keyword_coverage("data/output/whatever.docx", ["spend analysis", "fp&a", "sql"]))
```

`audit` reopens the file and reports what a parser would actually see: tables, text boxes,
drawings, headers, footers, and any em or en dashes that slipped through.

## Things that will bite

- **Python 3.9.** Every module needs `from __future__ import annotations`
- Provider keys are optional at import on purpose. If an LLM call fails with a clear
  "NIM_API_KEY is not set", that is the design working, not a bug
- The renderer raises `BlockedContentError` rather than silently dropping content. If a
  render fails, read the message: something tried to reach the page without citing a fact
