"""doeff-hy の置き場のうち環境変数が名指す物 — この package で環境を読む module はここだけ。

doeff-hy-check の cache の置き場は、検査の道具(CLI・pytest の収集)がどの Program よりも前に決めるので、
Ask ではなく環境変数で受ける。この module は読むだけ — 関数は 1 つの変数の素の値(か XDG の cache の根)を返し、
どの置き場を使うかは呼び手が決める。読みは os.environ を直に読まず、ReadEnvironment の効果を本物の答え手
subprocess_handler の下で問う(agora-redesign #3012 — 環境変数の読みは foundation の handler の中だけ)。
"""

from pathlib import Path


def cache_home() -> Path:
    """XDG の cache の根($XDG_CACHE_HOME、無ければ ~/.cache)— 本物の答え手の下の ReadEnvironment で読む。"""
    # doeff-core-effects は doeff-hy の macro を読むので、この module を読み込む時点ではなく呼ばれた時に読む。
    from doeff import run, with_handlers
    from doeff_core_effects.os_process import subprocess_handler
    from doeff_core_effects.process_effects import ReadEnvironment

    entries = run(with_handlers([subprocess_handler], ReadEnvironment(("XDG_CACHE_HOME",))))
    configured = next((entry.value for entry in entries if entry.name == "XDG_CACHE_HOME"), None)
    return Path(configured or Path.home() / ".cache")
