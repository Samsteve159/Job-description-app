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
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import config
from database.db import get_db
from database.models import GeneratedBlock, Package, ProfileFact
from modules import fit, gaps, tracker
from modules import contact as contact_module
from modules.ats import AtsBlocked, check as ats_check
from modules.extract import Extraction, NotAJobDescription, extract
from modules.fetch_jd import FetchError, clean, fetch
from modules.llm import LLMError
from modules.render_docx import BlockedContentError, render_resume
from modules.tailor import Block, TailorResult, tailor, to_payload

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
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
def dashboard(request: Request, db: Session = Depends(get_db)):
    """The landing screen. Numbers first, drafts underneath."""
    return render(request, "dashboard.html", section="dashboard", _db=db,
                  stats=tracker.stats(db), gmail_ready=False,
                  packages=db.query(Package).order_by(
                      Package.created_at.desc()).limit(8).all())


@router.get("/posts")
def posts(request: Request):
    return render(request, "not_built.html", module="posts", section="",
                  heading="Post Writer",
                  why="The second module. The article writer Sameer already runs "
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
    packages = db.query(Package).order_by(Package.created_at.desc()).limit(12).all()
    return render(request, "writer.html", section="writer", _db=db,
                  packages=packages, error=error or None)


@router.post("/job/writer/analyse")
def analyse(request: Request, db: Session = Depends(get_db),
            url: str = Form(""), job_text: str = Form("")):
    """URL or pasted text to a saved package. Two model calls, then the truth gate."""
    text = clean(job_text)
    if not text and url.strip():
        try:
            text = fetch(url)
        except FetchError as exc:
            return render(request, "writer.html", section="writer", url=url,
                          error=str(exc), error_title="Could not fetch that job",
                          error_hint="Most job sites are fine. The big boards are not.",
                          packages=db.query(Package).order_by(
                              Package.created_at.desc()).limit(12).all())
    if not text:
        return render(request, "writer.html", section="writer", url=url,
                      error="Give it a job URL or paste the description.",
                      error_title="Nothing to analyse",
                      packages=db.query(Package).order_by(
                          Package.created_at.desc()).limit(12).all())

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
            extraction = extract(text)
    except (NotAJobDescription, LLMError, RuntimeError) as exc:
        return render(request, "writer.html", section="writer", url=url, job_text=text,
                      error=f"Could not read that as a job description: {exc}",
                      error_title="That did not read as a job",
                      packages=db.query(Package).order_by(
                          Package.created_at.desc()).limit(12).all())

    facts = db.query(ProfileFact).order_by(ProfileFact.order_index).all()
    if not facts:
        return render(request, "writer.html", section="writer", job_text=text,
                      error="No career facts loaded. Run scripts/seed_profile.py first.",
                      error_title="Nothing to write from",
                      packages=[])

    try:
        result = tailor(extraction, facts)
    except (LLMError, RuntimeError) as exc:
        return render(request, "writer.html", section="writer", url=url, job_text=text,
                      error=f"The tailor stage failed: {exc}",
                      error_title="The writer stage failed",
                      packages=db.query(Package).order_by(
                          Package.created_at.desc()).limit(12).all())

    package = Package(
        job_text=text,
        jd_hash=jd_hash,
        company=extraction.company,
        title=extraction.title,
        extraction=extraction.as_dict(),
        placement=(result.placement.as_dict() if result.placement else {}),
        fit=_score_fit(db, extraction, result.placement, None),
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

    rendered = " ".join(
        b.text for b in shown
        if b.grade == "verified" or b.accepted
    ).lower()
    hit_keywords = {k for k in extraction.must_keywords() if k and k in rendered}

    contact_module.bootstrap(db)
    return render(request, "review.html", section="writer", _db=db,
                  placement=package.placement or {},
                  fit=package.fit or {},
                  fact_count=len(fact_rows),
                  orgs=gaps.roles(fact_rows),
                  package=package, extraction=extraction,
                  must=extraction.must, nice=extraction.nice,
                  blocks=shown, rejected=rejected, facts=facts,
                  hit_keywords=hit_keywords, report=report,
                  contact_warnings=contact_module.warnings(db),
                  error=error or None, message=message or None,
                  error_title=error_title or None, error_kind=error_kind or None)


@router.get("/job/writer/{package_id}")
def review(request: Request, package_id: int, db: Session = Depends(get_db)):
    package = db.get(Package, package_id)
    if package is None:
        return RedirectResponse("/job/writer", status_code=303)
    return _package_view(request, db, package)


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
    payload = to_payload(result, facts,
                         contact=contact_module.resume_lines(db),
                         name=contact_module.display_name(db))

    if not payload.experience and not payload.summary:
        return _package_view(request, db, package,
                             error="Nothing is accepted yet, so there is no resume to build. "
                                   "Tick the amber lines you want to use.",
                             error_title="Nothing to build")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"package-{package.id}.docx"
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


@router.get("/job/writer/{package_id}/download")
def download(package_id: int, db: Session = Depends(get_db)):
    """Re-runs the ATS gate before serving. The download is the export."""
    package = db.get(Package, package_id)
    if package is None or not package.resume_path:
        return RedirectResponse("/job/writer", status_code=303)

    path = Path(package.resume_path)
    if not path.exists():
        return RedirectResponse(f"/job/writer/{package_id}", status_code=303)

    extraction = Extraction.from_dict(package.extraction or {})
    report = ats_check(path, must_keywords=extraction.must_keywords(),
                       expect_roles=0, expect_phone=True)
    try:
        from modules.ats import gate as ats_gate
        ats_gate(report)
    except AtsBlocked:
        return RedirectResponse(f"/job/writer/{package_id}", status_code=303)

    package.status = "exported"
    db.commit()
    name = f"{(package.company or 'resume').replace(' ', '-')}-Sameer-Iyer.docx"
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
        result = tailor(extraction, facts)
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
    contact_module.bootstrap(db)
    return render(request, "details.html", section="details", _db=db,
                  details=contact_module.all_details(db),
                  name=contact_module.display_name(db),
                  lines=contact_module.resume_lines(db),
                  warnings=contact_module.warnings(db),
                  kinds=("name",) + contact_module.KINDS,
                  error=error or None, message=message or None)


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

@router.get("/job/finds")
def finds(request: Request):
    return render(request, "not_built.html", section="finds",
                  heading="Scout Finds",
                  why="This is where jobs found while your Mac was off will land, with a "
                      "fit score and a reason, and a button to hand one straight to the "
                      "writer. The adapters are not built yet.",
                  blocked_on=[
                      "An Adzuna app id and key, free tier, good India coverage",
                      "A RapidAPI key for JSearch, which aggregates the big boards "
                      "without scraping any of them",
                      "For the always-on half: SSH to the VM and a spare tunnel hostname",
                  ])


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
