"""The two guards around a live call: the response cache and the daily cap."""

import time

import pytest

import cache

RAW = [{"displayName": {"text": "Golden Wok Allston"}, "rating": 4.4}]


def test_unknown_query_is_a_miss():
    assert cache.get_cached("chinese food in allston") is None


def test_stored_response_comes_back():
    cache.store("chinese food in allston", RAW)

    assert cache.get_cached("chinese food in allston") == RAW


def test_case_and_spacing_do_not_buy_a_second_call():
    cache.store("Chinese Food   in Allston", RAW)

    # The point of normalizing: a sloppier retype must not cost $0.035.
    assert cache.get_cached("chinese food in allston") == RAW


def test_entry_older_than_the_ttl_is_a_miss(monkeypatch):
    cache.store("chinese food", RAW)

    # Read the real clock before patching it, or the replacement lambda
    # calls itself.
    later = time.time() + cache.CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: later)

    assert cache.get_cached("chinese food") is None


def test_unreadable_cache_file_is_a_miss_not_an_error():
    cache.store("chinese food", RAW)
    cache._responses_path().write_text("{ this is not json")

    # Worst case is one extra call, and the daily cap still bounds that,
    # so a broken cache must not take the app down with it.
    assert cache.get_cached("chinese food") is None


def test_budget_starts_empty_and_counts_up():
    assert cache.calls_today() == 0

    cache.record_call()
    cache.record_call()

    assert cache.calls_today() == 2


def test_budget_passes_while_under_the_cap():
    for _ in range(cache.MAX_CALLS_PER_DAY - 1):
        cache.record_call()

    cache.check_daily_budget()  # must not raise on the last allowed call


def test_budget_refuses_once_the_cap_is_reached():
    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    with pytest.raises(cache.DailyBudgetExceeded):
        cache.check_daily_budget()


def test_counter_resets_on_a_new_day(monkeypatch):
    monkeypatch.setattr(cache, "_today", lambda: "2026-09-15")
    for _ in range(cache.MAX_CALLS_PER_DAY):
        cache.record_call()

    monkeypatch.setattr(cache, "_today", lambda: "2026-09-16")

    assert cache.calls_today() == 0
    cache.check_daily_budget()  # must not raise


def test_corrupt_budget_file_fails_closed():
    cache.record_call()
    cache._budget_path().write_text("{ not json either")

    # Guessing zero here would silently discard the only spend protection
    # this project has, so an unknown counter must stop the app instead.
    with pytest.raises(cache.BudgetUnreadable):
        cache.calls_today()
