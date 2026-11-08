"""_fetch_live: the request sent to Google, and every way it can fail."""

import pytest
import requests

import find
from find import PlacesUnavailable

# The SKU tiers from docs/PROJECT.md § 3.1. Requesting any Atmosphere field
# re-prices the whole call from $35 to $40 per 1,000.
ATMOSPHERE_FIELDS = ["reviews", "editorialSummary", "generativeSummary"]

# Every field the app reads, and nothing else. Pro or Enterprise only, so
# the call bills at Enterprise - see the FIELD_MASK comment in find.py.
SCHEMA_FIELDS = [
    "displayName",
    "rating",
    "userRatingCount",
    "priceLevel",
    "regularOpeningHours",
    "utcOffsetMinutes",
    "primaryType",
    "formattedAddress",
    "googleMapsUri",
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")


def _capture_post(monkeypatch, response):
    """Replace requests.post and hand back the call it received."""
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, body=json, timeout=timeout)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(find.requests, "post", fake_post)
    return sent


# --- the field mask is the SKU control ------------------------------------


def test_field_mask_requests_exactly_the_schema():
    requested = find.FIELD_MASK.split(",")

    assert len(requested) == len(SCHEMA_FIELDS)
    for field in SCHEMA_FIELDS:
        assert any(f"places.{field}" in r for r in requested), field


def test_open_now_is_not_requested():
    # It is computed at fetch time and would sit in the 24-hour cache,
    # saying "open" long after closing. hours.py works it out per request.
    assert "currentOpeningHours" not in find.FIELD_MASK


def test_field_mask_contains_no_atmosphere_tier_field():
    # This test is the money guard. Adding one of these fields silently
    # moves every call to the Enterprise + Atmosphere SKU, and the free
    # allowance is spent 14% faster for data nothing currently reads.
    for field in ATMOSPHERE_FIELDS:
        assert field not in find.FIELD_MASK


# --- the request itself ---------------------------------------------------


def test_request_carries_the_key_mask_query_and_a_timeout(api_key, monkeypatch):
    sent = _capture_post(monkeypatch, FakeResponse(payload={"places": []}))

    find._fetch_live("dumplings in Allston, Boston")

    assert sent["url"] == find.SEARCH_TEXT_URL
    assert sent["headers"]["X-Goog-Api-Key"] == "test-key"
    assert sent["headers"]["X-Goog-FieldMask"] == find.FIELD_MASK
    assert sent["body"]["textQuery"] == "dumplings in Allston, Boston"
    # No timeout means a hung connection hangs the whole page.
    assert sent["timeout"] == find.REQUEST_TIMEOUT_SECONDS


def test_results_come_back_as_the_raw_places_list(api_key, monkeypatch):
    places = [{"displayName": {"text": "Golden Wok Allston"}, "rating": 4.4}]
    _capture_post(monkeypatch, FakeResponse(payload={"places": places}))

    assert find._fetch_live("chinese") == places


def test_no_matches_is_an_empty_list_not_a_failure(api_key, monkeypatch):
    # Google omits the "places" key entirely rather than sending [], so a
    # naive lookup would turn "nothing found" into a crash.
    _capture_post(monkeypatch, FakeResponse(payload={}))

    assert find._fetch_live("sushi on the moon") == []


# --- every failure becomes PlacesUnavailable ------------------------------


def test_missing_api_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    sent = _capture_post(monkeypatch, FakeResponse(payload={"places": []}))

    with pytest.raises(PlacesUnavailable, match="GOOGLE_PLACES_API_KEY"):
        find._fetch_live("chinese")

    assert sent == {}


def test_timeout_becomes_places_unavailable(api_key, monkeypatch):
    _capture_post(monkeypatch, requests.Timeout("timed out"))

    with pytest.raises(PlacesUnavailable):
        find._fetch_live("chinese")


def test_connection_error_becomes_places_unavailable(api_key, monkeypatch):
    _capture_post(monkeypatch, requests.ConnectionError("no route"))

    with pytest.raises(PlacesUnavailable):
        find._fetch_live("chinese")


def test_http_error_status_becomes_places_unavailable(api_key, monkeypatch):
    _capture_post(monkeypatch, FakeResponse(status_code=403, text="PERMISSION_DENIED"))

    with pytest.raises(PlacesUnavailable, match="403"):
        find._fetch_live("chinese")


def test_non_json_body_becomes_places_unavailable(api_key, monkeypatch):
    _capture_post(monkeypatch, FakeResponse(status_code=200, payload=None))

    with pytest.raises(PlacesUnavailable):
        find._fetch_live("chinese")


# --- query composition ----------------------------------------------------


def test_a_named_place_goes_into_the_text_with_no_bias():
    text, bias, key = find._plan("dumplings", "Boston", coords=(34.05, -118.24))

    # In LA, heading to Boston: the browser's LA position must not pull
    # the results back toward LA.
    assert text == "dumplings in Boston"
    assert bias is None
    assert key == f"dumplings in Boston #{find._MASK_TAG}"


def test_without_a_named_place_the_browser_position_becomes_a_bias():
    text, bias, key = find._plan("dumplings", None, coords=(42.353462, -71.132817))

    assert text == "dumplings"
    assert bias == (42.353, -71.133)
    assert key == f"dumplings @ 42.353,-71.133 #{find._MASK_TAG}"


def test_with_neither_there_is_nothing_to_search():
    # The page asks where before it gets here; reaching this is a bug,
    # and a loud one beats a silent search of somewhere arbitrary.
    with pytest.raises(ValueError):
        find._plan("dumplings", None, None)


def test_there_is_no_fallback_location_any_more():
    assert not hasattr(find, "FALLBACK_LOCATION")


def test_the_bias_is_sent_as_a_circle(api_key, monkeypatch):
    sent = _capture_post(monkeypatch, FakeResponse(payload={"places": []}))

    find._fetch_live("dumplings", (42.353, -71.133))

    circle = sent["body"]["locationBias"]["circle"]
    assert circle["center"] == {"latitude": 42.353, "longitude": -71.133}
    assert circle["radius"] == find.LOCATION_BIAS_RADIUS_METERS


def test_no_coordinates_means_no_bias_in_the_request(api_key, monkeypatch):
    sent = _capture_post(monkeypatch, FakeResponse(payload={"places": []}))

    find._fetch_live("dumplings in Boston")

    assert "locationBias" not in sent["body"]


def test_the_place_is_part_of_the_cache_key(monkeypatch):
    import cache

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: [{"q": q}])

    find.search_places("dumplings", "Cambridge, MA")
    find.search_places("dumplings", coords=(42.35, -71.13))

    # A result fetched for one place must not be served to someone
    # somewhere else, so no two places may share a cache entry.
    def key(location=None, coords=None):
        return find._plan("dumplings", location, coords)[2]

    assert cache.get_cached(key("Cambridge, MA")) is not None
    assert cache.get_cached(key(coords=(42.35, -71.13))) is not None
    assert cache.get_cached(key("Allston, Boston")) is None
    assert cache.get_cached(key(coords=(40.7, -74.0))) is None


def test_changing_the_field_mask_retires_old_cache_entries(monkeypatch):
    import cache

    # An entry stored under the old mask, before M4.5 added hours and
    # addresses: serving it would show "hours unknown" for 24 hours.
    cache.store("dumplings in Boston", [{"displayName": {"text": "Old Shape"}}])
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: [{"displayName": {"text": "New Shape"}}])

    raw = find.search_places("dumplings", "Boston")

    assert raw[0]["displayName"]["text"] == "New Shape"


def test_nearby_positions_share_a_cache_entry(monkeypatch):
    calls = []
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: calls.append(coords) or [])

    find.search_places("dumplings", coords=(42.35341, -71.13281))
    find.search_places("dumplings", coords=(42.35339, -71.13279))

    # A few metres of GPS jitter must not cost a second call.
    assert len(calls) == 1
