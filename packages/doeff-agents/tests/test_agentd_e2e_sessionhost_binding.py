"""agentd e2e 群の sessionhost 束縛(issue #575 M2・card ki-bb337cfd0336)。

daemon under test は host の入口の module(`python -m doeff_agents.sessionhost.host`)であり、
退役 Rust crate(rollback tag agentd-rust-final に保存)のビルド(cargo)・path 算出にも、
CLI コマンドの PATH の探索にも依存しない。起動の argv の頭は
sessionhost_bin.sessionhost_command の単一実装: DOEFF_AGENTD_BIN(明示 seam、契約語彙)→
無ければ実行中 interpreter の -m(ADR-DOE-AGENTS-004 R7 / retired-impl-lives-only-in-git-history)。
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def test_explicit_seam_wins(monkeypatch, tmp_path):
    from sessionhost_bin import sessionhost_command

    fake = tmp_path / "fake-agentd"
    monkeypatch.setenv("DOEFF_AGENTD_BIN", str(fake))
    assert sessionhost_command() == (str(fake),)


def test_default_is_the_entry_module_on_the_running_interpreter(monkeypatch, tmp_path):
    from sessionhost_bin import sessionhost_command

    monkeypatch.delenv("DOEFF_AGENTD_BIN", raising=False)
    # PATH に何が在っても(古い console script でも)見ない。
    monkeypatch.setenv("PATH", str(tmp_path))
    assert sessionhost_command() == (sys.executable, "-m", "doeff_agents.sessionhost.host")


def test_the_package_declares_no_sessionhost_console_script():
    # card ki-bb337cfd0336: code starts the host through the StartSessionHost effect and the
    # entry module — a console script would be a second, PATH-resolved way to start it.
    import tomllib

    pyproject = tomllib.loads((TESTS_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert "doeff-sessionhost" not in pyproject["project"]["scripts"]


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
