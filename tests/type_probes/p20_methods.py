# class の method に @do を付けた時
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Ask, Expand, do


class Service:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    @do
    def greet(self, name: str) -> Generator[Ask, Any, str]:
        extra = yield from Ask("extra")
        return f"{self.prefix}{name}{extra}"

    @staticmethod
    @do
    def static_greet(name: str) -> Generator[Ask, Any, str]:
        return name
        yield


svc = Service("hi ")
assert_type(svc.greet("a"), Expand[str, Ask])
svc.greet(1)  # expect-error
assert_type(Service.static_greet("a"), Expand[str, Ask])
