"""doeff-hy の置き場のうち環境変数が名指す物 — この package で環境を読む module はここだけ。

doeff-hy-check の cache の置き場は、検査の道具(CLI・pytest の収集)がどの Program よりも前に決めるので、
Ask ではなく環境変数で受ける。この module は読むだけ — 関数は 1 つの変数の素の値(か XDG の cache の根)を返し、
どの置き場を使うかは呼び手が決める。読みは os.environ を直に読まず、ReadEnvironment の効果を本物の答え手
subprocess_handler の下で問う(agora-redesign #3012 — 環境変数の読みは foundation の handler の中だけ)。
"""

from pathlib import Path


def cache_home() -> Path:
    """XDG の cache の根($XDG_CACHE_HOME、無ければ ~/.cache)— 本物の答え手の下の ReadEnvironment で読む。"""
    return Path(_read("XDG_CACHE_HOME") or Path.home() / ".cache")


def check_cache_setting() -> str | None:
    """$DOEFF_HY_CHECK_CACHE の素の値(doeff-hy-check の展開の保存先の dir)— 無い・空なら None。日次の検証のように HOME を
    走りごとに替える呼び手が、走りをまたいで残る dir を名指すため(agora-redesign #3863)。"""
    return _read("DOEFF_HY_CHECK_CACHE") or None


def _read(name: str) -> str | None:
    """環境変数 1 つの素の値(無ければ None)— 本物の答え手 subprocess_handler の下の ReadEnvironment で読む。"""
    # doeff-core-effects は doeff-hy の macro を読むので、この module を読み込む時点ではなく呼ばれた時に読む。
    from doeff import run, with_handlers
    from doeff_core_effects.os_process import subprocess_handler
    from doeff_core_effects.process_effects import ReadEnvironment

    entries = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in entries if entry.name == name), None)
