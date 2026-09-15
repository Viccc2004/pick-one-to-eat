"""_fetch_live: the request sent to Google, and every way it can fail."""

import pytest
import requests

import find
from find import PlacesUnavailable

# The SKU tiers from docs/PROJECT.md § 3.1. Requesting any Atmosphere field
# re-prices the whole call from $35 to $40 per 1,000.
ATMOSPHERE_FIELDS = ["reviews", "editorialSummary", "generativeSummary"]

M1_SCHEMA_FIELDS = [
    "displayName",
    "rating",
    "userRatingCount",
    "priceLevel",
    "currentOpeningHours",
    "primaryType",
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


def test_field_mask_requests_exactly_the_m1_schema():
    requested = find.FIELD_MASK.split(",")

    assert len(requested) == len(M1_SCHEMA_FIELDS)
    for field in M1_SCHEMA_FIELDS:
        assert any(f"places.{field}" in r for r in requested), field


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


def test_fallback_location_is_appended_when_none_is_given():
    assert find._build_query("dumplings") == f"dumplings in {find.FALLBACK_LOCATION}"


def test_a_given_location_replaces_the_fallback():
    assert find._build_query("dumplings", "Cambridge, MA") == "dumplings in Cambridge, MA"


def test_location_is_part_of_the_cache_key(monkeypatch):
    import cache

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q: [{"q": q}])

    find.search_places("dumplings", "Cambridge, MA")

    # A result fetched for Cambridge must not be served to someone in
    # Allston, so the two must not share a cache entry.
    assert cache.get_cached("dumplings in Cambridge, MA") is not None
    assert cache.get_cached("dumplings in Allston, Boston") is None
