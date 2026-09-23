"""effect 1 回の費用と import の床を下げた変更の回帰テスト(2026-09-23)。

- ``@do`` の handler は、VM が元の生成器関数を直接呼んで handler の stream にする(wrapper が
  effect ごとに作っていた Expand / Apply / Pure / Callable を作らない)。結果は従来の評価と同じ。
- ``import doeff`` は Hy・httpx・CLI を読み込まない(使う時に読む)。
"""

import subprocess
import sys

import pytest

from doeff import EffectBase, Pass, Resume, do, run, with_handlers


class Ping(EffectBase[str]):
    pass


@do
def body():
    return (yield Ping())


def test_do_handler_generator_runs_as_the_handler_stream() -> None:
    @do
    def answer(effect: Ping, k):
        value = yield Resume(k, "pong")
        return f"after:{value}"

    assert run(with_handlers([answer], body())) == "after:pong"


def test_do_handler_that_does_not_yield_returns_its_value() -> None:
    @do
    def abort(effect: Ping, k):
        return "aborted"

    assert run(with_handlers([abort], body())) == "aborted"


def test_do_handler_raising_before_its_first_yield_reaches_the_perform_site() -> None:
    @do
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
    outer = do(inner)  # the outer "generator function" returns an Expand, not a generator

    @do
    def fallback(effect: Ping, k):
        return (yield Resume(k, "fallback"))

    # outer(effect, k) returns inner's program as a value: the handler's result is that
    # Expand object, exactly as the wrapper path evaluates it.
    result = run(with_handlers([fallback, outer], body()))
    assert type(result).__name__ == "Expand"


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
        "from doeff_core_effects.http_effects import HttpResponse\n"
        "import doeff_core_effects._memo_handlers_impl\n"
        "print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()
    assert out == "ok"
