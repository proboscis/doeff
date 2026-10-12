"""doeff-hy の置き場のうち環境変数が名指す物 — この package で環境を読む module はここだけ。

doeff-hy-check の cache の置き場(と pyright の thread の数)は、検査の道具(CLI・pytest の収集)がどの Program よりも前に決めるので、
Ask ではなく環境変数で受ける。この module は読むだけ — 関数は 1 つの変数の素の値(か XDG の cache の根)を返し、
どの置き場・上限を使うかは呼び手が決める。読みは os.environ を直に読まず、設定の Ask を環境変数から答える foundation の答え手
env_var_ask の下で問う(agora-redesign #3012 — 環境変数の読みは foundation の handler の中だけ・card ki-efa3d187b136)。
機体の検の枠の正本が名指して読む変数と、名乗りに足す会話の id(doeff_hy.check_slot・card ki-26ebd3f636e0)も同じ訳でここで読む。
"""

from collections.abc import Iterator, Mapping
from pathlib import Path

#: doeff-hy-check が撃つ pyright の thread の数(pyright の --threads)を呼び手ごとに替える環境変数(値の判じは
#: doeff_hy.static_check.pyright_threads)。
PYRIGHT_THREADS_ENV = "DOEFF_HY_CHECK_PYRIGHT_THREADS"

#: agora が会話の process に渡す会話の id — doeff-hy-check が機体の検の枠の保持者の名乗りに足す(doeff_hy.check_slot)。
CONVERSATION_ENV = "AGORA_CONVERSATION_ID"


def cache_home() -> Path:
    """XDG の cache の根($XDG_CACHE_HOME、無ければ ~/.cache)— 本物の答え手の下の ReadEnvironment で読む。"""
    return Path(_read("XDG_CACHE_HOME") or Path.home() / ".cache")


def check_cache_setting() -> str | None:
    """$DOEFF_HY_CHECK_CACHE の素の値(doeff-hy-check の展開の保存先の dir)— 無い・空なら None。日次の検証のように HOME を
    走りごとに替える呼び手が、走りをまたいで残る dir を名指すため(agora-redesign #3863)。"""
    return _read("DOEFF_HY_CHECK_CACHE") or None


def pyright_threads_setting() -> str | None:
    """$DOEFF_HY_CHECK_PYRIGHT_THREADS の素の値(doeff-hy-check が撃つ pyright の thread の数)— 無い・空なら None。1 以上の整数かは
    呼び手の pyright_threads が判じる(card ki-1475765a3f18)。展開の子の数は環境変数でなく機体の検の枠の宣言が持つ。"""
    return _read(PYRIGHT_THREADS_ENV) or None


def conversation_setting() -> str | None:
    """$AGORA_CONVERSATION_ID の素の値 — 無い・空なら None。機体の検の枠の保持者の名乗りに、どの会話の検かを足すため
    (card ki-26ebd3f636e0)。"""
    return _read(CONVERSATION_ENV) or None


class EnvironmentByName(Mapping[str, str]):
    """環境変数を名指しで 1 つずつ読む写し(名ごとに _read で問う)。どの名を読むかを知る読み手(機体の検の枠の正本の take)へ渡し、
    渡す側が名を知らずに済ませるため(doeff_hy.check_slot・card ki-26ebd3f636e0 の盲検 A — 写す名を 1 つに絞ると、正本が新しい名を
    読んだ時に doeff だけが黙って従わない)。並べる読み(iter・len)は断る — 環境の全部を読む道は持たない(os.environ を値として
    使わない・DOEFF004)。"""

    def __getitem__(self, name: str) -> str:
        value = _read(name)
        if value is None:
            raise KeyError(name)
        return value

    def __iter__(self) -> Iterator[str]:
        raise TypeError("EnvironmentByName は名指しでだけ読む — 環境変数を並べない")

    def __len__(self) -> int:
        raise TypeError("EnvironmentByName は名指しでだけ読む — 環境変数を数えない")


def _read(name: str) -> str | None:
    """環境変数 1 つの素の値(無ければ None)— 設定の Ask を、環境変数から答える foundation の答え手 env_var_ask(接頭辞なし)の
    下で問う。無ければ外側の reader の既定 None が答える。

    以前は ReadEnvironment を process 全般の答え手 subprocess_handler(doeff_core_effects.os_process)の下で問うていた。読むのは
    変数 1 つなのに、子 process・thread・signal の答え手を持つ大きな Hy の module とその Hy の bytecode の確かめを読み込み、
    doeff-hy-check の子 process 1 回ごとに 1.8〜4.7 秒かかった(card acp:kanban-issue:ki-efa3d187b136)。env_var_ask は Python の
    foundation の module(doeff_core_effects.handlers — 層の宣言で環境変数の読みを許された所)に在り、読み込みは 0.3 秒前後。
    env_var_ask は `{module.path}` の形の値を import の名として読むので、置き場の path をその形で書かない。"""
    # doeff-core-effects は doeff-hy の macro を読むので、この module を読み込む時点ではなく呼ばれた時に読む。
    from doeff import run, with_handlers
    from doeff_core_effects.effects import Ask
    from doeff_core_effects.handlers import env_var_ask, reader

    value = run(with_handlers([reader({name: None}), env_var_ask(prefix="")], Ask(name)))
    return value if isinstance(value, str) else None
