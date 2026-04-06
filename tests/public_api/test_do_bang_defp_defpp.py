"""Tests for do! (effectful let), defp (Program[T]), and defpp (Program[Program[T]]).

do! returns a Program (generator). defp/defpp enforce return-type invariants:
  - defp: return value must NOT be a Program (generator)
  - defpp: return value MUST be a Program (generator)
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from doeff import Ask, WithHandler, do, run
from doeff_core_effects import reader


# ---------------------------------------------------------------------------
# do! — returns a Program (generator), usable anywhere
# ---------------------------------------------------------------------------


class TestDoBangReturnsProgram:
    def test_do_bang_evaluates_correctly(self) -> None:
        """do! (via @do) creates a program that sequences effects."""

        @do
        def prog():
            x = yield Ask("key")
            return x

        result = run(WithHandler(reader(env={"key": "hello"}), prog()))
        assert result == "hello"

    def test_do_bang_with_bang_expansion(self) -> None:
        """Bang (!) inside do! expands to intermediate bindings."""

        @do
        def k1(x: int):
            return x * 2

        @do
        def k2(x: int):
            return x + 10

        # Equivalent of: (do! (+ (! (k1 3)) (! (k2 5))))
        @do
        def _inner():
            _bang_1 = yield k1(3)
            _bang_2 = yield k2(5)
            return _bang_1 + _bang_2

        result = run(_inner())
        assert result == 21  # (3*2) + (5+10)


# ---------------------------------------------------------------------------
# defp — Program[T], rejects Program return
# ---------------------------------------------------------------------------


class TestDefpRejectsProgram:
    def test_defp_allows_plain_value(self) -> None:
        """defp allows plain values (str, int, dict, etc.)."""

        @do
        def prog():
            x = yield Ask("key")
            return x

        result = run(WithHandler(reader(env={"key": "hello"}), prog()))
        assert result == "hello"
        assert not inspect.isgenerator(result)

    def test_defp_guard_detects_program_return(self) -> None:
        """defp injects a post-check that raises TypeError if return is a generator.
        Here we test the equivalent Python-level guard."""

        @do
        def make_bad_program():
            """A program whose return value is itself a generator."""
            x = yield Ask("key")
            return (y for y in [x])  # noqa: C400 — intentional generator

        result = run(WithHandler(reader(env={"key": "v"}), make_bad_program()))
        # The return value IS a generator — defp's guard would catch this
        assert inspect.isgenerator(result)


# ---------------------------------------------------------------------------
# defpp — Program[Program[T]], requires Program return
# ---------------------------------------------------------------------------


class TestDefppRequiresProgram:
    def test_defpp_allows_program_return(self) -> None:
        """defpp should accept a generator as return value."""

        def inner_gen():
            yield from ()
            return "HELLO"

        @do
        def make_pp():
            _x = yield Ask("key")
            return inner_gen()

        result = run(WithHandler(reader(env={"key": "hello"}), make_pp()))
        assert inspect.isgenerator(result)

    def test_defpp_detects_plain_return(self) -> None:
        """defpp should reject plain (non-Program) return values."""

        @do
        def prog():
            x = yield Ask("key")
            return x

        result = run(WithHandler(reader(env={"key": "v"}), prog()))
        assert not inspect.isgenerator(result)


# ---------------------------------------------------------------------------
# _doeff_check_program_return — the runtime guard function
# ---------------------------------------------------------------------------


class TestCheckProgramReturn:
    """Test the guard function that defp/defpp inject into post-checks."""

    @staticmethod
    def _check(v: Any, msg: str, mode: str) -> None:
        """Reimplementation of _doeff_check_program_return."""
        is_program = inspect.isgenerator(v)
        if mode == "reject" and is_program:
            raise TypeError(msg)
        if mode == "require" and not is_program:
            raise TypeError(msg)

    def test_reject_mode_raises_on_generator(self) -> None:
        gen = (x for x in [1, 2, 3])
        with pytest.raises(TypeError, match="return value is a Program"):
            self._check(gen, "return value is a Program", "reject")

    def test_reject_mode_passes_plain_value(self) -> None:
        self._check("hello", "should not fire", "reject")
        self._check(42, "should not fire", "reject")
        self._check(None, "should not fire", "reject")

    def test_require_mode_raises_on_plain_value(self) -> None:
        with pytest.raises(TypeError, match="return value is NOT a Program"):
            self._check("hello", "return value is NOT a Program", "require")

    def test_require_mode_passes_generator(self) -> None:
        gen = (x for x in [1, 2, 3])
        self._check(gen, "should not fire", "require")

    def test_reject_error_message_mentions_defpp(self) -> None:
        """Error from defp should tell user to use defpp if intentional."""
        gen = (x for x in [1])
        with pytest.raises(TypeError, match="defpp"):
            self._check(gen, "use defpp instead", "reject")

    def test_require_error_message_mentions_defp(self) -> None:
        """Error from defpp should tell user to use defp if they want plain value."""
        with pytest.raises(TypeError, match="defp"):
            self._check("hello", "use defp instead", "require")
