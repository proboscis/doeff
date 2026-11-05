# A forgotten yield -- if the value is used, it becomes an error at that place (a forgotten yield whose value is unused is caught by p25's rule)
from collections.abc import Generator
from typing import Any

from doeff import Ask, do


@do
def prog() -> Generator[Ask, Any, str]:
    Ask("k")
    name = Ask("k")
    return name  # expect-error (returning Ask as a str)
