"""driver 層の program の待ちは doeff-time の ``Delay``(card ki-3c3d85253c2c・ADR-DOE-TIME-001 R1)。

program は時間の効果を yield し、1 秒が何であるかは呼び手が置く時間の handler が決める。ここの検は、program と
I/O の世界の間に仮想の時計(sim_time_handler)を挟み、時計がどれだけ進んだかを読む — program が自前の待ちの
効果を出すと、仮想の時計は進まず、ここで赤になる。

待ちは 4 か所: tmux.py の貼り付けの後の落ち着き待ち・送信の確認の最初の待ち・Enter の送り直しの間の待ちと、
agentd_client.py の常駐の起動待ち。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import hy  # noqa: F401  # .hy import hook — the I/O handlers are Hy modules
from doeff_agents._agentd_client import (
    AGENTD_START_POLL_SECONDS,
    AgentdClient,
    _wait_for_agentd_ready,
)
from doeff_agents.io_fake import with_fake_io
from doeff_agents.io_handlers import run_driver_io
from doeff_agents.io_root import IoRoot
from doeff_agents.tmux import (
    CONFIRM_INITIAL_SECONDS,
    CONFIRM_RETRY_WAITS,
    PASTE_SETTLE_SECONDS,
    send_keys_program,
)
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, SimClock, sim_time_handler
from fake_io_support import FakeIoWorld, ProcessOutcome

from doeff import Program, run

EMPTY_COMPOSER = "\u276f\u00a0\n"
PASTE_LEFT_IN_COMPOSER = "\u276f [Pasted text #1 +3 lines]\n"
AGENTD_SOCKET = "/run/agentd.sock"
AGENTD_DB = "/run/agentd.db"


def virtual_time_io_root(world: FakeIoWorld, clock: SimClock) -> IoRoot:
    """記憶の中の世界の内側に仮想の時計を置いた root — program の Delay は ``clock`` を進める。"""

    def root(program: Program) -> object:
        return run(scheduled(with_fake_io(world, sim_time_handler(clock=clock)(program))))

    return root


def captures(*outputs: str) -> Callable[[tuple[str, ...]], ProcessOutcome]:
    """capture-pane に ``outputs`` を順に答え(尽きたら最後の 1 つ)、他の tmux の命令は成功させる台本。"""
    shown = iter(outputs)

    def script(argv: tuple[str, ...]) -> ProcessOutcome:
        stdout = next(shown, outputs[-1]) if argv[1] == "capture-pane" else ""
        return ProcessOutcome(exit_code=0, stdout=stdout, stderr="", timed_out=False)

    return script


def listener_ready_on_the_third_ask() -> Callable[[str], str]:
    """状態の問いを 2 度断り、3 度目に答える常駐の socket の台本。"""
    ready = iter((False, False, True))

    def respond(payload: str) -> str:
        request_id = json.loads(payload)["id"]
        if next(ready):
            return json.dumps({"id": request_id, "ok": True, "result": {"db_path": AGENTD_DB}})
        return json.dumps({"id": request_id, "ok": False, "error": "starting"})

    return respond


def test_a_submitted_paste_waits_the_settle_and_the_first_confirm_on_the_time_handler() -> None:
    world = FakeIoWorld(processes={"*": captures(EMPTY_COMPOSER)})
    clock = SimClock()
    started = clock.current_time

    virtual_time_io_root(world, clock)(send_keys_program("tmux", "%42", "hello", literal=True, enter=True))

    assert clock.current_time - started == timedelta(seconds=PASTE_SETTLE_SECONDS + CONFIRM_INITIAL_SECONDS)


def test_each_enter_retry_waits_on_the_time_handler() -> None:
    # 貼り付けが 2 度の確認の間 composer に残り、3 度目で消える — Enter の送り直しは 2 度。
    world = FakeIoWorld(processes={"*": captures(PASTE_LEFT_IN_COMPOSER, PASTE_LEFT_IN_COMPOSER, EMPTY_COMPOSER)})
    clock = SimClock()
    started = clock.current_time

    virtual_time_io_root(world, clock)(send_keys_program("tmux", "%42", "hello", literal=True, enter=True))

    waited = PASTE_SETTLE_SECONDS + CONFIRM_INITIAL_SECONDS + sum(CONFIRM_RETRY_WAITS[:2])
    assert clock.current_time - started == timedelta(seconds=waited)
    enters = [argv for argv, _stdin, _cwd in world.commands if argv[1:] == ("send-keys", "-t", "%42", "Enter")]
    assert len(enters) == 3


def test_waiting_for_agentd_polls_on_the_time_handler() -> None:
    world = FakeIoWorld(sockets={AGENTD_SOCKET: listener_ready_on_the_third_ask()})
    clock = SimClock()
    started = clock.current_time
    root = virtual_time_io_root(world, clock)

    ready = _wait_for_agentd_ready(
        AgentdClient(AGENTD_SOCKET, io_root=root),
        expected_db_path=Path(AGENTD_DB),
        expected_socket_path=Path(AGENTD_SOCKET),
        command=["doeff-agentd"],
        timeout=5.0,
        io_root=root,
    )

    assert ready is True
    assert clock.current_time - started == timedelta(seconds=2 * AGENTD_START_POLL_SECONDS)


def test_the_fake_world_answers_a_delay_on_its_own_clock() -> None:
    # 時間の handler を挟まない root(fake_io_root の形)では、記憶の中の世界が自分の時計を待った秒だけ進める。
    world = FakeIoWorld()
    before = world.clock

    run(with_fake_io(world, Delay(0.5)))

    assert world.clock - before == 0.5
    assert world.sleeps == [0.5]


def test_the_production_root_answers_a_delay() -> None:
    # 本番の root(run_driver_io)は時間の handler を持つ — 無ければ Delay は答えの無い効果で落ちる。
    assert run_driver_io(Delay(0.001)) is None
