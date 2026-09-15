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

# Flipped to True in M3.3, once _fetch_live exists. Until then the whole
# app runs on fixtures with no API key, no network, and no cost.
USE_LIVE_API = False

# Where the query goes when the browser will not say where the user is.
# Appended to the query text rather than geocoded into coordinates: that
# is what M0 validated, and it avoids enabling a second billable API on
# the path that is already the degraded one. See docs/DECISIONS.md.
FALLBACK_LOCATION = "Allston, Boston"


class PlacesUnavailable(Exception):
    """Any reason the Places API did not produce usable results."""


def _fetch_fixtures() -> list[dict]:
    """Ten hand-written places, including deliberately incomplete ones."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)["places"]


def _fetch_live(query: str) -> list[dict]:
    """Call Places Text Search and return its raw items.

    Every failure becomes PlacesUnavailable, so callers have one thing to
    catch and the web layer can show a sentence instead of a traceback.
    """
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise PlacesUnavailable(
            "GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env "
            "and fill it in."
        )

    try:
        response = requests.post(
            SEARCH_TEXT_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            json={"textQuery": query, "maxResultCount": MAX_RESULTS},
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


def _build_query(query: str, location: str | None = None) -> str:
    """Attach a location to the user's sentence.

    The location is always appended, even when the sentence already names
    one - "dumplings in Allston" becomes "dumplings in Allston, Boston",
    which Text Search resolves fine. Detecting a location the user already
    gave is constraint extraction, which is M4's job and needs an LLM.
    """
    return f"{query} in {location or FALLBACK_LOCATION}"


def search_places(query: str, location: str | None = None) -> list[dict]:
    """Return raw Google response items for a text query.

    Fixture reads are free and bypass both guards entirely - counting them
    would exhaust the daily budget without a single call leaving the
    machine, which would make the cap look broken rather than useful.

    On the live path the order is deliberate: check the budget, count the
    call, then make it. Counting first is the conservative choice, because
    a request that times out may still have been served and billed.
    """
    if not USE_LIVE_API:
        return _fetch_fixtures()

    # The location is part of the cache key, not just the request. Without
    # it, a result fetched for Allston would be served to someone standing
    # somewhere else.
    full_query = _build_query(query, location)

    cached = cache.get_cached(full_query)
    if cached is not None:
        return cached

    cache.check_daily_budget()
    cache.record_call()
    raw = _fetch_live(full_query)
    cache.store(full_query, raw)
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
