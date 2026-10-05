"""with_handlers / handler() は、handler の印の無い Program → Program の関数を組む時に名指しで断る(agora-redesign #3724)。

印 ``_doeff_is_handler_fn`` の無い callable は生の (effect, k) の dispatcher と読まれ、``handler()`` で包まれる。
Program → Program の関数を印なしで渡す取り違えは、直す前は組む時に何も言わず、最初の effect で 2 引数で
呼ばれて TypeError になった。落ちる時期がテストの時刻の運で揺れるので、失敗ケースのテストが「落ちて画面が
暗い」のを緑と数えうる(agora-controllers の日次 t548 の deaf-records-handler)。複数の handler を 1 つの
installer に束ねる時は、印を自分で付けずに公開の口 ``stacked_handlers`` を使う。

判定は属性の読みだけで行う(``inspect.signature`` を呼ばない — with_handlers は熱い道)。callable の instance・
builtin など ``__code__`` を読めない形は判じずに今どおり通す。
"""

import functools
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import doeff_hy  # noqa: F401  # Hy の import hook(http の handler と検の部品は Hy)
import pytest
import tests.effects.http_request_support as http_support
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.http_handlers import http_fixture_handler, http_production_handler
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Pass, Pure, Resume, do, handler, run, stacked_handlers, with_handlers

REFUSAL = r"Program -> Program function without the handler marker"


@dataclass(frozen=True)
class Ping(EffectBase):
    value: str


@do
def outer(effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}>outer"))
    yield Pass(effect, k)


@do
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
    with pytest.raises(TypeError, match=REFUSAL + r": .*<lambda>"):
        with_handlers([lambda program: program], body())


def test_handler_refuses_an_unmarked_lambda() -> None:
    with pytest.raises(TypeError, match=r"^handler: " + REFUSAL):
        handler(lambda program: program)


def test_refusal_names_the_function_and_the_two_public_ways() -> None:
    with pytest.raises(TypeError, match=r"^with_handlers: " + REFUSAL) as caught:
        with_handlers([outer, wrap_program], body())
    message = str(caught.value)
    assert "wrap_program" in message
    assert "defhandler" in message
    assert "stacked_handlers" in message


@pytest.mark.parametrize(
    "one_argument",
    [
        wrap_program_with_do,
        PrefixHandler("m:").wrap,
        functools.partial(prefixed, "p:", Ping("bound")),
        functools.partial(suffixed, k=None),
        functools.partial(functools.partial(prefixed, "p:"), Ping("bound")),
    ],
    ids=["do-function", "bound-method", "partial-positional", "partial-keyword", "nested-partial"],
)
def test_refuses_every_judged_shape_that_takes_one_positional(one_argument) -> None:
    with pytest.raises(TypeError, match=REFUSAL):
        with_handlers([one_argument], body())


# --- 今どおり通る形 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dispatcher", "expected"),
    [
        (answer_ping, "start>plain"),
        (forward_all, "start>outer"),
        (outer, "start>outer"),
        (functools.partial(prefixed, "p"), "start>p"),
        (functools.partial(suffixed, suffix="kw"), "start>kw"),
        (PrefixHandler("m").method, "start>m"),
        (PrefixHandler("c"), "start>c"),
    ],
    ids=["plain-def", "star-args", "do-function", "partial", "partial-keyword-after-k",
         "bound-method", "callable-instance"],
)
def test_raw_dispatchers_still_install(dispatcher, expected) -> None:
    assert run(with_handlers([dispatcher], body())) == expected
    assert run(handler(dispatcher)(body())) == expected


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
    with pytest.raises(TypeError, match=r"^stacked_handlers: " + REFUSAL + r": .*wrap_program"):
        stacked_handlers(outer, wrap_program)


@pytest.mark.parametrize("invalid", [None, 7, object()])
def test_stacked_handlers_refuses_a_value_that_is_not_callable(invalid) -> None:
    with pytest.raises(TypeError, match=r"^stacked_handlers: handler must be callable, got "):
        stacked_handlers(outer, invalid)


def test_stacked_handlers_nest() -> None:
    nested = stacked_handlers(stacked_handlers(outer), stacked_handlers(relay_inner))

    assert run(nested(body())) == "start>inner>outer"


# --- 速さの見張り: 判定は属性の読みだけ ---------------------------------------------------------


def test_installing_does_not_call_inspect_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_signature(*_args, **_kwargs):
        raise AssertionError("with_handlers / handler() must not call inspect.signature")

    monkeypatch.setattr(inspect, "signature", broken_signature)
    installers = [
        handler(outer),
        stacked_handlers(outer),
        outer,
        answer_ping,
        forward_all,
        functools.partial(prefixed, "p"),
        PrefixHandler("m").method,
        PrefixHandler("c"),
    ]
    with_handlers(installers, body())
    stacked_handlers(*installers)
    with pytest.raises(TypeError, match=REFUSAL):
        with_handlers([wrap_program], body())
