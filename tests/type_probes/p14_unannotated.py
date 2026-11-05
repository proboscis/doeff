# Written as in the README (no annotations, yield), no errors at the default strictness (pyright standard, mypy default). The result type is unknown (Any)
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
