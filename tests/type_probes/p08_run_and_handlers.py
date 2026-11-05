# Result type after applying run and handlers; passing a non-program to a handler (gap 2 — fixed)
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Program, do, run
from doeff_core_effects import reader, state, writer
from doeff_core_effects import scheduled


@do
def prog() -> Generator[Ask, Any, int]:
    v = yield from Ask("k")
    return int(v)


assert_type(run(prog()), int)
assert_type(run(scheduled(prog())), int)
assert_type(reader(env={"k": 1})(prog()), Program[int, Any])
assert_type(run(reader(env={"k": 1})(prog())), int)
x: str = run(reader(env={"k": 1})(prog()))  # expect-error (int to str)
writer(prog)  # expect-error (passed the function without creating a program — runtime TypeError)
state()(42)  # expect-error (value that is not a program)
run(prog)  # expect-error (forgot to call)
run(42)  # expect-error
