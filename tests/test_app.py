"""End-to-end through the web layer, no network.

Gemini is stubbed at llm.generate_json, so the real extract.py logic -
what is missing, the question cap - runs inside every request here.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import cache
import extract
import find
import llm
from app import app

client = TestClient(app)


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("a live call was made when it should not have been")


def understood(**fields):
    """A complete Gemini answer: ramen, now, in Allston. Override per test."""
    base = {
        "search_text": "ramen",
        "location": "Allston",
        "when": "now",
        "day": None,
        "time": None,
        "price": None,
        "ask_location": "Where do you want to eat?",
        "ask_when": "Now or later?",
        "ask_time": "What time?",
    }
    base.update(fields)
    return base


@pytest.fixture(autouse=True)
def gemini(monkeypatch):
    """Every request in this file reads the user's words through this stub."""

    def fake(prompt, schema, system=None):
        fake.prompts.append(prompt)
        if isinstance(fake.reply, Exception):
            raise fake.reply
        return fake.reply

    fake.reply = understood()
    fake.prompts = []
    monkeypatch.setattr(extract.llm, "generate_json", fake)
    return fake


# --- the plain path: a complete request gets one restaurant ---------------------


def test_empty_form_renders_without_a_result(gemini):
    response = client.get("/")

    assert response.status_code == 200
    assert "pick-one-to-eat" in response.text
    assert "show me another" not in response.text
    assert gemini.prompts == []


def test_complete_request_returns_exactly_one_restaurant():
    response = client.get("/", params={"q": "cheap chinese food in Allston, now"})

    assert response.status_code == 200
    # Corner Dumpling Counter wins: 4.7 beats Sichuan Garden House's 4.6.
    assert "Corner Dumpling Counter" in response.text
    assert "Sichuan Garden House" not in response.text


def test_the_page_says_what_it_searched_for(gemini):
    gemini.reply = understood(search_text="cheap dumplings", location="Boston",
                              when="later", day="tomorrow", time="19:00")

    response = client.get("/", params={"q": "in LA, heading to Boston tomorrow, dumplings for dinner"})

    assert "cheap dumplings · Boston · tomorrow at 19:00" in response.text


def test_the_extracted_place_is_what_goes_to_google(gemini, monkeypatch):
    gemini.reply = understood(search_text="cheap dumplings", location="Boston")
    sent = []
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: sent.append(q) or [])

    client.get("/", params={"q": "in LA now, heading to Boston, cheap dumplings"})

    # Boston, not the user's whole sentence and not the Allston fallback.
    assert sent == ["cheap dumplings in Boston"]


def test_rank_parameter_shows_the_next_candidate():
    response = client.get("/", params={"q": "chinese", "rank": 1})

    assert "Corner Dumpling Counter" not in response.text
    assert "Sichuan Garden House" in response.text


def test_out_of_range_rank_does_not_crash():
    response = client.get("/", params={"q": "chinese", "rank": 99})

    assert response.status_code == 200


def test_show_me_another_link_carries_the_whole_conversation():
    response = client.get("/", params={"t": ["ramen", "Where?"], "q": "Allston"})

    link = response.text.split('class="more" href="')[1].split('"')[0]
    params = parse_qs(urlparse(link.replace("&amp;", "&")).query)
    assert params["t"] == ["ramen", "Where?"]
    assert params["q"] == ["Allston"]
    assert params["rank"] == ["1"]


# --- opening hours, address, Maps link (M4.5) ------------------------------------
# The clock is pinned in conftest.py to Wednesday 12:30 in Boston.


def test_the_card_shows_the_address_and_a_maps_link():
    response = client.get("/", params={"q": "chinese now"})

    assert "1 Packard&#39;s Corner, Allston" in response.text
    assert 'href="https://maps.google.com/?cid=' in response.text
    assert 'rel="noopener noreferrer"' in response.text


def only_fixture(monkeypatch, *names):
    """Serve just the named fixture places, to set up one situation exactly."""
    every = find._fetch_fixtures()
    keep = [p for p in every if p.get("displayName", {}).get("text") in names]
    monkeypatch.setattr(find, "_fetch_fixtures", lambda: keep)


def test_a_place_without_a_maps_link_gets_no_link(monkeypatch):
    only_fixture(monkeypatch, "New Dragon Kitchen")

    response = client.get("/", params={"q": "chinese now"})

    assert "New Dragon Kitchen" in response.text
    assert "Open in Google Maps" not in response.text


def test_a_dinner_only_place_is_not_offered_for_lunch():
    for rank in range(3):
        response = client.get("/", params={"q": "chinese now", "rank": rank})
        assert "Harvard Ave Hot Pot" not in response.text
        assert "Union Square Spicy Pot" not in response.text


def test_the_reason_names_the_time_that_was_checked(gemini, monkeypatch):
    only_fixture(monkeypatch, "Sichuan Garden House")
    gemini.reply = understood(when="later", day="today", time="19:00")

    response = client.get("/", params={"q": "chinese at 7"})

    assert "open today at 19:00" in response.text


def test_a_late_night_place_comes_back_late_at_night(gemini):
    # 23:30: the 11-22 and 11-23 places are shut; the one open 18:00-03:00
    # is not, and neither is the one with unknown hours.
    gemini.reply = understood(when="later", day="today", time="23:30")

    names = [client.get("/", params={"q": "chinese late", "rank": r}).text for r in range(3)]

    assert any("Allston Late Night Kitchen" in text for text in names)
    assert not any("Sichuan Garden House" in text for text in names)


def test_everything_closed_says_so(gemini, monkeypatch):
    only_fixture(monkeypatch, "Harvard Ave Hot Pot", "Union Square Spicy Pot")

    response = client.get("/", params={"q": "chinese now"})

    assert "Everything found is closed right now" in response.text


# --- the clarifying loop --------------------------------------------------------


def test_missing_information_asks_instead_of_searching(gemini, monkeypatch):
    gemini.reply = understood(location=None, when="unknown")
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    response = client.get("/", params={"q": "ramen"})

    assert response.status_code == 200
    assert "Where do you want to eat? Now or later?" in response.text
    assert "Corner Dumpling Counter" not in response.text


def test_the_question_carries_the_conversation_in_hidden_fields(gemini):
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": "ramen"})

    assert '<input type="hidden" name="t" value="ramen" />' in response.text
    assert '<input type="hidden" name="t" value="Where do you want to eat?" />' in response.text


def test_the_answer_is_read_together_with_what_came_before(gemini):
    client.get("/", params={"t": ["ramen", "Where do you want to eat?"], "q": "Allston"})

    prompt = gemini.prompts[-1]
    assert "User: ramen" in prompt
    assert "App: Where do you want to eat?" in prompt
    assert prompt.rstrip().endswith("User: Allston")


def test_after_two_questions_an_unknown_time_is_assumed_and_shown(gemini):
    gemini.reply = understood(when="unknown")
    history = ["ramen", "Now or later?", "hmm", "Now or later?"]

    response = client.get("/", params={"t": history, "q": "dunno"})

    assert "Corner Dumpling Counter" in response.text
    assert "assumes you&#39;re eating now" in response.text


def test_without_a_place_the_app_keeps_asking_past_the_cap(gemini, monkeypatch):
    gemini.reply = understood(location=None)
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)
    history = ["ramen", "Where?", "somewhere", "Where?", "anywhere", "Where?"]

    response = client.get("/", params={"t": history, "q": "you pick"})

    assert "Where do you want to eat?" in response.text


def test_a_hand_edited_odd_history_does_not_crash(gemini):
    response = client.get("/", params={"t": ["ramen", "Where?", "stray"], "q": "Allston"})

    assert response.status_code == 200
    assert "stray" not in gemini.prompts[-1]


def test_user_text_is_escaped_in_the_hidden_fields(gemini):
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": '"><script>alert(1)</script>'})

    assert "<script>alert(1)</script>" not in response.text


# --- location: named place > browser position > ask -----------------------------

BROWSER = {"lat": "42.3534", "lng": "-71.1328"}


def test_with_a_browser_position_no_place_is_needed(gemini, monkeypatch):
    gemini.reply = understood(location=None)
    sent = []
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: sent.append((q, coords)) or [])

    response = client.get("/", params={"q": "ramen now", **BROWSER})

    assert "Where do you want to eat?" not in response.text
    assert sent == [("ramen", (42.353, -71.133))]


def test_searching_near_you_is_said_out_loud(gemini):
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": "ramen now", **BROWSER})

    assert "ramen · near you · now" in response.text
    assert "searched near your current location" in response.text


def test_a_named_place_beats_the_browser_position(gemini, monkeypatch):
    gemini.reply = understood(location="Boston")
    sent = []
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", lambda q, coords=None: sent.append((q, coords)) or [])

    response = client.get("/", params={"q": "in LA, heading to Boston, ramen", **BROWSER})

    assert sent == [("ramen in Boston", None)]
    assert "searched near your current location" not in response.text


def test_nonsense_coordinates_count_as_no_location(gemini):
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": "ramen now", "lat": "999", "lng": "abc"})

    assert "Where do you want to eat?" in response.text


def test_a_follow_up_keeps_the_position_found_on_the_first_submit(gemini):
    gemini.reply = understood(location=None, when="unknown")

    response = client.get("/", params={"q": "ramen", **BROWSER})

    assert '<input type="hidden" name="lat" value="42.353" />' in response.text
    assert '<input type="hidden" name="lng" value="-71.133" />' in response.text


def test_a_new_search_asks_the_browser_again(gemini):
    # Only a follow-up carries the position. A fresh search leaves the
    # fields empty so the page script looks again - the user may have moved.
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": "ramen now", **BROWSER})

    assert '<input type="hidden" name="lat" value="" />' in response.text


def test_show_me_another_keeps_the_position(gemini):
    gemini.reply = understood(location=None)

    response = client.get("/", params={"q": "chinese now", **BROWSER})

    link = response.text.split('class="more" href="')[1].split('"')[0]
    params = parse_qs(urlparse(link.replace("&amp;", "&")).query)
    assert (params["lat"], params["lng"]) == (["42.353"], ["-71.133"])


def test_the_page_carries_the_geolocation_script():
    response = client.get("/")

    assert "navigator.geolocation" in response.text


# --- failures reach the page as a sentence, never as a traceback ----------------


def test_exhausted_places_budget_renders_a_message_not_a_500(monkeypatch):
    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    response = client.get("/", params={"q": "chinese"})

    assert response.status_code == 200
    assert "budget" in response.text.lower()


def test_unreachable_google_renders_a_message_not_a_500(monkeypatch):
    def boom(query, coords=None):
        raise find.PlacesUnavailable("Could not reach Google Places: timed out")

    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", boom)

    response = client.get("/", params={"q": "chinese"})

    # The M3 exit condition: the API being down produces a clear message
    # rather than a traceback.
    assert response.status_code == 200
    assert "Could not get restaurants from Google" in response.text
    assert "Traceback" not in response.text


def test_gemini_down_renders_a_message_and_makes_no_places_call(gemini, monkeypatch):
    gemini.reply = llm.LLMUnavailable("Gemini did not answer within 15s")
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(find, "_fetch_live", _must_not_be_called)

    response = client.get("/", params={"q": "ramen"})

    assert response.status_code == 200
    assert "Could not read your request right now." in response.text
    # The user's words stay in the box, so retrying is one click.
    assert 'value="ramen"' in response.text


def test_exhausted_gemini_budget_renders_a_message(gemini):
    gemini.reply = cache.DailyBudgetExceeded("400 of 400 gemini API calls already used today.")

    response = client.get("/", params={"q": "ramen"})

    assert response.status_code == 200
    assert "budget" in response.text.lower()


def test_a_failure_still_renders_the_form_so_the_user_can_retry(monkeypatch):
    monkeypatch.setattr(find, "USE_LIVE_API", True)
    monkeypatch.setattr(
        find, "_fetch_live", lambda q, coords=None: (_ for _ in ()).throw(find.PlacesUnavailable("x"))
    )

    response = client.get("/", params={"q": "chinese"})

    assert '<form id="ask" action="/"' in response.text
