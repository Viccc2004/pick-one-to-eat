"""extract: what the user wants, and what code decides is still missing."""

import pytest

import extract
import llm

ASKS = {
    "ask_location": "Where do you want to eat?",
    "ask_when": "Now or later?",
    "ask_time": "What time?",
}


def answer(**fields):
    """A complete, valid LLM answer; override fields per test."""
    base = {
        "search_text": "ramen",
        "location": None,
        "when": "now",
        "day": None,
        "time": None,
        "price": None,
        **ASKS,
    }
    base.update(fields)
    return base


@pytest.fixture
def gemini(monkeypatch):
    """Stub llm.generate_json with one canned answer; record what was asked."""
    sent = {}

    def fake(prompt, schema, system=None):
        sent.update(prompt=prompt, schema=schema, system=system)
        if isinstance(fake.reply, Exception):
            raise fake.reply
        return fake.reply

    fake.reply = answer()
    fake.sent = sent
    monkeypatch.setattr(extract.llm, "generate_json", fake)
    return fake


def ask(text, has_browser_location=True):
    return extract.extract([("user", text)], has_browser_location)


# --- the prompt ----------------------------------------------------------------


def test_prompt_carries_the_whole_conversation_in_order(gemini):
    extract.extract(
        [("user", "sushi"), ("app", "Now or later?"), ("user", "now")],
        has_browser_location=True,
    )

    prompt = gemini.sent["prompt"]
    assert prompt.index("User: sushi") < prompt.index("App: Now or later?")
    assert prompt.index("App: Now or later?") < prompt.rindex("User: now")
    assert gemini.sent["schema"] is extract.SCHEMA
    assert gemini.sent["system"] is extract.SYSTEM


def test_prompt_does_not_depend_on_location_permission(gemini):
    # The prompt is the cache key. Permission only changes what is missing,
    # which code decides, so it must not split the cache in two.
    ask("ramen", has_browser_location=True)
    with_permission = gemini.sent["prompt"]
    ask("ramen", has_browser_location=False)

    assert gemini.sent["prompt"] == with_permission


# --- the M4 exit cases ---------------------------------------------------------


def test_heading_somewhere_else_uses_where_they_will_eat(gemini):
    gemini.reply = answer(
        search_text="cheap dumplings",
        location="Boston",
        when="later",
        day="tomorrow",
        time="19:00",
        price="cheap",
    )

    req = ask("in LA now, flying to Boston tomorrow, cheap dumplings for dinner")

    assert req.location == "Boston"
    assert (req.when, req.day, req.time, req.price) == ("later", "tomorrow", "19:00", "cheap")
    assert req.missing == ()
    assert req.question is None


def test_no_place_named_but_browser_location_is_not_missing(gemini):
    req = ask("ramen now", has_browser_location=True)

    assert req.location is None
    assert req.missing == ()


def test_no_place_named_and_no_browser_location_asks_where(gemini):
    req = ask("ramen now", has_browser_location=False)

    assert req.missing == ("location",)
    assert req.question == "Where do you want to eat?"


# --- what is missing is decided by code, not by the LLM -------------------------


def test_unknown_time_asks_now_or_later(gemini):
    gemini.reply = answer(when="unknown")

    req = ask("ramen")

    assert req.missing == ("when",)
    assert req.question == "Now or later?"


def test_later_without_a_time_asks_what_time(gemini):
    gemini.reply = answer(when="later", day="tomorrow", time=None)

    req = ask("ramen tomorrow")

    assert req.missing == ("time",)
    assert req.question == "What time?"


def test_several_missing_items_are_asked_together_in_order(gemini):
    gemini.reply = answer(when="unknown")

    req = ask("ramen", has_browser_location=False)

    assert req.missing == ("location", "when")
    assert req.question == "Where do you want to eat? Now or later?"


def test_questions_for_items_not_missing_are_never_shown(gemini):
    # The first live run: asked to judge, the LLM asked "where?" of a user
    # whose browser had already said. All three questions always come back;
    # only the missing ones may reach the page.
    req = ask("ramen now", has_browser_location=True)

    assert req.question is None


def test_empty_llm_question_falls_back_to_a_template(gemini):
    gemini.reply = answer(when="unknown", ask_when="  ")

    req = ask("ramen")

    assert req.question == "Are you eating now or later?"


# --- the two-question cap ------------------------------------------------------

def after_questions(n, last="ramen"):
    """A conversation in which the app has already asked n questions."""
    turns = []
    for i in range(n):
        turns += [("user", f"answer {i}"), ("app", f"question {i}")]
    return turns + [("user", last)]


def test_below_the_cap_the_app_keeps_asking(gemini):
    gemini.reply = answer(when="unknown")

    req = extract.extract(after_questions(extract.MAX_QUESTIONS - 1), True)

    assert req.missing == ("when",)
    assert req.assumptions == ()


def test_at_the_cap_an_unknown_time_becomes_now_and_says_so(gemini):
    gemini.reply = answer(when="unknown")

    req = extract.extract(after_questions(extract.MAX_QUESTIONS), True)

    assert req.when == "now"
    assert req.missing == ()
    assert req.question is None
    assert any("assumes you're eating now" in a for a in req.assumptions)


def test_at_the_cap_a_later_meal_without_a_time_is_searched_but_flagged(gemini):
    gemini.reply = answer(when="later", day="tomorrow", time=None)

    req = extract.extract(after_questions(extract.MAX_QUESTIONS), True)

    # No honest default for "tomorrow, some time": search anyway, and say
    # the hours were not checked rather than inventing a time.
    assert (req.when, req.day, req.time) == ("later", "tomorrow", None)
    assert req.missing == ()
    assert any("call ahead" in a for a in req.assumptions)


def test_location_is_never_assumed_even_past_the_cap(gemini):
    gemini.reply = answer(when="unknown")

    req = extract.extract(after_questions(extract.MAX_QUESTIONS + 3), False)

    assert req.missing == ("location",)
    assert req.question == "Where do you want to eat?"
    assert req.when == "now"


# --- bad fields degrade to "not said", never to a crash or a guess --------------


def test_unknown_when_value_becomes_unknown_and_is_asked(gemini):
    gemini.reply = answer(when="soonish")

    req = ask("ramen")

    assert req.when == "unknown"
    assert "when" in req.missing


def test_malformed_time_is_dropped_and_asked(gemini):
    gemini.reply = answer(when="later", day="today", time="7pm")

    req = ask("ramen at 7pm")

    assert req.time is None
    assert req.missing == ("time",)


def test_time_without_a_day_means_today(gemini):
    gemini.reply = answer(when="later", day=None, time="20:00")

    assert ask("ramen at 8").day == "today"


def test_day_and_time_on_a_now_request_are_dropped(gemini):
    gemini.reply = answer(when="now", day="tomorrow", time="19:00")

    req = ask("ramen now")

    assert (req.day, req.time) == (None, None)


def test_wrong_types_are_treated_as_not_said(gemini):
    gemini.reply = answer(location=42, price=["cheap"], day="someday", search_text="")

    req = ask("ramen", has_browser_location=False)

    assert req.location is None
    assert req.price is None
    assert req.search_text == extract.DEFAULT_SEARCH_TEXT
    assert "location" in req.missing


def test_a_missing_field_is_treated_as_not_said(gemini):
    gemini.reply = {"search_text": "ramen", "when": "now"}

    req = ask("ramen now", has_browser_location=False)

    assert req.location is None
    assert req.missing == ("location",)
    assert req.question == "Where do you want to eat?"


def test_case_and_whitespace_in_enums_are_forgiven(gemini):
    gemini.reply = answer(when=" Later ", day="Tomorrow", time="19:00", price="CHEAP")

    req = ask("cheap ramen tomorrow at 7")

    assert (req.when, req.day, req.price) == ("later", "tomorrow", "cheap")


# --- LLM failures are not swallowed here -----------------------------------------


def test_llm_failure_reaches_the_caller(gemini):
    # The web layer owns the sentence the user sees (M4.3); hiding the
    # failure here would turn "Gemini is down" into a wrong question.
    gemini.reply = llm.LLMUnavailable("Could not reach Gemini")

    with pytest.raises(llm.LLMUnavailable):
        ask("ramen")
