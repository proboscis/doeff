"""一方の同期I/Oが止まっても、同じVMの別の処理を実行できる。"""

import importlib
import threading

import hy  # noqa: F401  # registers the .hy importer
from doeff_agents.sessionhost.acp.effects import AcpGet, SessionGet
from doeff_vm import EffectBase, K, Pass, Resume

from doeff import run

programs = importlib.import_module("sessionhost_async_dispatch_deftests")


def test_normal_send_waits_for_ping_without_retrying_other_failures() -> None:
    programs.test_cache_maintenance_conflict_waits_without_failing_normal_job(run)
    programs.test_other_send_refusal_is_not_retried(run)


def test_blocking_io_does_not_park_another_vm_task() -> None:
    entered = threading.Event()
    pinged = threading.Event()

    def dispatch(effect: EffectBase, continuation: K) -> Resume | Pass:
        if isinstance(effect, AcpGet):
            entered.set()
            assert pinged.wait(2), "通常通信がVMを塞ぎ、専用操作が動けません"
            return Resume(continuation, ())
        if isinstance(effect, SessionGet):
            assert entered.wait(2)
            pinged.set()
            return Resume(continuation, None)
        return Pass(effect, continuation)

    run(programs.exercise_async_dispatch(dispatch))
    assert pinged.is_set()


def test_worker_loop_keeps_maintenance_running_during_blocked_normal_io() -> None:
    from doeff_agents.sessionhost.acp.cache_operation import AcpCacheOperations
    from doeff_agents.sessionhost.acp.effects import (
        AGORA_KINDS_NAMESPACE,
        AcpRow,
        AgentdSettings,
        ClockNowMs,
    )
    from doeff_agents.sessionhost.acp.runtime import run_loop

    blocked = threading.Event()
    maintained = threading.Event()
    stop = threading.Event()
    errors: list[str] = []
    node = AcpRow(
        AGORA_KINDS_NAMESPACE, f"{AGORA_KINDS_NAMESPACE}:node:node", "node", "node",
        "v1", 1, 0, {}, {}, {"name": "node"}, {"state": "joined"},
    )

    def normal(effect: EffectBase, continuation: K) -> Resume | Pass:
        if isinstance(effect, ClockNowMs):
            blocked.set()
            assert maintained.wait(5), "通常処理のI/Oが専用操作のloopを止めています"
            raise RuntimeError("normal I/O finished after maintenance")
        return Pass(effect, continuation)

    def maintenance(effect: EffectBase, continuation: K) -> Resume | Pass:
        if isinstance(effect, AcpGet):
            assert blocked.wait(5)
            return Resume(continuation, (node,))
        if isinstance(effect, AcpCacheOperations):
            assert effect.node_row == "node"
            maintained.set()
            stop.set()
            return Resume(continuation, ())
        return Pass(effect, continuation)

    run_loop(AgentdSettings("node", node_capacity=0), (normal,), stop, errors.append,
             cache_dispatchers=(maintenance,))
    assert maintained.is_set()
    assert errors == ["agentd: tick failed: RuntimeError: normal I/O finished after maintenance"]
