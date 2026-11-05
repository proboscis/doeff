"""Regression test for the change that lowered the per-effect cost and the import floor (2026-09-23).

- A ``@do`` handler: the VM calls the original generator function directly to make the handler stream (without creating the
  Expand / Apply / Pure / Callable that the wrapper used to create per effect). The result is the same as the previous evaluation.
- ``import doeff`` does not load Hy, httpx, or the CLI (they are loaded on use).
"""

import subprocess
import sys

import pytest

from doeff import EffectBase, Pass, Resume, do, handler, run, with_handlers


class Ping(EffectBase[str]):
    pass


@do
def body():
    return (yield Ping())


def test_do_handler_generator_runs_as_the_handler_stream() -> None:
    @handler
    def answer(effect: Ping, k):
        value = yield Resume(k, "pong")
        return f"after:{value}"

    assert run(with_handlers([answer], body())) == "after:pong"


def test_do_handler_that_does_not_yield_returns_its_value() -> None:
    @handler
    def abort(effect: Ping, k):
        return "aborted"

    assert run(with_handlers([abort], body())) == "aborted"


def test_do_handler_raising_before_its_first_yield_reaches_the_perform_site() -> None:
    @handler
    def refuse(effect: Ping, k):
        raise PermissionError("no")

    @do
    def guarded():
        try:
            yield Ping()
        except PermissionError as error:
            return f"caught {error}"
        return "not raised"

    assert run(with_handlers([refuse], guarded())) == "caught no"


def test_twice_decorated_handler_keeps_the_outer_wrapper_semantics() -> None:
    def forward(effect, k):
        yield Pass(effect, k)

    inner = do(forward)
    outer = do(inner)  # the outer "generator function" returns a Call, not a generator

    @handler
    def fallback(effect: Ping, k):
        return (yield Resume(k, "fallback"))

    # outer(effect, k) returns inner's program as a value: the handler's result is that
    # program object, exactly as the wrapper path evaluates it. An @do call is the single
    # instruction Call since 48d7d073 (it was an Expand before).
    result = run(with_handlers([fallback, handler(outer)], body()))
    assert type(result).__name__ == "Call"


@pytest.mark.parametrize("module", ["hy", "httpx", "doeff.cli.run_services"])
def test_import_doeff_does_not_load(module: str) -> None:
    code = f"import sys, doeff; print({module!r} in sys.modules)"
    loaded = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()
    assert loaded == "False"


def test_lazy_names_still_import() -> None:
    code = (
        "from doeff import DoeffRunContext\n"
        "from doeff_core_effects import HttpRequest, http_production_handler\n"
        "from doeff_core_effects._http_effects import HttpResponse\n"
        "import doeff_core_effects._memo_handlers_impl\n"
        "print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()
    assert out == "ok"
