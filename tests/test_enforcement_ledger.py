"""ADR-DOE-ENFORCE-001 R5: enforcement 台帳の anti-drop ratchet。

orch の SpecInventorySpec の pytest 版。enforcement 資産(defadr ファイル数・
.semgrep.yaml ルール数・ADR 内 deftest/defsemgrep/law 数)の実数が台帳
docs/adr/enforcement-ledger.json と厳密一致しなければ fail する。

- 実数 < 台帳: enforcement の黙った喪失(侵食)— 削減の意図があるなら台帳を
  同じ変更セットで下げ、理由を ADR に記録すること。
- 実数 > 台帳: 追加の記帳漏れ — 台帳を上げること(増加も明示的に)。
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "adr" / "enforcement-ledger.json"


def _actual_counts() -> dict:
    adr_files = sorted((ROOT / "docs" / "adr").glob("defadr_*.hy"))
    adr_text = "".join(p.read_text() for p in adr_files)
    semgrep_text = (ROOT / ".semgrep.yaml").read_text()
    return {
        "defadr_files": len(adr_files),
        "semgrep_rules": len(re.findall(r"^  - id:", semgrep_text, re.MULTILINE)),
        "adr_deftest_enforcements": adr_text.count("(deftest "),
        "adr_defsemgrep_enforcements": adr_text.count("(defsemgrep "),
        "adr_laws": adr_text.count("(law "),
    }


def test_enforcement_inventory_matches_ledger():
    ledger = {k: v for k, v in json.loads(LEDGER.read_text()).items() if not k.startswith("_")}
    actual = _actual_counts()
    assert actual == ledger, (
        f"enforcement 台帳と実数が不一致 — ADR-DOE-ENFORCE-001 R5。\n"
        f"  台帳: {ledger}\n  実数: {actual}\n"
        f"減少 = 侵食(意図的なら台帳とADRを同時更新)、増加 = 記帳漏れ(台帳を上げる)。"
        f" 台帳: {LEDGER}"
    )
