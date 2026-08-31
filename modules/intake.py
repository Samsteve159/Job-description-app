"""Read a CV or a notes file and propose new facts from it.

This writes to the career record, which is the one file every claim in every document
traces back to. So it holds a hard line: **nothing here is ever rewritten by a model.**
Every proposal is a line lifted verbatim from the file, and none of it is saved until he
has ticked it.

That is not caution for its own sake. A model asked to tidy a CV into facts will smooth
"supported the migration" into "led the migration", and the truth gate downstream cannot
catch it, because by then the fact itself is the lie and the gate only checks that claims
cite facts. The one place a fabrication would be laundered into a source of truth is
exactly here, so the model is not invited.

    text = read(Path("~/Desktop/cv.pdf"))
    proposals = propose(text, existing_facts)
    # he ticks some
    accept(chosen)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from modules import keywords as kw

log = logging.getLogger(__name__)

SUFFIXES = (".pdf", ".md", ".markdown", ".txt")
MAX_BYTES = 10 * 1024 * 1024

MIN_CHARS = 12
MAX_CHARS = 400

_BULLET = re.compile(r"^\s*[-*•●▪‣⁃>]+\s*")
_MD_HEADING = re.compile(r"^\s*#{1,6}\s*")
_PAGE = re.compile(r"^\s*(page\s*)?\d+\s*(of\s*\d+)?\s*$", re.I)
_CONTACT = re.compile(r"@[\w.-]+\.\w+|\+\d[\d\s()-]{7,}|linkedin\.com/|github\.com/", re.I)
_DATE_ONLY = re.compile(
    r"^\s*((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*)?\d{4}"
    r"\s*[-–—to]*\s*((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*)?"
    r"(\d{4}|present|current)?\s*$", re.I)

_CERT = re.compile(
    r"\b(certifi\w+|accredit\w+|chartered|licen[cs]ed|CFA|FRM|CIMA|CPA|PMP|CSCP|CIPS|"
    r"Prince2|Scrum Master)\b", re.I)
_EDUCATION = re.compile(
    r"\b(bachelor|master|mba|m\.?sc|b\.?sc|b\.?com|m\.?com|b\.?tech|m\.?tech|phd|"
    r"doctorate|diploma|university|college|institute of)\b", re.I)

# A heading tells you what the lines under it are. Cheaper and more reliable than
# guessing from each line on its own, and it is information the file is already giving.
_SECTIONS = {
    "skill": re.compile(r"^\s*(technical\s+)?(skills?|competenc\w+|tools?|technolog\w+|"
                        r"proficienc\w+)\b", re.I),
    "cert": re.compile(r"^\s*(certification?s?|licen[cs]es?|accreditations?|courses?|"
                       r"training)\b", re.I),
    "education": re.compile(r"^\s*(education|qualifications?|academics?)\b", re.I),
    "bullet": re.compile(r"^\s*(experience|employment|work history|projects?|"
                         r"achievements?|accomplishments?|professional)\b", re.I),
}

# A skills line is usually a list, not a sentence.
_SPLITTERS = re.compile(r"\s*[|;•]\s*|\s*,\s(?=[A-Z])")

_VERB_START = re.compile(
    r"^(built|led|ran|owned|delivered|designed|created|automated|implemented|reduced|"
    r"increased|migrated|analysed|analyzed|managed|developed|launched|rebuilt|scaled|"
    r"drove|improved|resolved|shipped|introduced|established|negotiated|audited|"
    r"reconciled|forecast\w*|modelled|modeled|presented|partnered|supported)\b", re.I)


class UnreadableFile(RuntimeError):
    """The file cannot be read, in a way worth telling him about."""


@dataclass
class Candidate:
    """One proposed fact. `text` is verbatim from his file and is never edited here."""
    text: str
    kind: str = "bullet"
    tags: List[str] = field(default_factory=list)
    note: str = ""
    duplicate_of: Optional[str] = None

    @property
    def is_new(self) -> bool:
        return self.duplicate_of is None

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "kind": self.kind, "tags": self.tags,
                "note": self.note, "duplicate_of": self.duplicate_of,
                "is_new": self.is_new}


# ------------------------------------------------------------------------- reading

def read(path: Path) -> str:
    """A PDF, a markdown file or plain text, to plain text."""
    path = Path(path)
    if not path.exists():
        raise UnreadableFile(f"{path.name} is not there")
    if path.suffix.lower() not in SUFFIXES:
        raise UnreadableFile(
            f"{path.suffix or 'that'} is not a format this reads. Use PDF, Markdown or txt")
    if path.stat().st_size > MAX_BYTES:
        raise UnreadableFile(f"{path.name} is larger than 10MB")

    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # noqa: BLE001
        raise UnreadableFile("PDF support needs pypdf: pip install pypdf") from exc

    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            raise UnreadableFile(f"{path.name} is password protected")
        pages = [page.extract_text() or "" for page in reader.pages]
    except UnreadableFile:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises many things
        raise UnreadableFile(f"Could not read {path.name}: {exc}") from exc

    text = "\n".join(pages)
    if not text.strip():
        raise UnreadableFile(
            f"{path.name} has no readable text. A scanned PDF is an image, not words. "
            f"Export it from the original document instead")
    return text


# ------------------------------------------------------------------------ proposing

def _noise(line: str) -> bool:
    if len(line) < MIN_CHARS or len(line) > MAX_CHARS:
        return True
    if _PAGE.match(line) or _DATE_ONLY.match(line):
        return True
    if _CONTACT.search(line):
        return True
    # A line with no lowercase is a heading shouting, not a sentence.
    return not any(c.islower() for c in line)


def _classify(line: str, section: Optional[str]) -> str:
    # A heading he wrote beats a pattern I guessed. Under "Skills", "Power BI" is a
    # skill, whatever a credential pattern makes of the words.
    if section == "skill" and not _CERT.search(line):
        return "skill"
    if _CERT.search(line):
        return "cert"
    if _EDUCATION.search(line):
        return "education"
    if section in ("skill", "cert", "education"):
        return section
    if _VERB_START.match(line):
        return "bullet"
    # Short, no verb, no full stop: a capability rather than a claim about doing one.
    if len(line) < 60 and "." not in line.rstrip("."):
        return "skill"
    return "bullet"


def _section_of(line: str) -> Optional[str]:
    stripped = _MD_HEADING.sub("", line).strip()
    if len(stripped) > 40:
        return None
    for kind, pattern in _SECTIONS.items():
        if pattern.match(stripped):
            return kind
    return None


def _existing_index(facts: Sequence[Any]) -> List[set]:
    out = []
    for fact in facts or []:
        text = (getattr(fact, "text", None) or (
            fact.get("text") if isinstance(fact, dict) else "")) or ""
        tokens = set(kw.tokens(text))
        if tokens:
            out.append(tokens)
    return out


def _duplicate(line: str, index: Sequence[set]) -> Optional[str]:
    """Near-enough counts. Re-importing a CV must not double every bullet on it."""
    mine = set(kw.tokens(line))
    if not mine:
        return None
    for tokens in index:
        overlap = len(mine & tokens)
        if overlap and overlap / max(len(mine), 1) >= 0.75:
            return " ".join(sorted(tokens))[:60]
    return None


def propose(text: str, facts: Sequence[Any] = ()) -> List[Candidate]:
    """Lines from his file that look like facts and are not already on record."""
    index = _existing_index(facts)
    seen_here: List[set] = []
    out: List[Candidate] = []
    section: Optional[str] = None

    for raw in (text or "").splitlines():
        line = raw.replace("\t", " ")
        heading = _section_of(line)
        if heading:
            section = heading
            continue
        if _MD_HEADING.match(line):
            section = None
            continue

        line = _BULLET.sub("", line).strip()
        line = " ".join(line.split())
        if not line:
            continue

        pieces = [line]
        if section == "skill" and _SPLITTERS.search(line):
            pieces = [p.strip() for p in _SPLITTERS.split(line) if p and p.strip()]

        for piece in pieces:
            if section == "skill" and MIN_CHARS > len(piece) >= 2:
                pass          # a skill can legitimately be "SQL"
            elif _noise(piece):
                continue

            kind = _classify(piece, section)
            dup = _duplicate(piece, index) or _duplicate(piece, seen_here)
            out.append(Candidate(
                text=piece, kind=kind,
                note=("already on record, so it is off by default" if dup
                      else f"read as a {kind}"),
                duplicate_of=dup,
            ))
            seen_here.append(set(kw.tokens(piece)))

    log.info("intake: %d candidate(s), %d new", len(out), sum(1 for c in out if c.is_new))
    return out


def accept(candidates: Sequence[Candidate], db: Any = None, source: str = "imported",
           seed_file: Optional[Path] = None) -> int:
    """Write the ticked ones to the career record. Verified, because he ticked them.

    Both stores, JSON first. The file is the source of truth and survives the re-seed
    that wipes ProfileFact; the row is what lets the very next tailoring run cite the
    fact without restarting anything. File first, so a failed write inserts nothing and
    the two cannot drift.
    """
    from modules.gaps import add_fact

    written = 0
    for candidate in candidates:
        try:
            add_fact(candidate.text, kind=candidate.kind, tags=candidate.tags,
                     source=source, seed_file=seed_file)
        except ValueError as exc:
            log.warning("intake: refused %r: %s", candidate.text[:50], exc)
            continue

        if db is not None:
            from database.models import ProfileFact
            highest = db.query(ProfileFact).order_by(
                ProfileFact.order_index.desc()).first()
            db.add(ProfileFact(
                kind=candidate.kind, text=candidate.text, tags=list(candidate.tags),
                source=source, verified=True,
                order_index=((highest.order_index or 0) + 1) if highest else 0,
            ))
        written += 1

    if db is not None and written:
        db.commit()
    log.info("intake: %d fact(s) added from an imported file", written)
    return written
