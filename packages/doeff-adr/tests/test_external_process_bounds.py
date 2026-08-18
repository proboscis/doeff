"""外部 process 接触の待ちには必ず上限がある — 打ち切りは「検査通過」ではない。

doeff-adr は semgrep と pytest を子 process として起動する。この待ちは ADR 検査の
門の中にあるため、子が返らなければ門も返らず、門を待つ CI / land 列 / agent 席が
まとめて止まる。ホスト高負荷帯では外部 process の応答が実際に遅れることが観測
されている(2026-08-18 host-load 実測: load1 220〜266 = 12〜15/CPU、disk0 が瞬時
45,000 tps・723 MB/s に達する帯で `ls` 1 発が 120 秒で完走しない事例)。

打ち切りの読み方も検査の意味の一部である。走らなかった scan を「発火なし」と黙読
すると scanner の故障が偽の赤/緑に化ける(registry の既存コメントと同じ失敗の型)
のと同様に、打ち切られた scan を「findings なし」「検査通過」と読んではならない。
"""

import json
import math
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest
from doeff_adr import cli
from doeff_adr.process import (
    DEFAULT_EXTERNAL_PROCESS_TIMEOUT_SECONDS,
    EXTERNAL_PROCESS_TIMEOUT_ENV,
    ExternalProcessTimeoutError,
    external_process_timeout_seconds,
    run_external_process,
)
from doeff_adr.registry import _run_semgrep


def _hanging_script(tmp_path: Path, name: str) -> Path:
    """意図的に固まる子 process — 上限が無ければ呼び出し側は返らない。"""
    path = tmp_path / name
    path.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_run_external_process_stops_waiting_at_the_bound(tmp_path):
    """固まる子を短い上限で起動 → 無期限に待たず ExternalProcessTimeoutError。"""
    hanging = _hanging_script(tmp_path, "hangs")

    started = time.monotonic()
    with pytest.raises(ExternalProcessTimeoutError) as excinfo:
        run_external_process([str(hanging)], timeout_seconds=0.5)
    elapsed = time.monotonic() - started

    assert elapsed < 20.0, f"上限が効いていない(子の sleep 30 を待ち切った): {elapsed}s"
    message = str(excinfo.value)
    assert str(hanging) in message, f"どの command が打ち切られたか読めない: {message}"
    assert "0.5" in message, f"何秒で打ち切られたか読めない: {message}"
    assert excinfo.value.timeout_seconds == pytest.approx(0.5)
    assert excinfo.value.command == [str(hanging)]


def test_run_external_process_applies_the_bound_without_being_asked(tmp_path, monkeypatch):
    """呼び出し地点が timeout を渡さなくても上限は付く — 任意項目ではない。"""
    hanging = _hanging_script(tmp_path, "hangs-default")
    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "0.5")

    started = time.monotonic()
    with pytest.raises(ExternalProcessTimeoutError):
        run_external_process([str(hanging)])
    elapsed = time.monotonic() - started

    assert elapsed < 20.0, f"設定された上限が起動へ渡っていない: {elapsed}s"


def test_default_bound_is_finite_and_reaches_subprocess(monkeypatch):
    """既定経路でも subprocess へ有限の timeout が渡る(env 未設定でも無期限にしない)。"""
    monkeypatch.delenv(EXTERNAL_PROCESS_TIMEOUT_ENV, raising=False)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_external_process(["true"])

    bound = seen["timeout"]
    assert bound == DEFAULT_EXTERNAL_PROCESS_TIMEOUT_SECONDS
    assert isinstance(bound, float)
    assert math.isfinite(bound)
    assert bound > 0


def test_external_process_timeout_seconds_is_overridable_by_env(monkeypatch):
    monkeypatch.delenv(EXTERNAL_PROCESS_TIMEOUT_ENV, raising=False)
    assert external_process_timeout_seconds() == DEFAULT_EXTERNAL_PROCESS_TIMEOUT_SECONDS

    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "0.25")
    assert external_process_timeout_seconds() == pytest.approx(0.25)


def test_external_process_timeout_env_rejects_nonsense_loudly(monkeypatch):
    """読めない上限は「上限なし」に落とさない — 黙って無期限へ戻る経路を作らない。"""
    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "forever")

    with pytest.raises(ValueError, match=EXTERNAL_PROCESS_TIMEOUT_ENV):
        external_process_timeout_seconds()

    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "0")
    with pytest.raises(ValueError, match=EXTERNAL_PROCESS_TIMEOUT_ENV):
        external_process_timeout_seconds()


def test_run_semgrep_timeout_is_loud_not_empty_findings(tmp_path, monkeypatch):
    """固まる semgrep → 「findings なし」ではなく打ち切りを名指す AssertionError。"""
    hanging = _hanging_script(tmp_path, "fake-semgrep-hangs")
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")
    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "0.5")

    started = time.monotonic()
    with pytest.raises(AssertionError) as excinfo:
        _run_semgrep(str(hanging), config, [target], project_root=tmp_path)
    elapsed = time.monotonic() - started

    assert elapsed < 20.0, f"semgrep の待ちに上限が無い: {elapsed}s"
    message = str(excinfo.value)
    assert "0.5" in message, f"何秒で打ち切られたか読めない: {message}"
    assert str(hanging) in message, f"どの command が打ち切られたか読めない: {message}"
    assert "did not match" not in message, f"打ち切りが『発火なし』に化けている: {message}"


def test_verify_wiring_timeout_exits_nonzero_with_diagnosis(tmp_path, monkeypatch, capsys):
    """pytest --collect-only が固まる → 「wiring verified」と読ませず非ゼロで落ちる。"""
    hanging = _hanging_script(tmp_path, "python-hangs")
    monkeypatch.setattr(cli.sys, "executable", str(hanging))
    monkeypatch.setenv(EXTERNAL_PROCESS_TIMEOUT_ENV, "0.5")

    started = time.monotonic()
    exit_code = cli.main(["verify-wiring"])
    elapsed = time.monotonic() - started

    assert elapsed < 20.0, f"verify-wiring の待ちに上限が無い: {elapsed}s"
    # pytest 自身の終了コード(0..5)と重ならない値 — 打ち切りと collection の赤を
    # 呼び出し側が取り違えない。
    assert exit_code == cli.TIMEOUT_EXIT_CODE
    assert exit_code not in range(6)
    captured = capsys.readouterr()
    assert "wiring verified" not in captured.out
    assert str(hanging) in captured.err
    assert re.search(r"0\.5", captured.err), f"何秒で打ち切られたか読めない: {captured.err}"
