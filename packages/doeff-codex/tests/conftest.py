"""doeff-codex の検の実行環境(Hy の module を import できるようにするだけ)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 行の分類の検は、版を固定した本物の codex(0.162.1)が手元の偽の上流に答えた stdout の行(``tests/recorded/codex-0.162.1``)を読む。
  録り方は ``scripts/record_app_server_lines.py``。
"""

from __future__ import annotations

import hy  # noqa: F401  - Hy の module を import できるようにする
