"""一番外の読み込み 1 つ(module の本体の実行 — 中の import を含む)の間だけ置く覚えの持ち主。

.pyc の記録の照らし(:mod:`doeff_hy_bytecode_guard.macro_use`)は、覚えた macro の digest が今も使えるかを、digest が依った
file の状態(更新時刻・大きさ)で確かめる。照らしは Hy の module 1 つごとに走るので、確かめを照らしごとにすると、同じ file を
読み込み 1 つの中で何百回も stat する(test_emulated_screen.hy の読み込みで照らし 797 回・stat 9,700 回 — agora-redesign card
ki-be596ac8276f)。読みの口の包み(:mod:`doeff_hy_bytecode_guard.loader_hooks`)が一番外の module の本体の実行の間この覚えを置き、
照らしはその中で file の状態を 1 度だけ読む — 読み込み 1 つを file の状態の 1 つの写しとして扱う。次の読み込みは読み直す。
読み込みの外(道具が直に照らす)では、照らしが 1 回ごとに自分で置く。

起動時に読まれる包みから import されるので、標準 library しか import しない。
"""

from contextvars import ContextVar


class ImportScope:
    """一番外の読み込み 1 つの間の覚え — file の path → 状態(持ち主の照らしが置く・消えた file は None)と、この中で確かめた
    digest の覚え(鍵 → 覚え — 同じ覚えを 2 度確かめない)。"""

    __slots__ = ("confirmed", "states")

    def __init__(self) -> None:
        self.states: dict[str, object] = {}
        self.confirmed: dict[object, object] = {}


#: 今の読み込みの覚え(読み込みの外は None)。context ごとの値なので、別の thread の import には効かない。
IMPORT_SCOPE: ContextVar[ImportScope | None] = ContextVar("doeff_hy_import_scope", default=None)
