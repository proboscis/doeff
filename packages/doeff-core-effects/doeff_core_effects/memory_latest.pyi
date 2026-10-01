"""memory_latest.hy の公開面の型(最新の値の I/O なしの答え手 — 型検査のための宣言・実行時は memory_latest.hy を読む)。

- memory-latest-handler(Python の名 memory_latest_handler)は引数の無い defhandler — 値そのものが本文の Program に被せる関数
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program

_A = TypeVar("_A")

class _MemoryLatestHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

memory_latest_handler: _MemoryLatestHandler
