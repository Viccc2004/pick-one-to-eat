"""find.py: the M1 schema rules, and the guards around a live call."""

import pytest

import cache
import find
from find import parse_places, search_places

RAW_LIVE = [{"displayName": {"text": "Sichuan Garden House"}, "rating": 4.6}]


def _must_not_be_called(query):
    raise AssertionError("_fetch_live was called when it should not have been")


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


# --- search_places: the guards around a live call -------------------------


def test_fixture_reads_never_touch_the_budget():
    for _ in range(cache.MAX_CALLS_PER_DAY * 2):
        search_places("chinese food")

    # Offline runs cost nothing, so counting them would exhaust the day's
    # budget without a single request leaving the machine.
    assert cache.calls_today() == 0


def test_live_path_counts_the_call_and_stores_the_response(monkeypatch):
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q: RAW_LIVE)

    assert search_places("chinese food") == RAW_LIVE
    assert cache.calls_today() == 1
    # Stored under the composed query, location included - see
    # test_location_is_part_of_the_cache_key in test_places_api.py.
    assert cache.get_cached(find._build_query("chinese food")) == RAW_LIVE


def test_a_cache_hit_makes_no_call_and_costs_no_budget(monkeypatch):
    cache.store(find._build_query("chinese food"), RAW_LIVE)

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    assert search_places("chinese food") == RAW_LIVE
    assert cache.calls_today() == 0


def test_an_exhausted_budget_stops_the_call_before_it_happens(monkeypatch):
    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    with pytest.raises(cache.DailyBudgetExceeded):
        search_places("chinese food")


def test_a_failed_call_still_counts(monkeypatch):
    def boom(query):
        raise TimeoutError("Google did not answer")

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", boom)

    with pytest.raises(TimeoutError):
        search_places("chinese food")

    # A request that times out may still have been served and billed, so
    # the spend guard has to assume every attempt cost money.
    assert cache.calls_today() == 1


def test_missing_key_fails_before_spending_budget(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY")
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    with pytest.raises(find.PlacesUnavailable, match="GOOGLE_PLACES_API_KEY"):
        search_places("chinese food")

    # Nothing could have left the machine, so nothing may be counted -
    # otherwise a half-filled .env quietly eats the day's 30 calls.
    assert cache.calls_today() == 0
