"""Journal handler: appends `slog("jev_judge", ...)` to a file as one JSON line. Everything else is Pass.

The comparison between seimf and Jev (elapsed time, tokens, success or failure) is measured with this journal. Writing to the file happens only inside this handler
(the I/O boundary for observation).
"""

import json
import os
import time
from collections.abc import Callable
from typing import Any

from doeff_core_effects import SlogEffect

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler

JOURNAL_EVENT = "jev_judge"


def journal_handler(path: str, *, caller: str = "") -> Callable[[Any], Any]:
    """Append `slog("jev_judge", ...)` to `path` and consume it. Other slogs and effects are passed outward."""

    def append(fields: dict[str, Any]) -> None:
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "caller": caller, **fields}
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, default=repr) + "\n")
        except OSError:
            pass  # a journal that cannot be written must not stop the judgment

    @do
    def handler(effect: Any, k: Any) -> Any:
        if isinstance(effect, SlogEffect) and effect.msg == JOURNAL_EVENT:
            append(dict(effect.kwargs))
            return (yield Transfer(k, None))
        return (yield Pass(effect, k))

    return _program_handler(handler)
