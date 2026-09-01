"""ADR-DOE-ENFORCE-001 R5: enforcement 台帳の anti-drop ratchet。

orch の SpecInventorySpec の pytest 版。enforcement 資産(defadr ファイル数・
.semgrep.yaml ルール数・ADR 内 deftest/defsemgrep/law 数)の実数が台帳
docs/adr/enforcement-ledger.json と厳密一致しなければ fail する。

- 実数 < 台帳: enforcement の黙った喪失(侵食)— 削減の意図があるなら台帳を
  同じ変更セットで下げ、理由を ADR に記録すること。
- 実数 > 台帳: 追加の記帳漏れ — 台帳を上げること(増加も明示的に)。

勘定の定義点は scripts/check_enforcement_ledger.py の 1 点(R7 の著述時
git pre-commit hook と同じ家)— ここはそれを既定 pytest 収集に載せる面。
著述時に捕まえるには `make hooks-install`(ADR-DOE-ENFORCE-001 R7)。
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "adr" / "enforcement-ledger.json"
CHECKER = ROOT / "scripts" / "check_enforcement_ledger.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_enforcement_ledger", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enforcement_inventory_matches_ledger():
    checker = _load_checker()
    ledger = checker.worktree_ledger(ROOT)
    actual = checker.worktree_counts(ROOT)
    assert actual == ledger, (
        f"enforcement 台帳と実数が不一致 — ADR-DOE-ENFORCE-001 R5。\n"
        f"  台帳: {ledger}\n  実数: {actual}\n"
        f"減少 = 侵食(意図的なら台帳とADRを同時更新)、増加 = 記帳漏れ(台帳を上げる)。"
        f" 台帳: {LEDGER}\n"
        f"著述時に捕まえるには make hooks-install(ADR-DOE-ENFORCE-001 R7)。"
    )
