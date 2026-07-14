"""ADR-DOE-ENFORCE-001 R3: .semgrep.yaml の全ルールを既定 pytest ゲートで実行する。

`make lint-semgrep` と同一の走査(--config .semgrep.yaml doeff/ packages/)を pytest から
起動する集約ゲート。Makefile 版との違い:
1. fail-closed — semgrep バイナリ不在は skip ではなく hard fail(偽緑の禁止 — ACP で
   「初回実行時に 16 検査が即 red(バイナリ不在)」が起きた失敗様式の再発防止)。
2. baseline 等値 ratchet — 2026-07-14 の初回実行で既存違反 46 件が見つかったため
   (docs/adr/semgrep-baseline.json に内訳)、違反数が baseline より「増えたら fail
   (新規違反)・減ったら fail(baseline を下げる記帳を強制)」。目標は 0。

個別ルールの hit/clean fixture 化(defsemgrep installed-rule 形式)と既存違反の解消
バッチは T-B2 の残作業(codex 委譲)— 本ゲートはその間も 229 ルールが実際に走り、
後退しないことを保証する。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "adr" / "semgrep-baseline.json"


@pytest.mark.semgrep
@pytest.mark.slow
def test_semgrep_findings_match_baseline_ratchet():
    binary = shutil.which("semgrep")
    assert binary is not None, (
        "semgrep バイナリが見つからない — ADR-DOE-ENFORCE-001 R3 は skip を禁止する。"
        "`uv tool install semgrep` でインストールすること。"
    )
    proc = subprocess.run(
        [binary, "--config", ".semgrep.yaml", "doeff/", "packages/", "--error", "--quiet", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,  # 違反有無は returncode でなく results 数で判定する(baseline ratchet)
    )
    results = json.loads(proc.stdout)["results"]
    baseline = json.loads(BASELINE.read_text())["findings"]
    actual = len(results)
    if actual > baseline:
        newest = [f"{r['check_id']} {r['path']}:{r['start']['line']}" for r in results][-10:]
        raise AssertionError(
            f"semgrep 違反が baseline を超過: {actual} > {baseline} — 新規違反を修正すること"
            f"(ADR-DOE-ENFORCE-001 R3)。直近の検出例:\n" + "\n".join(newest)
        )
    assert actual == baseline, (
        f"semgrep 違反数 {actual} < baseline {baseline} — 改善を記帳すること: "
        f"{BASELINE} の findings を {actual} に下げる(黙った基準緩みの防止)。"
    )
