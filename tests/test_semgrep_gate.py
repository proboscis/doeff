"""ADR-DOE-ENFORCE-001 R3: .semgrep.yaml の全ルールを既定 pytest ゲートで実行する。

`make lint-semgrep` と同一の走査(--config .semgrep.yaml doeff/ packages/)を pytest から
起動する集約ゲート。Makefile 版との違い:
1. fail-closed — semgrep バイナリ不在は skip ではなく hard fail(偽緑の禁止 — ACP で
   「初回実行時に 16 検査が即 red(バイナリ不在)」が起きた失敗様式の再発防止)。
2. baseline 等値 ratchet — 違反数が baseline より「増えたら fail(新規違反)・減ったら
   fail(baseline を下げる記帳を強制)」。2026-07-14 に既存違反を解消し、現在の
   docs/adr/semgrep-baseline.json は 0 を維持する。

個別ルールの hit/clean fixture 化(defsemgrep installed-rule 形式)と既存違反の解消
バッチは T-B2 の残作業(codex 委譲)— 本ゲートはその間も 229 ルールが実際に走り、
後退しないことを保証する。
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "adr" / "semgrep-baseline.json"


def test_semgrep_is_declared_as_bounded_dev_dependency() -> None:
    config: dict[str, object] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependency_groups: dict[str, list[str]] = cast(
        dict[str, list[str]], config["dependency-groups"]
    )

    assert "semgrep>=1.161.0,<2" in dependency_groups["dev"]


def test_makefile_semgrep_targets_use_project_environment() -> None:
    makefile: str = (ROOT / "Makefile").read_text()

    assert "command -v semgrep" not in makefile
    assert makefile.count("uv run semgrep") == 3


def test_missing_semgrep_points_to_make_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_binary(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", missing_binary)

    with pytest.raises(AssertionError, match="make sync"):
        test_semgrep_findings_match_baseline_ratchet()


@pytest.mark.semgrep
@pytest.mark.slow
def test_semgrep_findings_match_baseline_ratchet():
    binary = shutil.which("semgrep")
    assert binary is not None, (
        "semgrep バイナリが見つからない — ADR-DOE-ENFORCE-001 R3 は skip を禁止する。"
        "semgrep は dev 依存に含まれるため、`make sync` を実行すること。"
    )
    proc = subprocess.run(
        # --metrics=off --disable-version-check: 検査の実行に network(送信・新版照会)を
        # 混ぜない — 到達性・応答時間という機体の事情が検査に入るのを断つ。
        # --project-root=ROOT: root を git metadata からの推論に任せない — .git の無い
        # 検査 tree では root が走査対象 dir に落ち、上位セグメントを参照する
        # paths.include を持つ rule が gate から無音で脱落する(zeus 実測 2026-08-17)。
        [
            binary,
            "--metrics=off",
            "--disable-version-check",
            "--project-root",
            str(ROOT),
            "--config",
            ".semgrep.yaml",
            "doeff/",
            "packages/",
            "--error",
            "--quiet",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,  # 違反有無は returncode でなく results 数で判定する(baseline ratchet)
    )
    # exit code は判定に使わない分、JSON の実在が「scan が走った」の唯一の証拠になる。
    # 起動時 crash(exit 1・stdout 空)を裸の JSONDecodeError にしない(zeus 実測
    # 2026-08-17: interpreter 不整合の crash が判読不能な赤で報告された)。
    try:
        results = json.loads(proc.stdout)["results"]
    except ValueError as exc:
        raise AssertionError(
            f"semgrep produced no JSON verdict (exit {proc.returncode}) — "
            f"the scan did not run; stderr:\n{proc.stderr}"
        ) from exc
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
