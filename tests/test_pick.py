"""pick: the LLM ranks and explains; the code checks what it wrote."""

import pytest

import cache
import llm
import pick
from recommend import build_reason

WHEN = "today at 19:00"
TURNS = [("user", "想吃火锅，今晚7点")]


def place(name, rating=4.6, count=1503, price="PRICE_LEVEL_MODERATE", open_then=True, ptype="chinese_restaurant"):
    return {
        "name": name,
        "rating": rating,
        "rating_count": count,
        "price_level": price,
        "place_type": ptype,
        "open_then": open_then,
        "address": "1 Somewhere St",
        "maps_url": None,
        "periods": None,
        "utc_offset_minutes": -300,
    }


PLACES = [place("A Hot Pot"), place("B Noodles", 4.4, 812), place("C Grill", 3.8, 155)]


@pytest.fixture
def gemini(monkeypatch):
    def fake(prompt, schema, system=None):
        fake.sent.update(prompt=prompt, schema=schema, system=system)
        if isinstance(fake.reply, Exception):
            raise fake.reply
        return fake.reply

    fake.sent = {}
    fake.reply = {"picks": []}
    monkeypatch.setattr(pick.llm, "generate_json", fake)
    return fake


def choose(places=PLACES):
    return pick.choose(TURNS, WHEN, places)


# --- the prompt ----------------------------------------------------------------


def test_prompt_carries_the_words_the_time_and_only_known_facts(gemini):
    choose([place("A Hot Pot"), place("Mystery", rating=None, count=None, price=None, open_then=None)])

    prompt = gemini.sent["prompt"]
    assert "User: 想吃火锅，今晚7点" in prompt
    assert "They want to eat: today at 19:00" in prompt
    assert "[1] name: A Hot Pot; type: chinese restaurant; rating: 4.6 from 1503 reviews; price: mid-priced; open today at 19:00: yes" in prompt
    assert "[2] name: Mystery; type: chinese restaurant; rating: unknown; open today at 19:00: unknown" in prompt
    assert gemini.sent["schema"] is pick.SCHEMA


def test_closed_places_are_not_offered(gemini):
    choose([place("Open"), place("Shut", open_then=False)])

    assert "Shut" not in gemini.sent["prompt"]


def test_nothing_open_means_no_call_and_no_picks(gemini):
    gemini.reply = AssertionError("must not be called")

    assert choose([place("Shut", open_then=False)]) == ([], None)


# --- picks are checked ----------------------------------------------------------


def test_picks_come_back_in_the_llms_order_with_its_reasons(gemini):
    gemini.reply = {"picks": [
        {"id": 2, "reason": "B 有 4.4 分，812 条评价。"},
        {"id": 1, "reason": "A 4.6 分，晚上7点营业。"},
    ]}

    chosen, note = choose()

    assert [p["name"] for p in chosen] == ["B Noodles", "A Hot Pot"]
    assert chosen[0]["reason"] == "B 有 4.4 分，812 条评价。"
    assert note is None


def test_unknown_duplicate_and_malformed_ids_are_skipped(gemini):
    gemini.reply = {"picks": [
        {"id": 9, "reason": "No such place."},
        {"id": True, "reason": "A bool is not an id."},
        {"id": "1", "reason": "A string is not an id."},
        {"id": 3, "reason": "C is fine."},
        {"id": 3, "reason": "C again."},
        "not even a dict",
    ]}

    chosen, _ = choose()

    assert [p["name"] for p in chosen] == ["C Grill"]


def test_at_most_three(gemini):
    many = [place(f"P{i}") for i in range(6)]
    gemini.reply = {"picks": [{"id": i, "reason": "ok"} for i in range(1, 7)]}

    chosen, _ = choose(many)

    assert len(chosen) == 3


# --- the reason may cite only supported numbers --------------------------------


def test_an_invented_number_replaces_the_reason_with_the_template(gemini):
    # 4.9 is not A's rating. The model's sentence is dropped, not edited.
    gemini.reply = {"picks": [{"id": 1, "reason": "A 有 4.9 分，人均 30 美元。"}]}

    chosen, _ = choose()

    assert chosen[0]["reason"] == build_reason(PLACES[0], WHEN)


@pytest.mark.parametrize("reason", [
    "4.6 stars from 1,503 reviews, open at 19:00.",
    "4.6分，1503条评价，晚上7点营业。",
    "Rated 4.6, but check the hours.",
    "No numbers at all.",
    "Fewer than 50 reviews would be weak; this has 1503.",
])
def test_supported_numbers_pass(reason):
    assert pick.numbers_are_supported(reason, PLACES[0], WHEN)


@pytest.mark.parametrize("reason", [
    "4.7 stars.",
    "About 10 minutes away.",
    "Open until 23:00.",
    "Mains around $25.",
])
def test_unsupported_numbers_fail(reason):
    assert not pick.numbers_are_supported(reason, PLACES[0], WHEN)


def test_an_overlong_reason_gets_the_template(gemini):
    gemini.reply = {"picks": [{"id": 1, "reason": "很好。" * 400}]}

    chosen, _ = choose()

    assert chosen[0]["reason"] == build_reason(PLACES[0], WHEN)


def test_an_empty_reason_gets_the_template(gemini):
    gemini.reply = {"picks": [{"id": 1, "reason": "   "}]}

    chosen, _ = choose()

    assert chosen[0]["reason"] == build_reason(PLACES[0], WHEN)


# --- when the call fails, the rules answer --------------------------------------


@pytest.mark.parametrize("failure", [
    llm.LLMUnavailable("Gemini is overloaded right now (503)."),
    cache.DailyBudgetExceeded("400 of 400 gemini API calls already used today."),
    cache.BudgetUnreadable("counter unreadable"),
])
def test_a_failed_call_falls_back_to_the_rules_and_says_so(gemini, failure):
    gemini.reply = failure

    chosen, note = choose()

    assert [p["name"] for p in chosen] == ["A Hot Pot", "B Noodles", "C Grill"]
    assert chosen[0]["reason"] == build_reason(PLACES[0], WHEN)
    assert note == pick.FALLBACK_NOTE


def test_no_usable_pick_falls_back_to_the_rules(gemini):
    gemini.reply = {"picks": [{"id": 42, "reason": "?"}]}

    chosen, note = choose()

    assert chosen[0]["name"] == "A Hot Pot"
    assert note == pick.FALLBACK_NOTE


def test_an_answer_without_picks_falls_back(gemini):
    gemini.reply = {"something": "else"}

    _, note = choose()

    assert note == pick.FALLBACK_NOTE
