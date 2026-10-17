# yield の付け忘れ(値を使わない)を pyright の reportUnusedCallResult で捕まえる(docs/23 の勧め)。mypy に同じ規則は無い
# pyright: reportUnusedCallResult=true
from collections.abc import Generator
from typing import Any

from doeff import Ask, do


@do
def prog() -> Generator[Ask, Any, None]:
    Ask("k")  # expect-error[pyright] (付け忘れ)
    _ = yield from Ask("k")
