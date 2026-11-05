# Catch a forgotten yield (value unused) with pyright's reportUnusedCallResult (the recommendation of docs/23). mypy has no equivalent rule
# pyright: reportUnusedCallResult=true
from collections.abc import Generator
from typing import Any

from doeff import Ask, do


@do
def prog() -> Generator[Ask, Any, None]:
    Ask("k")  # expect-error[pyright] (forgotten yield)
    _ = yield from Ask("k")
