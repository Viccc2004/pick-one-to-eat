"""Gemini client: one prompt in, one JSON object out.

Called over REST with requests rather than the google-genai SDK - see
docs/DECISIONS.md § M4.0. Same shape as find.py on purpose: every failure
becomes one exception the web layer can turn into a sentence, and every
live call passes the cache and the daily cap in cache.py first.

This module knows nothing about restaurants. What to ask and what shape
the answer takes belong to the callers (M4.2 onwards).
"""

import hashlib
import json
import os

import requests
from dotenv import load_dotenv

import cache

load_dotenv()

# Pinned, not "gemini-flash-lite-latest": an alias Google can repoint would
# make an M5 score change impossible to attribute to the prompt. Chosen for
# quota - 500 requests/day on the free tier, against 20 for the other Flash
# models. See docs/DECISIONS.md § M4.0.
MODEL = "gemini-3.1-flash-lite"

# Where the one retry goes. On 2026-11-08 the primary answered 503 "high
# demand" to every call for a stretch, while this one answered in 0.7s:
# overload is per model, so retrying the same model mostly retries the
# same refusal. Pinned for the same reason as MODEL.
FALLBACK_MODEL = "gemini-3.5-flash-lite"

GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Free-tier latency measured 2026-11-08: most calls 1-4s, a long tail to
# 17s, and two of fourteen hung past 30s. A hung call rarely recovers, and
# a fresh one usually answers in seconds, so the wait is cut short and
# retried once rather than sat out. Worst case 2 x 15s = 30s, the same as
# the old single timeout. See docs/DECISIONS.md § M4.3.
REQUEST_TIMEOUT_SECONDS = 15

# Two failures are retried, once each request: a timeout, and a 503
# "model is currently experiencing high demand" - Google's own words are
# "spikes in demand are usually temporary". A 429 (quota), a 500, a
# blocked prompt or malformed JSON would most likely fail the same way
# again, and each retry is a counted call. See docs/DECISIONS.md § M4.5
# follow-up.
RETRIES = 1

# Attempt n uses MODELS[n]: the primary first, the fallback for the retry.
MODELS = [MODEL, FALLBACK_MODEL]

API = "gemini"


class LLMUnavailable(Exception):
    """Any reason Gemini did not produce a usable JSON object."""


class _Transient(LLMUnavailable):
    """A failure worth one retry. Callers only ever see the parent."""


def _cache_key(prompt: str, schema: dict, system: str | None) -> str:
    """Hash of everything that can change the answer.

    The primary model is in the key so that changing MODEL cannot serve an answer
    written by the old one. A hash keeps the cache file readable and the
    key short however long the prompt gets.
    """
    material = json.dumps(
        {"model": MODEL, "system": system, "prompt": prompt, "schema": schema},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _request_body(prompt: str, schema: dict, system: str | None) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            # Gemini's own structured output: the model is constrained to
            # this JSON Schema, which removes most malformed output at the
            # source. The caller still has to check meaning, not syntax.
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return body


def _extract_text(body: dict) -> str:
    """Pull the answer text out of a generateContent response.

    A blocked prompt comes back as 200 with no candidates at all, and a
    cut-off answer as 200 with truncated text - both have to be caught
    here, because neither is an HTTP error.
    """
    candidates = body.get("candidates") or []
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise LLMUnavailable(f"Gemini returned no answer ({reason})")

    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish not in (None, "STOP"):
        raise LLMUnavailable(f"Gemini stopped early ({finish})")

    # Thinking models can return their reasoning as separate parts marked
    # "thought"; only the rest is the answer.
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LLMUnavailable("Gemini returned an empty answer")
    return text


def _fetch_live(
    api_key: str, prompt: str, schema: dict, system: str | None, model: str = MODEL
) -> dict:
    """One generateContent call. Every failure becomes LLMUnavailable."""
    try:
        response = requests.post(
            GENERATE_URL.format(model=model),
            # The key goes in a header, never the URL, so it cannot end up
            # in an error message or a log line that quotes the URL.
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=_request_body(prompt, schema, system),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise _Transient(
            f"Gemini did not answer within {REQUEST_TIMEOUT_SECONDS}s"
        ) from exc
    except requests.RequestException as exc:
        raise LLMUnavailable(f"Could not reach Gemini: {exc}") from exc

    if response.status_code == 429:
        raise LLMUnavailable(
            "Gemini's free-tier limit is used up for now (429). The daily "
            "limit resets at midnight Pacific time."
        )
    if response.status_code == 503:
        raise _Transient(
            "Gemini is overloaded right now (503). This is usually brief - "
            "try again in a minute."
        )
    if response.status_code != 200:
        raise LLMUnavailable(
            f"Gemini returned {response.status_code}: {response.text[:200]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise LLMUnavailable("Gemini returned a non-JSON body") from exc

    text = _extract_text(body)

    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise LLMUnavailable(f"Gemini's answer was not valid JSON: {text[:200]}") from exc

    if not isinstance(answer, dict):
        raise LLMUnavailable(f"Gemini's answer was not a JSON object: {text[:200]}")

    return answer


def generate_json(prompt: str, schema: dict, system: str | None = None) -> dict:
    """Ask Gemini for a JSON object matching schema.

    Order, as in find.search_places: cache, then key, then budget, then
    count, then call. The key is checked before counting so a missing
    .env entry does not burn the day's budget. A timeout or a 503 is
    retried once, on FALLBACK_MODEL, and the retry passes the budget check
    and is counted like any other call. A fallback answer is cached under
    the same key as a primary one: it answers the same question, and the
    next identical request should not go back to an overloaded model. Only a parsed, valid answer is cached - a malformed one
    must not be served back for 24 hours.

    Raises LLMUnavailable, cache.DailyBudgetExceeded, or
    cache.BudgetUnreadable.
    """
    key = _cache_key(prompt, schema, system)

    cached = cache.get_cached(key, api=API)
    if cached is not None:
        return cached

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMUnavailable(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )

    for attempt in range(1 + RETRIES):
        cache.check_daily_budget(api=API)
        cache.record_call(api=API)
        try:
            answer = _fetch_live(api_key, prompt, schema, system, MODELS[attempt])
        except _Transient:
            if attempt == RETRIES:
                raise
            continue
        cache.store(key, answer, api=API)
        return answer
