"""SA-008 runtime contract regressions (converted from ad-hoc probes).

These tests codify correctness expectations for Rust VM run/store behavior.
"""

from __future__ import annotations

from doeff import do
from doeff.effects.state import Get, Modify, Put
from doeff.rust_vm import default_handlers, run


def test_sa008_run_store_seed_and_put_get_roundtrip() -> None:
    @do
    def prog():
        _ = yield Put("x", 5)
        value = yield Get("x")
        return value

    result = run(prog(), handlers=default_handlers(), store={"x": 0})

    assert result.value == 5
    assert result.raw_store["x"] == 5


def test_sa008_modify_returns_old_value_and_updates_store() -> None:
    @do
    def prog():
        old = yield Modify("x", lambda v: (v or 0) + 1)
        new = yield Get("x")
        return (old, new)

    result = run(prog(), handlers=default_handlers(), store={"x": 3})

    assert result.value == (3, 4)
    assert result.raw_store["x"] == 4
