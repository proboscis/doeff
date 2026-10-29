# README の書き方(注記なし・yield)のまま、既定の強さ(pyright standard・mypy の既定)で誤りが出ない。結果の型は分からない(Any)
# pyright: standard
# mypy: allow-untyped-defs, allow-untyped-calls
from typing import Any, assert_type

from doeff import Ask, do, run
from doeff_core_effects import reader


@do
def prog():
    greeting = yield Ask("greeting")
    return greeting


assert_type(run(reader(env={"greeting": "hello"})(prog())), Any)
