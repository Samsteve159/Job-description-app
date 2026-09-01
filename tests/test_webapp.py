"""The app's screens, and the one thing a UI must never be allowed to do.

Runs against a throwaway database, so it never touches the real one and makes no model
calls. DB_PATH is set before anything imports config, because config reads it at import.

The test that matters is the last group. A screen must not be able to export content the
gates refuse. render_docx.gate and ats.gate raise rather than filter precisely so a caller
cannot skip them by accident, and the download route re-checks rather than trusting that
the review page already did.

    python3 tests/test_webapp.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="jobapp-test-")
os.environ["DB_PATH"] = str(Path(_TMP) / "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from database.db import get_db, init_db  # noqa: E402
from database.models import GeneratedBlock, Package, ProfileFact  # noqa: E402
from main import app  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


MUST = ["spend analysis", "sql", "unspsc"]


def seed():
    """A package as `analyse` would have written it, without the model calls."""
    init_db()
    db = next(get_db())
    for row in [
        ProfileFact(kind="name", text="Jane Doe", order_index=0),
        ProfileFact(kind="contact", text="jane.doe@example.com", order_index=1),
        ProfileFact(kind="contact", text="+91 90000 00000", order_index=2),
        ProfileFact(kind="role", text="Data Analyst", org="Purchasing Index",
                    date_from="Dec 2023", date_to="Present", order_index=3),
        ProfileFact(kind="education", text="Master of Business Analytics, 2023",
                    order_index=4),
    ]:
        db.add(row)
    db.flush()

    package = Package(
        job_text="Spend analysis role.", company="Acme", title="Spend Analyst",
        extraction={"title": "Spend Analyst", "company": "Acme", "seniority": "mid",
                    "archetype": "corporate", "keywords": MUST,
                    "must": [{"text": f"Needs {k}", "keyword": k, "weight": 1.0,
                              "kind": "must"} for k in MUST],
                    "nice": []},
        status="draft",
    )
    db.add(package)
    db.flush()

    db.add(GeneratedBlock(
        package_id=package.id, section="summary",
        text="Analyst working in spend analysis and SQL.",
        fact_ids=[4], grade="verified", order_index=0))
    db.add(GeneratedBlock(
        package_id=package.id, section="experience", org="Purchasing Index",
        text="Ran spend analysis in SQL against UNSPSC categories.",
        fact_ids=[4], grade="inferred", rationale="reframed", order_index=1))
    db.add(GeneratedBlock(
        package_id=package.id, section="experience", org="Purchasing Index",
        text="Invented a thing nobody asked for.",
        fact_ids=[], grade="blocked", rationale="cites no real fact", order_index=999))
    db.commit()
    pid = package.id
    db.close()
    return pid


pid = seed()

with TestClient(app) as client:
    print("screens")
    for path, marker in [
        ("/job/writer", "Resume &amp; Cover Writer"),
        ("/job/details", "My Details"),
        ("/job/brief", "Interview Brief"),
    ]:
        r = client.get(path)
        check(f"{path} renders", r.status_code == 200 and marker in r.text, r.status_code)

    r = client.get("/", follow_redirects=False)
    check("/ redirects to the dashboard", r.headers.get("location") == "/job")
    check("static css is served", client.get("/static/app.css").status_code == 200)

    print("\ndashboard, tracker and chrome")
    r = client.get("/job")
    check("the dashboard is the landing screen", r.status_code == 200 and "Dashboard" in r.text)
    check("it shows the counters", "Applied" in r.text and "Reached a human" in r.text)
    check("packages sit below the dashboard, not above",
          r.text.index("Applied") < r.text.index("Recent packages")
          if "Recent packages" in r.text else True)

    r = client.get("/posts")
    check("the second module has a home", r.status_code == 200 and "Post Writer" in r.text)

    r = client.post("/job/tracker/add",
                    data={"title": "Treasury Analyst", "company": "Northwind Bank",
                          "source": "manual", "url": ""}, follow_redirects=True)
    check("an application can be logged", "Northwind Bank" in r.text)
    r = client.get("/job")
    check("the dashboard counts it", ">1<" in r.text)
    check("the nav badge shows the live count", 'class="count-label"' in r.text)

    r = client.post("/job/tracker/add", data={"title": "", "company": "", "source": "manual"},
                    follow_redirects=True)
    check("an empty application is refused in a modal",
          'class="modal"' in r.text and "Nothing to log" in r.text)

    r = client.get("/job/tracker")
    import re as _re
    aid = _re.search(r'name="application_id" value="(\d+)"', r.text).group(1)
    r = client.post("/job/tracker/status", data={"application_id": aid, "status": "interview"},
                    follow_redirects=True)
    check("an outcome can be set", "interview" in r.text.lower())
    r = client.post("/job/tracker/status", data={"application_id": aid, "status": "nonsense"},
                    follow_redirects=True)
    check("a bad status is refused in a modal", 'class="modal"' in r.text)

    r = client.get("/job/writer")
    check("theme toggle is present", 'class="theme-toggle"' in r.text)
    check("the theme is applied before paint", "localStorage.getItem" in r.text
          and r.text.index("localStorage.getItem") < r.text.index("<body"))
    css = client.get("/static/app.css").text
    check("dark is defined for both the OS setting and an explicit choice",
          "prefers-color-scheme: dark" in css and '[data-theme="dark"]' in css)
    check("an explicit light choice can override a dark OS",
          ':root:not([data-theme="light"])' in css)

    print("\nthe analyse screen")
    r = client.get("/job/writer")
    forms = r.text.count('action="/job/writer/analyse"')
    check("there are two ways in, each with its own button", forms == 2, forms)
    # Counted within the two analyse forms, not across the page. The packages table
    # below them now carries a Clear button per row, and a page-wide count made this
    # assertion fail for a reason that had nothing to do with the analyse screen.
    top = r.text.split("House spec")[0]
    buttons = top.count('type="submit"')
    check("and each has exactly one submit", buttons == 2, buttons)
    check("the link form submits on its own", 'id="urlGo"' in r.text)
    check("no rule about which input wins is needed any more",
          "the pasted text wins" not in r.text)
    check("blocked boards are named before he submits, not after",
          "linkedin.com" in r.text and "HTTP 999" in r.text)
    check("and the text box is offered as the way round it",
          "the only way in for LinkedIn" in r.text)
    check("sites that do work are named too",
          "Greenhouse" in r.text and "Workday" in r.text)

    check("a skeleton is shown while it waits", "showSkeleton" in r.text
          and "sk-line" in r.text)
    check("the stages named are the real ones",
          "cites a fact" in r.text and "Scoring the fit" in r.text)
    check("and it gives up rather than animating forever",
          "Giving up on this one" in r.text and "300000" in r.text)
    # It used to give up at 120s while two model calls were still legitimately running,
    # and told him to check a terminal that a desktop app does not have.
    check("it waits longer than the two model calls it is waiting on",
          "120000" not in r.text and "Check the terminal" not in r.text)
    check("giving up says what to actually do about it",
          "Quit Job App from the Dock" in r.text and "JobApp.log" in r.text)
    # The skeleton used to replace main.innerHTML on submit, which removed the form the
    # browser was in the middle of submitting. The request was never sent at all, so the
    # screen sat there and eventually blamed a model call that had never been made.
    check("the skeleton never replaces the element holding the form",
          "main.innerHTML =" not in r.text)
    check("it hides main and inserts beside it instead",
          'main.style.display = "none"' in r.text
          and "insertBefore" in r.text)
    css = client.get("/static/app.css").text
    check("the skeleton has styles to match", ".sk-line" in css and "sk-sweep" in css)
    check("and it stops moving if the viewer asked for less motion",
          "prefers-reduced-motion" in css)

    # An icon now, not a word. Which means the label has to live somewhere a person can
    # still reach, so the tooltip carries both the state and what clicking does.
    home = client.get("/job").text
    check("the theme control is an icon", "themeIcon" in home and ">Theme<" not in home)
    check("with all three states drawn",
          all(k in home for k in ("light:", "dark:", "system:")), )
    check("and a label for anyone who cannot see it",
          'aria-label="Theme"' in home and "Click to force dark" in home)

    print("\ninput handling")
    r = client.post("/job/writer/analyse", data={"url": "", "job_text": ""},
                    follow_redirects=True)
    check("an empty submit is refused, not analysed", "Give it a job URL" in r.text)

    r = client.post("/job/writer/analyse",
                    data={"url": "https://linkedin.com/jobs/view/1", "job_text": ""},
                    follow_redirects=True)
    check("a LinkedIn URL is refused with the reason", "999" in r.text)
    check("and it does not silently analyse nothing", "Refused by the truth gate" not in r.text)

    print("\nreview screen")
    r = client.get(f"/job/writer/{pid}")
    check("the package opens", r.status_code == 200)
    check("blocked content is shown as refused", "Refused by the truth gate" in r.text)
    check("blocked content is not offered for acceptance",
          "Invented a thing" in r.text
          and r.text.count('name="accept"') == 1, r.text.count('name="accept"'))
    ids = re.findall(r'name="accept" value="(\d+)"', r.text)
    check("only the amber block can be ticked", len(ids) == 1, ids)

    r = client.get("/job/writer/99999", follow_redirects=False)
    check("an unknown package redirects rather than 500s",
          r.status_code == 303, r.status_code)

    print("\nthe gate cannot be skipped from a screen")
    # Nothing accepted: the amber bullet is withheld, so the keyword floor is not met.
    r = client.post(f"/job/writer/{pid}/accept", data={"action": "render"},
                    follow_redirects=True)
    check("building with nothing ticked is refused by the ATS gate",
          "export refused" in r.text, "export allowed" in r.text)

    r = client.get(f"/job/writer/{pid}/download", follow_redirects=False)
    check("download of a refused resume redirects instead of serving",
          r.status_code == 303, r.status_code)

    r = client.post(f"/job/writer/{pid}/accept",
                    data={"action": "render", "accept": ids}, follow_redirects=True)
    check("ticking the amber block clears the gate", "export allowed" in r.text)
    score = re.search(r'class="score">(\d+)<', r.text)
    check("a score is shown", score is not None, r.text[:0])

    r = client.get(f"/job/writer/{pid}/download", follow_redirects=False)
    check("the docx is served once it passes", r.status_code == 200, r.status_code)
    disposition = r.headers.get("content-disposition", "")
    check("the filename carries the person and the role, not the company",
          "Jane" in disposition and "Spend%20Analyst" in disposition, disposition)
    check("the package id is not inflicted on the recipient",
          "(" not in disposition, disposition)
    check("it is a real docx", r.content[:2] == b"PK", r.content[:8])

    # And the whole point: untick it, and the export closes again.
    client.post(f"/job/writer/{pid}/accept", data={"action": "save"})
    r = client.get(f"/job/writer/{pid}/download", follow_redirects=False)
    check("unticking closes the export again", r.status_code == 303, r.status_code)

    print("\ncover letter")
    # Seeded rather than generated, so this exercises the screens and the gate without a
    # model call. cover.write is covered in tests/test_cover.py.
    from database.models import Package as _Pkg
    _db = next(get_db())
    _p = _db.get(_Pkg, pid)
    _p.cover = {
        "greeting": "Dear Hiring Manager,", "sign_off": "Kind regards,",
        "paragraphs": [
            {"text": "Ran spend analysis in SQL against UNSPSC categories.",
             "fact_ids": [4], "grade": "verified", "accepted": False, "order_index": 0},
            {"text": "That is the same problem your posting describes.",
             "fact_ids": [4], "grade": "inferred", "accepted": False, "order_index": 1},
        ],
        "rejected": [{"text": "I am writing to apply for this role.",
                      "why": "cliche: 'i am writing to apply'"}],
        "warnings": [],
    }
    _db.commit(); _db.close()

    r = client.get(f"/job/writer/{pid}")
    check("the letter shows on the review screen", "Dear Hiring Manager," in r.text)
    check("what the gate refused is shown, not hidden",
          "Refused before you saw them" in r.text and "i am writing to apply" in r.text)
    para = re.findall(r'name="para" value="(\d+)"', r.text)
    check("only the reaching paragraph needs a tick", para == ["1"], para)

    r = client.post(f"/job/writer/{pid}/cover/accept", data={"action": "build"},
                    follow_redirects=True)
    check("it builds on the verified paragraph alone", "Cover letter built" in r.text)
    r = client.get(f"/job/writer/{pid}/cover/download", follow_redirects=False)
    check("the letter downloads", r.status_code == 200, r.status_code)
    check("it is a real docx", r.content[:2] == b"PK")
    check("the filename says who it is from and what it is",
          "Jane" in r.headers.get("content-disposition", "")
          and "Cover%20Letter" in r.headers.get("content-disposition", ""),
          r.headers.get("content-disposition"))

    r = client.post(f"/job/writer/{pid}/cover/accept",
                    data={"action": "build", "para": ["1"]}, follow_redirects=True)
    check("ticking the reaching paragraph makes it longer",
          "Cover letter built" in r.text)

    # and the same rule as the resume: changing your mind discards the built file
    client.post(f"/job/writer/{pid}/cover/accept", data={"action": "save"})
    r = client.get(f"/job/writer/{pid}/cover/download", follow_redirects=False)
    check("changing the choices discards the stale letter",
          r.status_code == 303, r.status_code)

    print("\ngap closer")
    # SEED_FILE is redirected so a test can never append to the real career record.
    import json as _json, tempfile as _tf
    from modules import gaps as _gaps
    _seed = Path(_tf.mkdtemp(prefix="jobapp-seed-")) / "facts.json"
    _seed.write_text(_json.dumps({"meta": {}, "facts": [
        {"kind": "role", "org": "Purchasing Index", "text": "Data Analyst",
         "children": []}]}), encoding="utf-8")
    _gaps.SEED_FILE = _seed

    r = client.get(f"/job/writer/{pid}")
    check("the review screen offers to close gaps", "Ask me about the gaps" in r.text)

    r = client.post(f"/job/writer/{pid}/gaps/save",
                    data={"text": "Monitored FX exposure across the account structure.",
                          "org": "Purchasing Index", "keyword": "treasury risk"},
                    follow_redirects=True)
    check("an answer is accepted", "Saved to your record" in r.text, r.status_code)
    saved = _json.loads(_seed.read_text(encoding="utf-8"))
    check("it reaches the JSON, not only the database",
          len(saved["facts"][0]["children"]) == 1, saved)
    check("it is marked as attested",
          saved["facts"][0]["children"][0]["verified"] is True)

    r = client.post(f"/job/writer/{pid}/gaps/save", data={"text": "no", "org": ""},
                    follow_redirects=True)
    check("a too-short answer is refused in a modal", 'class="modal"' in r.text)
    saved = _json.loads(_seed.read_text(encoding="utf-8"))
    check("and nothing was written", len(saved["facts"][0]["children"]) == 1)

    print("\ndetails")
    r = client.post("/job/details/set", data={"kind": "phone", "value": "+91 98200 12345"},
                    follow_redirects=True)
    check("a detail can be edited", "+91 98200 12345" in r.text)
    r = client.post("/job/details/set", data={"kind": "address", "value": "12 Some Road"},
                    follow_redirects=True)
    check("an address is stored", "12 Some Road" in r.text)
    check("and is hidden from the resume line by default",
          "12 Some Road" not in r.text.split('class="panel"')[1], "shown in the preview")
    r = client.post("/job/details/set", data={"kind": "phone", "value": ""},
                    follow_redirects=True)
    check("an empty value is refused", "cannot be empty" in r.text)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
