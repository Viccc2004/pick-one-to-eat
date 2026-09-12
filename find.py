"""Restaurant search and normalization.

Split at the network boundary: search_places touches the outside world,
parse_places is pure. See docs/DECISIONS.md.
"""

import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "places_sample.json"


def search_places(query: str) -> list[dict]:
    """Return raw Google response items for a text query.

    M2: reads hand-written fixtures instead of calling the API, so the
    whole app runs with no API key, no network, and no cost. M3 replaces
    the body with a real Places call; the return shape does not change.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)["places"]


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
