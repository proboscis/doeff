"""memory_stack_dump.hy の公開面の型(全 thread の stack を書く見張りの答え手 — 型検査のための宣言・実行時は memory_stack_dump.hy を読む)。

- memory-stack-dump-handler(Python の名 memory_stack_dump_handler)は引数の無い defhandler — 値そのものが本文の Program に被せる関数
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program

_A = TypeVar("_A")

class _MemoryStackDumpHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

memory_stack_dump_handler: _MemoryStackDumpHandler
