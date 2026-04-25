from __future__ import annotations

from doeff_core_effects.cache import cache

from doeff import do, run


def test_cache_without_memo_handlers_computes_without_caching():
    calls = {"count": 0}

    @cache()
    @do
    def cached_double(value: int):
        calls["count"] += 1
        return value * 2

    @do
    def program():
        first = yield cached_double(21)
        second = yield cached_double(21)
        return (first, second)

    result = run(program())

    assert result == (42, 42)
    assert calls["count"] == 2
