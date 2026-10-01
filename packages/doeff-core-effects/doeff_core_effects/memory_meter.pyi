"""memory_meter.hy の公開面の型(計器の I/O なしの答え手 — 型検査のための宣言・実行時は memory_meter.hy を読む)。

- memory-meter-handler(Python の名 memory_meter_handler)は計器の設定 MeterSettings を受け、本文の Program に被せる関数を返す
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.meter_effects import MeterSettings

_A = TypeVar("_A")

class _MemoryMeterHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def memory_meter_handler(settings: MeterSettings) -> _MemoryMeterHandler: ...
