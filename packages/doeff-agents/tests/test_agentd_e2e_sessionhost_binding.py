"""agentd e2e 群の sessionhost 束縛(issue #575 M2)。

daemon under test は正典 executor doeff-sessionhost であり、退役 Rust crate
(rollback tag agentd-rust-final に保存)のビルド(cargo)・path 算出に
依存しない。解決は
sessionhost_bin.resolve_sessionhost_bin の単一実装: DOEFF_AGENTD_BIN(明示
seam、契約語彙)→ 実行中 interpreter 隣接の console script → PATH → 解決
不能は loud fail(ADR-DOE-AGENTS-004 R7 / retired-impl-lives-only-in-git-history)。

TDD red(2026-07-30): 3 つの e2e が cargo build 束縛のため、ヘルパ実装 +
再束縛まで red。
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def test_resolve_explicit_seam_wins(monkeypatch, tmp_path):
    from sessionhost_bin import resolve_sessionhost_bin

    fake = tmp_path / "fake-agentd"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("DOEFF_AGENTD_BIN", str(fake))
    assert resolve_sessionhost_bin() == fake


def test_resolve_default_is_interpreter_adjacent(monkeypatch):
    from sessionhost_bin import resolve_sessionhost_bin

    monkeypatch.delenv("DOEFF_AGENTD_BIN", raising=False)
    sibling = Path(sys.executable).parent / "doeff-sessionhost"
    assert sibling.exists(), "前提: venv console script が無い(make sync を実行すること)"
    assert resolve_sessionhost_bin() == sibling


def test_resolve_missing_everywhere_is_loud(monkeypatch, tmp_path):
    import sessionhost_bin

    monkeypatch.delenv("DOEFF_AGENTD_BIN", raising=False)
    lonely = tmp_path / "lonely-bin"
    lonely.mkdir()
    monkeypatch.setattr(sessionhost_bin.sys, "executable", str(lonely / "python"))
    monkeypatch.setenv("PATH", str(lonely))
    try:
        sessionhost_bin.resolve_sessionhost_bin()
    except AssertionError:
        return
    raise AssertionError("解決不能なのに loud fail しなかった")


def test_e2e_supports_have_no_retired_crate_binding():
    # 退役 crate 束縛の残骸ゼロ: cargo 呼び出しと crate dir の path 算出
    # (semgrep doeff-agentd-retired-crate-reference-forbidden の runtime 対)
    for name in (
        "agentd_result_retry_e2e_support.py",
        "agentd_real_agent_result_retry_e2e_support.py",
        "test_agentd_byte_faithful_transport_e2e.py",
    ):
        src = (TESTS_DIR / name).read_text(encoding="utf-8")
        assert "cargo" not in src, f"{name}: cargo 束縛が残っている"
        assert '/ "doeff-agentd"' not in src, f"{name}: crate dir の path 算出が残っている"
