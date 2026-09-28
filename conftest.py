"""Shared test setup."""

import pytest

import cache
import find


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Point the cache at a throwaway directory for every test.

    Autouse and unconditional on purpose. A test that wrote to the real
    .cache/ could consume the day's API budget, or leave a stale response
    that makes an unrelated test pass for the wrong reason.
    """
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")


def _no_network(*args, **kwargs):
    raise AssertionError("a test reached the real network; stub requests.post")


@pytest.fixture(autouse=True)
def fixtures_not_google(monkeypatch):
    """Run every test on fixtures, whatever find.USE_LIVE_API says.

    The switch is True in the app, and .env holds a real key, so without
    this a plain fixture test would make a billed call. Tests of the live
    path turn it back on and stub _fetch_live or requests.post themselves;
    anything that forgets fails here instead of spending.
    """
    monkeypatch.setattr(find, "USE_LIVE_API", False)
    monkeypatch.setattr(find.requests, "post", _no_network)
