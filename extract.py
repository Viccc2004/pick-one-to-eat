"""Turn the conversation into a structured request, and decide what is missing.

The LLM reads; the code decides. Gemini reports what the user said, and
always phrases one question per askable item in the user's language.
Whether anything is actually missing, and so which of those questions are
shown, is decided here - the LLM is never asked to judge it. The first
live run showed why: asked to decide, it asked for things it had and
skipped things it lacked. See docs/DECISIONS.md § M4.2.

The prompt carries no clock. The LLM answers in relative terms ("tomorrow",
"19:00") and code turns those into dates later (M4.5), so the same words
always make the same prompt and "show me another" is a cache hit.
"""

import re
from dataclasses import dataclass

import llm

DAYS = [
    "today",
    "tomorrow",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

PRICES = ["cheap", "moderate", "expensive"]

WHENS = ["now", "later", "unknown"]

# What the search falls back to when the user named no food. Food is not
# something the app asks about: "somewhere to eat near me" is a complete
# request. Only where and when are worth a follow-up question.
DEFAULT_SEARCH_TEXT = "restaurant"

_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

SCHEMA = {
    "type": "object",
    "properties": {
        "search_text": {"type": "string"},
        "location": {"type": ["string", "null"]},
        "when": {"type": "string", "enum": WHENS},
        "day": {"type": ["string", "null"], "enum": [*DAYS, None]},
        "time": {"type": ["string", "null"]},
        "price": {"type": ["string", "null"], "enum": [*PRICES, None]},
        "ask_location": {"type": "string"},
        "ask_when": {"type": "string"},
        "ask_time": {"type": "string"},
    },
    "required": [
        "search_text",
        "location",
        "when",
        "day",
        "time",
        "price",
        "ask_location",
        "ask_when",
        "ask_time",
    ],
}

SYSTEM = """\
You read a short conversation in which someone is deciding where to eat, \
and extract what they asked for. Report only what they actually said. \
Never fill a gap with a guess; the only exception is the meal-time rule.

Fields:
- search_text: a short restaurant search phrase: the food, cuisine or kind \
of place, plus any qualities they asked for ("cheap dumplings", "quiet ramen \
bar"). Never include a location or a time. If they named no food or kind of \
place at all, use "restaurant". Always in English: translate if they wrote \
in another language ("火锅" becomes "hot pot").
- location: the place where they will eat, if they named one ("Boston", \
"Back Bay", "near Fenway Park"). If they say where they are now but will eat \
somewhere else, use where they will eat. null if they did not name it. \
Phrases like "near me" or "around here" are not a location: null.
- when: "now" if they want to eat right away; "later" if they named a later \
time or day; "unknown" if they did not say.
- day: only when "later": "today", "tomorrow", or a weekday name in lower \
case. If they gave a time but no day, "today". Otherwise null.
- time: only when "later": 24-hour "HH:MM". If they named a meal instead of \
a clock time, use breakfast 08:00, brunch 11:00, lunch 12:00, dinner 19:00, \
late night 22:00. null if they gave neither.
- price: "cheap", "moderate" or "expensive" if they said anything about \
price, else null.

Always write all three of these, whatever the conversation already says. \
Each is one short, friendly question in the language the user wrote in:
- ask_location: asks where they want to eat.
- ask_when: asks whether they want to eat now or later.
- ask_time: asks what time they plan to eat.
"""

# Used for any item whose LLM-written question came back empty.
_FALLBACK_QUESTIONS = {
    "location": "where do you want to eat",
    "when": "are you eating now or later",
    "time": "what time are you planning to eat",
}


@dataclass(frozen=True)
class Request:
    """What the user wants, as far as the conversation says.

    `missing` and `question` are decided by code. An empty `missing` means
    the request is ready to search.
    """

    search_text: str
    location: str | None
    when: str
    day: str | None
    time: str | None
    price: str | None
    missing: tuple[str, ...]
    question: str | None


def build_prompt(turns: list[tuple[str, str]]) -> str:
    """The conversation as the LLM sees it.

    turns is a list of (speaker, text) with speaker "user" or "app",
    oldest first. Deterministic on purpose: the prompt is the cache key.
    Whether the browser gave a location is deliberately not in it - that
    only affects what is missing, which code decides - so the same words
    hit the same cache entry with or without location permission.
    """
    lines = ["Conversation:"]
    for speaker, text in turns:
        label = "User" if speaker == "user" else "App"
        lines.append(f"{label}: {text.strip()}")
    return "\n".join(lines)


def _str_or_none(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _one_of(value, allowed: list[str]) -> str | None:
    value = _str_or_none(value)
    if value is None:
        return None
    value = value.lower()
    return value if value in allowed else None


def _clean(answer: dict) -> dict:
    """Coerce each field on its own; a bad field becomes unknown.

    Gemini's structured output makes bad values rare, not impossible. A
    field that fails its check is treated as not said, which then shows up
    in `missing` and turns into a question - the request degrades to
    asking, never to a crash or a guess.
    """
    when = _one_of(answer.get("when"), WHENS) or "unknown"

    time = _str_or_none(answer.get("time"))
    if time is not None and not _TIME_RE.match(time):
        time = None

    day = _one_of(answer.get("day"), DAYS)

    if when != "later":
        # A day or time attached to "now" or "unknown" is contradictory;
        # drop it rather than pick a side.
        day, time = None, None
    elif day is None and time is not None:
        day = "today"

    return {
        "search_text": _str_or_none(answer.get("search_text")) or DEFAULT_SEARCH_TEXT,
        "location": _str_or_none(answer.get("location")),
        "when": when,
        "day": day,
        "time": time,
        "price": _one_of(answer.get("price"), PRICES),
        "asks": {
            item: _str_or_none(answer.get(f"ask_{item}"))
            for item in ("location", "when", "time")
        },
    }


def find_missing(fields: dict, has_browser_location: bool) -> tuple[str, ...]:
    """What still has to be asked before searching. Order is display order."""
    missing = []
    if fields["location"] is None and not has_browser_location:
        missing.append("location")
    if fields["when"] == "unknown":
        missing.append("when")
    elif fields["when"] == "later" and fields["time"] is None:
        missing.append("time")
    return tuple(missing)


def _question(missing: tuple[str, ...], asks: dict) -> str | None:
    """One question per missing item, joined. None when nothing is missing."""
    if not missing:
        return None
    parts = []
    for item in missing:
        ask = asks.get(item)
        if ask is None:
            fallback = _FALLBACK_QUESTIONS[item]
            ask = fallback[0].upper() + fallback[1:] + "?"
        parts.append(ask)
    return " ".join(parts)


def extract(turns: list[tuple[str, str]], has_browser_location: bool) -> Request:
    """Read the conversation and return what the user wants.

    Raises llm.LLMUnavailable, cache.DailyBudgetExceeded or
    cache.BudgetUnreadable - the web layer turns each into a sentence.
    """
    answer = llm.generate_json(build_prompt(turns), SCHEMA, system=SYSTEM)
    fields = _clean(answer)
    missing = find_missing(fields, has_browser_location)

    return Request(
        search_text=fields["search_text"],
        location=fields["location"],
        when=fields["when"],
        day=fields["day"],
        time=fields["time"],
        price=fields["price"],
        missing=missing,
        question=_question(missing, fields["asks"]),
    )
