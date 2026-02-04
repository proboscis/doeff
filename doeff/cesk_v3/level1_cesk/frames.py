from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generator


@dataclass(frozen=True)
class ReturnFrame:
    generator: Generator[Any, Any, Any]
