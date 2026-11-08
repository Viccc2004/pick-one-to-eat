"""Ranking and reason text."""

from recommend import build_reason, rank_places


def _place(name, rating=None, count=None, price=None, open_then=None, ptype=None):
    return {
        "name": name,
        "rating": rating,
        "rating_count": count,
        "price_level": price,
        "open_then": open_then,
        "place_type": ptype,
    }


def test_high_rating_from_few_reviews_does_not_win():
    places = [
        _place("Brighton Ave Noodle Bar", 4.9, 3),
        _place("Sichuan Garden House", 4.6, 1503),
    ]

    assert rank_places(places)[0]["name"] == "Sichuan Garden House"


def test_unrated_place_ranks_behind_rated_but_is_not_dropped():
    places = [_place("New Dragon Kitchen"), _place("Golden Wok Allston", 4.4, 812)]

    ranked = rank_places(places)

    assert ranked[0]["name"] == "Golden Wok Allston"
    assert ranked[1]["name"] == "New Dragon Kitchen"


def test_returns_at_most_three():
    places = [_place(f"Place {i}", 4.0 + i / 10, 100) for i in range(8)]

    assert len(rank_places(places)) == 3


def test_reason_cites_only_fields_that_exist():
    reason = build_reason(_place("Corner Dumpling Counter", 4.7, 289, "PRICE_LEVEL_INEXPENSIVE"))

    assert "4.7 stars from 289 reviews" in reason
    assert "cheap" in reason
    assert "open" not in reason


def test_unknown_hours_tell_the_user_to_call():
    reason = build_reason(_place("Corner Dumpling Counter", 4.7, 289))

    assert "call ahead" in reason


def test_thin_rating_is_flagged_rather_than_hidden():
    reason = build_reason(_place("Brighton Ave Noodle Bar", 4.9, 3))

    assert "only from 3 reviews" in reason


def test_place_with_no_usable_data_says_so():
    reason = build_reason(_place("Mystery Spot"))

    assert "No rating, price, or opening hours" in reason


def test_a_place_closed_at_the_requested_time_is_dropped_however_good():
    places = [
        _place("Sichuan Garden House", 4.6, 1503, open_then=False),
        _place("Golden Wok Allston", 4.4, 812, open_then=True),
    ]

    assert [p["name"] for p in rank_places(places)] == ["Golden Wok Allston"]


def test_unknown_hours_are_not_treated_as_closed():
    places = [_place("Corner Dumpling Counter", 4.7, 289, open_then=None)]

    assert len(rank_places(places)) == 1


def test_reason_names_the_time_that_was_checked():
    place = _place("Golden Wok Allston", 4.4, 812, open_then=True)

    assert "open tomorrow at 19:00" in build_reason(place, "tomorrow at 19:00")
    assert "open right now" in build_reason(place)
