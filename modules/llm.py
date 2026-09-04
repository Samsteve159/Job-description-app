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
import time
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from config import PAID_PROVIDERS, config, is_thinking_model

log = logging.getLogger(__name__)

# Deliberately shorter than it looks like it should be. A stage that fails over spends
# this twice, once on NIM and once on the paid fallback, and analyse runs two stages. At
# 120s that is eight minutes of worst case behind a screen that gives up at five, which
# is exactly how a working app comes to look hung. 60 keeps the worst case to four
# minutes, inside the window, and no successful call has ever come close to it.
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class LLMError(RuntimeError):
    pass


def _split_route(route: str) -> Tuple[str, str]:
    if ":" not in route:
        raise LLMError(f"Malformed route {route!r}. Expected '<provider>:<model>'.")
    provider, model = route.split(":", 1)
    return provider.strip().lower(), model.strip()


# ------------------------------------------------------------------------- providers

# A 429 from NIM's free tier means "not yet", not "no". Falling straight through to the
# paid fallback on a rate limit turns a free stack into a billed one for a condition that
# clears in seconds, and the gap closer proved it: six questions, six 429s, six PAID CALL
# warnings. Waiting is the correct response to being asked to wait.
_RATE_LIMIT_BACKOFF = (2.0, 5.0, 12.0)


def _call_nim(model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
    if not config.nim_api_key:
        raise LLMError("NIM_API_KEY is not set")

    for attempt, pause in enumerate(_RATE_LIMIT_BACKOFF + (None,)):
        try:
            return _nim_once(model, system, user, max_tokens, temperature)
        except (_RateLimited, httpx.TimeoutException) as exc:
            # A read timeout is the same condition arriving a different way: the model is
            # busy, not gone. It used to fall straight through to the paid route without
            # a single retry.
            if pause is None:
                raise LLMError(
                    f"NIM unavailable after {len(_RATE_LIMIT_BACKOFF)} retries: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            log.info("NIM said try later on %s (%s), waiting %.0fs (retry %d of %d)",
                     model, type(exc).__name__, pause, attempt + 1,
                     len(_RATE_LIMIT_BACKOFF))
            time.sleep(pause)
    raise LLMError("unreachable")


class _RateLimited(Exception):
    """NIM said try later. Internal: never escapes _call_nim."""


# 503 and 429 are the same sentence in different words: not now, try again. 502 and 504
# are a gateway between here and the model, not the model itself. All four are transient
# and none of them is a reason to start paying, which is what falling through to the
# fallback means. Seen the day the routes were re-pointed: the replacement model answered
# a run cleanly, then returned "Service temporarily overloaded" on the next one and sent
# both stages to Anthropic.
_TRY_AGAIN = {429, 502, 503, 504}


def _nim_once(model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
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
    if resp.status_code in _TRY_AGAIN:
        raise _RateLimited(f"HTTP {resp.status_code}: {resp.text[:110]}")
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

# Extended thinking is adaptive, and its tokens come out of the same max_tokens budget as
# the answer. On a small prompt Claude does not use it and nothing looks wrong. On this
# app's tailor prompt, 19k of system instructions plus a house spec, it spent all 12,000
# tokens reasoning and returned a reply with no text block in it at all. That is a total
# failure, not a degraded one, and it cost a paid call to produce nothing.
#
# Turned off rather than budgeted for, because the budget cannot be raised: past a
# certain size the SDK demands streaming. With it off the same call answers in 2,480
# tokens. Nothing here needs it. Every stage asks for JSON against an explicit contract,
# which is instruction following, and the truth guards do not care how hard the model
# thought.
_NO_THINKING_PARAM = set()


def _rejects_thinking(exc: Exception) -> bool:
    text = str(exc).lower()
    return "thinking" in text and ("unexpected" in text or "unsupported" in text
                                   or "not supported" in text or "unrecognized" in text)


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
    if model not in _NO_THINKING_PARAM:
        kwargs["thinking"] = {"type": "disabled"}

    # Two parameters, each of which some model somewhere refuses, and refusing is a 400
    # that takes the whole route down. Learned once for temperature and applied to both:
    # drop the parameter, remember the model, and let the rest of the process skip it.
    def _create():
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - narrowed by the two _rejects_ helpers
            if "thinking" in kwargs and _rejects_thinking(exc):
                log.info("%s rejects thinking, retrying without it and remembering", model)
                _NO_THINKING_PARAM.add(model)
                kwargs.pop("thinking")
                return _create()
            if "temperature" in kwargs and _rejects_temperature(exc):
                log.info("%s rejects temperature, retrying without it and remembering", model)
                _NO_TEMPERATURE.add(model)
                kwargs.pop("temperature")
                return _create()
            raise

    msg = _create()

    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    # "empty response" on its own is a dead end. There are three different reasons the
    # text can come back empty and they need three different fixes: the budget was too
    # small, the model declined, or it answered in a block type this does not read.
    # Saying which one turns a mystery into a one-line change.
    if not text.strip():
        kinds = sorted({getattr(b, "type", "?") for b in msg.content}) or ["no blocks"]
        raise LLMError(
            f"no text in the reply. stop_reason={msg.stop_reason} "
            f"blocks={','.join(kinds)} "
            f"in={getattr(msg.usage, 'input_tokens', '?')} "
            f"out={getattr(msg.usage, 'output_tokens', '?')} of max_tokens={max_tokens}"
        )
    return text


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
            return _unwrap(stage, json.loads(candidate))
        except (json.JSONDecodeError, TypeError):
            continue

    # "did not return parseable JSON" is true of a truncated reply and points at the
    # wrong thing: it reads as a model that cannot follow a schema, when the model
    # followed it exactly and ran out of room. Saying which costs one line and saves the
    # next person an hour.
    body = raw.strip()
    truncated = (
        body.startswith(("{", "["))
        and not body.endswith(("}", "]"))
        and len(body) > 200
    )
    if truncated:
        raise LLMError(
            f"stage={stage} returned JSON that stops mid-value after {len(raw)} chars, "
            f"which means max_tokens={max_tokens} was too small for this model rather "
            f"than the model failing the schema. Last 120 chars: {raw[-120:]!r}"
        )
    raise LLMError(
        f"stage={stage} did not return parseable JSON. First 300 chars: {raw[:300]!r}"
    )


def _unwrap(stage: str, data: Any) -> Any:
    """Coerce the one wrong shape that is unambiguous: a lone object in a list.

    Every stage in this app asks for an object and every one of them raises on anything
    else. Models differ on whether the answer to "return an object" is `{...}` or
    `[{...}]`, and the difference is a wrapper rather than a disagreement about content,
    so unwrapping loses nothing. Found when the routes moved off a retired model and the
    replacement returned a list on a prompt the old one had answered with an object.

    Narrow on purpose. A list of two is a real difference in shape and is passed through
    to fail loudly at the call site, because guessing there would be guessing at content.
    """
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        log.info("stage=%s returned its object wrapped in a list, unwrapping", stage)
        return data[0]
    return data


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
