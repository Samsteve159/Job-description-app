"""Configuration. Every setting is an env var, read once at import into a frozen Config.

Pattern carried over from Linkedin_Automation/config.py: _require fails fast at import,
_require_if only demands a key when the feature that needs it is actually switched on.
That is what lets NIM and Anthropic coexist while only the active provider must be present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _require_if(condition: bool, key: str) -> str:
    """Required only when the feature that uses it is enabled."""
    if not condition:
        return os.environ.get(key, "").strip()
    return _require(key)


def _flag(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- routing
# Every LLM call names a stage. The stage's route decides provider and model.
# Format: "<provider>:<model>"  e.g. "nim:meta/llama-3.3-70b-instruct"
#                                    "anthropic:claude-sonnet-5"
STAGES = (
    "score",       # Find vs positioning -> fit, brief, gaps
    "extract",     # JD -> structured requirements
    "tailor",      # ProfileFact + requirements -> resume blocks
    "cover",       # cover letter
    "brief",       # interview brief (Section 3, standalone)
    "gaps",        # asks him about work the record does not cover
)

# "normalise" is deliberately NOT a stage. Mapping one job board's JSON fields onto our own
# Find record is find-and-copy work. Deterministic code does it perfectly, instantly, free,
# and cannot invent a company name. An LLM there would be slower, cost money and add risk
# for nothing. See modules/normalise.py when the adapters land.

# Catalogue as at Aug 2026. Free NIM models can be deprecated at short notice, so
# scripts/check_models.py validates every route before a run rather than discovering it
# through a silent fallback weeks later.
# Chosen from scripts/probe_models.py against the live key, not from a published list.
# The first set of ids came from a blog and every one of them was dead. See ERROR_LOG.md.
# minimaxai/minimax-m3 was NIM_FAST and was the right pick on the probe: 2.6s extract,
# seniority correct, all must-haves found. It is now rate limited to the point of being
# unavailable, 0 of 4 calls answered with 429 while both gpt-oss models answered 4 of 4 in
# about a second. Availability beats benchmark scores: a model that will not answer is not
# fast. Left here as a comment rather than deleted, because free-tier throttling moves and
# it may be worth re-probing later.
NIM_FAST = "openai/gpt-oss-20b"                    # 1.0s, answered every call
NIM_MAIN = "openai/gpt-oss-120b"                   # 3.2s tailor, fastest on the hard task
NIM_BIG = "nvidia/nemotron-3-super-120b-a12b"      # slower but a large MoE, bake-off candidate
PAID_PROVIDERS = ("anthropic",)
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

STAGE_DEFAULTS = {
    "score": NIM_FAST,        # highest volume, fastest usable model
    "extract": NIM_MAIN,      # sets the keywords every later stage is measured against
    "tailor": NIM_MAIN,       # fastest model that held the citation shape on the hard task
    "cover": NIM_MAIN,        # prose is untested by the probe. Bake-off before trusting it
    "brief": NIM_FAST,
    # Writes a question rather than a claim, which sounded like the easy half of the job.
    # It is not: the output is structured JSON with a judgement call in it, and gpt-oss-20b
    # returned an empty response often enough to fall through to the paid fallback.
    "gaps": NIM_MAIN,
}

# Thinking models run an internal chain of thought before answering. Sampling above roughly
# zero destabilises that reasoning, so llm.py clamps their temperature regardless of what a
# caller asks for.
THINKING_MARKERS = ("deepseek-v4-pro", "k2-thinking", "-thinking", "reasoning")


def is_thinking_model(model: str) -> bool:
    m = (model or "").lower()
    return any(marker in m for marker in THINKING_MARKERS)


def _routes() -> Dict[str, str]:
    """Per-stage defaults, each overridable with LLM_ROUTE_<STAGE>."""
    override_all = os.environ.get("NIM_MODEL", "").strip()
    out = {}
    for stage in STAGES:
        default = f"nim:{override_all or STAGE_DEFAULTS[stage]}"
        out[stage] = os.environ.get(f"LLM_ROUTE_{stage.upper()}", default).strip()
    return out


@dataclass(frozen=True)
class Config:
    # paths
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    output_dir: Path = BASE_DIR / "data" / "output"
    db_path: str = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "job_app.db"))

    # llm routing
    routes: Dict[str, str] = field(default_factory=_routes)
    # Anthropic is the fallback by choice: a working fallback is worth more than a
    # free-only guarantee. It is a PAID call, so modules/llm.py logs it at WARNING with the
    # word PAID whenever it fires. Every primary route is still free NIM.
    fallback: str = os.environ.get("LLM_FALLBACK", f"anthropic:{DEFAULT_CLAUDE_MODEL}").strip()

    # providers. _require_if so an unused provider need not be configured.
    nim_api_key: str = ""
    nim_base_url: str = os.environ.get(
        "NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
    ).strip()
    anthropic_api_key: str = ""

    # job apis (slice 3+)
    adzuna_app_id: str = os.environ.get("ADZUNA_APP_ID", "").strip()
    adzuna_app_key: str = os.environ.get("ADZUNA_APP_KEY", "").strip()
    rapidapi_key: str = os.environ.get("RAPIDAPI_KEY", "").strip()

    # vm scout worker sync (slice 4)
    worker_url: str = os.environ.get("WORKER_URL", "").strip()
    worker_token: str = os.environ.get("WORKER_TOKEN", "").strip()

    # app
    host: str = os.environ.get("HOST", "127.0.0.1").strip()
    port: int = int(os.environ.get("PORT", "8100"))
    auth_enabled: bool = _flag("AUTH_ENABLED", False)
    # blocks a block whose figures are not in the facts it cites. Only ever fires
    # on an invented number, so it never stands between you and a true claim.
    strict_numbers: bool = _flag("STRICT_NUMBERS", True)
    dry_run: bool = _flag("DRY_RUN", False)

    def __post_init__(self) -> None:
        # Provider keys are read here but NOT required here. Seeding the profile, rendering
        # a docx and running the ATS check all touch no API, and should work on a machine
        # with no keys at all. modules/llm.py raises a clear error at call time instead.
        object.__setattr__(self, "nim_api_key", os.environ.get("NIM_API_KEY", "").strip())
        object.__setattr__(
            self, "anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", "").strip()
        )
        # Structural config does fail fast, because a malformed route is a bug, not a
        # missing credential.
        for stage, route in self.routes.items():
            if ":" not in route:
                raise RuntimeError(
                    f"Malformed LLM_ROUTE_{stage.upper()}={route!r}. "
                    "Expected '<provider>:<model>'."
                )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def missing_keys(self) -> list:
        """Which provider keys the current routing actually needs but does not have."""
        needed = {r.split(":", 1)[0] for r in self.routes.values()}
        needed.add(self.fallback.split(":", 1)[0])
        missing = []
        if "nim" in needed and not self.nim_api_key:
            missing.append("NIM_API_KEY")
        if "anthropic" in needed and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing

    def route_for(self, stage: str) -> str:
        if stage not in self.routes:
            raise KeyError(f"Unknown stage {stage!r}. Known stages: {', '.join(STAGES)}")
        return self.routes[stage]

    def describe(self) -> str:
        """One greppable line naming the full flag state. profile_bot lesson 10."""
        routes = " ".join(f"{s}={self.routes[s]}" for s in STAGES)
        return (
            f"[config] db={self.db_path} host={self.host}:{self.port} "
            f"auth={self.auth_enabled} dry_run={self.dry_run} strict_numbers={self.strict_numbers} "
            f"fallback={self.fallback} | {routes}"
        )


config = Config()
