"""Guards around the network call: a response cache and a daily call cap.

Two jobs, one module, because both answer the same question — may this
query actually go to Google, and what did it say last time?

The cap lives here rather than in the Google console because the console
cannot express it: Places API (New) quotas are not adjustable, and a Maps
quota is a rate limit rather than a spending limit anyway. See
docs/PROJECT.md § 3.5. That makes this file the only thing standing
between a runaway loop and a real bill, which is why it fails closed.
"""

import json
import os
import time
from datetime import date
from pathlib import Path

# Google's terms permit caching Places content only briefly. 24 hours is
# well inside that window and is long enough that a day of debugging the
# same query costs one call. See docs/DECISIONS.md.
CACHE_TTL_SECONDS = 24 * 60 * 60

# 30 x 31 = 930 < the 1,000 free Enterprise calls per month, so staying in
# the free tier is arithmetic rather than a hope. This is a budget of
# distinct queries, not of page loads - repeats inside the TTL are free.
MAX_CALLS_PER_DAY = 30

CACHE_DIR = Path(__file__).parent / ".cache"


class DailyBudgetExceeded(Exception):
    """Raised instead of making a call that would exceed MAX_CALLS_PER_DAY."""


class BudgetUnreadable(Exception):
    """Raised when the budget file exists but cannot be understood.

    Deliberately fatal. A corrupt counter means the day's spend is unknown,
    and guessing zero would silently throw away the only protection there
    is. Deleting the file is a conscious reset, which is the point.
    """


def _responses_path() -> Path:
    return CACHE_DIR / "responses.json"


def _budget_path() -> Path:
    return CACHE_DIR / "budget.json"


def _today() -> str:
    """Local date. A separate function so tests can move the clock."""
    return date.today().isoformat()


def _write_json(path: Path, data: dict) -> None:
    """Write via a temp file and rename, so a crash cannot leave a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _normalize(query: str) -> str:
    """Cache key. Case and spacing should not buy a second API call."""
    return " ".join(query.lower().split())


def get_cached(query: str) -> list[dict] | None:
    """Return the stored response for query, or None if absent or stale.

    A missing or unreadable cache file is a miss, not an error: the worst
    case is one extra call, and the daily cap still bounds that.
    """
    try:
        with open(_responses_path(), encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    entry = entries.get(_normalize(query))
    if entry is None:
        return None

    if time.time() - entry["fetched_at"] > CACHE_TTL_SECONDS:
        return None

    return entry["raw"]


def store(query: str, raw: list[dict]) -> None:
    """Record a response against the query that produced it."""
    try:
        with open(_responses_path(), encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        entries = {}

    entries[_normalize(query)] = {"fetched_at": time.time(), "raw": raw}
    _write_json(_responses_path(), entries)


def _read_budget() -> dict:
    """Today's counter, reset automatically when the date rolls over."""
    try:
        with open(_budget_path(), encoding="utf-8") as f:
            budget = json.load(f)
    except FileNotFoundError:
        return {"date": _today(), "count": 0}
    except (OSError, json.JSONDecodeError) as exc:
        raise BudgetUnreadable(
            f"{_budget_path()} could not be read, so today's API spend is "
            f"unknown. Delete the file to reset the counter to zero."
        ) from exc

    if budget.get("date") != _today():
        return {"date": _today(), "count": 0}

    return budget


def calls_today() -> int:
    """How many live calls have been made today. Safe to call anywhere."""
    return _read_budget()["count"]


def check_daily_budget() -> None:
    """Raise DailyBudgetExceeded if another call would break the cap."""
    used = calls_today()
    if used >= MAX_CALLS_PER_DAY:
        raise DailyBudgetExceeded(
            f"{used} of {MAX_CALLS_PER_DAY} API calls already used today. "
            f"The cap resets at midnight local time."
        )


def record_call() -> None:
    """Count one live call.

    Called before the request, not after. A request that times out or
    errors may still have been served and billed, so the safe assumption
    for a spend guard is that every attempt counts.
    """
    budget = _read_budget()
    budget["count"] += 1
    _write_json(_budget_path(), budget)
