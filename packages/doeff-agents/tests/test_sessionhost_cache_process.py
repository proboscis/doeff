"""再起動したhandlerが、記録済みの同じprocessだけを停止する。"""

import os
import subprocess
import sys
from dataclasses import replace

import psutil
import pytest

from doeff_agents.sessionhost.cache_process import identify_process, stop_identified_process


def test_process_identity_survives_handler_recreation_and_rejects_reused_pid() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        identity = identify_process(child.pid)
        assert identity is not None
        # 同じPIDでも起動時刻が異なるものは、記録されたprocessではない。
        stop_identified_process(replace(identity, created_at=identity.created_at - 10))
        assert child.poll() is None
        stop_identified_process(identity)
        child.wait(timeout=5)
        stop_identified_process(identity)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Linuxのwaitid(WNOWAIT)で未回収を保持する")
def test_exited_unreaped_process_is_not_an_active_cache_writer() -> None:
    """親がまだwaitしていなくても、終了済みprocessは履歴へ書き込めない。"""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert psutil.Process(child.pid).status() == psutil.STATUS_ZOMBIE
        assert identify_process(child.pid) is None
    finally:
        child.wait(timeout=5)
