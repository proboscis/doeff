"""SPEC-TYPES-001 §11.6 — Type Validation Rejection Tests (TV-01 through TV-22).

Every typed parameter in the public API MUST raise TypeError for wrong types.
Tests cover both acceptance (happy path) and rejection (informative TypeError).

SPEC-009 §12: No duck-typing, no silent coercion, no deferred errors.
"""

from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Delegate,
    Get,
    Resume,
    Transfer,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.program import DoExpr, GeneratorProgram
from doeff.types import EffectBase


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram."""
    return GeneratorProgram(gen_factory)


def _valid_program():
    """A minimal valid program for tests that need one."""

    def gen():
        return 42
        yield  # noqa: RET504

    return _prog(gen)


def _valid_handler(effect, k):
    """A minimal handler that delegates everything."""
    yield Delegate()


# ===========================================================================
# §11.6 Entrypoints: run() input validation (TV-01 through TV-10)
# ===========================================================================


class TestTV01RunRejectsInt:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="DoExpr"):
            run(42)

    def test_mentions_actual_type(self) -> None:
        with pytest.raises(TypeError, match="int"):
            run(42)


class TestTV02RunRejectsStr:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="str"):
            run("hello")


class TestTV03RunRejectsLambda:
    def test_raises_typeerror_with_hint(self) -> None:
        with pytest.raises(TypeError, match="(?i)(do|callable|function)"):
            run(lambda: 42)


class TestTV04RunRejectsUncalledFunction:
    def test_raises_typeerror_with_hint(self) -> None:
        @do
        def my_func(x: int):
            return x

        # Passing the KleisliProgram itself (uncalled) — this is actually
        # a valid callable but NOT a DoExpr. User probably meant my_func(1).
        # However, KleisliProgram might not be a DoExpr.
        # The real test: passing a plain function (not @do decorated).
        def plain_func():
            yield Get("x")
            return 1

        with pytest.raises(TypeError, match="(?i)(call|function)"):
            run(plain_func)


class TestTV05RunRejectsRawGenerator:
    def test_raises_typeerror_with_hint(self) -> None:
        def gen():
            yield Get("x")
            return 1

        with pytest.raises(TypeError, match="(?i)(generator|@do|GeneratorProgram)"):
            run(gen())


class TestTV06RunRejectsNonSequenceHandlers:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            run(_valid_program(), handlers="not_a_list")


class TestTV07RunRejectsNonDictEnv:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="(?i)dict"):
            run(_valid_program(), env="not_a_dict")


class TestTV08RunRejectsNonDictStore:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="(?i)dict"):
            run(_valid_program(), store=[1, 2, 3])


class TestTV09RunAcceptsNoneEnv:
    def test_none_env_accepted(self) -> None:
        # Should not raise — None is valid for env
        result = run(_valid_program(), handlers=default_handlers(), env=None)
        assert result is not None


class TestTV10RunAcceptsNoneStore:
    def test_none_store_accepted(self) -> None:
        result = run(_valid_program(), handlers=default_handlers(), store=None)
        assert result is not None


# ===========================================================================
# §11.6 Entrypoints: run() accepts all DoExpr subtypes
# ===========================================================================


class TestRunAcceptsAllDoExpr:
    """run() must accept DoThunk, EffectBase, and DoCtrl — not just Programs."""

    def test_accepts_generator_program(self) -> None:
        """DoThunk: GeneratorProgram with to_generator."""
        result = run(_valid_program(), handlers=default_handlers())
        assert result.value == 42

    def test_accepts_bare_effect(self) -> None:
        """EffectBase: bare Ask effect as top-level program."""
        result = run(Ask("key"), handlers=default_handlers(), env={"key": "val"})
        assert result.value == "val"

    def test_accepts_bare_get(self) -> None:
        """EffectBase: bare Get effect as top-level program."""
        result = run(Get("x"), handlers=default_handlers(), store={"x": 99})
        assert result.value == 99

    def test_accepts_with_handler(self) -> None:
        """DoCtrl: WithHandler as top-level program."""

        def handler(effect, k):
            yield Delegate()

        def body():
            return 42
            yield  # noqa: RET504

        result = run(
            WithHandler(handler, _prog(body)),
            handlers=default_handlers(),
        )
        assert result.value == 42


# ===========================================================================
# §11.6 Dispatch primitives: construction-time validation (TV-11 through TV-16)
# ===========================================================================


class TestTV11ResumeRejectsNonK:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="(?i)K"):
            Resume("not_k", 42)

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            Resume(123, 42)

    def test_rejects_none(self) -> None:
        with pytest.raises(TypeError):
            Resume(None, 42)


class TestTV12ResumeAcceptsValidK:
    """Resume(k, value) with a real K must not raise.

    We can't easily construct a K outside the VM, so this test
    exercises Resume inside a handler where k is provided by dispatch.
    """

    def test_resume_with_real_k(self) -> None:
        """If construction-time validation exists, this proves valid K passes."""

        class _TestEffect(EffectBase):
            pass

        def handler(effect, k):
            if isinstance(effect, _TestEffect):
                # k is a real K here — Resume construction must not raise
                yield Resume(k, "handled")
            else:
                yield Delegate()

        def body():
            result = yield _TestEffect()
            return result

        def main():
            result = yield WithHandler(handler, _prog(body))
            return result

        result = run(_prog(main))
        assert result.value == "handled"


class TestTV13TransferRejectsNonK:
    def test_raises_typeerror(self) -> None:
        with pytest.raises(TypeError, match="(?i)K"):
            Transfer("not_k", 42)

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            Transfer(123, 42)


class TestTV14DelegateRejectsNonEffect:
    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError, match="(?i)(Effect|EffectBase)"):
            Delegate(42)

    def test_rejects_str(self) -> None:
        with pytest.raises(TypeError):
            Delegate("not_an_effect")


class TestTV15DelegateAcceptsNoArgs:
    def test_no_args(self) -> None:
        d = Delegate()
        assert d is not None


class TestTV16DelegateAcceptsEffect:
    def test_with_effect(self) -> None:
        d = Delegate(Ask("key"))
        assert d is not None


# ===========================================================================
# §11.6 WithHandler: construction-time validation (TV-17 through TV-19)
# ===========================================================================


class TestTV17WithHandlerRejectsNonCallableHandler:
    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError, match="(?i)(callable|handler)"):
            WithHandler("not_callable", _valid_program())

    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            WithHandler(42, _valid_program())


class TestTV18WithHandlerRejectsNonDoExprProgram:
    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError, match="(?i)DoExpr"):
            WithHandler(_valid_handler, 42)

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            WithHandler(_valid_handler, "not_a_program")


class TestTV19WithHandlerAcceptsValid:
    def test_callable_handler_and_program(self) -> None:
        wh = WithHandler(_valid_handler, _valid_program())
        assert wh is not None


# ===========================================================================
# §11.6 @do decorator: validation (TV-20 through TV-22)
# ===========================================================================


class TestTV20DoRejectsNonCallable:
    def test_rejects_int(self) -> None:
        with pytest.raises(TypeError):
            do(42)

    def test_rejects_string(self) -> None:
        with pytest.raises(TypeError):
            do("not_a_function")

    def test_rejects_none(self) -> None:
        with pytest.raises(TypeError):
            do(None)


class TestTV21DoAcceptsRegularFunction:
    def test_non_generator_accepted(self) -> None:
        @do
        def pure_add(a: int, b: int):
            return a + b  # no yields — early return pattern

        kpc = pure_add(3, 4)
        assert kpc is not None


class TestTV22DoAcceptsGeneratorFunction:
    def test_generator_accepted(self) -> None:
        @do
        def effectful(key: str):
            val = yield Ask(key)
            return val

        kpc = effectful("api_key")
        assert kpc is not None
