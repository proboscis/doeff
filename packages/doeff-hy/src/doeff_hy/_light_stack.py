"""Hy の require 系が呼ぶ `inspect.stack` を、frame だけを持つ軽い版にする回避。

本家の Hy 1.3.1 は `hy.compiler` と `hy.macros` の中で `inspect.stack()[n][0]`(呼び出し元の frame)だけを
使うが、`inspect.stack` は全 frame のファイル名・行・ソース文脈を読むので、bytecode からの読み込みでも
require 系の呼び出しごとに遅い(agora-controllers の模擬のテスト 1 file の import で 5.4 秒・agora-redesign #1293)。
Hy からの呼び出しの時だけ、frame を 1 要素目に持つ tuple の列を返す軽い版へ渡す。Hy 以外からの呼び出しは
元の関数へそのまま渡す。

この module は doeff の他の部分を import しない(PyPI の Hy の上でも単独で読めるように)。
"""
import inspect
import sys

# Hy 1.3.1 で inspect.stack を呼ぶ module。どれも `[n][0]` の使い方だけ(#1293 の責務の表)。
_HY_CALLER_MODULES = frozenset({"hy.compiler", "hy.macros"})

_original_stack = inspect.stack


def _light_stack(context=1):
    caller = sys._getframe(1)
    if caller.f_globals.get("__name__") not in _HY_CALLER_MODULES:
        return _original_stack(context)
    frames = []
    frame = caller
    while frame is not None:
        frames.append((frame,))
        frame = frame.f_back
    return frames


def install():
    """差し替えを入れる。すでに入っていれば何もしない(2 重に包まない)。"""
    if inspect.stack is not _light_stack:
        inspect.stack = _light_stack


def uninstall():
    """差し替えを外して元の `inspect.stack` に戻す。"""
    if inspect.stack is _light_stack:
        inspect.stack = _original_stack
