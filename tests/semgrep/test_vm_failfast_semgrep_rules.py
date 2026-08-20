from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]


def _semgrep_results(config: Path, target: str, *, cwd: Path) -> list[dict[str, Any]]:
    semgrep_bin = shutil.which("semgrep")
    if semgrep_bin is None:
        pytest.skip("semgrep is not installed")

    completed = subprocess.run(
        # --metrics=off --disable-version-check: 検査の実行に network(送信・新版照会)を
        # 混ぜない — 到達性・応答時間という機体の事情が検査に入るのを断つ。
        # --project-root=REPO_ROOT: root を git metadata からの推論に任せない — .git の
        # 無い検査 tree では root が走査対象 dir 自身に落ち、対象より上のセグメントを
        # 参照する paths.include だけが無音で死ぬ(zeus 実測 2026-08-17)。root は cwd
        # (fixture dir)ではなく tree の根 — rule 側の fixtures 写し include
        # (/tests/semgrep/fixtures/...)は repo-root 相対で書かれている。
        [
            semgrep_bin,
            "--metrics=off",
            "--disable-version-check",
            "--project-root",
            str(REPO_ROOT),
            "--no-git-ignore",
            "--config",
            str(config),
            "--json",
            target,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise AssertionError(
            f"semgrep failed:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    # exit 1 は「findings あり」と「起動時 crash」の両方が返す — JSON が読めた時だけ
    # scan が走ったと言える(zeus 実測 2026-08-17: interpreter 不整合の crash が
    # exit 1 + 空 stdout で返り、JSONDecodeError の裸の traceback に化けた)。
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"semgrep produced no JSON verdict (exit {completed.returncode}) — "
            f"the scan did not run; stderr:\n{completed.stderr}"
        ) from exc
    return cast(list[dict[str, Any]], payload.get("results", []))


def _semgrep_rule_ids(config: Path, target: str, *, cwd: Path) -> set[str]:
    return {str(result["check_id"]) for result in _semgrep_results(config, target, cwd=cwd)}


def _has_rule(check_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(check_id == expected_rule_id or check_id.endswith(suffix) for check_id in check_ids)


def _rule_start_lines(results: list[dict[str, Any]], expected_rule_id: str) -> set[int]:
    lines: set[int] = set()
    for result in results:
        check_id = str(result["check_id"])
        if not _has_rule({check_id}, expected_rule_id):
            continue
        start = cast(dict[str, int], result["start"])
        lines.add(start["line"])
    return lines


def test_vm_failfast_rust_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / "packages/doeff-vm/.semgrep.yaml",
        "packages",
        cwd=fixture_root,
    )

    expected = {
        "no-bare-ok-in-handler-can-handle",
        "no-silent-if-let-current-segment",
        "no-bare-ok-in-traceback-build",
        "doeff-vm-no-dispatch-id-runtime",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_vm_failfast_python_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "doeff",
        cwd=fixture_root,
    )

    expected = {
        "no-silent-except-in-traceback",
        "no-silent-except-return-none",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_withhandler_return_clause_rule_detects_external_legacy_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/withhandler_return_clause_sample.py",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-withhandler-no-return-clause") == {20, 21, 22, 23}


def test_public_withhandler_rule_detects_legacy_hy_import_and_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/public_withhandler_sample.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-no-public-withhandler-shim") == {1}


def test_agentd_only_worker_route_rule_detects_conductor_handler_bypass() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py",
        cwd=fixture_root,
    )

    assert _has_rule(check_ids, "adr0001-d1-agentd-only-worker-route")


def test_k4_deadline_rule_bans_transport_timeout_on_agent_task_specs() -> None:
    """L-K4-3 guard: `timeout_seconds` must not return to AgentTask/AgentSpec."""
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/deadline_timeout_sample.py",
        cwd=fixture_root,
    )

    # Both the AgentTask and the AgentSpec construction must fire.
    assert len(_rule_start_lines(results, "k4-deadline-not-transport-timeout")) == 2


def test_real_agent_e2e_semgrep_rules_detect_missing_and_skipped_coverage() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/tests",
        cwd=fixture_root,
    )

    expected = {
        "doeff-agents-require-real-claude-result-retry-e2e",
        "doeff-agents-require-real-codex-result-retry-e2e",
        "doeff-agents-real-agent-e2e-must-not-be-skipped",
        "doeff-agents-real-agent-e2e-must-not-use-command-override",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_agent_anthropic_api_key_semgrep_rule_detects_env_injection() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src",
        cwd=fixture_root,
    )

    assert _has_rule(check_ids, "doeff-agents-no-anthropic-api-key-agent-env")


def test_api_limit_possessive_verbatim_rule_detects_reenumeration() -> None:
    # ACP ADR 0049 R9 (revised 2026-08-07): the possessive api-limit family
    # is matched by the bounded regex in markers.hy; adding a verbatim
    # possessive substring re-creates the thrice-broken enumeration.
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/sessionhost/impls/"
        "api_limit_possessive_verbatim_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(
        results, "doeff-agents-api-limit-possessive-verbatim-forbidden"
    ) == {10}


def test_defhandler_must_be_top_level_rule_detects_nested_handler() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/handlers/nested_defhandler_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-hy-defhandler-must-be-top-level") == {4}


def test_doeff_agents_rule_rejects_handler_callable_vocabulary() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/handlers/handler_callable_forbidden.py",
        cwd=fixture_root,
    )

    assert _has_rule(check_ids, "doeff-agents-no-handler-callable-vocabulary")


def test_ready_gate_semgrep_rules_detect_ungated_prompt_paste() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src",
        cwd=fixture_root,
    )

    expected = {
        "doeff-agents-built-in-adapters-must-declare-ready-pattern",
        "doeff-agents-prompt-paste-must-be-ready-gated",
        "doeff-agents-hy-prompt-paste-must-be-ready-gated",
        "doeff-agents-paste-buffer-must-be-bracketed",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_session_registration_rule_detects_ready_gated_registration() -> None:
    """Issue agentd-session-registration-after-ready-gate: the BOOTING
    snapshot recorded only after deliver_prompt_when_ready is the banned
    pre-fix shape — registration must not wait for TUI readiness."""
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/handlers/"
        "registration_after_ready_gate_forbidden.py",
        cwd=fixture_root,
    )

    assert _has_rule(
        check_ids, "doeff-agents-session-registration-must-precede-ready-gate"
    )


def test_ready_physics_single_home_rules_detect_redefinitions() -> None:
    """ADR-DOE-AGENTS-008: re-defining a ready-pattern literal in an adapter
    or the repl-idle budget literal outside effects.hy is the banned
    two-homes shape (ADR-DOE-AGENTS-004 protocol-physics-has-one-home)."""
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents",
        cwd=fixture_root,
    )

    expected = {
        "doeff-agents-ready-pattern-literal-outside-physics-home",
        "doeff-agents-repl-idle-budget-literal-single-home",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected), check_ids


def test_koine_session_surface_rules_detect_forbidden_shapes() -> None:
    """ADR-DOE-AGENTS-007: the three koine session-surface guards fire on
    their hit fixtures — adopt mutating the substrate, a monitor arm that
    terminalizes before the reap exemption, and a turn-stamp path touching
    the substrate."""
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/sessionhost",
        cwd=fixture_root,
    )

    expected = {
        "doeff-agents-adopt-must-not-mutate-substrate",
        "doeff-agents-interactive-must-not-be-terminalized",
        "doeff-agents-turn-rpc-must-not-touch-substrate",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected), check_ids


def test_koine_interactive_terminalize_rule_is_clean_on_fixed_policy() -> None:
    """The ordering rule must NOT fire on the shipped policy.hy (the reap
    exemption precedes every terminalizing arm) — guards against the rule
    rotting into an always-on false positive."""
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy",
        cwd=REPO_ROOT,
    )
    assert (
        _rule_start_lines(results, "doeff-agents-interactive-must-not-be-terminalized")
        == set()
    )


# issue #575 M2: doeff-agentd-repl-ready-wait-must-not-discard-readiness の
# 発火 assert は rule・fixture(tests/semgrep/fixtures/rust/packages/
# doeff-agentd/)と 3 点セットで退役した — 対象(退役 Rust crate の src)が
# M3 で消滅し、include が crate src のみの死に rule になるため。readiness
# discard の不変量は現役側の同契約(sessionhost/launch.hy)の deftest 群が
# 引き続き守る。rollback 座標 = git tag agentd-rust-final。


def test_copyreg_per_call_resolution_rule_detects_pre_fix_reduce_ex() -> None:
    """doeff-vm-no-per-call-copyreg-resolution は改修前の __reduce_ex__ 形に発火する。

    2026-08-07 の hypha 常駐 runtime 滞留(948 threads が import 鍵で park)の
    発生源だった「呼び出しごとの copyreg 解決」の回帰ガード。
    """
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-vm/src/python_generator_stream.rs",
        cwd=fixture_root,
    )

    # py.import("copyreg") と getattr("__newobj__") の 2 行に発火する
    assert _rule_start_lines(results, "doeff-vm-no-per-call-copyreg-resolution") == {6, 7}


def test_copyreg_per_call_resolution_rule_is_clean_on_shipped_source() -> None:
    """出荷中の python_generator_stream.rs には発火しない(PyOnceLock 化済み)。"""
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-vm/src/python_generator_stream.rs",
        cwd=REPO_ROOT,
    )

    assert _rule_start_lines(results, "doeff-vm-no-per-call-copyreg-resolution") == set()


def test_copyreg_per_call_resolution_rule_reaches_vm_crate_subdirectories() -> None:
    """paths.include は src 直下だけでなく src 配下の subdir にも届く。

    doeff-vm-core は dispatch/step/handler を `src/vm/` に置く。`src/*.rs` だけの
    include はそこへ届かず、rule が無音で素通りする(禁止形が書けてしまう)。
    """
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-vm-core/src/vm/dispatch.rs",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-vm-no-per-call-copyreg-resolution") == {6, 7}


def test_copyreg_per_call_resolution_rule_is_clean_on_shipped_vm_core_subdir() -> None:
    """出荷中の doeff-vm-core `src/vm/` には発火しない(禁止形は 0 箇所)。"""
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-vm-core/src/vm",
        cwd=REPO_ROOT,
    )

    assert _rule_start_lines(results, "doeff-vm-no-per-call-copyreg-resolution") == set()
