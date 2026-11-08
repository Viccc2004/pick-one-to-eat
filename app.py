"""Web entry point. Run with: uvicorn app:app --reload"""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import extract
import find
import hours
import pick
from cache import BudgetUnreadable, DailyBudgetExceeded
from find import PlacesUnavailable, parse_places, search_places
from llm import LLMUnavailable

app = FastAPI()

# Absolute path, so the app works no matter which directory uvicorn is
# started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Said whenever the search ran on the browser's position rather than a
# place the user named, so the default is never silent. See
# docs/DECISIONS.md § Location: named place > browser location > ask.
NEAR_YOU_NOTE = (
    "You didn't name a place, so this searched near your current location. "
    "Name one to search somewhere else."
)

# Each expected failure gets a sentence; none gets a traceback.
_FAILURE_MESSAGES = {
    DailyBudgetExceeded: "Today's API budget is used up.",
    PlacesUnavailable: "Could not get restaurants from Google right now.",
    LLMUnavailable: "Could not read your request right now.",
    BudgetUnreadable: "The spend counter is unreadable, so no call was made.",
}
_EXPECTED_FAILURES = tuple(_FAILURE_MESSAGES)


def _failure(exc: Exception) -> dict:
    for kind, message in _FAILURE_MESSAGES.items():
        if isinstance(exc, kind):
            return {"error": message, "detail": str(exc)}
    raise exc  # pragma: no cover - only reached for an unexpected type


def _pair_up(history: list[str]) -> list[tuple[str, str]]:
    """Turn the hidden-field history back into speaker-tagged turns.

    The form sends prior turns as repeated `t` fields, oldest first and
    alternating user, app, user, app. It always ends on an app question,
    so a well-formed history has even length; a trailing odd entry can
    only come from a hand-edited URL and is dropped.
    """
    if len(history) % 2:
        history = history[:-1]
    return [("user" if i % 2 == 0 else "app", text) for i, text in enumerate(history)]


def _coords(lat: str, lng: str) -> tuple[float, float] | None:
    """The browser's position, or None if absent, refused, or nonsense.

    Arrives as text from hidden form fields, so anything that is not a
    real coordinate pair is treated as "no location" rather than an error:
    the user is then simply asked where.
    """
    try:
        lat_f, lng_f = float(lat), float(lng)
    except ValueError:
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None
    return find.round_coords(lat_f, lng_f)


def _describe(req: extract.Request) -> str:
    """What was actually searched, in one line, so no assumption is silent."""
    where = req.location or "near you"
    if req.when == "now":
        when = "now"
    elif req.time:
        when = f"{req.day} at {req.time}"
    else:
        when = req.day or "later"
    return f"{req.search_text} · {where} · {when}"


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    t: list[str] = Query(default=[]),
    rank: int = 0,
    lat: str = "",
    lng: str = "",
    by: str = "",
):
    """Render the form, a follow-up question, or a single result.

    Stateless: the conversation so far travels in hidden `t` fields, the
    browser's position in `lat`/`lng`, and the candidate index in the URL,
    so neither asking a question nor "show me another" needs session
    storage.
    """
    coords = _coords(lat, lng)

    context = {
        "q": "",
        "rank": rank,
        "history": [],
        "question": None,
        "carry": [],
        "carry_coords": None,
        "understood": None,
        "assumptions": (),
        "near_you": None,
        "place": None,
        "reason": None,
        "more_url": None,
        "error": None,
        "detail": None,
        "searched": False,
        "all_closed": False,
        "fallback_note": None,
        "live": find.USE_LIVE_API,
        # The usage rules on the page are built from the same constants the
        # code runs on, so the page cannot describe a rule that is not real.
        "max_questions": extract.MAX_QUESTIONS,
        "meal_times": extract.MEAL_TIMES,
    }

    def render():
        return templates.TemplateResponse(
            request=request, name="index.html", context=context
        )

    if not q.strip():
        return render()

    turns = _pair_up(t) + [("user", q.strip())]

    try:
        understood = extract.extract(turns, has_browser_location=coords is not None)
    except _EXPECTED_FAILURES as exc:
        context.update(_failure(exc))
        context["q"] = q
        return render()

    if understood.missing:
        # Ask, and carry the whole conversation forward for the next submit.
        # No Places call: the search cannot run on a request with gaps.
        context["history"] = turns
        context["question"] = understood.question
        context["carry"] = [text for _, text in turns] + [understood.question]
        # The answer to a follow-up keeps the position the first submit
        # found, so the browser is not asked again mid-conversation.
        context["carry_coords"] = coords
        return render()

    context["understood"] = _describe(understood)
    context["assumptions"] = understood.assumptions
    context["near_you"] = NEAR_YOU_NOTE if understood.location is None else None
    context["searched"] = True

    try:
        places = parse_places(
            search_places(
                understood.search_text,
                location=understood.location,
                coords=coords,
            )
        )
    except _EXPECTED_FAILURES as exc:
        context.update(_failure(exc))
        return render()

    # Open-ness is worked out now, for the time the user asked about, not
    # read from the cache. Closed places never reach the ranking.
    hours.annotate(places, understood.when, understood.day, understood.time)
    when_label = hours.describe_when(understood.when, understood.day, understood.time)
    candidates, context["fallback_note"] = pick.choose(
        turns, when_label, places, use_llm=by != "rules"
    )
    context["all_closed"] = bool(places) and not candidates
    context["when_label"] = when_label

    if candidates:
        rank = max(0, min(rank, len(candidates) - 1))
        place = candidates[rank]

        context["rank"] = rank
        context["place"] = place
        context["reason"] = place["reason"]

        if rank + 1 < len(candidates):
            # Same conversation, next candidate. Re-reading it is a cache
            # hit for both Gemini calls and for Places, so paging costs no
            # call: the ranking call already wrote all three reasons.
            params = [("t", text) for text in t] + [("q", q), ("rank", rank + 1)]
            if coords is not None:
                params += [("lat", coords[0]), ("lng", coords[1])]
            if context["fallback_note"]:
                # Keep paging the list this page came from.
                params.append(("by", "rules"))
            context["more_url"] = "/?" + urlencode(params)

    return render()
