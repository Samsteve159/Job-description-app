"""ATS gate. The resume has to survive the filter and reach a human.

`render_docx.audit` only answered "is the file structurally safe". That is half the job. An
applicant tracking system does not just open the file, it tries to pull structured data out
of it: name, email, phone, then a list of employers with titles and date ranges. If that
extraction fails the application is dropped or lands in front of a recruiter with empty
fields, and neither of those reaches a person.

So this module simulates the extraction rather than inspecting the markup, then scores
keyword coverage weighted by what the job actually said was required.

Deliberately no model. A real parser is dumb and literal, so the checker has to be dumb and
literal too. An LLM would helpfully understand that "procurement analytics" satisfies a
requirement for "spend analysis". Taleo will not.

Blocking failures stop the export. Warnings do not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from modules.render_docx import SECTION_HEADINGS, audit

# how much each dimension contributes to the score out of 100
WEIGHTS = {"format": 20, "contact": 20, "sections": 10, "dates": 15, "must": 30, "nice": 5}

# below this, the resume is judged unlikely to clear a keyword filter
MUST_COVERAGE_FLOOR = 0.70

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}")
URL = re.compile(r"(?:linkedin\.com/in/|github\.com/|https?://)\S+", re.I)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
DATE_RANGE = re.compile(
    rf"(?:{_MONTH}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})\s*[-–—to]+\s*"
    rf"(?:{_MONTH}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}|Present|Current)",
    re.I,
)


@dataclass
class Parsed:
    """What a parser would manage to pull out."""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    links: List[str] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
    date_ranges: List[str] = field(default_factory=list)


@dataclass
class AtsReport:
    score: int = 0
    blocking: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    parsed: Parsed = field(default_factory=Parsed)
    must: Dict[str, bool] = field(default_factory=dict)
    nice: Dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.blocking

    @property
    def must_coverage(self) -> float:
        return (sum(self.must.values()) / len(self.must)) if self.must else 1.0

    def missing_must(self) -> List[str]:
        return [k for k, v in self.must.items() if not v]

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "BLOCKED"
        return (
            f"ATS {verdict}  score={self.score}/100  "
            f"must-have keywords {sum(self.must.values())}/{len(self.must)}  "
            f"{len(self.blocking)} blocking, {len(self.warnings)} warnings"
        )


class AtsBlocked(RuntimeError):
    """Raised when an export is attempted on a resume that would not survive the filter."""


def simulate_parse(text: str) -> Parsed:
    """Extract the way a parser does: top-down, literal, no understanding."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    head = "\n".join(lines[:6])          # parsers look for contact details near the top

    email = EMAIL.search(head) or EMAIL.search(text)
    phone_match = None
    for candidate in PHONE.finditer(head):
        digits = re.sub(r"\D", "", candidate.group(0))
        if 8 <= len(digits) <= 15:       # avoid matching years or postcodes
            phone_match = candidate
            break

    name = None
    if lines:
        first = lines[0]
        # a name is short, has no digits and no @
        if 2 <= len(first.split()) <= 5 and not any(c.isdigit() for c in first) and "@" not in first:
            name = first

    return Parsed(
        name=name,
        email=email.group(0) if email else None,
        phone=phone_match.group(0).strip() if phone_match else None,
        links=URL.findall(text)[:5],
        sections=[h for h in SECTION_HEADINGS.values() if h in text],
        date_ranges=DATE_RANGE.findall(text),
    )


def check(
    path: Path,
    must_keywords: Sequence[str] = (),
    nice_keywords: Sequence[str] = (),
    expect_roles: int = 0,
    expect_phone: bool = False,
) -> AtsReport:
    """Run the full gate over a rendered resume."""
    structural = audit(path)
    text = str(structural["text"])
    lowered = text.lower()
    parsed = simulate_parse(text)
    report = AtsReport(parsed=parsed)

    earned = 0.0

    # 1. format. Already checked by audit, promoted to blocking here.
    if structural["ok"]:
        earned += WEIGHTS["format"]
    else:
        for problem in structural["problems"]:
            report.blocking.append(f"format: {problem}")

    # 2. contact extraction. The most common real-world ATS failure.
    contact_points = 0
    if parsed.email:
        contact_points += 1
    else:
        report.blocking.append("contact: no email found in the first lines of the document")
    if parsed.name:
        contact_points += 1
    else:
        report.blocking.append("contact: the first line does not read as a name")
    if parsed.phone:
        contact_points += 1
    elif expect_phone:
        report.warnings.append("contact: no phone number a parser would recognise")
    else:
        contact_points += 1   # not expected, so not penalised
    earned += WEIGHTS["contact"] * (contact_points / 3)

    # 3. section headings
    found = len(parsed.sections)
    if found >= 3:
        earned += WEIGHTS["sections"] * min(1.0, found / 4)
    else:
        report.blocking.append(
            f"sections: only {found} standard heading(s) found. A parser needs at least three"
        )

    # 4. dates. Tenure is calculated from these, so unparseable dates distort the record.
    if expect_roles:
        if len(parsed.date_ranges) >= expect_roles:
            earned += WEIGHTS["dates"]
        else:
            report.blocking.append(
                f"dates: {len(parsed.date_ranges)} parseable date range(s) for "
                f"{expect_roles} role(s). Every role needs one a parser can read"
            )
    elif parsed.date_ranges:
        earned += WEIGHTS["dates"]
    else:
        report.warnings.append("dates: no parseable date ranges found")

    # 5. must-have keywords. This is what the filter actually screens on.
    report.must = {k.lower(): k.lower() in lowered for k in must_keywords}
    if report.must:
        coverage = report.must_coverage
        earned += WEIGHTS["must"] * coverage
        if coverage < MUST_COVERAGE_FLOOR:
            report.blocking.append(
                f"keywords: only {coverage:.0%} of must-have terms present "
                f"(floor is {MUST_COVERAGE_FLOOR:.0%}). Missing: "
                f"{', '.join(report.missing_must()[:6])}"
            )
    else:
        earned += WEIGHTS["must"]

    # 6. nice-to-have keywords. Never blocking.
    report.nice = {k.lower(): k.lower() in lowered for k in nice_keywords}
    if report.nice:
        hit = sum(report.nice.values()) / len(report.nice)
        earned += WEIGHTS["nice"] * hit
        if hit < 0.5:
            report.warnings.append(
                f"keywords: {hit:.0%} of nice-to-have terms present. Not blocking, but each "
                f"one is a free ranking point"
            )
    else:
        earned += WEIGHTS["nice"]

    # 7. length. Warning only.
    chars = int(structural["characters"])
    if chars < 1500:
        report.warnings.append(f"length: {chars} characters is thin for nine years of history")
    elif chars > 9000:
        report.warnings.append(f"length: {chars} characters likely runs past two pages")

    report.score = int(round(earned))
    return report


def gate(report: AtsReport) -> None:
    """Raise unless the resume would survive the filter. Call before any export."""
    if not report.passed:
        raise AtsBlocked(
            f"{report.summary()}\n  " + "\n  ".join(report.blocking)
        )
