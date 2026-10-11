"""展開(compile)の間に実行の始まった Python の code object を拾う — macro の記録の digest が、閉包の関数のうち、その module の
展開で実行された関数の code の中身だけを覆うため(:mod:`doeff_hy_bytecode_guard.macro_use`・agora-redesign #3769 の子 —
defk の展開で呼ばれない binding_forms._Walker._assign の変更で、defk の使い手が全部冷えた)。

拾い方: sys.monitoring(Python 3.12 から)の PY_START(関数の実行の始まり)と PY_RESUME(generator の再開 — 拾いを開く前に
始まった generator)を、拾いを開いている間だけ受ける。受けた code を開いている拾いの全部に足し、その code の場所の知らせを
止める(DISABLE — 同じ code の 2 度目からは費用が無い)。拾いを 1 つ開くたびに止めた知らせを戻す(restart_events)ので、前の
拾いの間に止めた code も新しい拾いでは 1 度知らされる。thread を分けない — 別の thread の実行も開いている拾いの全部に入る
(多く拾うのは、記録が古いと判じやすくなるだけ)。入れ子の拾い(展開の中の require が別の Hy の module を compile する)も同じ:
内の拾いの間の実行は外の拾いにも入る。

拾えない環境(Python 3.11 まで・sys.monitoring の道具の番号が空いていない)では :func:`start` が :class:`Uncollectable` を返し、
記録は閉包の関数の code を全部覆う(今までの形)。
"""

import sys
import threading
from dataclasses import dataclass
from types import CodeType

#: sys.monitoring の道具の番号の候補 — 0・1・2・5 は debugger・coverage・profiler・optimizer に名指された番号なので、名指しの
#: 無い 3 と 4 だけを使う。
_TOOL_CANDIDATES = (3, 4)
_TOOL_NAME = "doeff-hy-called-code"

_LOCK = threading.Lock()

#: 開いている拾いの集合の組(1 つの欄 — 開け閉めは lock の下で組ごと替え、知らせの callback は lock を取らずに読む)。
_OPEN: list[tuple[set[CodeType], ...]] = [()]


@dataclass(frozen=True)
class Uncollectable:
    """この process では拾えない(理由の文 — sys.monitoring が無い・道具の番号が空いていない)。"""

    reason: str


#: 使う道具の番号の決め(鍵 "tool" の 1 つ — 初めて拾いを開く時に決め、process の間変えない)。
_TOOL: dict[str, "int | Uncollectable"] = {}


class CalledCode:
    """開いた拾い 1 つ — :meth:`codes` が今までに拾った code、:meth:`close` が拾いを閉じる(2 度目からは何もしない)。"""

    def __init__(self, tool: int) -> None:
        self._tool = tool
        self._codes: set[CodeType] = set()

    def codes(self) -> frozenset[CodeType]:
        """今までに拾った code(閉じる前でも読める — 読んだ後に拾った code は入らない)。"""
        return frozenset(self._codes)

    def reopen(self) -> None:
        """閉じた拾いを開き直す(拾った code は残して続きを足す — 開いている時は何もしない)。"""
        with _LOCK:
            if not any(codes is self._codes for codes in _OPEN[0]):
                self._open()

    def close(self) -> None:
        """拾いを閉じる — 開いている拾いが無くなれば知らせを受けるのをやめる。"""
        with _LOCK:
            remaining = tuple(codes for codes in _OPEN[0] if codes is not self._codes)
            if len(remaining) == len(_OPEN[0]):
                return
            _OPEN[0] = remaining
            if not remaining:
                sys.monitoring.set_events(self._tool, 0)

    def _open(self) -> None:
        """拾いを開く(lock の下で呼ぶ)— 開いた後に止めた知らせを戻し、前の拾いの間に止めた code も知らせを受ける。"""
        first = not _OPEN[0]
        _OPEN[0] = (*_OPEN[0], self._codes)
        if first:
            events = sys.monitoring.events
            sys.monitoring.set_events(self._tool, events.PY_START | events.PY_RESUME)
        sys.monitoring.restart_events()


def start() -> CalledCode | Uncollectable:
    """拾いを開く(拾えない環境では :class:`Uncollectable` — 呼び手は閉包の関数の code を全部覆う)。"""
    with _LOCK:
        match _tool():
            case int() as tool:
                called = CalledCode(tool)
                called._open()
                return called
            case Uncollectable() as uncollectable:
                return uncollectable


def _started(code: CodeType, offset: int) -> object:
    """sys.monitoring の知らせ — code を開いている拾いの全部に足し、この場所の知らせを止める。"""
    for codes in _OPEN[0]:
        codes.add(code)
    return sys.monitoring.DISABLE


def _tool() -> "int | Uncollectable":
    """使う道具の番号(初めての時に空いている番号を取り、知らせの callback を置く — lock の下で呼ぶ)。"""
    known = _TOOL.get("tool")
    if known is None:
        known = _TOOL.setdefault("tool", _take_tool())
    return known


def _take_tool() -> "int | Uncollectable":
    """空いている道具の番号を取り、知らせの callback を置く。"""
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None:
        return Uncollectable(
            f"sys.monitoring が無い(Python {sys.version_info[0]}.{sys.version_info[1]})"
        )
    for candidate in _TOOL_CANDIDATES:
        if monitoring.get_tool(candidate) is not None:
            continue
        try:
            monitoring.use_tool_id(candidate, _TOOL_NAME)
        except ValueError:  # 別の道具が先に取った
            continue
        monitoring.register_callback(candidate, monitoring.events.PY_START, _started)
        monitoring.register_callback(candidate, monitoring.events.PY_RESUME, _started)
        return candidate
    taken = ", ".join(f"{index}={monitoring.get_tool(index)}" for index in _TOOL_CANDIDATES)
    return Uncollectable(f"sys.monitoring の道具の番号が空いていない({taken})")
