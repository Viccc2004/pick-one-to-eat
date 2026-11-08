"""Web entry point. Run with: uvicorn app:app --reload"""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import extract
import find
from cache import BudgetUnreadable, DailyBudgetExceeded
from find import PlacesUnavailable, parse_places, search_places
from llm import LLMUnavailable
from recommend import build_reason, rank_places

app = FastAPI()

# Absolute path, so the app works no matter which directory uvicorn is
# started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# M4.4 replaces this with navigator.geolocation. Until then the server
# never knows where the browser is, so a request that names no place is
# asked where - which is also exactly the "location refused" path.
HAS_BROWSER_LOCATION = False

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
):
    """Render the form, a follow-up question, or a single result.

    Stateless: the conversation so far travels in hidden `t` fields and
    the candidate index in the URL, so neither asking a question nor
    "show me another" needs session storage.
    """
    context = {
        "q": "",
        "rank": rank,
        "history": [],
        "question": None,
        "carry": [],
        "understood": None,
        "assumptions": (),
        "place": None,
        "reason": None,
        "more_url": None,
        "error": None,
        "detail": None,
        "searched": False,
        "live": find.USE_LIVE_API,
    }

    def render():
        return templates.TemplateResponse(
            request=request, name="index.html", context=context
        )

    if not q.strip():
        return render()

    turns = _pair_up(t) + [("user", q.strip())]

    try:
        understood = extract.extract(turns, has_browser_location=HAS_BROWSER_LOCATION)
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
        return render()

    context["understood"] = _describe(understood)
    context["assumptions"] = understood.assumptions
    context["searched"] = True

    try:
        candidates = rank_places(
            parse_places(
                search_places(understood.search_text, location=understood.location)
            )
        )
    except _EXPECTED_FAILURES as exc:
        context.update(_failure(exc))
        return render()

    if candidates:
        rank = max(0, min(rank, len(candidates) - 1))
        place = candidates[rank]

        context["rank"] = rank
        context["place"] = place
        context["reason"] = build_reason(place)

        if rank + 1 < len(candidates):
            # Same conversation, next candidate. Re-reading it is a cache
            # hit for both Gemini and Places, so paging costs no call.
            params = [("t", text) for text in t] + [("q", q), ("rank", rank + 1)]
            context["more_url"] = "/?" + urlencode(params)

    return render()
