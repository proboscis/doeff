"""sessionhost再起動後のprocess停止。PIDと起動時刻の両方が一致する場合だけ操作する。"""

import psutil

from doeff_agents.sessionhost.cache_host_model import CacheProcessIdentity


def identify_process(pid: int) -> CacheProcessIdentity | None:
    try:
        process = psutil.Process(pid)
        return CacheProcessIdentity(pid, process.create_time())
    except psutil.NoSuchProcess:
        return None


def stop_identified_process(identity: CacheProcessIdentity) -> None:
    """停止を確認できなければ例外。呼び手は会話への排他を解かない。"""
    try:
        process = psutil.Process(identity.pid)
        if process.create_time() != identity.created_at:
            return
        if process.status() == psutil.STATUS_ZOMBIE:
            return
        # psutilのsignal操作もPIDの再利用を再検査する。
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    except psutil.NoSuchProcess:
        return
