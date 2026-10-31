"""一番外の読み込み(module の本体の実行 — 中の import を含む)の間だけ、GC の閾値を上げる持ち主。

CPython 3.14 free-threaded の GC は、前の GC から正味の object が閾値(既定 2000)を越えて増え、生き残りの 1/4 以上で、RSS が
10% 超増えると heap 全体を掃く。読み込みは長く生きる object(module・関数・型)で heap を育てるので、ごみが 1% 前後でも RSS が
10% 増えるたびに掃く — agora-controllers の test_emulated_screen.hy の読み込み 1 つ(約 2 秒)で 7 回・0.28〜0.38 秒だった。

読みの口の包み(:mod:`doeff_hy_bytecode_guard._loader_hooks`)が一番外の module の本体の実行の間 :func:`hold` で閾値を
:data:`IMPORT_GC_THRESHOLD` へ上げ、終わりに :func:`release` で戻す。止めず(gc.disable)有限の値にするのは、閾値が「前の GC から
溜まりうるごみの object の数」の上限でもあるため — 読み込みの間でも GC は起き、溜まるごみはこの数で頭打ちになる(止めると
test_emulated_screen.hy で最大 RSS が 11 MB 増え、溜めた分の GC がテスト本体へ移った)。

閾値は process に 1 つなので、どの thread の読み込みが続いている間も上げたままにし、最後の読み込みが終わった時に戻す。読み込みの
途中で読まれた module が自分で閾値を決めたら(上げた値と違えば)その値を残す。閾値 0(自動の GC を切ってある)は動かさない。
別の thread の読み込みの途中で fork した子は、その thread を持たず読み込みが子の中で終わらないので、fork した thread の分だけを残す。

起動時に読まれる包みから import されるので、標準 library の軽い module しか import しない(threading も読まない)。
"""

import gc
import os
from _thread import allocate_lock, get_ident

#: 一番外の読み込みの間の閾値(前の GC から正味で増えてよい object の数)。test_emulated_screen.hy の読み込みで 150,000 / 200,000 /
#: 250,000 / 400,000 を比べ、読み込みの中の GC が 7 回 → 2 回になり、テスト本体の中へ GC を移さず、最大 RSS が揺れの中だった値。
IMPORT_GC_THRESHOLD = 250_000


class _ImportHold:
    """閾値を上げている間の覚え(process に 1 つ — 持ち主はこの module だけ)。"""

    __slots__ = ("before", "holders", "lock")

    def __init__(self) -> None:
        self.lock = allocate_lock()
        #: 読み込み中の thread の id → その thread の一番外の読み込みの数(同じ thread でも context が違えば一番外が重なりうる)。
        #: fork した子が、fork した thread の分だけを残すために thread ごとに持つ。
        self.holders: dict[int, int] = {}
        #: 上げる前の閾値(上げていなければ None)。
        self.before: int | None = None


_HOLD = _ImportHold()


def hold() -> None:
    """一番外の読み込みの始まり — 読み込みの間の GC を減らすため、読み込み中の thread が他に無ければ閾値を上げる。"""
    with _HOLD.lock:
        ident = get_ident()
        first = not _HOLD.holders
        _HOLD.holders[ident] = _HOLD.holders.get(ident, 0) + 1
        if first:
            _raise_threshold()


def release() -> None:
    """一番外の読み込みの終わり(例外でも)— 読み込みの外の GC を既定に戻すため、読み込み中の thread が無くなれば閾値を戻す。"""
    with _HOLD.lock:
        ident = get_ident()
        left = _HOLD.holders.get(ident, 0) - 1
        if left > 0:
            _HOLD.holders[ident] = left
        else:
            _HOLD.holders.pop(ident, None)
        if not _HOLD.holders:
            _restore_threshold()


def _raise_threshold() -> None:
    """閾値を読み込みの間の値へ上げ、前の値を覚える(切ってある GC・既に高い閾値は、利用者の決めとして触らない)。"""
    current = gc.get_threshold()
    if current[0] == 0 or current[0] >= IMPORT_GC_THRESHOLD:
        _HOLD.before = None
        return
    _HOLD.before = current[0]
    gc.set_threshold(IMPORT_GC_THRESHOLD, *current[1:])


def _restore_threshold() -> None:
    """覚えた前の閾値へ戻す — 読み込まれた module が閾値を自分で決めていれば(上げた値と違えば)その決めを残す。"""
    before, _HOLD.before = _HOLD.before, None
    current = gc.get_threshold()
    if before is not None and current[0] == IMPORT_GC_THRESHOLD:
        gc.set_threshold(before, *current[1:])


def _after_fork_in_child() -> None:
    """fork した子で、子に無い thread の読み込みの分を外す — その読み込みは子の中で終わらず、閾値が上がったまま残るため。
    fork した thread 自身の読み込みは子の中で終わるので残す。lock は fork の時に他の thread が持っていたかもしれないので作り直す。"""
    _HOLD.lock = allocate_lock()
    ident = get_ident()
    mine = _HOLD.holders.get(ident, 0)
    _HOLD.holders = {ident: mine} if mine else {}
    if not mine:
        _restore_threshold()


os.register_at_fork(after_in_child=_after_fork_in_child)
