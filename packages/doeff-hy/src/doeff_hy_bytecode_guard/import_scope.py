"""一番外の読み込み 1 つ(module の本体の実行 — 中の import を含む)の間だけ置く覚えの持ち主。

.pyc の記録の照らし(:mod:`doeff_hy_bytecode_guard.macro_use`)は、覚えた macro の digest が今も使えるかを、digest が依った
file の状態(更新時刻・大きさ)で確かめる。照らしは Hy の module 1 つごとに走るので、確かめを照らしごとにすると、同じ file を
読み込み 1 つの中で何百回も stat する(test_emulated_screen.hy の読み込みで照らし 797 回・stat 9,700 回 — agora-redesign card
ki-be596ac8276f)。読みの口の包み(:mod:`doeff_hy_bytecode_guard.loader_hooks`)が一番外の module の本体の実行の間この覚えを置き、
照らしはその中で file の状態を 1 度だけ読む — 読み込み 1 つを file の状態の 1 つの写しとして扱う。次の読み込みは読み直す。
読み込みの外(道具が直に照らす)では、照らしが 1 回ごとに自分で置く。

覚えは、置いた thread の中で、置いた口が閉じるまでだけ使う(:func:`current_scope`)。読み込みの中で起きた thread(free-threaded の
3.14 では起こした側の context を継ぐ — ``sys.flags.thread_inherit_context``)と写した context は、ContextVar の値を読み込みの後も
持ち続けるので、値があるだけでは今の読み込みの覚えと決まらない(設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の盲検 A の付録)。

起動時に読まれる包みから import されるので、標準 library の起動時に読み込み済みの module しか import しない。
"""

from _thread import get_ident  # threading は起動時に読まれていない — 同じ関数の元
from contextvars import ContextVar


class ImportScope:
    """一番外の読み込み 1 つの間の覚え — file の path → 状態(持ち主の照らしが置く・消えた file は None)と、この中で確かめた
    digest の覚え(鍵 → 覚え — 同じ覚えを 2 度確かめない)。置いた thread と、置いた口が閉じたかを持つ。"""

    __slots__ = ("_mut_closed", "confirmed", "owner", "states")

    def __init__(self) -> None:
        self.states: dict[str, object] = {}
        self.confirmed: dict[object, object] = {}
        self.owner = get_ident()
        self._mut_closed = False

    @property
    def closed(self) -> bool:
        """置いた口が閉じたか。"""
        return self._mut_closed

    def close(self) -> None:
        """置いた口が閉じた — 継いだ thread や写した context に値が残っても、もう使わない。"""
        self._mut_closed = True


#: 今の読み込みの覚え(読み込みの外は None)。値を置く・戻すのは覚えを置く口だけで、読むのは :func:`current_scope` を通す —
#: 読み込みの中で起きた thread と写した context は値を継ぐ。
IMPORT_SCOPE: ContextVar[ImportScope | None] = ContextVar("doeff_hy_import_scope", default=None)


def current_scope() -> ImportScope | None:
    """今の読み込みの覚え — 置いた thread の中で、置いた口が閉じる前だけ(それ以外は None — 照らしは自分の覚えを置く)。"""
    scope = IMPORT_SCOPE.get()
    if scope is None or scope.closed or scope.owner != get_ident():
        return None
    return scope
