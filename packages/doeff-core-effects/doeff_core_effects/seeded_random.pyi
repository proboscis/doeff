"""seeded_random.hy の公開面の型(乱数の fake の答え手 — 型検査のための宣言・実行時は seeded_random.hy を読む・agora-redesign #2323)。

- seeded-random-handler(Python の名 seeded_random_handler)は種を受け、本文の Program に被せる関数を返す
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Any, Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program

_A = TypeVar("_A")

def seeded_bytes(seed: int, call: int, count: int) -> Program[bytes, Any]: ...

class _SeededRandomHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def seeded_random_handler(seed: int) -> _SeededRandomHandler: ...
