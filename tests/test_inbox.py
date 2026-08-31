"""Reading job alerts out of his inbox.

The boards refuse to be searched by a program. They will email him the same listings, so
this is the scout arriving by the one route that scrapes nobody. Parsing is deterministic
throughout: turning an anchor into fields is find-and-copy work, and a model here would
cost money, add latency and occasionally invent an employer.

    python3 tests/test_inbox.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import inbox  # noqa: E402
from modules.inbox import Listing, from_subject, is_noise, listings_in, split_anchor  # noqa: E402

passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def msg(subject, domain="linkedin.com"):
    return SimpleNamespace(subject=subject, sender_domain=domain,
                           received=datetime(2026, 9, 1), id="x")


print("what is not a job")
# These outnumbered real listings in his actual inbox. A list that is mostly noise gets
# ignored, which is the same as not having built it.
for junk in ("13 people visited your profile", "You have 3 new messages",
             "Sameer: your job alert for Consultant in India has been created",
             "Congratulate Priya on her work anniversary",
             "Security alert: new sign-in"):
    check(f"noise: {junk[:44]!r}", is_noise(junk))
for real in ("Senior Data Analyst at Grant Thornton Australia",
             "Associate Analyst - Remote @ Termgrid Inc."):
    check(f"a job: {real[:44]!r}", not is_noise(real))

print("\nreading a LinkedIn digest")
# Real anchor text from a real alert.
BODY = '''
<a href="https://www.linkedin.com/comm/jobs/view/4446611097/?trk=x">
  Senior Associate, Data Analytics Grant Thornton Australia &middot; Melbourne, VIC (Hybrid)</a>
<a href="https://www.linkedin.com/comm/jobs/view/4454350551/?trk=y">
  Data Analyst - Finance TMX Transform &middot; Melbourne, VIC</a>
<a href="https://www.linkedin.com/comm/jobs/view/4446611097/?trk=dup">the same job again</a>
'''
found = listings_in(msg("Senior Associate at Grant Thornton Australia"), BODY)
check("both distinct jobs are read", len(found) == 2, [l.external_id for l in found])
check("the same job linked twice is read once",
      len({l.external_id for l in found}) == 2)
check("the url is rebuilt clean, without the tracking",
      found[0].url == "https://www.linkedin.com/jobs/view/4446611097", found[0].url)
check("the location is separated off", found[0].location == "Melbourne, VIC (Hybrid)",
      found[0].location)
check("the title keeps what the email said",
      "Grant Thornton" in found[0].title, found[0].title)

print("\nnot repeating the employer twice")
# LinkedIn writes "Title Company" in the anchor and "Title at Company" in the subject.
# Taking both gave "Senior Data Analyst Department of Justice, Victoria at Department of
# Justice, Victoria".
dup = Listing(source="linkedin", external_id="1", url="u",
              title="Senior Data Analyst Department of Justice, Victoria",
              company="Department of Justice, Victoria")
check("the company is dropped when the title already carries it",
      dup.label == dup.title, dup.label)
sane = Listing(source="indeed", external_id="2", url="u",
               title="Associate Analyst", company="Termgrid Inc")
check("and kept when it does not",
      sane.label == "Associate Analyst at Termgrid Inc", sane.label)

print("\nsubject lines")
check("'at' splits role from employer",
      from_subject("Senior Data Analyst at ANZ") == {"title": "Senior Data Analyst",
                                                     "company": "ANZ"})
check("'@' does too",
      from_subject("Invoice Specialist @ Parexel")["company"] == "Parexel")
check("a subject that is not a job splits into nothing",
      from_subject("You have 3 new messages") == {})

print("\nan email with a job in the subject but no readable link")
bare = listings_in(msg("Invoice Specialist @ Parexel", "match.indeed.com"), "<p>hi</p>")
check("is still listed rather than lost", len(bare) == 1, bare)
check("with no url, rather than a made-up one", bare[0].url == "")

print("\nnothing is invented")
half = split_anchor("Data Analyst Skillfield")
check("no separator means no location guessed", "location" not in half, half)
check("and the title is kept whole", half["title"] == "Data Analyst Skillfield")
check("an empty anchor yields nothing", split_anchor("") == {})

print("\noff-target listings are flagged, not hidden")
# Every listing in his real inbox was Melbourne, though his alerts name India and the
# UAE. The board matches on where he lives, and no amount of alert configuration on our
# side changes that: it is worth saying out loud.
mixed = [Listing("linkedin", "1", "u", title="Analyst", location="Melbourne, VIC"),
         Listing("linkedin", "2", "u", title="Analyst", location="Mumbai, India"),
         Listing("linkedin", "3", "u", title="Analyst", location="Dubai, UAE"),
         Listing("linkedin", "4", "u", title="Analyst", location="Remote"),
         Listing("linkedin", "5", "u", title="Analyst", location="")]
off = inbox.off_target(mixed)
check("the wrong country is flagged", [l.external_id for l in off] == ["1"],
      [l.external_id for l in off])
check("a target city is not", all(l.external_id != "2" for l in off))
check("remote is not", all(l.external_id != "4" for l in off))
check("and an unknown location is not accused", all(l.external_id != "5" for l in off))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
