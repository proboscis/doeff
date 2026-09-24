"""受け付け(claim から送信まで)が拍を塞がないことの焦点の検(card acp:kanban-issue:ki-e786e72e2ae7)。

本番の実測(2026-09-24・pool の pod agentd-pool-1): 受け付けの 1 本(預かり所からの借り・郵便と履歴の読み・器の
起動)が 5.5〜34 秒かかり、その間は同じ拍の他の job の claim も、走っている手番の実況も、器の観測も止まっていた。
ここでは本物の loop(runtime.run_loop — 本番と同じ scheduler と async-dispatch)を fake の世界の上で回し、
1 本の受け付けを預かり所の借りで止めたまま、別の会話の job が 1 秒以内に送られ、拍が回り続けることを見る。
"""

import threading
import time
from collections.abc import Callable

from doeff_agents.sessionhost.acp.effects import (
    AGENT_JOB_KIND,
    AGENT_JOB_NAMESPACE,
    AGORA_KINDS_NAMESPACE,
    MESSAGE_KIND,
    METRIC_TICK_MS,
    PHASE_RUNNING,
    CustodyLeaseBorrow,
)
from doeff_agents.sessionhost.acp.runtime import StateHolder, run_loop
from doeff_vm import EffectBase, K, Pass, Resume
from test_sessionhost_acp import TOKEN, World, bound_job, row

SLOW_CONVERSATION = "c-01ARZ3NDEKTSV4RRFFQ69G5FAA"
FAST_CONVERSATION = "c-01ARZ3NDEKTSV4RRFFQ69G5FBB"


def _mail(world: World, message_id: str, body: str) -> None:
    world.acp.put_row(
        row(AGORA_KINDS_NAMESPACE, MESSAGE_KIND, message_id, {"id": message_id, "body": body}, {"state": "inbox"})
    )


def _wait_until(predicate: Callable[[], bool], seconds: float) -> bool:
    """本物の loop は別の thread で回るので、実時間で条件を待つ(10 ms ごとに読み直す)。"""
    tick = threading.Event()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        tick.wait(0.01)
    return predicate()


def _sent_to_job(world: World, job_id: str) -> bool:
    job = world.acp.rows.get(f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:{job_id}")
    if job is None or job.status is None or job.status.get("phase") != PHASE_RUNNING:
        return False
    handle = job.status.get("sessionHandle")
    if not isinstance(handle, dict):
        return False
    session_id = handle.get("sessionId")
    return any(sid == session_id for sid, _text, _fresh in world.sessions.sends) or any(
        launch.get("session_id") == session_id for launch in world.sessions.launches
    )


def test_a_slow_intake_does_not_hold_another_jobs_send_or_the_tick() -> None:
    world = World()
    world.custody.tokens["slow"] = TOKEN
    _mail(world, "lt-1", "slow hello")
    _mail(world, "lt-2", "fast hello")
    # 行の順で先に来る job(a-…)の受け付けを止める — 直列の拍なら後ろの b-… は a-… の後ろに並ぶ。
    world.acp.put_row(bound_job("a-slow", inputs=["lt-1"], account="slow", subject=SLOW_CONVERSATION))
    world.acp.put_row(bound_job("b-fast", inputs=["lt-2"], account="acct", subject=FAST_CONVERSATION))

    slow_entered = threading.Event()
    release = threading.Event()

    def custody(effect: EffectBase, k: K) -> Resume | Pass:
        if isinstance(effect, CustodyLeaseBorrow) and effect.account == "slow":
            slow_entered.set()
            release.wait(10)
        return world.custody.dispatch(effect, k)

    stop = threading.Event()
    holder = StateHolder()
    errors: list[str] = []

    def loop() -> None:
        run_loop(
            world.settings,
            [world.acp.dispatch, custody, world.sessions.dispatch, world.local.dispatch],
            stop,
            errors.append,
            holder,
        )

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        assert slow_entered.wait(5), "遅い job の受け付けが始まらない"
        entered_at = time.monotonic()
        ticks_before = sum(1 for line in world.local.metrics if line.get("metric") == METRIC_TICK_MS)
        assert _wait_until(lambda: _sent_to_job(world, "b-fast"), 1.0), (
            "遅い受け付けが止まっている間に、別の会話の job が 1 秒以内に送られない"
            f"(sends={world.sessions.sends} launches={[x.get('session_id') for x in world.sessions.launches]})"
        )
        assert time.monotonic() - entered_at < 1.0
        # 拍は回り続ける(heartbeat・観測・受けの腕が受け付けの後ろに並ばない)。
        assert _wait_until(
            lambda: sum(1 for line in world.local.metrics if line.get("metric") == METRIC_TICK_MS) >= ticks_before + 3,
            1.0,
        ), "遅い受け付けの間に拍が回らない"
        assert not _sent_to_job(world, "a-slow"), "止めている受け付けが先へ進んでいる"
        release.set()
        assert _wait_until(lambda: _sent_to_job(world, "a-slow"), 5.0), "放した受け付けが送られない"
        # 受け付けを終えた job は memory に 1 度だけ載る(2 度受けない・拾い直しの腕に渡らない)。
        assert _wait_until(
            lambda: sorted(job.job_id for job in holder.state.jobs) == ["a-slow", "b-fast"], 2.0
        ), [job.job_id for job in holder.state.jobs]
        assert len(world.sessions.launches) == 2, world.sessions.launches
        assert sorted(purpose for _kind, _account, purpose in world.custody.borrowed) == [
            "agent-job a-slow",
            "agent-job b-fast",
        ]
    finally:
        release.set()
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    assert errors == [], errors


def test_merged_intake_takes_only_the_jobs_own_entries() -> None:
    """I3: 受け付けが返した状態からは、その job の項だけを写す(受け付けの間に拍が進めた他の項を古い写しで戻さない)。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp.judgment import merged_intake

    from doeff import run

    world = World()
    _mail(world, "lt-1", "hello")
    world.acp.put_row(bound_job("s-1", inputs=["lt-1"]))
    world.tick()
    (claimed,) = world.state.jobs
    other = replace(claimed, job_id="s-other", subject="c-other")
    snapshot = replace(world.state, jobs=(), deferred=("s-old",))
    after = replace(snapshot, jobs=(claimed,), deferred=("s-old", "s-1"), last_resync_ms=1)
    current = replace(world.state, jobs=(other,), deferred=(), last_resync_ms=99)
    merged = run(merged_intake(current, "s-1", after))
    assert [job.job_id for job in merged.jobs] == ["s-other", "s-1"]
    assert merged.deferred == ("s-1",)
    assert merged.last_resync_ms == 99


def test_a_second_turn_of_the_same_conversation_waits_for_the_first_intake() -> None:
    """I2: 1 つの会話の受け付けは同時に 1 本まで — 受け付け中の会話の Bound は claim せず持ち越す。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp.effects import (
        INTAKE_ROUTE_DEFER,
        INTAKE_ROUTE_INLINE,
        INTAKE_ROUTE_SPAWN,
    )
    from doeff_agents.sessionhost.acp.judgment import intake_route_of

    from doeff import run

    world = World()
    busy = replace(world.state, intakes=(("s-1", SLOW_CONVERSATION),))
    assert run(intake_route_of(busy, SLOW_CONVERSATION)) == INTAKE_ROUTE_DEFER
    assert run(intake_route_of(busy, FAST_CONVERSATION)) == INTAKE_ROUTE_SPAWN
    _mail(world, "lt-1", "hello")
    world.acp.put_row(bound_job("s-1", inputs=["lt-1"], subject=FAST_CONVERSATION))
    world.tick()
    assert run(intake_route_of(world.state, FAST_CONVERSATION)) == INTAKE_ROUTE_INLINE


def test_to_send_metric_names_the_wait_since_the_binding() -> None:
    """計器 agent-job-to-send は生まれからの所要(配置の待ちを含む)に加えて、結ばれてから送るまで(受け付けの所要)を
    名乗る — 配置の待ちと受け付けを 1 行で分ける(card acp:kanban-issue:ki-e786e72e2ae7 の「段ごとに測る計器」)。
    結びの時刻は配置が書く status.binding.at(epoch ms)。欄の無い行は今日の欄だけ。"""
    from dataclasses import replace

    world = World()
    _mail(world, "lt-1", "hello")
    job = bound_job("s-1", inputs=["lt-1"])
    assert job.status is not None
    binding = dict(job.status["binding"])  # type: ignore[arg-type]
    binding["at"] = 700
    world.acp.put_row(replace(job, status={**job.status, "binding": binding}))
    world.tick()
    line = [m for m in world.local.metrics if m["metric"] == "agent-job-to-send"][-1]
    assert line["boundAtMs"] == 700, line
    assert line["boundToSendMs"] == line["sentAtMs"] - 700, line

    plain = World()
    _mail(plain, "lt-1", "hello")
    plain.acp.put_row(bound_job("s-1", inputs=["lt-1"]))
    plain.tick()
    line = [m for m in plain.local.metrics if m["metric"] == "agent-job-to-send"][-1]
    assert "boundAtMs" not in line and "boundToSendMs" not in line, line


def test_a_sent_turn_names_the_intake_stage_by_stage() -> None:
    """送れた手番は受け付けの段ごとの所要を 1 行名乗る(起こし方の解き・郵便・借り・温かい session の片付け・
    器の起動 / 送り・送った後の書き)— どの段が 2 秒を食っているかを本番の log から読むため。"""
    world = World()
    _mail(world, "lt-1", "hello")
    world.acp.put_row(bound_job("s-1", inputs=["lt-1"]))
    world.tick()
    stages = [m for m in world.local.metrics if m["metric"] == "agent-job-intake-stages"]
    assert len(stages) == 1, world.local.metrics
    line = stages[0]
    parts = ["resolveMs", "mailMs", "borrowMs", "warmMs", "incarnateMs", "afterStartMs"]
    assert all(isinstance(line[name], int) and line[name] >= 0 for name in parts), line
    assert sum(line[name] for name in parts) == line["totalMs"], line
