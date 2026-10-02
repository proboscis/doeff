"""doeff-hy の置き場のうち環境変数が名指す物 — この package で環境を読む module はここだけ。

doeff-hy-check の cache の置き場は、検査の道具(CLI・pytest の収集)がどの Program よりも前に決めるので、
Ask ではなく環境変数で受ける。この module は読むだけ — 関数は 1 つの変数の素の値(か XDG の cache の根)を返し、
どの置き場を使うかは呼び手が決める。``packages/doeff-hy/architecture.hy`` がこの module を名指して DOEFF004 から
外す(agora-redesign #2860)。この package のほかの module は今までどおり環境を読めない。
"""

import os
from pathlib import Path


def cache_home() -> Path:
    """XDG の cache の根($XDG_CACHE_HOME、無ければ ~/.cache)。"""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
