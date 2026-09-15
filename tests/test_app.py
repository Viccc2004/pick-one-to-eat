"""End-to-end through the web layer, no network."""

from fastapi.testclient import TestClient

import find
from app import app

client = TestClient(app)


def _must_not_be_called(query):
    raise AssertionError("a live call was made when it should not have been")


def test_empty_form_renders_without_a_result():
    response = client.get("/")

    assert response.status_code == 200
    assert "pick-one-to-eat" in response.text
    assert "show me another" not in response.text


def test_query_returns_exactly_one_restaurant():
    response = client.get("/", params={"q": "cheap chinese food in Allston"})

    assert response.status_code == 200
    # Corner Dumpling Counter wins: 4.7 beats Sichuan Garden House's 4.6.
    assert "Corner Dumpling Counter" in response.text
    assert "Sichuan Garden House" not in response.text


def test_rank_parameter_shows_the_next_candidate():
    response = client.get("/", params={"q": "chinese", "rank": 1})

    assert "Corner Dumpling Counter" not in response.text
    assert "Sichuan Garden House" in response.text


def test_out_of_range_rank_does_not_crash():
    response = client.get("/", params={"q": "chinese", "rank": 99})

    assert response.status_code == 200


# --- failures reach the page as a sentence, never as a traceback ----------


def test_exhausted_budget_renders_a_message_not_a_500(monkeypatch):
    import cache

    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    response = client.get("/", params={"q": "chinese"})

    assert response.status_code == 200
    assert "budget" in response.text.lower()


def test_unreachable_google_renders_a_message_not_a_500(monkeypatch):
    def boom(query):
        raise find.PlacesUnavailable("Could not reach Google Places: timed out")

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", boom)

    response = client.get("/", params={"q": "chinese"})

    # The M3 exit condition: the API being down produces a clear message
    # rather than a traceback.
    assert response.status_code == 200
    assert "Could not get restaurants from Google" in response.text
    assert "Traceback" not in response.text


def test_a_failure_still_renders_the_form_so_the_user_can_retry(monkeypatch):
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(
        find, "_fetch_live", lambda q: (_ for _ in ()).throw(find.PlacesUnavailable("x"))
    )

    response = client.get("/", params={"q": "chinese"})

    assert '<form action="/"' in response.text
