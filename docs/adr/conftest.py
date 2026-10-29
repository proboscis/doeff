"""docs/adr 用 doeff_interpreter fixture — doeff-adr deftest enforcement の実行時。

責務境界(doeff-adr の方針): 収集は doeff-adr pytest plugin が所有し、
実行時 fixture は消費リポジトリ(ここでは doeff 自身)が所有する。

この fixture は ADR-DOE-HY-002 R3 の参照実装を兼ねる:
- deftest の :env は reader ハンドラ経由で必ず反映する(黙って無視しない)。
- エラーは再送出する(RunResult 吸収による偽緑を作らない)。
- ADR の deftest が (<- spec (GetAdr ADR-ID)) で引く記録は、doeff-adr の adr_registry() が登録簿から答える
  (card ki-24a2bca4339f — 前の関数 get_adr は内側へ移した)。
"""

import pytest


@pytest.fixture
def doeff_interpreter():
    def run_program(program, *, env=None):
        from doeff import run
        from doeff_adr import adr_registry

        program = adr_registry()(program)
        if env:
            from doeff_core_effects import reader

            program = reader(dict(env))(program)
        return run(program)

    return run_program
