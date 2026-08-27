"""ATS-safe .docx rendering.

Everything here exists to survive a resume parser, which is a much dumber reader than a
human. The rules that matter, in rough order of how often they break parsing:

  no tables            parsers flatten them in unpredictable column order
  no text boxes        content inside them is frequently dropped entirely
  no headers/footers   often skipped, so contact details there vanish
  no floating shapes   the old CV has one, which is why it was failing
  single column        two-column layouts interleave into nonsense
  real list paragraphs not manual bullet glyphs
  standard headings    "EXPERIENCE", not "Where I've Made An Impact"
  plain hyphen dates   "Apr 2018 - Oct 2021", not en dashes

The gating rule from the plan is enforced here rather than in the UI, because the UI is
not the last line of defence. A block that cites no fact, or a reach that has not been
accepted, cannot reach the page even if a caller asks for it.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

BODY_FONT = "Calibri"
BODY_SIZE = Pt(10.5)
NAME_SIZE = Pt(20)
HEADING_SIZE = Pt(11)
INK = RGBColor(0x14, 0x18, 0x1D)
MUTED = RGBColor(0x44, 0x4C, 0x55)

SECTION_ORDER = ["summary", "skills", "experience", "education", "certifications"]
SECTION_HEADINGS = {
    "summary": "PROFESSIONAL SUMMARY",
    "skills": "SKILLS",
    "experience": "EXPERIENCE",
    "education": "EDUCATION",
    "certifications": "CERTIFICATIONS",
}


# Characters a model reaches for that an ATS parser has no reason to handle well. The
# em dash is the famous one, but the first live run leaked U+2011 NON-BREAKING HYPHEN
# into four blocks, and audit() only screened for em and en dashes, so it passed clean.
# Anything non-ASCII in a resume is a liability: parsers built in the 2000s mangle it,
# and a keyword filter comparing "AI\u2011agent" to "ai agent" scores zero.
PUNCTUATION_MAP = {
    "\u2014": ", ",   # em dash
    "\u2013": "-",    # en dash
    "\u2012": "-",    # figure dash
    "\u2011": "-",    # non-breaking hyphen
    "\u2010": "-",    # hyphen
    "\u2018": "'",    # curly quotes
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",    # non-breaking space
    "\u200b": "",     # zero width space
    "\u2022": "",     # bullet glyph. The list style supplies the bullet
    "\u2192": " to ", # arrow
    "\u00ad": "",     # soft hyphen
}


def plain_text(text: str) -> str:
    """Force text down to characters a parser from 2004 would survive.

    Applied at write time rather than trusted to the prompt. The house style tells the
    model not to do this; this makes it true regardless of whether it listened.
    """
    for bad, good in PUNCTUATION_MAP.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


class BlockedContentError(RuntimeError):
    """Raised when a caller tries to render content that has not cleared the gate."""


@dataclass
class Role:
    title: str
    org: str
    dates: str
    location: str = ""
    bullets: List[str] = field(default_factory=list)


@dataclass
class ResumePayload:
    name: str
    contact: List[str]                      # ["email", "phone", "linkedin", "location"]
    summary: str = ""
    skills: List[str] = field(default_factory=list)
    experience: List[Role] = field(default_factory=list)
    education: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)


# ------------------------------------------------------------------------ the gate

def gate(blocks: List[dict]) -> List[dict]:
    """Return only blocks allowed onto the page. Raises if a blocked block is present.

    blocks are dicts with at least: text, grade, accepted, fact_ids
    """
    allowed: List[dict] = []
    for block in blocks:
        grade = (block.get("grade") or "blocked").lower()
        fact_ids = block.get("fact_ids") or []

        if grade == "blocked" or not fact_ids:
            raise BlockedContentError(
                f"Block cites no facts and cannot be rendered: {block.get('text', '')[:80]!r}"
            )
        if grade in {"inferred", "stretch"} and not block.get("accepted"):
            raise BlockedContentError(
                f"Block graded {grade!r} has not been accepted: {block.get('text', '')[:80]!r}"
            )
        allowed.append(block)
    return allowed


# --------------------------------------------------------------------- docx writing

def _configure(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = BODY_SIZE
    normal.font.color.rgb = INK
    pf = normal.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(2)
    pf.line_spacing = 1.05

    for section in doc.sections:
        section.top_margin = Pt(40)
        section.bottom_margin = Pt(40)
        section.left_margin = Pt(48)
        section.right_margin = Pt(48)


def _heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = HEADING_SIZE
    run.font.name = BODY_FONT
    run.font.color.rgb = INK


def _line(doc: Document, text: str, bold: bool = False, muted: bool = False,
          size: Optional[Pt] = None, space_before: int = 0) -> None:
    p = doc.add_paragraph()
    if space_before:
        p.paragraph_format.space_before = Pt(space_before)
    run = p.add_run(plain_text(text))
    run.bold = bold
    run.font.name = BODY_FONT
    run.font.size = size or BODY_SIZE
    run.font.color.rgb = MUTED if muted else INK


def _bullet(doc: Document, text: str) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    run = p.add_run(plain_text(text))
    run.font.name = BODY_FONT
    run.font.size = BODY_SIZE
    run.font.color.rgb = INK


def render_resume(payload: ResumePayload, out_path: Path) -> Path:
    """Write an ATS-safe .docx. No tables, text boxes, headers, footers or shapes."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    _configure(doc)

    # name, as ordinary body text rather than a Heading style so parsers read it plainly
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(plain_text(payload.name))
    run.bold = True
    run.font.size = NAME_SIZE
    run.font.name = BODY_FONT
    run.font.color.rgb = INK

    if payload.contact:
        _line(doc, " | ".join(payload.contact), muted=True)

    if payload.summary:
        _heading(doc, SECTION_HEADINGS["summary"])
        _line(doc, payload.summary)

    if payload.skills:
        _heading(doc, SECTION_HEADINGS["skills"])
        _line(doc, " | ".join(payload.skills))

    if payload.experience:
        _heading(doc, SECTION_HEADINGS["experience"])
        for role in payload.experience:
            _line(doc, f"{role.title}, {role.org}", bold=True, space_before=8)
            meta = role.dates if not role.location else f"{role.dates} | {role.location}"
            _line(doc, meta, muted=True)
            for bullet in role.bullets:
                _bullet(doc, bullet)

    if payload.education:
        _heading(doc, SECTION_HEADINGS["education"])
        for item in payload.education:
            _line(doc, item)

    if payload.certifications:
        _heading(doc, SECTION_HEADINGS["certifications"])
        for item in payload.certifications:
            _line(doc, item)

    doc.save(str(out_path))
    return out_path


# ------------------------------------------------------------------------ ATS audit

HOSTILE_TAGS = {
    "w:tbl": "table",
    "w:drawing": "floating drawing or image",
    "w:pict": "vml picture",
    "w:txbxContent": "text box",
    "mc:AlternateContent": "fallback shape content",
}


@dataclass
class CoverPayload:
    name: str
    contact: List[str]
    greeting: str = "Dear Hiring Manager,"
    paragraphs: List[str] = field(default_factory=list)
    sign_off: str = "Kind regards,"
    role: str = ""
    company: str = ""
    date_line: str = ""


def render_cover(payload: CoverPayload, out_path: Path) -> Path:
    """Write an ATS-safe cover letter.

    Same constraints as the resume, for the same reason: some systems parse the cover
    letter too, and the ones that do not still store it as a file somebody opens. No
    tables, no headers, no text boxes, and every run through plain_text.

    Refuses an empty letter rather than writing a page containing a greeting and a
    signature, which is worse than no file at all because it looks like it worked.
    """
    body = [p for p in payload.paragraphs if (p or "").strip()]
    if not body:
        raise BlockedContentError(
            "a cover letter with no paragraphs is not a cover letter. Accept at least "
            "one, or fix what the truth gate refused"
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    _configure(doc)

    p = doc.add_paragraph()
    run = p.add_run(plain_text(payload.name))
    run.bold = True
    run.font.size = Pt(15)
    run.font.name = BODY_FONT
    run.font.color.rgb = INK

    if payload.contact:
        _line(doc, " | ".join(payload.contact), muted=True)
    if payload.date_line:
        _line(doc, payload.date_line, muted=True, space_before=10)

    if payload.role or payload.company:
        subject = "Application: " + " at ".join(
            part for part in (payload.role, payload.company) if part)
        _line(doc, subject, bold=True, space_before=12)

    _line(doc, payload.greeting, space_before=12)
    for text in body:
        _line(doc, text, space_before=8)

    _line(doc, payload.sign_off, space_before=14)
    _line(doc, payload.name)
    doc.save(str(out_path))
    return out_path


def audit(path: Path) -> Dict[str, object]:
    """Re-open a rendered docx and report what a parser would actually see."""
    import zipfile

    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
        names = zf.namelist()

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)

    problems = []
    for tag, label in HOSTILE_TAGS.items():
        if f"<{tag}" in xml:
            problems.append(f"contains {label} ({tag})")
    if any(n.startswith("word/header") for n in names):
        problems.append("contains a header")
    if any(n.startswith("word/footer") for n in names):
        problems.append("contains a footer")
    if len(doc.tables) > 0:
        problems.append(f"{len(doc.tables)} table(s) via python-docx")
    for bad in PUNCTUATION_MAP:
        if bad in text:
            problems.append(f"contains U+{ord(bad):04X} {unicodedata.name(bad, '?')}")
    stray = sorted({c for c in text if ord(c) > 127} - set(PUNCTUATION_MAP))
    if stray:
        problems.append(
            "contains non-ASCII characters a parser may mangle: "
            + ", ".join(f"U+{ord(c):04X}" for c in stray[:8])
        )

    headings = [h for h in SECTION_HEADINGS.values() if h in text]

    return {
        "ok": not problems,
        "problems": problems,
        "headings_found": headings,
        "paragraphs": len(doc.paragraphs),
        "characters": len(text),
        "text": text,
    }


def keyword_coverage(path: Path, keywords: List[str]) -> Dict[str, bool]:
    """Which of the JD's must-have keywords actually appear in the rendered document."""
    text = audit(path)["text"].lower()
    return {kw: kw.lower() in text for kw in keywords}
