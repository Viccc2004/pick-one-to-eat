"""Restaurant search and normalization.

Split at the network boundary: search_places touches the outside world,
parse_places is pure. See docs/DECISIONS.md.
"""

import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

import cache

load_dotenv()

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "places_sample.json"

SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

# The field mask is the SKU control, not a performance tweak. Google bills
# each call at the highest tier of any field requested, so one careless
# addition re-prices every call. These six are exactly the M1 schema and
# sit at the Enterprise tier: 1,000 free calls per month. Adding anything
# from the Atmosphere tier - reviews, editorialSummary, generativeSummary -
# is a deliberate decision, not a small one. See docs/PROJECT.md § 3.1.
FIELD_MASK = ",".join(
    [
        "places.displayName.text",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.currentOpeningHours.openNow",
        "places.primaryType",
    ]
)

# Enough candidates for the ranker to have a choice. Cost is per call, not
# per result, so this number is about payload size rather than money.
MAX_RESULTS = 10

REQUEST_TIMEOUT_SECONDS = 10

# Live since M3.4. Every call still passes the 24-hour cache and the
# 30/day cap in cache.py first. Set to False to run on fixtures with no
# key, no network, and no cost. Tests pin it to False in conftest.py.
USE_LIVE_API = True

# How far around the browser's position results are preferred. A bias,
# not a restriction: Google may still return something further out when
# nothing closer matches, which beats an empty page. 5 km is a short
# drive or a long walk.
LOCATION_BIAS_RADIUS_METERS = 5000

# Coordinates are rounded to 3 decimals (about 110 m) before they are used.
# Precise enough for a 5 km bias; coarse enough that requests from the same
# block share a cache entry, and that the exact position never lands in a
# URL or a cache file. The page rounds the same way before submitting.
COORD_DECIMALS = 3


class PlacesUnavailable(Exception):
    """Any reason the Places API did not produce usable results."""


def _fetch_fixtures() -> list[dict]:
    """Ten hand-written places, including deliberately incomplete ones."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)["places"]


def _api_key() -> str:
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise PlacesUnavailable(
            "GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env "
            "and fill it in."
        )
    return api_key


def _fetch_live(query: str, coords: tuple[float, float] | None = None) -> list[dict]:
    """Call Places Text Search and return its raw items.

    With coords, results are biased toward that point; without, the
    query text has to carry the place itself.

    Every failure becomes PlacesUnavailable, so callers have one thing to
    catch and the web layer can show a sentence instead of a traceback.
    """
    api_key = _api_key()

    body = {"textQuery": query, "maxResultCount": MAX_RESULTS}
    if coords is not None:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": coords[0], "longitude": coords[1]},
                "radius": LOCATION_BIAS_RADIUS_METERS,
            }
        }

    try:
        response = requests.post(
            SEARCH_TEXT_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise PlacesUnavailable(f"Could not reach Google Places: {exc}") from exc

    if response.status_code != 200:
        raise PlacesUnavailable(
            f"Google Places returned {response.status_code}: {response.text[:200]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise PlacesUnavailable("Google Places returned a non-JSON body") from exc

    # A search with no matches comes back as {} with no "places" key at
    # all, not as an empty list. Treating that as an error would turn an
    # ordinary "nothing found" into a failure message.
    return body.get("places", [])


def round_coords(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, COORD_DECIMALS), round(lng, COORD_DECIMALS))


def _plan(
    query: str, location: str | None, coords: tuple[float, float] | None
) -> tuple[str, tuple[float, float] | None, str]:
    """Decide how the place reaches Google: (text query, bias, cache key).

    A named place wins and goes into the text - "dumplings in Boston" -
    with no bias, because the user may be standing somewhere else. Only
    with no named place do the browser's coordinates become a bias. There
    is no third option: with neither, there is nothing to search near,
    and the page asks instead of calling. See docs/DECISIONS.md § M4.4.
    """
    if location:
        text = f"{query} in {location}"
        return text, None, text
    if coords is not None:
        coords = round_coords(*coords)
        return query, coords, f"{query} @ {coords[0]:.{COORD_DECIMALS}f},{coords[1]:.{COORD_DECIMALS}f}"
    raise ValueError("search_places needs a named place or coordinates")


def search_places(
    query: str,
    location: str | None = None,
    coords: tuple[float, float] | None = None,
) -> list[dict]:
    """Return raw Google response items for a search phrase and a place.

    Fixture reads are free and bypass both guards entirely - counting them
    would exhaust the daily budget without a single call leaving the
    machine, which would make the cap look broken rather than useful.

    On the live path the order is deliberate: check the key, check the
    budget, count the call, then make it. The key comes first so a missing
    .env entry fails without spending budget - nothing can leave the
    machine without it. Counting before calling is the conservative
    choice, because a request that times out may still have been served
    and billed.
    """
    if not USE_LIVE_API:
        return _fetch_fixtures()

    # The place is part of the cache key, not just the request. Without it,
    # a result fetched for Allston would be served to someone standing
    # somewhere else.
    text, bias, key = _plan(query, location, coords)

    cached = cache.get_cached(key)
    if cached is not None:
        return cached

    _api_key()
    cache.check_daily_budget()
    cache.record_call()
    raw = _fetch_live(text, bias)
    cache.store(key, raw)
    return raw


def parse_places(raw: list[dict]) -> list[dict]:
    """Normalize raw Google items into the internal schema.

    Missing fields stay None rather than being coerced, so a restaurant
    with no rating stays distinguishable from one rated 0. A place with
    no name is dropped: it can be neither recommended nor displayed.
    """
    places = []

    for item in raw:
        name = item.get("displayName", {}).get("text")
        if not name:
            continue

        places.append(
            {
                "name": name,
                "rating": item.get("rating"),
                "rating_count": item.get("userRatingCount"),
                "price_level": item.get("priceLevel"),
                "open_now": item.get("currentOpeningHours", {}).get("openNow"),
                "place_type": item.get("primaryType"),
            }
        )

    return places
