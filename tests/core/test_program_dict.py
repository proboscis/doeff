"""TDD tests for Program.dict(), Program.list(), Program.tuple(), Program.set().

ISSUE-CORE-510: All collection combinators pass ProgramBase instances to gather(),
but the Rust scheduler only understands waitable handles (Task/Promise/ExternalPromise).
These tests should FAIL before the fix and PASS after.
"""

from __future__ import annotations

import pytest

from doeff import Program, default_handlers, do, run


class TestProgramDict:
    """Program.dict() must execute programs and return a plain dict of values."""

    def test_dict_plain_values(self) -> None:
        """Program.dict() with plain (non-Program) values."""
        result = run(Program.dict(a="hello", b=42), handlers=default_handlers())
        assert result.value == {"a": "hello", "b": 42}

    def test_dict_program_values(self) -> None:
        """Program.dict() with Program.pure() values."""
        result = run(
            Program.dict(x=Program.pure("world"), y=Program.pure(99)),
            handlers=default_handlers(),
        )
        assert result.value == {"x": "world", "y": 99}

    def test_dict_mixed_values(self) -> None:
        """Program.dict() with a mix of plain and Program values."""
        result = run(
            Program.dict(plain="raw", lifted=Program.pure("computed")),
            handlers=default_handlers(),
        )
        assert result.value == {"plain": "raw", "lifted": "computed"}

    def test_dict_empty(self) -> None:
        """Program.dict() with no arguments returns empty dict."""
        result = run(Program.dict(), handlers=default_handlers())
        assert result.value == {}

    def test_dict_with_effectful_program(self) -> None:
        """Program.dict() values can be effectful programs."""
        from doeff import Get, Put

        @do
        def compute():
            yield Put("counter", 10)
            return (yield Get("counter"))

        result = run(
            Program.dict(static="fixed", dynamic=compute()),
            handlers=default_handlers(),
        )
        assert result.value == {"static": "fixed", "dynamic": 10}


class TestProgramList:
    """Program.list() must execute programs and return a list of values."""

    def test_list_plain_values(self) -> None:
        result = run(Program.list("a", "b", "c"), handlers=default_handlers())
        assert result.value == ["a", "b", "c"]

    def test_list_program_values(self) -> None:
        result = run(
            Program.list(Program.pure(1), Program.pure(2), Program.pure(3)),
            handlers=default_handlers(),
        )
        assert result.value == [1, 2, 3]

    def test_list_empty(self) -> None:
        result = run(Program.list(), handlers=default_handlers())
        assert result.value == []


class TestProgramTuple:
    """Program.tuple() must execute programs and return a tuple of values."""

    def test_tuple_plain_values(self) -> None:
        result = run(Program.tuple("x", "y"), handlers=default_handlers())
        assert result.value == ("x", "y")

    def test_tuple_program_values(self) -> None:
        result = run(
            Program.tuple(Program.pure(10), Program.pure(20)),
            handlers=default_handlers(),
        )
        assert result.value == (10, 20)


class TestProgramSet:
    """Program.set() must execute programs and return a set of values."""

    def test_set_plain_values(self) -> None:
        result = run(Program.set(1, 2, 3), handlers=default_handlers())
        assert result.value == {1, 2, 3}

    def test_set_program_values(self) -> None:
        result = run(
            Program.set(Program.pure("a"), Program.pure("b")),
            handlers=default_handlers(),
        )
        assert result.value == {"a", "b"}


class TestProgramSequence:
    """Program.sequence() must execute a list of programs and return list of values."""

    def test_sequence_basic(self) -> None:
        programs = [Program.pure(i) for i in range(5)]
        result = run(Program.sequence(programs), handlers=default_handlers())
        assert result.value == [0, 1, 2, 3, 4]

    def test_sequence_empty(self) -> None:
        result = run(Program.sequence([]), handlers=default_handlers())
        assert result.value == []


class TestProgramTraverse:
    """Program.traverse() must map+sequence a list of values through a function."""

    def test_traverse_basic(self) -> None:
        result = run(
            Program.traverse([1, 2, 3], lambda x: Program.pure(x * 10)),
            handlers=default_handlers(),
        )
        assert result.value == [10, 20, 30]
