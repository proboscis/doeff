from doeff_core_effects.cache import cache

from doeff import do, run


def test_cache_decorator_runs_without_any_memo_handlers():
    calls = {"count": 0}

    @cache()
    @do
    def expensive(value):
        calls["count"] += 1
        return value + 5

    assert run(expensive(37)) == 42
    assert calls["count"] == 1
