"""with_handlers / stacked_handlers / handler() は、handler でない物を組む時に名指しで断る(agora-redesign #3724・ADR-DOE-CORE-003)。

handler は ``@handler`` を付けた def で、``@handler`` がそれを ``@do`` 関数にする(持ち主の決定 2026-10-20 13:07・16:26 JST)。
``with_handlers`` と ``stacked_handlers`` は印 ``_doeff_is_handler_fn`` の無い物を「not a handler」で断り、中で ``handler()`` に通さない。
``handler()`` は def と ``@do`` 関数(とその partial・bound method)を受け、ほか(lambda・素の method・callable の instance)を
「not a handler body」で断る。def でも引数を 1 つしか
取らない物(Program → Program の取り違え)は、前と同じく組む時に断る — 最初の effect で 2 引数で呼ばれて落ちる時期がテストの
時刻の運で揺れ、失敗ケースのテストが緑に数えられうるため(agora-controllers の日次 t548 の deaf-records-handler)。

判定は属性の読みだけで行う(``inspect.signature`` を呼ばない — with_handlers は熱い道)。
"""

import functools
import inspect
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import doeff_hy  # noqa: F401  # Hy の import hook(http の handler と検の部品は Hy)
import pytest
import tests.effects.http_request_support as http_support
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.http_handlers import http_fixture_handler, http_production_handler
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Pass, Pure, Resume, do, handler, run, stacked_handlers, with_handlers

REFUSAL = r"Program -> Program function without the handler marker"
NOT_A_HANDLER = r"is not a handler"
NOT_BODY = r"is not a handler body"


@dataclass(frozen=True)
class Ping(EffectBase):
    value: str


@handler
def outer(effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}>outer"))
    yield Pass(effect, k)


@handler
def relay_inner(effect, k):
    """Ping を外へ 1 度送り直してから答える — 外と内のどちらが先に答えたかが答えの綴りに残る。"""
    if isinstance(effect, Ping):
        answer = yield Ping(f"{effect.value}>inner")
        return (yield Resume(k, answer))
    yield Pass(effect, k)


@do
def body():
    return (yield Ping("start"))


def wrap_program(program):
    """印の無い Program → Program の関数(取り違えの形)。"""
    return program


@do
def wrap_program_with_do(program):
    return (yield program)


def answer_ping(effect, k):
    if isinstance(effect, Ping):
        return Resume(k, f"{effect.value}>plain")
    return Pass(effect, k)


def forward_all(*args):
    return outer(*args)


@do
def prefixed(prefix, effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}>{prefix}"))
    yield Pass(effect, k)


@do
def suffixed(effect, k, suffix="suffix"):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}>{suffix}"))
    yield Pass(effect, k)


class PrefixHandler:
    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, effect, k):
        return prefixed(self.prefix, effect, k)

    def method(self, effect, k):
        return prefixed(self.prefix, effect, k)

    def wrap(self, program):
        return program


class MarkedPassThrough:
    """印つきの callable の instance — ``__name__`` を持たない installer も束ねられる事を確かめるため。"""

    _doeff_is_handler_fn = True

    def __call__(self, program):
        return program


# --- 組む時に断る形 ---------------------------------------------------------------------------


def test_with_handlers_refuses_an_unmarked_lambda_before_run() -> None:
    with pytest.raises(TypeError, match=r"^with_handlers: .*<lambda> " + NOT_A_HANDLER):
        with_handlers([lambda program: program], body())


def test_handler_refuses_an_unmarked_lambda() -> None:
    with pytest.raises(TypeError, match=r"^handler: .*<lambda> " + NOT_BODY):
        handler(lambda program: program)


def test_refusal_names_the_function_and_the_public_ways() -> None:
    with pytest.raises(TypeError, match=r"^with_handlers: wrap_program " + NOT_A_HANDLER) as caught:
        with_handlers([outer, wrap_program], body())
    message = str(caught.value)
    assert "@handler" in message
    assert "defhandler" in message


def test_handler_refuses_a_do_function_that_takes_one_positional() -> None:
    with pytest.raises(TypeError, match=r"^handler: " + REFUSAL + r": .*wrap_program_with_do"):
        handler(wrap_program_with_do)


@pytest.mark.parametrize(
    "not_a_def",
    [
        PrefixHandler("m:").wrap,
        PrefixHandler("m").method,
        PrefixHandler("c"),
    ],
    ids=["bound-method-one-positional", "bound-method", "callable-instance"],
)
def test_handler_refuses_what_is_not_a_def(not_a_def) -> None:
    with pytest.raises(TypeError, match=NOT_BODY):
        handler(not_a_def)
    with pytest.raises(TypeError, match=NOT_A_HANDLER):
        with_handlers([not_a_def], body())


@pytest.mark.parametrize("plain_def", [answer_ping, forward_all], ids=["plain-def", "star-args"])
def test_handler_takes_a_def_and_with_handlers_refuses_it_unmarked(plain_def) -> None:
    assert getattr(handler(plain_def), "_doeff_is_handler_fn", False) is True
    with pytest.raises(TypeError, match=NOT_A_HANDLER):
        with_handlers([plain_def], body())


def test_a_def_returning_its_resume_unrun_is_refused_when_it_runs() -> None:
    with pytest.raises(TypeError, match=r"answer_ping returned Resume\(\.\.\.\) without yielding it"):
        run(handler(answer_ping)(body()))


@pytest.mark.parametrize(
    ("bound", "expected"),
    [
        (functools.partial(prefixed, "p"), "start>p"),
        (functools.partial(suffixed, suffix="kw"), "start>kw"),
    ],
    ids=["partial", "partial-keyword-after-k"],
)
def test_handler_takes_a_partial_of_a_do_function(bound, expected) -> None:
    # A partial of a @do function is still a @do Kleisli with some arguments bound.
    assert run(handler(bound)(body())) == expected
    with pytest.raises(TypeError, match=NOT_A_HANDLER):
        with_handlers([bound], body())


@pytest.mark.parametrize(
    "one_argument",
    [
        functools.partial(prefixed, "p:", Ping("bound")),
        functools.partial(functools.partial(prefixed, "p:"), Ping("bound")),
    ],
    ids=["partial-positional", "nested-partial"],
)
def test_handler_refuses_a_partial_left_with_one_positional(one_argument) -> None:
    with pytest.raises(TypeError, match=r"^handler: " + REFUSAL):
        handler(one_argument)


def test_with_handlers_refuses_a_do_function_without_the_handler_mark() -> None:
    with pytest.raises(TypeError, match=r"^with_handlers: prefixed " + NOT_A_HANDLER):
        with_handlers([prefixed], body())


# --- 通る形: @handler を付けた @do 関数 ------------------------------------------------------------


def test_a_handler_installs_through_every_public_way() -> None:
    assert run(outer(body())) == "start>outer"
    assert handler(outer) is outer
    assert run(with_handlers([outer], body())) == "start>outer"
    assert run(stacked_handlers(outer)(body())) == "start>outer"


def test_http_production_handler_installs_and_closes_its_client() -> None:
    client = http_support.FakeAsyncClient([])
    production = http_production_handler(client_factory=lambda: client)

    assert handler(production) is production
    assert run(scheduled(with_handlers([await_handler(), production], Pure("ok")))) == "ok"
    assert client.close_calls == 1


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_http_fixture_handler_installs_in_both_modes(
    tmp_path: Path, mode: Literal["record", "replay"]
) -> None:
    fixture = http_fixture_handler(
        tmp_path / "fixtures.pkl",
        mode=mode,
        client_factory=lambda: http_support.FakeAsyncClient([]),
    )
    assert handler(fixture) is fixture
    assert run(scheduled(with_handlers([await_handler(), fixture], Pure("ok")))) == "ok"


# --- 束ねる公開の口 -----------------------------------------------------------------------------


def test_stacked_handlers_runs_the_first_outermost_and_the_last_innermost() -> None:
    stacked = stacked_handlers(outer, relay_inner)

    # 内の relay_inner が先に Ping を受け、外の outer へ送り直す。
    assert run(with_handlers([stacked], body())) == "start>inner>outer"
    assert run(stacked(body())) == "start>inner>outer"
    assert run(with_handlers([outer, relay_inner], body())) == "start>inner>outer"
    assert run(stacked_handlers(relay_inner, outer)(body())) == "start>outer"


def test_stacked_handlers_is_a_marked_installer_called_with_the_program_only() -> None:
    stacked = stacked_handlers(handler(outer), relay_inner)

    assert getattr(stacked, "_doeff_is_handler_fn", False) is True
    assert handler(stacked) is stacked
    # 型の ProgramHandler は関数の名を宣言しないので、束ねが関数である事を確かめてから名を読む
    assert isinstance(stacked, types.FunctionType)
    assert stacked.__name__ == "stacked_handlers"
    assert "outer" in (stacked.__doc__ or "")
    assert "relay_inner" in (stacked.__doc__ or "")
    # 印が無ければ with_handlers が (effect, k) で呼び、最初の effect で TypeError になる。
    assert run(with_handlers([outer, stacked], body())) == "start>inner>outer"

    with_instance = stacked_handlers(MarkedPassThrough(), outer)
    assert "MarkedPassThrough" in (with_instance.__doc__ or "")
    assert run(with_instance(body())) == "start>outer"


def test_empty_stacked_handlers_is_identity() -> None:
    program = Pure("ok")

    assert stacked_handlers()(program) is program
    assert with_handlers([stacked_handlers()], program) is program


def test_stacked_handlers_refuses_an_unmarked_program_function_when_bundling() -> None:
    with pytest.raises(TypeError, match=r"^stacked_handlers: wrap_program " + NOT_A_HANDLER):
        stacked_handlers(outer, wrap_program)


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_stacked_handlers_refuses_a_value_that_is_not_callable(invalid) -> None:
    with pytest.raises(TypeError, match=r"^stacked_handlers: handler must be callable, got "):
        stacked_handlers(outer, invalid)


def test_stacked_handlers_nest() -> None:
    nested = stacked_handlers(stacked_handlers(outer), stacked_handlers(relay_inner))

    assert run(nested(body())) == "start>inner>outer"


# --- 束の宣言の印: 中の全部が __doeff_handles__ / __doeff_effects__ を宣言する時だけ和を付ける(vg-w39 の読み) ----------


@dataclass(frozen=True)
class Pong(EffectBase):
    value: str


@dataclass(frozen=True)
class Tick(EffectBase):
    value: str


def declared_dispatcher(handles: tuple[type, ...], effects: tuple[type, ...]):
    """答える効果と出す効果を印で宣言した生の dispatcher(節を静的に読めない handler の宣言 — http の handler と同じ形)。"""

    @handler
    def dispatch(effect, k):
        yield Pass(effect, k)

    marks = cast(Any, dispatch)  # 宣言の印は installer の属性(http の handler と同じ置き場)
    marks.__doeff_handles__ = handles
    marks.__doeff_effects__ = effects
    return dispatch


def test_stacked_handlers_declares_the_union_of_its_declared_handlers() -> None:
    bundle = stacked_handlers(declared_dispatcher((Ping,), (Pong,)), declared_dispatcher((Pong,), (Tick,)))

    assert bundle.__doeff_handles__ == (Ping, Pong)
    # 外の Ping の handler は Pong を出し(束の外へ)、内の Pong の handler は Tick を出す(外の Ping の handler は Tick に答えない)
    assert bundle.__doeff_effects__ == (Pong, Tick)


def test_stacked_handlers_drops_effects_an_outer_handler_in_the_bundle_answers() -> None:
    # 内の handler が出す Pong は、束の中の外の handler が答える = 束の外へ出ない
    bundle = stacked_handlers(declared_dispatcher((Pong,), (Tick,)), declared_dispatcher((Ping,), (Pong,)))

    assert bundle.__doeff_handles__ == (Pong, Ping)
    assert bundle.__doeff_effects__ == (Tick,)


def test_stacked_handlers_stays_unreadable_when_one_handler_does_not_declare() -> None:
    bundle = stacked_handlers(declared_dispatcher((Ping,), (Pong,)), outer)

    assert not hasattr(bundle, "__doeff_handles__")
    assert not hasattr(bundle, "__doeff_effects__")


# --- 速さの見張り: 判定は属性の読みだけ ---------------------------------------------------------


def test_installing_does_not_call_inspect_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_signature(*_args, **_kwargs):
        raise AssertionError("with_handlers / handler() must not call inspect.signature")

    monkeypatch.setattr(inspect, "signature", broken_signature)
    installers = [handler(outer), stacked_handlers(outer), outer]
    with_handlers(installers, body())
    stacked_handlers(*installers)
    with pytest.raises(TypeError, match=NOT_A_HANDLER):
        with_handlers([wrap_program], body())
    handler(answer_ping)
    with pytest.raises(TypeError, match=NOT_BODY):
        handler(PrefixHandler("c"))
    with pytest.raises(TypeError, match=REFUSAL):
        handler(wrap_program_with_do)
