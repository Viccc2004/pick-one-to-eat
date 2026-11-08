"""llm.generate_json: the request sent to Gemini, the guards, every failure."""

import pytest
import requests

import cache
import llm
from llm import LLMUnavailable

SCHEMA = {
    "type": "object",
    "properties": {"location": {"type": ["string", "null"]}},
    "required": ["location"],
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def gemini_says(text, finish="STOP", extra_parts=()):
    """A 200 body shaped like a real generateContent response."""
    return FakeResponse(
        payload={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [*extra_parts, {"text": text}]},
                    "finishReason": finish,
                }
            ]
        }
    )


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


@pytest.fixture
def post(monkeypatch):
    """Replace requests.post with a queue of canned replies; record calls."""
    calls = []
    replies = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "body": json, "timeout": timeout})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm.requests, "post", fake_post)
    fake_post.calls = calls
    fake_post.replies = replies
    return fake_post


# --- the happy path and the request itself -----------------------------------


def test_returns_the_parsed_json_object(api_key, post):
    post.replies.append(gemini_says('{"location": "Boston"}'))

    assert llm.generate_json("heading to Boston", SCHEMA) == {"location": "Boston"}


def test_request_carries_model_key_schema_and_timeout(api_key, post):
    post.replies.append(gemini_says('{"location": null}'))

    llm.generate_json("dumplings", SCHEMA, system="Extract the request.")

    sent = post.calls[0]
    assert llm.MODEL in sent["url"]
    assert sent["headers"]["x-goog-api-key"] == "test-key"
    assert sent["timeout"] == llm.REQUEST_TIMEOUT_SECONDS
    config = sent["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SCHEMA
    assert sent["body"]["systemInstruction"]["parts"][0]["text"] == "Extract the request."


def test_key_never_appears_in_the_url(api_key, post):
    # A URL ends up in error messages and logs; a header does not.
    post.replies.append(gemini_says('{"location": null}'))

    llm.generate_json("dumplings", SCHEMA)

    assert "test-key" not in post.calls[0]["url"]


def test_thought_parts_are_not_part_of_the_answer(api_key, post):
    post.replies.append(
        gemini_says('{"location": "Boston"}', extra_parts=[{"text": "hmm, Boston?", "thought": True}])
    )

    assert llm.generate_json("heading to Boston", SCHEMA) == {"location": "Boston"}


# --- the guards ---------------------------------------------------------------


def test_repeat_question_is_a_cache_hit_and_costs_no_call(api_key, post):
    post.replies.append(gemini_says('{"location": "Boston"}'))

    llm.generate_json("heading to Boston", SCHEMA)
    again = llm.generate_json("heading to Boston", SCHEMA)

    assert again == {"location": "Boston"}
    assert len(post.calls) == 1
    assert cache.calls_today(api="gemini") == 1


def test_a_different_system_prompt_is_a_different_question(api_key, post):
    # Changing the prompt must not be answered from the old prompt's cache,
    # or prompt iteration in M5 would measure nothing.
    post.replies += [gemini_says('{"location": "Boston"}'), gemini_says('{"location": null}')]

    llm.generate_json("heading to Boston", SCHEMA, system="v1")
    llm.generate_json("heading to Boston", SCHEMA, system="v2")

    assert len(post.calls) == 2


def test_cap_reached_refuses_without_calling(api_key, post, monkeypatch):
    monkeypatch.setattr(cache, "MAX_GEMINI_CALLS_PER_DAY", 2)
    cache.record_call(api="gemini")
    cache.record_call(api="gemini")

    with pytest.raises(cache.DailyBudgetExceeded):
        llm.generate_json("dumplings", SCHEMA)

    assert post.calls == []


def test_gemini_calls_do_not_spend_the_places_budget(api_key, post):
    post.replies.append(gemini_says('{"location": null}'))

    llm.generate_json("dumplings", SCHEMA)

    assert cache.calls_today(api="gemini") == 1
    assert cache.calls_today(api="places") == 0


def test_missing_key_fails_before_spending_budget(monkeypatch, post):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LLMUnavailable, match="GEMINI_API_KEY"):
        llm.generate_json("dumplings", SCHEMA)

    assert post.calls == []
    assert cache.calls_today(api="gemini") == 0


# --- every failure is LLMUnavailable -----------------------------------------


def test_network_error(api_key, post):
    post.replies.append(requests.ConnectionError("no route"))

    with pytest.raises(LLMUnavailable, match="Could not reach"):
        llm.generate_json("dumplings", SCHEMA)


def test_a_timeout_is_retried_once_and_both_attempts_count(api_key, post):
    post.replies += [requests.Timeout("read timed out"), gemini_says('{"location": null}')]

    assert llm.generate_json("dumplings", SCHEMA) == {"location": None}
    # The hung call may still have been served; the spend guard assumes so.
    assert cache.calls_today(api="gemini") == 2


def test_two_timeouts_give_up(api_key, post):
    post.replies += [requests.Timeout("read timed out"), requests.Timeout("again")]

    with pytest.raises(LLMUnavailable, match="did not answer"):
        llm.generate_json("dumplings", SCHEMA)

    assert len(post.calls) == 1 + llm.RETRIES
    assert cache.calls_today(api="gemini") == 2


def test_the_retry_respects_the_cap(api_key, post, monkeypatch):
    # The first attempt uses the last unit of budget; the retry must not
    # quietly go one over.
    monkeypatch.setattr(cache, "MAX_GEMINI_CALLS_PER_DAY", 1)
    post.replies += [requests.Timeout("read timed out"), gemini_says('{"location": null}')]

    with pytest.raises(cache.DailyBudgetExceeded):
        llm.generate_json("dumplings", SCHEMA)

    assert len(post.calls) == 1


def test_an_overloaded_model_is_retried_once_on_the_fallback(api_key, post):
    # Seen live on 2026-11-08: "This model is currently experiencing high
    # demand. Spikes in demand are usually temporary."
    post.replies += [FakeResponse(status_code=503, text="UNAVAILABLE"), gemini_says('{"location": null}')]

    assert llm.generate_json("dumplings", SCHEMA) == {"location": None}
    assert llm.MODEL in post.calls[0]["url"]
    assert llm.FALLBACK_MODEL in post.calls[1]["url"]
    assert cache.calls_today(api="gemini") == 2


def test_a_timeout_is_retried_on_the_fallback_too(api_key, post):
    post.replies += [requests.Timeout("read timed out"), gemini_says('{"location": null}')]

    llm.generate_json("dumplings", SCHEMA)

    assert llm.FALLBACK_MODEL in post.calls[1]["url"]


def test_a_fallback_answer_is_reused_from_the_cache(api_key, post):
    # Going back to the overloaded primary for the same question would
    # spend a call to get a refusal.
    post.replies += [FakeResponse(status_code=503, text="UNAVAILABLE"), gemini_says('{"location": "Boston"}')]

    llm.generate_json("heading to Boston", SCHEMA)
    again = llm.generate_json("heading to Boston", SCHEMA)

    assert again == {"location": "Boston"}
    assert len(post.calls) == 2


def test_overloaded_twice_gives_a_plain_message(api_key, post):
    post.replies += [FakeResponse(status_code=503, text="UNAVAILABLE")] * 2

    with pytest.raises(LLMUnavailable, match="overloaded right now") as caught:
        llm.generate_json("dumplings", SCHEMA)

    # The page shows this text; raw JSON from Google is not a sentence.
    assert "{" not in str(caught.value)


def test_other_failures_are_not_retried(api_key, post):
    post.replies += [FakeResponse(status_code=500, text="internal")]

    with pytest.raises(LLMUnavailable):
        llm.generate_json("dumplings", SCHEMA)

    assert len(post.calls) == 1


def test_free_tier_limit_429_gets_its_own_message(api_key, post):
    post.replies.append(FakeResponse(status_code=429, text="RESOURCE_EXHAUSTED"))

    with pytest.raises(LLMUnavailable, match="free-tier limit"):
        llm.generate_json("dumplings", SCHEMA)


def test_other_http_error(api_key, post):
    post.replies.append(FakeResponse(status_code=500, text="internal"))

    with pytest.raises(LLMUnavailable, match="500"):
        llm.generate_json("dumplings", SCHEMA)


def test_non_json_body(api_key, post):
    post.replies.append(FakeResponse(payload=None))

    with pytest.raises(LLMUnavailable, match="non-JSON body"):
        llm.generate_json("dumplings", SCHEMA)


def test_blocked_prompt_has_no_candidates(api_key, post):
    post.replies.append(FakeResponse(payload={"promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(LLMUnavailable, match="SAFETY"):
        llm.generate_json("dumplings", SCHEMA)


def test_answer_cut_off_by_token_limit(api_key, post):
    post.replies.append(gemini_says('{"location": "Bos', finish="MAX_TOKENS"))

    with pytest.raises(LLMUnavailable, match="MAX_TOKENS"):
        llm.generate_json("dumplings", SCHEMA)


def test_malformed_json_is_an_error_and_is_not_cached(api_key, post):
    post.replies += [gemini_says("not json at all"), gemini_says('{"location": "Boston"}')]

    with pytest.raises(LLMUnavailable, match="not valid JSON"):
        llm.generate_json("heading to Boston", SCHEMA)

    # Caching the broken answer would serve it back for 24 hours.
    assert llm.generate_json("heading to Boston", SCHEMA) == {"location": "Boston"}
    assert len(post.calls) == 2


def test_json_that_is_not_an_object(api_key, post):
    post.replies.append(gemini_says('["Boston"]'))

    with pytest.raises(LLMUnavailable, match="not a JSON object"):
        llm.generate_json("heading to Boston", SCHEMA)


def test_conftest_blocks_the_real_network(api_key):
    # No `post` fixture here: this is the guard in conftest.py, which must
    # catch a Gemini call exactly as it catches a Places call.
    with pytest.raises(AssertionError, match="real network"):
        llm.generate_json("dumplings", SCHEMA)
