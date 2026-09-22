"""記録の handler — `slog("jev_judge", …)` を 1 行の JSON として file に追記する。他は Pass。

seimf と Jev の比較(所要・トークン・成否)はこの記録で測る。file への書き込みはこの handler
の中だけ(観測の I/O 境界)。
"""

import json
import os
import time
from collections.abc import Callable
from typing import Any

from doeff_core_effects.effects import SlogEffect

from doeff import Pass, Transfer, do
from doeff import handler as _program_handler

JOURNAL_EVENT = "jev_judge"


def journal_handler(path: str, *, caller: str = "") -> Callable[[Any], Any]:
    """`slog("jev_judge", …)` を `path` に追記して消費する。それ以外の slog・effect は外へ Pass。"""

    def append(fields: dict[str, Any]) -> None:
        line = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "caller": caller, **fields}
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, default=repr) + "\n")
        except OSError:
            pass  # 記録が書けないことで判定を止めない

    @do
    def handler(effect: Any, k: Any) -> Any:
        if isinstance(effect, SlogEffect) and effect.msg == JOURNAL_EVENT:
            append(dict(effect.kwargs))
            return (yield Transfer(k, None))
        return (yield Pass(effect, k))

    return _program_handler(handler)
