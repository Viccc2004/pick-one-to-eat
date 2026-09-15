"""Shared test setup."""

import pytest

import cache


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Point the cache at a throwaway directory for every test.

    Autouse and unconditional on purpose. A test that wrote to the real
    .cache/ could consume the day's API budget, or leave a stale response
    that makes an unrelated test pass for the wrong reason.
    """
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
