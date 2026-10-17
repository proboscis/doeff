# with_handlers と stacked_handlers は結果の型を保つ
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Program, do, run, stacked_handlers, with_handlers
from doeff_core_effects.handlers import reader, state


@do
def prog() -> Generator[Ask, Any, int]:
    return int((yield from Ask("k")))


assert_type(with_handlers([reader(env={"k": 1}), state()], prog()), Program[int, Any])
assert_type(run(with_handlers([reader(env={"k": 1})], prog())), int)
both = stacked_handlers(reader(env={"k": 1}), state())
assert_type(both(prog()), Program[int, Any])
