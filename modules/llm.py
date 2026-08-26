"""Provider router.

Every LLM call names a stage. The stage's route decides provider and model, so switching
the tailor stage from NIM to Claude is a one-line env change and nothing else moves.

NIM is OpenAI-compatible, so it is a plain POST over httpx. No extra SDK dependency.

Fallback is loud. Learned on an earlier system: a silent fallback is indistinguishable from a
system that is quietly producing worse output for weeks.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from config import PAID_PROVIDERS, config, is_thinking_model

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class LLMError(RuntimeError):
    pass


def _split_route(route: str) -> Tuple[str, str]:
    if ":" not in route:
        raise LLMError(f"Malformed route {route!r}. Expected '<provider>:<model>'.")
    provider, model = route.split(":", 1)
    return provider.strip().lower(), model.strip()


# ------------------------------------------------------------------------- providers

def _call_nim(model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
    if not config.nim_api_key:
        raise LLMError("NIM_API_KEY is not set")
    resp = httpx.post(
        f"{config.nim_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.nim_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise LLMError(f"NIM HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"NIM returned an unexpected shape: {exc}") from exc


# Models that reject `temperature` outright. Claude Sonnet 5 returns
# 400 "`temperature` is deprecated for this model" at any value but the default, which
# took the fallback route down on its first real health check. Learned at runtime rather
# than hardcoded, for the same reason model ids are probed rather than trusted: the list
# is a moving target and a stale constant fails closed on a route meant to be the safety
# net. First call for a model pays one retry, the rest of the process skips the parameter.
_NO_TEMPERATURE = set()


def _rejects_temperature(exc: Exception) -> bool:
    text = str(exc).lower()
    return "temperature" in text and (
        "deprecated" in text or "unsupported" in text or "not supported" in text
    )


def _call_anthropic(model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
    if not config.anthropic_api_key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    import anthropic  # imported lazily so a NIM-only run needs no SDK

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if model not in _NO_TEMPERATURE:
        kwargs["temperature"] = temperature

    try:
        msg = client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 - narrowed immediately by _rejects_temperature
        if "temperature" not in kwargs or not _rejects_temperature(exc):
            raise
        log.info("%s rejects temperature, retrying without it and remembering", model)
        _NO_TEMPERATURE.add(model)
        kwargs.pop("temperature")
        msg = client.messages.create(**kwargs)

    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


_PROVIDERS = {"nim": _call_nim, "anthropic": _call_anthropic}


# ----------------------------------------------------------------------------- public

def complete(
    stage: str,
    system: str,
    user: str,
    max_tokens: int = 4000,
    temperature: float = 0.4,
) -> str:
    """Run one completion for a named stage, falling back loudly on failure."""
    if config.dry_run:
        log.warning("DRY RUN: stage=%s returned canned output, no API call made", stage)
        return f"[dry-run output for stage {stage}]"

    route = config.route_for(stage)
    attempts = [route]
    if config.fallback and config.fallback != route:
        attempts.append(config.fallback)

    last: Optional[Exception] = None
    for index, attempt in enumerate(attempts):
        provider, model = _split_route(attempt)
        fn = _PROVIDERS.get(provider)
        if fn is None:
            last = LLMError(f"Unknown provider {provider!r} in route {attempt!r}")
            continue
        temp = temperature
        if is_thinking_model(model) and temp > 0.05:
            log.info(
                "stage=%s model=%s is a thinking model, clamping temperature %.2f -> 0.0",
                stage, model, temp,
            )
            temp = 0.0
        try:
            out = fn(model, system, user, max_tokens, temp)
            if not out.strip():
                raise LLMError("empty response")
            if index > 0:
                paid = " *** PAID CALL ***" if provider in PAID_PROVIDERS else " (free)"
                log.warning(
                    "FALLBACK USED%s: stage=%s primary=%s failed (%s), served by %s",
                    paid, stage, route, last, attempt,
                )
            return out
        except Exception as exc:  # noqa: BLE001 - we genuinely want any failure to fall back
            last = exc
            log.warning("stage=%s route=%s failed: %s", stage, attempt, exc)

    raise LLMError(f"stage={stage} failed on every route {attempts}: {last}")


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def complete_json(
    stage: str,
    system: str,
    user: str,
    max_tokens: int = 4000,
    temperature: float = 0.2,
) -> Any:
    """Completion that must return JSON. Tolerates fenced output and leading prose.

    Open models fence and preamble far more than Claude does, and this router defaults to
    NIM, so the parser has to be forgiving or half the stages break on formatting alone.
    """
    raw = complete(stage, system, user, max_tokens=max_tokens, temperature=temperature)

    candidates = []
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw.strip())
    # last resort: the outermost {...} or [...] in the response
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            candidates.append(raw[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    raise LLMError(
        f"stage={stage} did not return parseable JSON. First 300 chars: {raw[:300]!r}"
    )


def health() -> Dict[str, str]:
    """Cheap reachability check per configured provider. Used by scripts/check_env.py."""
    out: Dict[str, str] = {}
    providers = {_split_route(r)[0] for r in config.routes.values()}
    providers.add(_split_route(config.fallback)[0])
    for provider in sorted(providers):
        try:
            complete_stage = next(iter(config.routes))
            model = _split_route(
                config.fallback if provider == _split_route(config.fallback)[0]
                else config.routes[complete_stage]
            )[1]
            _PROVIDERS[provider](model, "Reply with the single word OK.", "ping", 16, 0.0)
            out[provider] = "ok"
        except Exception as exc:  # noqa: BLE001
            out[provider] = f"FAILED: {exc}"
    return out
