"""HTTP routes. Four screens, one of which does real work.

Everything runs synchronously. Analysing a job is two model calls and about ten seconds,
and this is a single-user app on localhost, so a task queue would be machinery serving
nobody. If a stage grows slow enough to need one, the fix is a job table and a polling
page, not threads.

The gates are not re-implemented here. `render_docx.gate` and `ats.gate` raise, this
catches and displays. A screen must never be the thing deciding what is safe to export,
because a second caller would then have no protection at all.
"""
from __future__ import annotations

import hashlib
import time
from datetime import timezone
from urllib.parse import quote
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import config
from database.db import get_db
from database.models import (DismissedAlert, GeneratedBlock, Package,
                             ProfileFact)
from modules import agent, cover, fit, gaps, house, tracker
from modules import contact as contact_module
from modules import design
from modules import keywords as kwmod
from modules import intake
from modules.ats import AtsBlocked, check as ats_check
from modules.extract import Extraction, NotAJobDescription, extract
from modules.fetch_jd import FetchError, clean, fetch
from modules.llm import LLMError
from modules.render_docx import (BlockedContentError, CoverPayload, export_name,  # noqa: E501
                                 audit, render_cover, render_resume)
from modules.tailor import Block, TailorResult, tailor, to_payload

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Status is shown in two lists and had to agree in both. Filters rather than a computed
# field on the row, because the answer depends on the tracker and a Package has no
# business knowing about applications.
_STATUS_CLASS = {
    "applied": "verified",     # green. It is out of his hands and that is the goal
    "exported": "inferred",    # amber. Built and downloaded, but not yet sent
    "ready": "inferred",
    "draft": "blocked",        # red. Nothing has left the machine
}

templates.env.filters["status_of"] = lambda package, keys: tracker.display_status(
    package, keys or set())
templates.env.filters["status_class"] = lambda status: _STATUS_CLASS.get(status, "section")


def _local(value: Any, fmt: str = "%d %b, %H:%M") -> str:
    """A time in his timezone, not the server's idea of one.

    Gmail hands back UTC and the screen printed it raw, so an email that reached him at
    half past eight this morning read as yesterday evening. He is ten hours ahead of UTC
    and correctly concluded the pull was stale.
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


templates.env.filters["local"] = _local
router = APIRouter()

OUTPUT_DIR = BASE_DIR / "data" / "output"


def render(request: Request, template: str, **context) -> Any:
    # `template`, not `name`, because context variables are keyword arguments here and
    # the details screen legitimately wants to pass a variable called name.
    context.setdefault("section", "")
    context.setdefault("module", "job")
    # the nav badge, computed from the same function the dashboard uses so the two
    # can never disagree. Screens that pass no session simply do not show it.
    db = context.pop("_db", None)
    if "live_count" not in context:
        context["live_count"] = _live_count(db) if db is not None else None
    return templates.TemplateResponse(request, template, context)


def _live_count(db: Session) -> Optional[int]:
    try:
        return tracker.stats(db)["live"] or None
    except Exception:  # noqa: BLE001 - a nav badge must never take a page down
        return None


# --------------------------------------------------------------------------- writer

@router.get("/")
def home():
    return RedirectResponse("/job", status_code=303)


@router.get("/job")
def dashboard(request: Request, db: Session = Depends(get_db), message: str = ""):
    """The landing screen. Numbers first, drafts underneath."""
    from modules import gmail
    return render(request, "dashboard.html", section="dashboard", _db=db,
                  stats=tracker.stats(db), gmail_ready=gmail.connected(),
                  message=message or None, applied_keys=tracker.applied_keys(db),
                  packages=db.query(Package).filter(Package.status != "deleted").order_by(
                      Package.created_at.desc()).limit(8).all())


@router.post("/job/scan")
def scan_inbox(db: Session = Depends(get_db)):
    """Read the acknowledgements employers sent and count them as applications.

    Not run on every dashboard load. It costs a Gmail round trip per candidate message,
    and a landing screen that takes four seconds to appear is a landing screen he stops
    opening.
    """
    from modules import applications, gmail

    if not gmail.connected():
        return RedirectResponse("/job?message=" + quote(
            "Gmail is not connected. Run python3 scripts/gmail_auth.py"), status_code=303)
    try:
        found = applications.scan(days=60)
        added = applications.record(db, found)
    except Exception as exc:  # noqa: BLE001 - a dead token must not be a stack trace
        return RedirectResponse("/job?message=" + quote(f"Could not read your inbox: {exc}"),
                                status_code=303)

    if not found:
        note = ("No application confirmations in the last 60 days. Employers usually "
                "acknowledge within minutes, so this means none have arrived yet.")
    elif added:
        note = f"Found {len(found)}, {added} new. The rest were already counted."
    else:
        note = f"Found {len(found)}, all of them already counted."
    return RedirectResponse("/job?message=" + quote(note), status_code=303)


@router.get("/posts")
def posts(request: Request):
    return render(request, "not_built.html", module="posts", section="",
                  heading="Post Writer",
                  why="The second module. The article writer he already runs "
                      "elsewhere lands here, so both tools share one window, one theme "
                      "and one set of details.",
                  blocked_on=["A decision on whether to port the existing app in or "
                              "rebuild it against this codebase's prompts and gates"])


def _tracker_view(request: Request, db: Session, **kw):
    """Plain function, not a route. FastAPI reads **kwargs on a route as query
    parameters and answers 422, so the shared view has to sit outside the decorator."""
    rows = tracker.all_applications(db)
    return render(request, "tracker.html", section="tracker", _db=db,
                  applications=rows,
                  stale_ids={r.id for r in rows if tracker.is_stale(r)},
                  status_labels=tracker.STATUS_LABELS,
                  sources=tracker.SOURCES,
                  ghost_days=tracker.GHOST_AFTER_DAYS, **kw)


@router.get("/job/tracker")
def tracker_page(request: Request, db: Session = Depends(get_db)):
    return _tracker_view(request, db)


@router.post("/job/tracker/add")
def tracker_add(request: Request, db: Session = Depends(get_db),
                title: str = Form(""), company: str = Form(""),
                url: str = Form(""), source: str = Form("manual")):
    if not (title.strip() or company.strip()):
        return _tracker_view(request, db, error="Give it at least a role or a company.",
                            error_title="Nothing to log")
    try:
        tracker.log_application(db, title=title, company=company, url=url, source=source)
    except ValueError as exc:
        return _tracker_view(request, db, error=str(exc), error_title="Could not log that")
    return RedirectResponse("/job/tracker", status_code=303)


@router.post("/job/tracker/status")
def tracker_status(request: Request, db: Session = Depends(get_db),
                   application_id: int = Form(...), status: str = Form(...)):
    try:
        tracker.set_status(db, application_id, status)
    except ValueError as exc:
        return _tracker_view(request, db, error=str(exc), error_title="Could not update that")
    return RedirectResponse("/job/tracker", status_code=303)


@router.post("/job/tracker/remove")
def tracker_remove(db: Session = Depends(get_db), application_id: int = Form(...)):
    tracker.remove(db, application_id)
    return RedirectResponse("/job/tracker", status_code=303)


@router.get("/job/writer")
def writer_form(request: Request, db: Session = Depends(get_db), error: str = ""):
    return _writer_view(request, db, error=error)


def _writer_view(request: Request, db: Session, **kw):
    packages = (db.query(Package).filter(Package.status != "deleted")
                .order_by(Package.created_at.desc()).limit(10).all())
    context = dict(packages=packages,
                   applied_keys=tracker.applied_keys(db),
                   resume_spec=design.active(db, "resume"),
                   cover_spec=design.active(db, "cover"))
    context.update({k: (v or None) for k, v in kw.items()})
    return render(request, "writer.html", section="writer", _db=db, **context)


@router.post("/job/writer/analyse")
def analyse(request: Request, db: Session = Depends(get_db),
            url: str = Form(""), job_text: str = Form("")):
    """URL or pasted text to a saved package. Two model calls, then the truth gate."""
    # Each form submits one field, so there is no longer a rule about which input wins
    # when both are filled in. There was one, it was "the pasted text", and nobody could
    # have guessed it from looking at the screen.
    text = clean(job_text)
    if not text and url.strip():
        try:
            text = fetch(url)
        except FetchError as exc:
            return render(request, "writer.html", section="writer", url=url,
                          error=str(exc), error_title="Could not fetch that job",
                          error_hint="Most job sites are fine. The big boards are not.",
                          packages=db.query(Package).filter(Package.status != "deleted").order_by(
                              Package.created_at.desc()).limit(10).all())
    if not text:
        return render(request, "writer.html", section="writer", url=url,
                      error="Give it a job URL or paste the description.",
                      error_title="Nothing to analyse",
                      packages=db.query(Package).filter(Package.status != "deleted").order_by(
                          Package.created_at.desc()).limit(10).all())

    # The same posting must give the same keywords every time. Without this, closing a
    # gap and analysing again could move the score for reasons that have nothing to do
    # with the gap: extract names a slightly different candidate set on each call, so the
    # denominator would shift underneath him and a real improvement could read as a drop.
    jd_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    seen = (db.query(Package)
            .filter(Package.jd_hash == jd_hash, Package.extraction.isnot(None))
            .order_by(Package.created_at.desc()).first())

    try:
        if seen and seen.extraction:
            extraction = Extraction.from_dict(seen.extraction)
            log.info("reusing the extraction from package %d, same posting", seen.id)
        else:
            t0 = time.monotonic()
            extraction = extract(text)
            log.info("analyse: extract took %.1fs for %d characters",
                     time.monotonic() - t0, len(text))
    except (NotAJobDescription, LLMError, RuntimeError) as exc:
        return render(request, "writer.html", section="writer", url=url, job_text=text,
                      error=f"Could not read that as a job description: {exc}",
                      error_title="That did not read as a job",
                      packages=db.query(Package).filter(Package.status != "deleted").order_by(
                          Package.created_at.desc()).limit(10).all())

    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    if not facts:
        return render(request, "writer.html", section="writer", job_text=text,
                      error="No career facts loaded. Run scripts/seed_profile.py first.",
                      error_title="Nothing to write from",
                      packages=[])

    try:
        t0 = time.monotonic()
        # Not a single tailoring call any more. The agent writes, reads which of the
        # posting's requirements no bullet answers and which bullets the gates refused,
        # and repairs only those, up to three rounds. Everything that passed is left
        # alone, so the extra rounds cost seconds rather than re-running the document.
        run = agent.write(extraction, facts,
                          house_spec=design.instruction(db, "resume"))
        result = run.result
        log.info("analyse: writer took %.1fs across %d round(s), family=%s",
                 time.monotonic() - t0, len(run.rounds), run.family)
    except (LLMError, RuntimeError) as exc:
        return render(request, "writer.html", section="writer", url=url, job_text=text,
                      error=f"The tailor stage failed: {exc}",
                      error_title="The writer stage failed",
                      packages=db.query(Package).filter(Package.status != "deleted").order_by(
                          Package.created_at.desc()).limit(10).all())

    # A prompt is advisory. Every list of forbidden wording in this app has eventually
    # been ignored by some model on some run, so the spec's banned phrases are checked
    # here too, and anything carrying one is downgraded for him to look at rather than
    # rendered.
    spec = design.active(db, "resume")
    if spec and spec.rules:
        for block in result.blocks:
            hits = design.violations(block.text, spec.rules)
            if hits and block.grade == "verified":
                block.grade = "inferred"
                block.accepted = False
                block.rationale = ((block.rationale or "") +
                                   f" Your spec bans: {', '.join(hits)}.").strip()

    package = Package(
        job_text=text,
        jd_hash=jd_hash,
        company=extraction.company,
        title=extraction.title,
        extraction=extraction.as_dict(),
        placement=dict((result.placement.as_dict() if result.placement else {}),
                       unanswered=kwmod.unanswered(extraction.must_keywords(),
                                                   result.blocks)),
        fit=_score_fit(db, extraction, result.placement, None),
        writer_run=run.as_dict(),
        status="draft",
    )
    db.add(package)
    db.flush()

    for index, block in enumerate(result.blocks):
        db.add(GeneratedBlock(
            package_id=package.id, section=block.section, org=block.org,
            text=block.text, fact_ids=block.fact_ids, grade=block.grade,
            rationale=block.rationale, accepted=block.accepted,
            order_index=block.order_index if block.order_index is not None else index,
        ))
    # kept, not discarded, so the review screen can show what the gate refused
    for block, why in result.rejected:
        db.add(GeneratedBlock(
            package_id=package.id, section=block.section, org=block.org,
            text=block.text, fact_ids=block.fact_ids, grade="blocked",
            rationale=why, accepted=False, order_index=999,
        ))
    db.commit()
    log.info("package %d: %s", package.id, result.summary_line())
    return RedirectResponse(f"/job/writer/{package.id}", status_code=303)


def _score_fit(db: Session, extraction, placement, ats_report) -> dict:
    """One place computes the score, so no two screens can disagree about it."""
    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    contact_module.bootstrap(db)
    home = next((d.value for d in contact_module.all_details(db)
                 if d.kind == "location"), "")
    try:
        return fit.assess(extraction, placement, facts, ats_report, location=home).as_dict()
    except Exception as exc:  # noqa: BLE001 - a score must never lose the package
        log.warning("fit scoring failed: %s", exc)
        return {}


def _package_view(request: Request, db: Session, package: Package,
                  report=None, error: str = "", message: str = "",
                  error_title: str = "", error_kind: str = ""):
    extraction = Extraction.from_dict(package.extraction or {})
    blocks = db.query(GeneratedBlock).filter(
        GeneratedBlock.package_id == package.id
    ).order_by(GeneratedBlock.order_index, GeneratedBlock.id).all()

    shown = [b for b in blocks if b.grade != "blocked"]
    rejected = [(b.text, b.rationale or "cited nothing real")
                for b in blocks if b.grade == "blocked"]

    fact_rows = db.query(ProfileFact).all()
    facts = {f.id: f.text for f in fact_rows}

    # Two things were wrong here and they compounded.
    #
    # The colouring was decided by membership in extraction.must_keywords(), which is the
    # scored keyword list, while the pills on screen sit beside extraction.must and show
    # r.keyword. Those are different vocabularies. Three of the six keywords displayed on
    # one real posting, "timely decisions", "risk management" and "risk and controls",
    # were not in the scored list at all, so they showed red permanently no matter what
    # the document said. A false gap is worse than no signal: it sends him looking for
    # evidence to close something that was never open.
    #
    # And a keyword could be red for two opposite reasons. Absent from the document
    # because nothing in the record supports it, or present in a block he has not ticked
    # yet. The first is a gap in the record and the second is one click.
    #
    # So both sets are now built from the keywords actually on screen, and decided by
    # looking at the text rather than by asking which list a term came from.
    rendered = " ".join(
        b.text for b in shown
        if b.grade == "verified" or b.accepted
    ).lower()
    written = " ".join(b.text for b in shown).lower()
    on_screen = {k for k in extraction.must_keywords() if k}
    on_screen |= {r.keyword for r in extraction.must if getattr(r, "keyword", None)}
    hit_keywords = {k for k in on_screen if k.lower() in rendered}
    pending_keywords = {
        k for k in on_screen if k not in hit_keywords and k.lower() in written
    }

    # Style faults, read off the bullets that would actually be exported. Reported and
    # never enforced: a stiff sentence is worth a second pass and is not a reason to
    # refuse a document, which is the line between this and the truth gates above.
    tells = house.natural_language([
        b.text for b in shown
        if b.section == "experience" and (b.grade == "verified" or b.accepted)
    ])

    contact_module.bootstrap(db)
    return render(request, "review.html", section="writer", _db=db,
                  writing_tells=tells,
                  placement=package.placement or {},
                  writer_run=package.writer_run or {},
                  cover=_cover_from(package) if package.cover else None,
                  fit=package.fit or {},
                  fact_count=len(fact_rows),
                  orgs=gaps.roles(fact_rows),
                  package=package, extraction=extraction,
                  must=extraction.must, nice=extraction.nice,
                  blocks=shown, rejected=rejected, facts=facts,
                  hit_keywords=hit_keywords, pending_keywords=pending_keywords,
                  report=report,
                  contact_warnings=contact_module.warnings(db),
                  error=error or None, message=message or None,
                  error_title=error_title or None, error_kind=error_kind or None)


@router.get("/job/writer/{package_id}")
def review(request: Request, package_id: int, db: Session = Depends(get_db),
           error: str = "", error_title: str = ""):
    package = db.get(Package, package_id)
    if package is None:
        return RedirectResponse("/job/writer", status_code=303)
    return _package_view(request, db, package, error=error or None,
                         error_title=error_title or None,
                         error_kind="bad" if error else None)


@router.post("/job/writer/{package_id}/accept")
def accept(request: Request, package_id: int, db: Session = Depends(get_db),
           accept: List[int] = Form(default=[]), action: str = Form("save")):
    package = db.get(Package, package_id)
    if package is None:
        return RedirectResponse("/job/writer", status_code=303)

    chosen = set(accept)
    blocks = db.query(GeneratedBlock).filter(
        GeneratedBlock.package_id == package_id).all()
    changed = False
    for block in blocks:
        if block.grade in ("inferred", "stretch"):
            wanted = block.id in chosen
            if block.accepted != wanted:
                block.accepted = wanted
                changed = True

    if changed:
        _invalidate(package)
    db.commit()

    if action != "render":
        return RedirectResponse(f"/job/writer/{package_id}", status_code=303)
    return _build(request, db, package)


def _drop_file(path_str: str) -> None:
    try:
        Path(path_str).unlink(missing_ok=True)
    except OSError as exc:  # a locked file must not take the request down
        log.warning("could not remove %s: %s", path_str, exc)


def _invalidate(package: Package) -> None:
    """Throw away a built resume whose choices have since changed.

    Without this, unticking a block leaves the passing file on disk and the download
    route serves it, because that route checks the file rather than the choices. Accept
    a stretch claim, build, untick it, download, and the claim is still in the document.
    The file is deleted rather than just unlinked from the row, so nothing can serve it.
    """
    if not package.resume_path:
        return
    stale = Path(package.resume_path)
    try:
        stale.unlink(missing_ok=True)
    except OSError as exc:  # a locked file must not take the request down
        log.warning("could not remove stale resume %s: %s", stale, exc)
    log.info("package %d: choices changed, discarded the built resume", package.id)
    package.resume_path = None
    package.status = "draft"


def _build(request: Request, db: Session, package: Package):
    """Render and run the ATS gate. Both gates raise; this reports rather than routes around."""
    extraction = Extraction.from_dict(package.extraction or {})
    rows = db.query(GeneratedBlock).filter(
        GeneratedBlock.package_id == package.id,
        GeneratedBlock.grade != "blocked",
    ).order_by(GeneratedBlock.order_index, GeneratedBlock.id).all()

    result = TailorResult(blocks=[
        Block(section=r.section, text=r.text, fact_ids=list(r.fact_ids or []),
              grade=r.grade, org=r.org, rationale=r.rationale,
              accepted=bool(r.accepted), order_index=r.order_index)
        for r in rows
    ])

    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    contact_module.bootstrap(db)
    # The header claims what the bullets prove. Writing the posting's title under his
    # name reads as aspirational when the body underneath describes a different job, and
    # a screener checks the experience section rather than the header anyway. So it goes
    # on only when the record is close enough to the role to carry it.
    fit_now = package.fit or {}
    seniority = next((c for c in fit_now.get("components", [])
                      if c.get("name") == "Seniority"), None)
    earned = not seniority or seniority.get("points", 0) >= 12

    payload = to_payload(result, facts,
                         contact=contact_module.resume_lines(db),
                         name=contact_module.display_name(db),
                         headline=(package.title or "") if earned else "",
                         wanted_terms=extraction.must_keywords())

    if not payload.experience and not payload.summary:
        return _package_view(request, db, package,
                             error="Nothing is accepted yet, so there is no resume to build. "
                                   "Tick the amber lines you want to use.",
                             error_title="Nothing to build")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / export_name(payload.name, package.title, package.id)
    try:
        path = render_resume(payload, out)
    except BlockedContentError as exc:
        return _package_view(request, db, package,
                             error=f"The truth gate refused this: {exc}",
                             error_title="The truth gate refused this",
                             error_kind="bad")

    report = ats_check(
        path,
        must_keywords=extraction.must_keywords(),
        nice_keywords=extraction.nice_keywords(),
        expect_roles=len(payload.experience),
        expect_phone=True,
    )

    package.resume_path = str(path)
    package.status = "ready" if report.passed else "draft"
    # rescored now that a real document exists to judge
    package.fit = _score_fit(db, extraction, package.placement or {}, report)
    db.commit()

    message = ""
    if report.passed:
        message = f"Resume built and cleared the check at {report.score}/100."
    return _package_view(request, db, package, report=report, message=message)


@router.post("/job/writer/{package_id}/remove")
def package_remove(package_id: int, db: Session = Depends(get_db)):
    """Soft delete, and the built files go with it. Nothing here is hard deleted."""
    package = db.get(Package, package_id)
    if package is not None:
        for path in (package.resume_path, package.cover_path):
            if path:
                _drop_file(path)
        package.status = "deleted"
        package.resume_path = None
        package.cover_path = None
        db.commit()
        log.info("package %d removed from the list", package_id)
    return RedirectResponse("/job/writer", status_code=303)


@router.get("/job/writer/{package_id}/download")
def download(package_id: int, db: Session = Depends(get_db)):
    """Re-runs the ATS gate before serving. The download is the export."""
    package = db.get(Package, package_id)
    if package is None or not package.resume_path:
        return RedirectResponse("/job/writer", status_code=303)

    path = Path(package.resume_path)
    if not path.exists():
        return RedirectResponse(
            f"/job/writer/{package_id}?error_title=That+file+is+gone"
            "&error=The+built+resume+is+no+longer+on+disk.+Build+it+again.",
            status_code=303)

    extraction = Extraction.from_dict(package.extraction or {})
    report = ats_check(path, must_keywords=extraction.must_keywords(),
                       expect_roles=0, expect_phone=True)
    try:
        from modules.ats import gate as ats_gate
        ats_gate(report)
    except AtsBlocked as exc:
        # Silently bouncing back looked like a broken button. Say what the filter would do.
        return RedirectResponse(
            f"/job/writer/{package_id}?error_title=" + quote("An ATS would reject this")
            + "&error=" + quote(str(exc)), status_code=303)

    package.status = "exported"
    db.commit()
    contact_module.bootstrap(db)
    name = export_name(contact_module.display_name(db), package.title or "")
    return FileResponse(path, filename=name, media_type=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))


@router.post("/job/writer/{package_id}/rewrite")
def rewrite(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Write it again against the same posting, using whatever the record says now.

    The point of closing a gap is seeing it count. That only works if the posting is
    held still: the stored extraction is reused rather than re-derived, so any movement
    in the score is movement in the record and not the model picking different words.
    """
    package = db.get(Package, package_id)
    if package is None or not package.extraction:
        return RedirectResponse("/job/writer", status_code=303)

    extraction = Extraction.from_dict(package.extraction)
    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    before = (package.fit or {}).get("score")

    try:
        t0 = time.monotonic()
        run = agent.write(extraction, facts,
                          house_spec=design.instruction(db, "resume"))
        result = run.result
        log.info("rewrite: writer took %.1fs across %d round(s), family=%s",
                 time.monotonic() - t0, len(run.rounds), run.family)
    except (LLMError, RuntimeError) as exc:
        return _package_view(request, db, package, error=str(exc),
                             error_title="Could not write it again")

    db.query(GeneratedBlock).filter(GeneratedBlock.package_id == package.id).delete()
    for index, block in enumerate(result.blocks):
        db.add(GeneratedBlock(
            package_id=package.id, section=block.section, org=block.org,
            text=block.text, fact_ids=block.fact_ids, grade=block.grade,
            rationale=block.rationale, accepted=block.accepted,
            order_index=block.order_index if block.order_index is not None else index,
        ))
    for block, why in result.rejected:
        db.add(GeneratedBlock(
            package_id=package.id, section=block.section, org=block.org,
            text=block.text, fact_ids=block.fact_ids, grade="blocked",
            rationale=why, accepted=False, order_index=999,
        ))

    kept = (package.placement or {}).get("suggestions")
    package.placement = result.placement.as_dict() if result.placement else {}
    if kept:
        package.placement["suggestions"] = kept
    package.fit = _score_fit(db, extraction, result.placement, None)
    _invalidate(package)
    db.commit()

    after = (package.fit or {}).get("score")
    moved = ""
    if before is not None and after is not None:
        delta = after - before
        moved = (f" Fit moved {before} to {after}." if delta
                 else f" Fit unchanged at {after}.")
    return _package_view(request, db, package,
                         message=f"Written again against the same posting.{moved}")


# -------------------------------------------------------------------- cover letter

def _cover_from(package: Package) -> cover.CoverLetter:
    """Rebuild the stored letter, so acceptance and rendering share one shape."""
    stored = package.cover or {}
    letter = cover.CoverLetter(
        greeting=stored.get("greeting") or "Dear Hiring Manager,",
        sign_off=stored.get("sign_off") or "Kind regards,",
        warnings=list(stored.get("warnings") or []),
    )
    for index, item in enumerate(stored.get("paragraphs") or []):
        letter.paragraphs.append(Block(
            section="cover", text=item.get("text", ""),
            fact_ids=list(item.get("fact_ids") or []),
            grade=item.get("grade", "inferred"),
            rationale=item.get("rationale"),
            accepted=bool(item.get("accepted")),
            order_index=item.get("order_index", index),
        ))
    letter.rejected = [(Block(section="cover", text=r.get("text", ""), grade="blocked"),
                        r.get("why", "refused"))
                       for r in (stored.get("rejected") or [])]
    return letter


@router.post("/job/writer/{package_id}/cover")
def cover_write(request: Request, package_id: int, db: Session = Depends(get_db)):
    package = db.get(Package, package_id)
    if package is None or not package.extraction:
        return RedirectResponse("/job/writer", status_code=303)

    extraction = Extraction.from_dict(package.extraction)
    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    # what the resume already says, so the letter builds on it rather than repeating it
    written = db.query(GeneratedBlock).filter(
        GeneratedBlock.package_id == package.id,
        GeneratedBlock.section == "experience",
        GeneratedBlock.grade == "verified",
    ).order_by(GeneratedBlock.order_index).all()

    try:
        letter = cover.write(extraction, facts, resume_blocks=written,
                             house_spec=design.instruction(db, "cover"))
    except (LLMError, RuntimeError) as exc:
        return _package_view(request, db, package, error=str(exc),
                             error_title="Could not draft the letter")

    package.cover = letter.as_dict()
    db.commit()
    return RedirectResponse(f"/job/writer/{package_id}#cover", status_code=303)


@router.post("/job/writer/{package_id}/cover/accept")
def cover_accept(request: Request, package_id: int, db: Session = Depends(get_db),
                 para: List[int] = Form(default=[]), action: str = Form("save")):
    # `para`, not `accept`: the resume form's checkboxes carry database ids and these
    # carry list positions. Two different things under one name is the kind of overlap
    # that costs an afternoon the first time a form gets moved.
    package = db.get(Package, package_id)
    if package is None or not package.cover:
        return RedirectResponse("/job/writer", status_code=303)

    stored = dict(package.cover)
    chosen = set(para)
    paragraphs = []
    for index, item in enumerate(stored.get("paragraphs") or []):
        item = dict(item)
        if item.get("grade") in ("inferred", "stretch"):
            item["accepted"] = index in chosen
        paragraphs.append(item)
    stored["paragraphs"] = paragraphs
    package.cover = stored
    if package.cover_path:
        _drop_file(package.cover_path)      # the choices moved, so the file is stale
        package.cover_path = None
    db.commit()

    if action != "build":
        return RedirectResponse(f"/job/writer/{package_id}#cover", status_code=303)

    letter = _cover_from(package)
    contact_module.bootstrap(db)
    payload = CoverPayload(
        name=contact_module.display_name(db),
        contact=contact_module.resume_lines(db),
        greeting=letter.greeting,
        paragraphs=[b.text for b in letter.usable],
        sign_off=letter.sign_off,
        role=package.title or "",
        company=package.company or "",
        date_line=date.today().strftime("%d %B %Y"),
    )
    out = OUTPUT_DIR / export_name(payload.name, package.title, package.id,
                                   kind="Cover Letter")
    try:
        path = render_cover(payload, out)
    except BlockedContentError as exc:
        return _package_view(request, db, package, error=str(exc),
                             error_title="Nothing to put in the letter")

    package.cover_path = str(path)
    db.commit()
    report = audit(path)
    message = f"Cover letter built, {letter.word_count} words."
    if not report["ok"]:
        return _package_view(request, db, package,
                             error="; ".join(str(x) for x in report["problems"]),
                             error_title="The letter would not parse cleanly")
    return _package_view(request, db, package, message=message)


@router.get("/job/writer/{package_id}/cover/download")
def cover_download(package_id: int, db: Session = Depends(get_db)):
    package = db.get(Package, package_id)
    if package is None or not package.cover_path:
        return RedirectResponse("/job/writer", status_code=303)
    path = Path(package.cover_path)
    if not path.exists():
        return RedirectResponse(f"/job/writer/{package_id}", status_code=303)
    contact_module.bootstrap(db)
    name = export_name(contact_module.display_name(db), package.title or "",
                       kind="Cover Letter")
    return FileResponse(path, filename=name, media_type=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))


# ---------------------------------------------------------------------- gap closer

@router.post("/job/writer/{package_id}/gaps")
def gaps_ask(request: Request, package_id: int, db: Session = Depends(get_db)):
    """Ask him about the gaps the record is anywhere near. Costs a few model calls."""
    package = db.get(Package, package_id)
    if package is None:
        return RedirectResponse("/job/writer", status_code=303)

    report = package.placement or {}
    wanted = list(report.get("gaps") or []) + list(report.get("unsupported") or [])
    if not wanted:
        return _package_view(request, db, package,
                             message="No gaps to close on this one.")

    facts = [f for f in db.query(ProfileFact).order_by(ProfileFact.order_index).all()
             if f.verified]
    try:
        suggestions = gaps.suggest(wanted, facts, role=package.title or "")
    except Exception as exc:  # noqa: BLE001 - a failed suggestion must not lose the package
        log.warning("gap closer failed: %s", exc)
        return _package_view(request, db, package,
                             error=f"Could not draft the questions: {exc}",
                             error_title="The gap closer failed")

    package.placement = {**report, "suggestions": [s.as_dict() for s in suggestions]}
    db.commit()
    return RedirectResponse(f"/job/writer/{package_id}#gaps", status_code=303)


@router.post("/job/writer/{package_id}/gaps/save")
def gaps_save(request: Request, package_id: int, db: Session = Depends(get_db),
              text: str = Form(""), org: str = Form(""), keyword: str = Form("")):
    """Turn what he typed into a fact. His words, his attestation, his record."""
    package = db.get(Package, package_id)
    if package is None:
        return RedirectResponse("/job/writer", status_code=303)
    try:
        gaps.save_answer(db, text, org=org or None, keyword=keyword or None)
    except (ValueError, OSError, KeyError) as exc:
        return _package_view(request, db, package, error=str(exc),
                             error_title="Could not save that")
    return _package_view(
        request, db, package,
        message=("Saved to your record. Rebuild the resume and it can be cited now. "
                 "It is in data/profile_facts.json too, so a re-seed keeps it."))


# -------------------------------------------------------------------------- details

@router.get("/job/details")
def details(request: Request, db: Session = Depends(get_db),
            error: str = "", message: str = ""):
    return _details_view(request, db, error=error, message=message)


def _details_view(request: Request, db: Session, **kw):
    contact_module.bootstrap(db)
    context = dict(
        details=contact_module.all_details(db),
        name=contact_module.display_name(db),
        lines=contact_module.resume_lines(db),
        warnings=contact_module.warnings(db),
        kinds=("name",) + contact_module.KINDS,
        fact_count=db.query(ProfileFact).count(),
    )
    context.update({k: (v or None) for k, v in kw.items()})
    return render(request, "details.html", section="details", _db=db, **context)


@router.post("/job/writer/spec")
async def writer_spec(request: Request, db: Session = Depends(get_db),
                      kind: str = Form(...), upload: UploadFile = File(...)):
    """Upload a house spec. It joins the instruction the writer gets from the next run."""
    suffix = Path(upload.filename or "").suffix.lower()
    tmp = OUTPUT_DIR / f"_spec{suffix or '.md'}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(await upload.read())
    try:
        text = intake.read(tmp)
        spec = design.save(db, kind, Path(upload.filename or "spec").stem, text)
    except (intake.UnreadableFile, ValueError) as exc:
        return _writer_view(request, db, error=str(exc),
                            error_title="Could not use that spec")
    finally:
        tmp.unlink(missing_ok=True)

    banned = len((spec.rules or {}).get("banned") or [])
    return _writer_view(
        request, db,
        message=f"{kind.title()} spec \u201c{spec.name}\u201d is now in force. "
                f"{banned} banned phrase(s) will be checked in code as well as asked for "
                f"in the prompt.")


@router.post("/job/writer/spec/remove")
def writer_spec_remove(request: Request, db: Session = Depends(get_db),
                       kind: str = Form(...)):
    from database.models import DesignSpec
    for row in db.query(DesignSpec).filter(DesignSpec.kind == kind,
                                           DesignSpec.active.is_(True)).all():
        row.active = False
    db.commit()
    return _writer_view(request, db, message=f"{kind.title()} spec switched off. "
                                             f"House defaults apply again.")


@router.post("/job/details/import")
async def details_import(request: Request, db: Session = Depends(get_db),
                         upload: UploadFile = File(...)):
    """Read a CV or notes file and show what it found. Nothing is saved on this step."""
    suffix = Path(upload.filename or "").suffix.lower()
    tmp = OUTPUT_DIR / f"_import{suffix or '.txt'}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(await upload.read())
    try:
        text = intake.read(tmp)
        facts = db.query(ProfileFact).all()
        proposals = intake.propose(text, facts)
    except intake.UnreadableFile as exc:
        return _details_view(request, db, error=str(exc),
                             error_title="Could not read that file")
    finally:
        tmp.unlink(missing_ok=True)

    if not proposals:
        return _details_view(request, db,
                             error="Nothing in there looked like a fact about your career.",
                             error_title="Found nothing to add")
    return _details_view(request, db, proposals=proposals,
                         source_name=upload.filename or "your file")


@router.post("/job/details/import/save")
def details_import_save(request: Request, db: Session = Depends(get_db),
                        text: List[str] = Form(default=[]),
                        kind: List[str] = Form(default=[])):
    """Write only the ticked lines. Verified, because he read them and ticked them."""
    chosen = [intake.Candidate(text=value, kind=(kind[i] if i < len(kind) else "bullet"))
              for i, value in enumerate(text) if value.strip()]
    if not chosen:
        return _details_view(request, db, error="Nothing was ticked, so nothing was added.",
                             error_title="Nothing to add")
    written = intake.accept(chosen, db=db)
    return _details_view(
        request, db,
        message=f"Added {written} fact(s) to your record. Re-analyse a job and the "
                f"writer can cite them straight away.")


@router.post("/job/details/set")
def details_set(request: Request, db: Session = Depends(get_db),
                kind: str = Form(...), value: str = Form("")):
    try:
        contact_module.set_detail(db, kind, value)
    except ValueError as exc:
        return details(request, db, error=str(exc))
    return RedirectResponse("/job/details", status_code=303)


@router.post("/job/details/visibility")
def details_visibility(request: Request, db: Session = Depends(get_db),
                       detail_id: int = Form(...), renders: str = Form("on")):
    try:
        contact_module.set_renders(db, detail_id, renders == "on")
    except ValueError as exc:
        return details(request, db, error=str(exc))
    return RedirectResponse("/job/details", status_code=303)


# ------------------------------------------------------------- not built yet

@router.get("/job/alerts")
def alerts(request: Request, db: Session = Depends(get_db), days: int = 14,
           message: str = "", error: str = "", error_title: str = ""):
    """Jobs the boards emailed him. The scout, arriving by the only honest route."""
    from modules import gmail, inbox

    if not gmail.connected():
        return render(request, "not_built.html", section="alerts",
                      heading="Job Alerts",
                      why="Set alerts on LinkedIn and Naukri and they will email you the "
                          "listings. This reads them out of your inbox, which is the one "
                          "way to get at those boards without scraping them.",
                      blocked_on=["Gmail is not connected. Run "
                                  "python3 scripts/gmail_auth.py"])
    # Read from what the last pull stored. Reading Gmail here cost nine seconds on
    # eighteen messages, because every one needs its body fetched to find the links, and
    # a screen that takes nine seconds is a screen he stops opening. Sync is a button.
    listings = inbox.stored(db)

    cleared = {row.key for row in db.query(DismissedAlert).all()}
    listings = [l for l in listings if l.key not in cleared]
    facts = db.query(ProfileFact).all()

    # Sorted by relevance, not by date. A feed ordered by arrival makes him read all of
    # it to find the two worth opening.
    rows = sorted(
        ({"listing": l, "score": inbox.relevance(l, facts)} for l in listings),
        key=lambda r: r["score"], reverse=True)
    for row in rows:
        row["band"] = inbox.band(row["score"])

    return render(request, "alerts.html", section="alerts", days=days, rows=rows,
                  listings=listings, off_target=inbox.off_target(listings),
                  last_sync=inbox.last_sync(db),
                  message=message or None, error=error or None,
                  error_title=error_title or None)


@router.post("/job/alerts/sync")
def alerts_sync(db: Session = Depends(get_db), days: int = Form(14)):
    """Pull fresh alerts from Gmail. The only place in this screen that waits."""
    from modules import gmail, inbox

    if not gmail.connected():
        return RedirectResponse("/job/alerts?error=" + quote(
            "Gmail is not connected. Run python3 scripts/gmail_auth.py"), status_code=303)
    try:
        found = inbox.scan(days=days)
        added = inbox.store(db, found)
    except Exception as exc:  # noqa: BLE001 - a dead token must not be a stack trace
        return RedirectResponse("/job/alerts?error=" + quote(f"Gmail did not answer: {exc}")
                                + "&error_title=" + quote("Could not sync"), status_code=303)

    if not found:
        note = (f"Nothing in the last {days} days. Either no alerts have arrived, or "
                f"they are not being emailed to this address.")
    elif added:
        note = f"Read {len(found)}, {added} new."
    else:
        note = f"Read {len(found)}. Nothing new since the last pull."
    return RedirectResponse("/job/alerts?message=" + quote(note), status_code=303)


@router.post("/job/alerts/clear")
def alerts_clear(db: Session = Depends(get_db), key: List[str] = Form(default=[]),
                 label: List[str] = Form(default=[])):
    """Clear one row, or the lot. Keys are kept so a mistake can be undone."""
    from modules import inbox

    keys = [k for k in key if k.strip()]
    if not keys:
        return RedirectResponse("/job/alerts", status_code=303)

    known = {row.key for row in db.query(DismissedAlert).all()}
    for index, k in enumerate(keys):
        if k in known:
            continue
        db.add(DismissedAlert(key=k, label=label[index] if index < len(label) else None))
    db.commit()
    log.info("alerts: cleared %d row(s)", len(keys))
    return RedirectResponse("/job/alerts", status_code=303)


@router.get("/job/brief")
def brief(request: Request):
    return render(request, "not_built.html", section="brief",
                  heading="Interview Brief",
                  why="Paste a job description and get the questions they are likely to "
                      "ask, what the company is actually like, and what is worth asking "
                      "them. Standalone on purpose: it reads none of your career facts "
                      "and writes nothing back, so it can be deleted without touching the "
                      "rest of the app.",
                  blocked_on=None)
