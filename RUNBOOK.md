# RUNBOOK

## Starting the app

```bash
./run.command            # double-clickable from Finder, or:
python3 main.py
```

Opens `http://127.0.0.1:8100` in your browser. Control-C stops it.

Loopback only, which is why there is no login. If you ever set `HOST` to anything else you
put an unauthenticated app holding your full career history on the network, and `main.py`
will warn you at startup rather than let that pass quietly.

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


## Connecting Gmail, so applications count themselves

This is the one setup step nobody can do for you: it needs an account you own, and a
consent screen only you can click through. About ten minutes.

The scope requested is `gmail.readonly`. The app can read, and cannot send, delete or
modify anything. That is not a promise in a comment, it is what Google enforces on the
token.

**1. Make a project.** Go to <https://console.cloud.google.com/projectcreate>. Name it
anything, `job-app` is fine. Create it and wait for it to become the selected project.

**2. Turn the Gmail API on.** Go to
<https://console.cloud.google.com/apis/library/gmail.googleapis.com> and press Enable.

**3. Set up the consent screen.** APIs and Services, then OAuth consent screen.

- User type: **External**. Internal only exists for Workspace organisations
- App name: anything. Support email and developer email: your own
- On the Scopes step, add `https://www.googleapis.com/auth/gmail.readonly` and nothing else
- On the Test users step, **add your own Gmail address**. This matters. An app in testing
  will only ever authorise accounts on that list, and the failure message if you skip it
  does not say so

Leave it in Testing. Publishing it would start a Google verification review you have no
use for. Testing mode expires the refresh token every 7 days, which for a single user
means clicking through the consent screen again once a week. That is the trade for not
submitting an app for review.

**4. Make the client.** APIs and Services, then Credentials, then Create Credentials,
then OAuth client ID.

- Application type: **Desktop app**
- Download the JSON

**5. Put it where the app looks.**

```bash
mv ~/Downloads/client_secret_*.json "Job_App/data/gmail_client.json"
```

`data/gmail_client.json` and `data/gmail_token.json` are both gitignored. The client file
is not a password, but the token that appears beside it after you authorise **is** a live
credential, so neither belongs in the repo.

**6. Authorise once.**

```bash
python3 scripts/gmail_auth.py
```

A browser opens, you pick your account, you approve the read-only scope. It writes
`data/gmail_token.json` and prints how many messages it can see. Nothing is scanned yet.

### If it goes wrong

| What you see | What it means |
|---|---|
| `access_blocked` / "app has not completed verification" | Your address is not in Test users. Step 3 |
| `redirect_uri_mismatch` | The client was made as Web application, not Desktop app. Make a new one |
| It worked, then stopped about a week later | Testing-mode refresh tokens expire after 7 days. Run `gmail_auth.py` again |

### What it will and will not do

It reads **sent mail only**, looking for the shape of an application: a subject or body
naming a role and a company, or a confirmation reply from a known ATS domain
(Workday, Greenhouse, Lever, SuccessFactors, Taleo). Every match is written with the
message id attached, so a rescan cannot count the same application twice, and with a
confidence score, because a guess should look like a guess.

It never sends, never replies, never deletes, and never reads anything outside the
matches. Anything it gets wrong you can fix by hand on the Tracker.
