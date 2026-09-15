"""Web entry point. Run with: uvicorn app:app --reload"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from cache import BudgetUnreadable, DailyBudgetExceeded
import find
from find import PlacesUnavailable, parse_places, search_places
from recommend import build_reason, rank_places

app = FastAPI()

# Absolute path, so the app works no matter which directory uvicorn is
# started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = "", rank: int = 0):
    """Render the search form, and a single result when q is given.

    Stateless: the query and which candidate to show both travel in the
    URL, so "show me another" needs no session storage.
    """
    context = {
        "q": q,
        "rank": rank,
        "place": None,
        "reason": None,
        "has_more": False,
        "error": None,
        "detail": None,
        "live": find.USE_LIVE_API,
    }

    if q.strip():
        # Three failures are expected rather than exceptional: the day's
        # budget is gone, Google is unreachable, or the counter file is
        # unreadable. Each gets a sentence; none gets a traceback.
        try:
            candidates = rank_places(parse_places(search_places(q)))
        except DailyBudgetExceeded as exc:
            context["error"] = "Today's API budget is used up."
            context["detail"] = str(exc)
            candidates = []
        except PlacesUnavailable as exc:
            context["error"] = "Could not get restaurants from Google right now."
            context["detail"] = str(exc)
            candidates = []
        except BudgetUnreadable as exc:
            context["error"] = "The spend counter is unreadable, so no call was made."
            context["detail"] = str(exc)
            candidates = []

        if candidates:
            rank = max(0, min(rank, len(candidates) - 1))
            place = candidates[rank]

            context["rank"] = rank
            context["place"] = place
            context["reason"] = build_reason(place)
            context["has_more"] = rank + 1 < len(candidates)

    return templates.TemplateResponse(request=request, name="index.html", context=context)
