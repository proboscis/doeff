"""据えた事のある素の関数の handler を、VM が Python を呼ばずに据える事の検(agora-redesign #2927)。

VM は handler を据える度に、その handler の据えの形(HandlerSpec — 受ける effect の型・@do の生成器の関数・末尾の
Resume の行・素通しの effect)を doeff_vm._effect_types.handler_spec から受け取る。素の関数なら答えは関数の __dict__ に
覚えてあるが、VM は毎回 Python の handler_spec を呼んで覚えた答えを引き、答えの NamedTuple から 4 つの欄を読み、行の
list を写していた(doeff-traverse の sequential は件ごとに 2 回据える — 1 回あたり Python の側だけで約 220 ns)。
据えの形は VM の型 doeff_vm.HandlerSpec にし(欄は作る時に 1 度だけ検める)、素の関数の __dict__ に覚えた答えは
VM が直に読む。規則(何を受け・何を覚えるか)は今までどおり handler_spec の 1 か所にある。
"""

import subprocess
import sys

import doeff_vm
import pytest
from doeff_vm._effect_types import handler_spec

from doeff import EffectBase, Resume, do

INSTALLS = """
import doeff_vm._effect_types as et

calls = []
resolve = et.handler_spec


def counting(handler):
    calls.append(getattr(handler, "__qualname__", ""))
    return resolve(handler)


et.handler_spec = counting  # VM は規則の関数を最初に据える時に 1 度だけ引く — それより前に差し替える

from dataclasses import dataclass

from doeff import EffectBase, Resume, do, run, with_handlers


@dataclass(frozen=True)
class Ping(EffectBase):
    n: int


@do
def answer(effect: Ping, k):
    return (yield Resume(k, effect.n + 1))


class Owner:
    @do
    def answer(self, effect: Ping, k):
        return (yield Resume(k, effect.n + 2))


@do
def ask(n):
    return (yield Ping(n))


@do
def body():
    total = 0
    for n in range(50):
        total += yield with_handlers([answer], ask(n))
    owner = Owner()
    for n in range(3):
        total += yield with_handlers([owner.answer], ask(n))
    return total


print(run(body()), calls.count("answer"), calls.count("Owner.answer"))
"""


@do
def plain(effect: EffectBase, k):
    return (yield Resume(k, None))


def test_the_spec_of_a_handler_is_the_vms_own_type() -> None:
    spec = handler_spec(plain)
    assert isinstance(spec, doeff_vm.HandlerSpec)
    assert spec.effect_types is None
    assert spec.generator_function is plain.__dict__["__doeff_generator_function__"]
    assert spec.passed is None
    assert handler_spec(plain) is spec, "素の関数の答えは関数に覚える"


def test_a_plain_function_installed_again_is_installed_without_calling_python() -> None:
    done = subprocess.run([sys.executable, "-c", INSTALLS], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    total, plain_calls, method_calls = done.stdout.split()
    assert int(total) == sum(n + 1 for n in range(50)) + sum(n + 2 for n in range(3))
    assert int(plain_calls) == 1, "素の関数は最初に据える時だけ規則を呼び、2 回目からは関数に覚えた答えを VM が直に読む"
    assert int(method_calls) == 3, "束ねた method は覚えない(別の instance と共有しない)— 据える度に規則を呼ぶ"


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        (("not a tuple", None, (), None), "effect_types"),
        ((None, None, ("12",), None), "tail_resume_lines"),
        ((None, None, (), ((int,),)), "passed"),
        ((None, None, (), ((int,), "x")), "passed"),
    ],
)
def test_a_malformed_spec_is_refused_when_it_is_made(fields: tuple[object, ...], named: str) -> None:
    with pytest.raises(TypeError, match=named):
        doeff_vm.HandlerSpec(*fields)
