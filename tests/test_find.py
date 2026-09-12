"""parse_places: the schema and the missing-field rules from M1."""

from find import parse_places, search_places


def test_complete_record_maps_every_field():
    raw = [
        {
            "displayName": {"text": "Golden Wok Allston"},
            "rating": 4.4,
            "userRatingCount": 812,
            "priceLevel": "PRICE_LEVEL_INEXPENSIVE",
            "currentOpeningHours": {"openNow": True},
            "primaryType": "chinese_restaurant",
        }
    ]

    assert parse_places(raw) == [
        {
            "name": "Golden Wok Allston",
            "rating": 4.4,
            "rating_count": 812,
            "price_level": "PRICE_LEVEL_INEXPENSIVE",
            "open_now": True,
            "place_type": "chinese_restaurant",
        }
    ]


def test_place_without_a_name_is_dropped():
    raw = [{"rating": 4.5, "userRatingCount": 430}]

    assert parse_places(raw) == []


def test_missing_fields_stay_none_and_are_not_coerced():
    raw = [{"displayName": {"text": "New Dragon Kitchen"}}]

    place = parse_places(raw)[0]

    assert place["rating"] is None
    assert place["rating_count"] is None
    assert place["price_level"] is None
    assert place["open_now"] is None
    assert place["place_type"] is None
    # An unrated restaurant must stay distinguishable from a 0-rated one.
    assert place["rating"] != 0


def test_open_now_false_is_kept_as_false_not_none():
    raw = [
        {
            "displayName": {"text": "Union Square Spicy Pot"},
            "currentOpeningHours": {"openNow": False},
        }
    ]

    assert parse_places(raw)[0]["open_now"] is False


def test_fixtures_parse_and_drop_the_nameless_one():
    places = parse_places(search_places("anything"))

    assert len(places) == 9
    assert all(p["name"] for p in places)
