"""Test that the VM installs a previously installed plain-function handler without calling Python.

Each time the VM installs a handler, it receives that handler's install shape (HandlerSpec - the effect types it accepts, the @do generator function, the trailing
Resume line, and the pass-through effects) from doeff_vm._effect_types.handler_spec. For a plain function the answer is
remembered in the function's __dict__, but the VM used to call Python's handler_spec every time to fetch the remembered answer, read 4 fields from the NamedTuple answer, and copy the line
list (doeff-traverse's sequential installs twice per item - about 220 ns per install on the Python side alone).
The install shape is now the VM type doeff_vm.HandlerSpec (fields are validated only once, at construction), and the answer remembered in a plain function's __dict__ is
read directly by the VM. The rules (what to accept and what to remember) remain in one place, handler_spec, as before.
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


et.handler_spec = counting  # The VM fetches the rule function only once, at the first install - replace it before that

from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run, with_handlers


@dataclass(frozen=True)
class Ping(EffectBase):
    n: int


@handler
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
        total += yield with_handlers([handler(owner.answer)], ask(n))
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
    assert handler_spec(plain) is spec, "a plain function's answer is remembered on the function"


def test_a_plain_function_installed_again_is_installed_without_calling_python() -> None:
    done = subprocess.run([sys.executable, "-c", INSTALLS], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    total, plain_calls, method_calls = done.stdout.split()
    assert int(total) == sum(n + 1 for n in range(50)) + sum(n + 2 for n in range(3))
    assert int(plain_calls) == 1, "a plain function calls the rule only at the first install; from the second time on the VM reads the answer remembered on the function directly"
    assert int(method_calls) == 3, "a bound method is not remembered (not shared with other instances) - the rule is called on every install"


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
