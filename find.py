"""Restaurant search and normalization.

Split at the network boundary: search_places touches the outside world,
parse_places is pure. See docs/DECISIONS.md.
"""

import json
from pathlib import Path

import cache

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "places_sample.json"

# Flipped to True in M3.3, once _fetch_live exists. Until then the whole
# app runs on fixtures with no API key, no network, and no cost.
USE_LIVE_API = False

# Where the query goes when the browser will not say where the user is.
# Appended to the query text rather than geocoded into coordinates: that
# is what M0 validated, and it avoids enabling a second billable API on
# the path that is already the degraded one. See docs/DECISIONS.md.
FALLBACK_LOCATION = "Allston, Boston"


def _fetch_fixtures() -> list[dict]:
    """Ten hand-written places, including deliberately incomplete ones."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)["places"]


def _fetch_live(query: str) -> list[dict]:
    """Real Places Text Search call. M3.3."""
    raise NotImplementedError("M3.3: real Places call not written yet")


def search_places(query: str) -> list[dict]:
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

    cached = cache.get_cached(query)
    if cached is not None:
        return cached

    cache.check_daily_budget()
    cache.record_call()
    raw = _fetch_live(query)
    cache.store(query, raw)
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
