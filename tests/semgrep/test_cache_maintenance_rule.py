"""キャッシュ維持の専用経路が通常turnへ戻る回帰を検出する。"""

from pathlib import Path

from test_vm_failfast_semgrep_rules import _semgrep_results


def test_cache_maintenance_rule_rejects_only_normal_turn_effects() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture_root = root / "tests/semgrep/fixtures/python"
    findings = _semgrep_results(
        root / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/sessionhost/cache_host.hy",
        cwd=fixture_root,
    )
    hits = [finding["start"]["line"] for finding in findings
            if finding["check_id"].endswith("cache-maintenance-never-becomes-normal-turn")]
    assert hits == [2, 4]
