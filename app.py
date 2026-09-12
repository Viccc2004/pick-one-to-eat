"""Web entry point. Run with: uvicorn app:app --reload"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from find import parse_places, search_places
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
    context = {"q": q, "rank": rank, "place": None, "reason": None, "has_more": False}

    if q.strip():
        candidates = rank_places(parse_places(search_places(q)))

        if candidates:
            rank = max(0, min(rank, len(candidates) - 1))
            place = candidates[rank]

            context["rank"] = rank
            context["place"] = place
            context["reason"] = build_reason(place)
            context["has_more"] = rank + 1 < len(candidates)

    return templates.TemplateResponse(request=request, name="index.html", context=context)
