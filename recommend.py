"""Ranking and reason generation.

M2 ranks with a fixed rule and builds the reason from a template. M4
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

    Places with a trusted rating come first, ordered by rating. Everything
    else keeps its original order behind them - an unrated restaurant is
    not bad, it is unknown, so it is not sorted as though it scored zero.
    """
    trusted = [p for p in places if _has_trusted_rating(p)]
    untrusted = [p for p in places if not _has_trusted_rating(p)]

    trusted.sort(key=lambda p: p["rating"], reverse=True)

    return (trusted + untrusted)[:3]


def build_reason(place: dict) -> str:
    """Build a one-sentence reason citing only supported conditions.

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

    if place["open_now"] is True:
        clauses.append("open right now")
    elif place["open_now"] is False:
        clauses.append("closed right now")

    if not clauses:
        reason = "No rating, price, or opening hours are available for this one."
    else:
        reason = f"{place['name']} is {', '.join(clauses)}."

    if place["open_now"] is None:
        reason += " Opening hours are unknown - call ahead."

    return reason
