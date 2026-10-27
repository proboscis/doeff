"""Harness start-command resolution — the host's entry module is the default daemon under test.

issue #575 M1(ADR-DOE-AGENTS-004 R7 / retired-impl-lives-only-in-git-history)・card ki-bb337cfd0336:
conformance の既定 daemon は host の入口の module(実行中 interpreter の
`-m doeff_agents.sessionhost.host`)であり、CLI コマンドも PATH の探索も、退役 Rust crate の
ビルド(cargo)経路も無い。`CONFORMANCE_AGENTD_BIN` は最優先の明示 seam のまま(契約語彙)。
"""

import sys
from pathlib import Path

import harness


def test_explicit_seam_still_wins(monkeypatch, tmp_path):
    fake = tmp_path / "fake-agentd"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CONFORMANCE_AGENTD_BIN", str(fake))
    assert harness.resolve_agentd_command() == (str(fake),)


def test_default_is_the_entry_module_on_the_running_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("CONFORMANCE_AGENTD_BIN", raising=False)
    # PATH に何が在っても(古い console script でも)見ない。
    monkeypatch.setenv("PATH", str(tmp_path))
    assert harness.resolve_agentd_command() == (
        sys.executable,
        "-m",
        "doeff_agents.sessionhost.host",
    )


def test_a_seam_that_is_not_executable_is_loud(monkeypatch, tmp_path):
    # silent fallback 禁止(R7): 実行できない seam は AssertionError で loud fail
    monkeypatch.setenv("CONFORMANCE_AGENTD_BIN", str(tmp_path / "missing-agentd"))
    try:
        harness.resolve_agentd_command()
    except AssertionError:
        return
    raise AssertionError("実行できない seam なのに loud fail しなかった")


def test_no_retired_crate_binding_left():
    # cargo 経路・crate dir 束縛の残骸ゼロ(#575 M1 — semgrep exclude 撤去と対)
    assert not hasattr(harness, "AGENTD_CRATE")
    src = Path(harness.__file__).read_text(encoding="utf-8")
    assert "cargo" not in src, "harness に cargo 束縛の残骸がある"
    assert "build_agentd" not in src, "誤称 build_agentd(もう build しない)が残っている"
