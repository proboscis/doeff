"""EffectBase(別名 doeff.Effect)が、どこにも残らない引数を TypeError で断る事の検(取り組み doeff-publish・
区切り ms-4aca097efa40 の card 7)。

失敗ケース(docs の例を 0.5.0 で run まで走らせた報告 2026-10-17-doeff-docs-137-run.html の MARKERS L37・L245):
docs/MARKERS.md の例 `yield Effect("fetch", url="...")` は、0.5.0 で何も言わずに走る。EffectBase の `__new__` は
`__init__` を持つ部分の class(dataclass の effect)のために引数を全部受けて捨て、EffectBase 自身は `__init__` も
`__dict__` も持たないので、名も url も消える。handler には中身の無い EffectBase が届き、fetch・process・save の 3 つを
区別できない。

断る条件は CPython の object と同じ「引数を受ける `__init__` が無い」: EffectBase を直に呼んだ時と、`__init__` を
持たない部分の class を引数つきで呼んだ時。`__init__` を持つ部分の class(dataclass・自前の `__init__`)と、
引数の無い呼び(`Skip()`・pickle の戻し)は今まで通り通す。
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass

import doeff_vm
import pytest

from doeff import Effect, EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


class Marker(EffectBase):
    """欄も `__init__` も無い effect(doeff-traverse の Skip と同じ形)。"""


class Payload(EffectBase):
    """自前の `__init__` で欄を持つ effect(13 章の API の一覧の形)。"""

    def __init__(self, payload: object) -> None:
        super().__init__()
        self.payload = payload


class Routed(EffectBase):
    """自前の `__new__` で作る class を選び、`__init__` で欄を持つ effect(doeff-events の PublishEffect の形)。"""

    def __new__(cls, event: object) -> Routed:
        return EffectBase.__new__(cls)

    def __init__(self, event: object) -> None:
        super().__init__()
        self.event = event


def test_effect_with_a_name_and_arguments_is_refused() -> None:
    # MARKERS の旧い例の形そのもの。
    with pytest.raises(TypeError, match=r"EffectBase\(\) takes no arguments"):
        Effect("fetch", url="api.example.com")


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [(("fetch",), {}), ((), {"url": "api.example.com"}), (("save",), {"data": [1]})],
    ids=["positional", "keyword", "both"],
)
def test_effect_base_refuses_every_argument_it_would_drop(
    args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    with pytest.raises(TypeError, match=r"EffectBase\(\) takes no arguments"):
        doeff_vm.EffectBase(*args, **kwargs)


def test_the_refusal_says_where_the_arguments_belong() -> None:
    with pytest.raises(TypeError) as raised:
        Effect("fetch", url="api.example.com")
    message = str(raised.value)
    # 読み手が次に書く物 = 欄を持つ effect の class と、その作り方。
    assert "@dataclass(frozen=True)" in message, message
    assert "(EffectBase)" in message, message


def test_subclass_without_init_refuses_arguments_and_names_itself() -> None:
    with pytest.raises(TypeError, match=r"Marker\(\) takes no arguments"):
        Marker("fetch")
    with pytest.raises(TypeError, match=r"Marker\(\) takes no arguments"):
        Marker(url="api.example.com")


def test_calls_without_arguments_still_make_effects() -> None:
    assert type(doeff_vm.EffectBase()) is doeff_vm.EffectBase
    assert isinstance(Marker(), Marker)


def test_subclasses_with_an_init_keep_their_arguments() -> None:
    assert Fetch(url="api.example.com").url == "api.example.com"
    assert Fetch("api.example.com").url == "api.example.com"
    assert Payload({"id": 1}).payload == {"id": 1}
    assert Routed("ping").event == "ping"


def test_pickle_round_trip_still_rebuilds_effects() -> None:
    # EffectBase の __reduce_ex__ は copyreg.__newobj__(cls) で引数無しに作り直す。
    assert pickle.loads(pickle.dumps(Fetch(url="api.example.com"))) == Fetch(url="api.example.com")
    assert pickle.loads(pickle.dumps(Payload("x"))).payload == "x"
    assert isinstance(pickle.loads(pickle.dumps(Marker())), Marker)


def test_handler_reads_the_fields_of_an_effect_class() -> None:
    # 直した MARKERS の例の形: 引数は effect の class の欄に残り、handler が読める。
    @do
    def fetch_data():
        return (yield Fetch(url="api.example.com"))

    @handler
    @do
    def fake_fetch(effect: Fetch, k):
        return (yield Resume(k, f"response from {effect.url}"))

    assert run(handler(fake_fetch)(fetch_data())) == "response from api.example.com"
