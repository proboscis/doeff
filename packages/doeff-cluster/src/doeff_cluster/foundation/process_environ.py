"""子 process へ渡す環境変数の組を、この process の環境から組む生の読み(生の環境変数に触ってよいのは foundation の層 — DOEFF106)。

使い手は起動の script の入口 worker/entry/boot_wheel(doeff-vm を組む uv build の子の環境 — worker の uv の子と同じ env-mode EXTEND と
env-drop の組み方)。doeff の process の答え手(doeff_core_effects.os_process の child-environment)と同じ規則だが、こちらは doeff-vm が
入る前の venv の python で動くので、標準ライブラリだけを import する。値は log・答え・断りの文に出さない。
"""

import fnmatch
import os
from typing import Protocol

# 層 foundation の文脈と役の名乗り(DOEFF104)— 隣の child_environ.hy と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "foundation"}


class NamedValue(Protocol):
    """足す環境変数 1 つ(name = 名・value = 値)。"""

    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...


def child_environ(drop: tuple[str, ...], add: tuple[NamedValue, ...]) -> dict[str, str]:
    """子 process の環境変数の全部: この process の環境から drop の型(fnmatch・大文字小文字を区別)に当たる名を外し、add を足す(同じ名は
    add が勝つ)。答えは subprocess へそのまま渡す写像。"""
    kept = {name: value for name, value in os.environ.items() if not any(fnmatch.fnmatchcase(name, p) for p in drop)}
    return kept | {variable.name: variable.value for variable in add}
