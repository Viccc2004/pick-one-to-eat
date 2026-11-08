"""Ranking and reason generation.

M2 ranks with a fixed rule and builds the reason from a template. M4.6
replaces both with an LLM call; everything else in the app stays put.
"""

# A high rating from a handful of reviews is not comparable to the same
# rating from a thousand. Below this count the rating is not trusted for
# ranking - see docs/DECISIONS.md.
MIN_TRUSTED_RATING_COUNT = 50

PRICE_LABELS = {
    "PRICE_LEVEL_INEXPENSIVE": "cheap",
    "PRICE_LEVEL_MODERATE": "mid-priced",
    "PRICE_LEVEL_EXPENSIVE": "expensive",
    "PRICE_LEVEL_VERY_EXPENSIVE": "very expensive",
}


def _has_trusted_rating(place: dict) -> bool:
    return (
        place["rating"] is not None
        and place["rating_count"] is not None
        and place["rating_count"] >= MIN_TRUSTED_RATING_COUNT
    )


def rank_places(places: list[dict]) -> list[dict]:
    """Return the top 3 candidates, best first.

    A place known to be closed at the requested time is dropped first - it
    cannot be the answer however good it is. Unknown hours stay in:
    unknown is not closed. Then places with a trusted rating come first,
    ordered by rating. Everything else keeps its original order behind
    them - an unrated restaurant is not bad, it is unknown, so it is not
    sorted as though it scored zero.
    """
    places = [p for p in places if p.get("open_then") is not False]
    trusted = [p for p in places if _has_trusted_rating(p)]
    untrusted = [p for p in places if not _has_trusted_rating(p)]

    trusted.sort(key=lambda p: p["rating"], reverse=True)

    return (trusted + untrusted)[:3]


def build_reason(place: dict, when_label: str = "right now") -> str:
    """Build a one-sentence reason citing only supported conditions.

    when_label names the time open_then was checked for - "right now",
    "tomorrow at 19:00" - so the sentence says what was actually checked.

    Every clause below is guarded by the field it depends on. A field that
    is None produces no claim - the app says nothing rather than guessing.
    """
    clauses = []

    if _has_trusted_rating(place):
        clauses.append(f"{place['rating']} stars from {place['rating_count']} reviews")
    elif place["rating"] is not None and place["rating_count"] is not None:
        clauses.append(
            f"{place['rating']} stars, but only from {place['rating_count']} reviews"
        )

    if place["price_level"] in PRICE_LABELS:
        clauses.append(PRICE_LABELS[place["price_level"]])

    open_then = place.get("open_then")
    if open_then is True:
        clauses.append(f"open {when_label}")
    elif open_then is False:
        clauses.append(f"closed {when_label}")

    if not clauses:
        reason = "No rating, price, or opening hours are available for this one."
    else:
        reason = f"{place['name']} is {', '.join(clauses)}."

    if open_then is None:
        reason += " Opening hours are unknown - call ahead."

    return reason
