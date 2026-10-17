# run と handler を付けた後の結果の型・handler に program でない物を渡した時(抜け 2 — 直った)
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Program, do, run
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled


@do
def prog() -> Generator[Ask, Any, int]:
    v = yield from Ask("k")
    return int(v)


assert_type(run(prog()), int)
assert_type(run(scheduled(prog())), int)
assert_type(reader(env={"k": 1})(prog()), Program[int, Any])
assert_type(run(reader(env={"k": 1})(prog())), int)
x: str = run(reader(env={"k": 1})(prog()))  # expect-error (int を str へ)
writer(prog)  # expect-error (program を作らずに関数を渡した — 実行時 TypeError)
state()(42)  # expect-error (program でない値)
run(prog)  # expect-error (呼び忘れ)
run(42)  # expect-error
