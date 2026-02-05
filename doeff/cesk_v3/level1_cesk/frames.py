from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Generator

_frame_id_counter = itertools.count(1)


def _next_frame_id() -> int:
    return next(_frame_id_counter)


@dataclass(frozen=True)
class ReturnFrame:
    generator: Generator[Any, Any, Any]
    frame_id: int = field(default_factory=_next_frame_id, compare=False)
