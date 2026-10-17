# yield の付け忘れ — 値を使えば、その所で誤りになる(値を使わない付け忘れは p25 の規則で捕まえる)
from collections.abc import Generator
from typing import Any

from doeff import Ask, do


@do
def prog() -> Generator[Ask, Any, str]:
    Ask("k")
    name = Ask("k")
    return name  # expect-error (Ask を str として返す)
