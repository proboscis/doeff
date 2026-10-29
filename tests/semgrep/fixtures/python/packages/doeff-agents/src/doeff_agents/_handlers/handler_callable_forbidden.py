from collections.abc import Callable
from typing import Any

ProtocolHandler = Callable[..., Any]


def _make_protocol_handler(handler: Any) -> ProtocolHandler:
    return handler
