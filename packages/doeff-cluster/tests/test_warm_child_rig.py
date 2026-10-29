"""待ちの子を起こす検は、温めた Hy の code の置き場を渡す共通の起こし方(fixture start_warm_child)を通す(card ki-2121cf77fe73)。

失敗ケース: 検が `-m doeff_cluster.worker.entry.warm_child` を subprocess.Popen で直に起こすと、env が探し道だけで置き場に届かず、
準備のたびに Hy を source から 25 秒以上 compile し直す(30 秒の期限と余裕が 0 になる)。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import warm_child_rig
from tests.warm_child_rig import warm_child_env

TESTS = Path(__file__).resolve().parent
RIG = {"warm_child_rig.py", "test_warm_child_rig.py"}
WARM_MODULE = re.compile(r"doeff_cluster\.worker\.entry\.warm_child")
SPAWN = re.compile(r"\bPopen\(|subprocess\.(?:run|call|check_call|check_output)\(")


def test_no_test_spawns_the_warm_child_outside_the_shared_rig() -> None:
    """待ちの子の module 名を持ち、process を起こす検は、共通の起こし方の file の外に無い(起こすなら fixture start_warm_child を通す)。"""
    offenders = sorted(
        path.name
        for path in TESTS.glob("test_*.py")
        if path.name not in RIG
        for text in [path.read_text(encoding="utf-8")]
        if WARM_MODULE.search(text) and SPAWN.search(text)
    )
    assert not offenders, f"待ちの子を start_warm_child の外で起こしている: {offenders}"


def test_the_shared_env_names_the_warmed_store(job_child_code_store: None) -> None:
    """共通の env は、温めた置き場(job_child_code_store が満たす所)の名指しを持つ。"""
    from doeff_hy_bytecode_guard._code_store import STORE_ENV, store_dir

    env = warm_child_env()
    assert env[STORE_ENV] == store_dir()
    assert "PYTHONPATH" in env


def test_a_start_that_never_gets_ready_fails_by_the_cold_store_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """準備の印が期限までに出ない(冷たい compile の形)と、期限の数字でなく置き場の名で落ちる。"""
    monkeypatch.setattr(warm_child_rig, "READY_SECONDS", 0.3)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stderr=subprocess.PIPE)
    try:
        with pytest.raises(AssertionError, match="Hy の code の置き場が使われていない"):
            warm_child_rig.wait_ready_file(process, tmp_path / "never.json")
    finally:
        process.kill()
        process.wait(timeout=10)
