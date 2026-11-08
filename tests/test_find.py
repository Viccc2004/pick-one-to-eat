"""find.py: the M1 schema rules, and the guards around a live call."""

import pytest

import cache
import find
from find import parse_places, search_places

RAW_LIVE = [{"displayName": {"text": "Sichuan Garden House"}, "rating": 4.6}]


def _must_not_be_called(query):
    raise AssertionError("_fetch_live was called when it should not have been")


WEEKDAYS_11_TO_22 = [
    {"open": {"day": d, "hour": 11, "minute": 0}, "close": {"day": d, "hour": 22, "minute": 0}}
    for d in range(7)
]


def test_complete_record_maps_every_field():
    raw = [
        {
            "displayName": {"text": "Golden Wok Allston"},
            "rating": 4.4,
            "userRatingCount": 812,
            "priceLevel": "PRICE_LEVEL_INEXPENSIVE",
            "regularOpeningHours": {"periods": WEEKDAYS_11_TO_22},
            "utcOffsetMinutes": -300,
            "primaryType": "chinese_restaurant",
            "formattedAddress": "100 Harvard Ave, Allston, MA 02134, USA",
            "googleMapsUri": "https://maps.google.com/?cid=123",
        }
    ]

    assert parse_places(raw) == [
        {
            "name": "Golden Wok Allston",
            "rating": 4.4,
            "rating_count": 812,
            "price_level": "PRICE_LEVEL_INEXPENSIVE",
            "place_type": "chinese_restaurant",
            "address": "100 Harvard Ave, Allston, MA 02134, USA",
            "maps_url": "https://maps.google.com/?cid=123",
            "periods": WEEKDAYS_11_TO_22,
            "utc_offset_minutes": -300,
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
    assert place["periods"] is None
    assert place["utc_offset_minutes"] is None
    assert place["place_type"] is None
    assert place["address"] is None
    assert place["maps_url"] is None
    # An unrated restaurant must stay distinguishable from a 0-rated one.
    assert place["rating"] != 0


def test_only_google_maps_links_are_kept():
    def link(uri):
        return parse_places([{"displayName": {"text": "x"}, "googleMapsUri": uri}])[0]["maps_url"]

    assert link("https://maps.google.com/?cid=123") == "https://maps.google.com/?cid=123"
    assert link("https://www.google.com/maps/place/?q=place_id:abc") is not None
    # Autoescaping does not stop these in an href; the allowlist does.
    assert link("javascript:alert(1)") is None
    assert link("http://maps.google.com/?cid=123") is None
    assert link("https://maps.google.com.evil.example/?cid=1") is None
    assert link(42) is None


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
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: RAW_LIVE)

    assert search_places("chinese food", "Allston") == RAW_LIVE
    assert cache.calls_today() == 1
    # Stored under the composed query, place included - see
    # test_the_place_is_part_of_the_cache_key in test_places_api.py.
    assert cache.get_cached(find._plan("chinese food", "Allston", None)[2]) == RAW_LIVE


def test_a_cache_hit_makes_no_call_and_costs_no_budget(monkeypatch):
    cache.store(find._plan("chinese food", "Allston", None)[2], RAW_LIVE)

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    assert search_places("chinese food", "Allston") == RAW_LIVE
    assert cache.calls_today() == 0


def test_an_exhausted_budget_stops_the_call_before_it_happens(monkeypatch):
    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    with pytest.raises(cache.DailyBudgetExceeded):
        search_places("chinese food", "Allston")


def test_a_failed_call_still_counts(monkeypatch):
    def boom(query, coords=None):
        raise TimeoutError("Google did not answer")

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", boom)

    with pytest.raises(TimeoutError):
        search_places("chinese food", "Allston")

    # A request that times out may still have been served and billed, so
    # the spend guard has to assume every attempt cost money.
    assert cache.calls_today() == 1


def test_missing_key_fails_before_spending_budget(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY")
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    with pytest.raises(find.PlacesUnavailable, match="GOOGLE_PLACES_API_KEY"):
        search_places("chinese food", "Allston")

    # Nothing could have left the machine, so nothing may be counted -
    # otherwise a half-filled .env quietly eats the day's 30 calls.
    assert cache.calls_today() == 0
