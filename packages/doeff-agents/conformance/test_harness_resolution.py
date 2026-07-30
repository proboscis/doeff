"""Harness binary resolution — sessionhost is the default daemon under test.

issue #575 M1(ADR-DOE-AGENTS-004 R7 / retired-impl-lives-only-in-git-history):
conformance の既定 daemon は正典 executor doeff-sessionhost(実行中 interpreter
隣接の console script → PATH)であり、退役 Rust crate のビルド(cargo)経路は
存在しない。`CONFORMANCE_AGENTD_BIN` は最優先の明示 seam のまま(契約語彙)。

TDD red(2026-07-30): 現行 harness は env 未設定 → cargo build で退役 Rust を
既定にしているため、本ファイルは実装反転まで red。
"""

import os
import sys
from pathlib import Path

import harness


def test_explicit_seam_still_wins(monkeypatch, tmp_path):
    fake = tmp_path / "fake-agentd"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CONFORMANCE_AGENTD_BIN", str(fake))
    assert harness.resolve_agentd_bin() == fake


def test_default_resolution_is_interpreter_adjacent_sessionhost(monkeypatch):
    monkeypatch.delenv("CONFORMANCE_AGENTD_BIN", raising=False)
    sibling = Path(sys.executable).parent / "doeff-sessionhost"
    assert sibling.exists(), "前提: venv console script が無い(make sync を実行すること)"
    assert os.access(sibling, os.X_OK), "前提: venv console script が実行可能でない"
    assert harness.resolve_agentd_bin() == sibling


def test_path_fallback_when_no_sibling(monkeypatch, tmp_path):
    monkeypatch.delenv("CONFORMANCE_AGENTD_BIN", raising=False)
    # interpreter 隣接に console script が無い状況を偽装し、PATH 解決へ落ちる
    lonely = tmp_path / "lonely-bin"
    lonely.mkdir()
    monkeypatch.setattr(harness.sys, "executable", str(lonely / "python"))
    path_dir = tmp_path / "path-bin"
    path_dir.mkdir()
    path_bin = path_dir / "doeff-sessionhost"
    path_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    assert harness.resolve_agentd_bin() == path_bin


def test_missing_everywhere_is_loud(monkeypatch, tmp_path):
    # silent fallback 禁止(R7): 解決不能は AssertionError で loud fail
    monkeypatch.delenv("CONFORMANCE_AGENTD_BIN", raising=False)
    lonely = tmp_path / "lonely-bin"
    lonely.mkdir()
    monkeypatch.setattr(harness.sys, "executable", str(lonely / "python"))
    monkeypatch.setenv("PATH", str(lonely))
    try:
        harness.resolve_agentd_bin()
    except AssertionError:
        return
    raise AssertionError("解決不能なのに loud fail しなかった")


def test_no_retired_crate_binding_left():
    # cargo 経路・crate dir 束縛の残骸ゼロ(#575 M1 — semgrep exclude 撤去と対)
    assert not hasattr(harness, "AGENTD_CRATE")
    src = Path(harness.__file__).read_text(encoding="utf-8")
    assert "cargo" not in src, "harness に cargo 束縛の残骸がある"
    assert "build_agentd" not in src, "誤称 build_agentd(もう build しない)が残っている"
