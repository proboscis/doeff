"""agent-job の終端は必ず result.cause を運ぶ(段 12 lane 12k・agora-redesign #349 行 3 粒 3a・既知の形 CI runner (i)
「手番の終わりに終端の状態を必ず返す」・ADR-DOE-AGENTS-012 R47)— 終端の場面の表。

書き手は agentd の 1 者(judgment.ended-status-of の 1 点が result に cause を載せる)・読み手 ACP の awaitOutcomeOf は
cause の category で終端の意味を読み、result の有無や conditions 頼みにしない。category は effects.CauseCategory の閉語彙
5 語(契約 ACP docs/contracts/scheduling.json resultCause.categories の写し)。fake の handler で同じ program(agentd.hy)を
一周させる(test_sessionhost_acp.py の World)。
"""

from __future__ import annotations

from typing import get_args

import hy  # noqa: F401  # registers the .hy importer
import pytest
from doeff_agents.sessionhost.acp import judgment
from doeff_agents.sessionhost.acp.effects import (
    AGENT_JOB_KIND,
    AGENT_JOB_NAMESPACE,
    CAUSE_CATEGORIES,
    CAUSE_CATEGORY_AGENTD_STOPPED,
    CAUSE_CATEGORY_CANCELLED,
    CAUSE_CATEGORY_COMPLETED,
    CAUSE_CATEGORY_FAILED,
    CAUSE_CATEGORY_INTERRUPTED,
    PHASE_ENDED,
    CauseCategory,
    JobOutcome,
    JSONObject,
)
from test_sessionhost_acp import (
    World,
    bound_job,
    message,
    row,
    running_job,
    turn_record_row,
)

from doeff import run


def _start(world: World, job_id: str = "j-1") -> str:
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job(job_id, inputs=["m-1"]))
    world.tick()
    assert len(world.state.jobs) == 1
    return world.sid(job_id)


def _status_of(world: World, job_id: str) -> JSONObject:
    job = world.job(job_id)
    assert job.status is not None
    return job.status


def _result_of(world: World, job_id: str) -> JSONObject:
    result = _status_of(world, job_id)["result"]
    assert isinstance(result, dict)
    return result


def _condition_types(status: JSONObject) -> list[str]:
    conditions = status["conditions"]
    assert isinstance(conditions, list)
    return [str(item["type"]) for item in conditions if isinstance(item, dict)]


def test_the_closed_categories_are_the_contracts_five_words() -> None:
    """category の閉語彙は 5 語(D-349r3a-1: condition の型と 1:1 にしない — reason が語を運ぶ)。定義点は effects.CauseCategory の
    Literal 1 点で、表 CAUSE_CATEGORIES はそれから導く。ACP の契約 scheduling.json resultCause.categories と同じ綴り・同じ順。"""
    words = CAUSE_CATEGORIES
    assert words == ("completed", "cancelled", "failed", "interrupted", "agentd-stopped")
    assert words == get_args(CauseCategory)
    assert words == (
        CAUSE_CATEGORY_COMPLETED,
        CAUSE_CATEGORY_CANCELLED,
        CAUSE_CATEGORY_FAILED,
        CAUSE_CATEGORY_INTERRUPTED,
        CAUSE_CATEGORY_AGENTD_STOPPED,
    )


def test_a_naturally_ended_turn_carries_completed_with_or_without_a_value() -> None:
    """場面 1 / 2: 自然に終わった手番 = {category: completed}。value があれば同じ result に(dict の結末はその欄 cause に)、
    無ければ cause だけ。"""
    world = World()
    sid = _start(world)
    world.sessions.finish(sid, "done", {"ok": True})
    world.tick(advance_ms=1_000)
    assert _result_of(world, "j-1") == {"ok": True, "cause": {"category": "completed"}}
    assert _status_of(world, "j-1")["phase"] == PHASE_ENDED

    bare = World()
    sid2 = _start(bare, "j-2")
    bare.sessions.finish(sid2, "done")
    bare.tick(advance_ms=1_000)
    assert _result_of(bare, "j-2") == {"cause": {"category": "completed"}}


def test_a_failed_session_carries_failed_with_the_condition_type() -> None:
    """場面 3: done 以外の終端 = failed / SessionFailed(条件 SessionFailed と同じ拍)。"""
    world = World()
    sid = _start(world)
    world.sessions.finish(sid, "failed")
    world.tick(advance_ms=1_000)
    assert _result_of(world, "j-1") == {"cause": {"category": "failed", "reason": "SessionFailed"}}
    assert _condition_types(_status_of(world, "j-1"))[-1] == "SessionFailed"


def test_a_provider_limit_refusal_carries_failed_with_provider_limit_and_no_value() -> None:
    """場面 4: provider の限度で断られた手番 = 条件 ProviderLimit(段 11 lane 11n 便 C)+ result.cause {failed, ProviderLimit}。
    value は書かない(value は手番が報告した結果)— 「result に書かない」の旧規則は「value は書かない・cause は書く」へ。"""
    world = World()
    sid = _start(world)
    world.sessions.finish(sid, "failed", cause={"category": "rate_limited", "reason": "You've reached your Opus limit"})
    world.tick(advance_ms=1_000)
    assert _result_of(world, "j-1") == {"cause": {"category": "failed", "reason": "ProviderLimit"}}
    types = _condition_types(_status_of(world, "j-1"))
    assert "ProviderLimit" in types
    assert "SessionFailed" in types


def test_a_turn_closed_without_a_session_carries_failed_with_the_closing_condition() -> None:
    """場面 5: session なしで閉じる終端(end-job-now・実弾 002 の孤児 = SessionFailed)= failed / <閉じた条件の型>。"""
    world = World()
    world.acp.put_row(running_job("s-orphan"))
    world.acp.put_row(turn_record_row("s-orphan"))
    world.tick()
    assert _result_of(world, "s-orphan") == {"cause": {"category": "failed", "reason": "SessionFailed"}}


def test_a_withdrawn_running_turn_carries_interrupted_withdrawn_on_the_withdrawn_row() -> None:
    """場面 6: 取り下げ(phase Withdrawn — 書き手は作った側)で走っている手番を止めた = Withdrawn の行に result.cause
    {interrupted, withdrawn}(条件 Interrupted と同じ書き・phase は書かない)。"""
    world = World()
    _start(world)
    running = world.job("j-1")
    assert running.status is not None
    withdrawn: JSONObject = dict(running.status)
    withdrawn["phase"] = "Withdrawn"
    world.acp.put_row(row(AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, "j-1", running.spec, withdrawn, created_at_ms=running.created_at_ms))
    world.tick(advance_ms=1_000)
    status = _status_of(world, "j-1")
    assert status["phase"] == "Withdrawn"
    assert status["result"] == {"cause": {"category": "interrupted", "reason": "withdrawn"}}
    assert _condition_types(status) == ["Interrupted"]


def test_the_stop_of_agentd_carries_agentd_stopped_drain_deadline() -> None:
    """場面 7: agentd の停止の排水の期限で閉じた手番 = agentd-stopped / drain-deadline(条件 AgentdRestart と同じ拍)。"""
    world = World()
    _start(world)
    world.close_for_stop("SIGTERM")
    assert _result_of(world, "j-1") == {"cause": {"category": "agentd-stopped", "reason": "drain-deadline"}}
    assert _condition_types(_status_of(world, "j-1"))[-1] == "AgentdRestart"


def test_the_terminal_write_refuses_a_missing_or_foreign_cause_and_composes_the_result() -> None:
    """判断の純関数: terminal-cause-of は閉語彙の外を断る・ended-status-of は cause 無し / 語彙外を断り、result に cause を
    載せる(dict の結末 → 欄 cause・None → cause だけ・dict でない結末 → {value, cause})・command-cause-of は条件の有無で
    completed / failed(先頭の条件の型)・outcome-with-limit は completed / failed だけを ProviderLimit に置き換え、取り消しの
    cause は上書きしない。"""
    assert run(judgment.terminal_cause_of("completed", None)) == {"category": "completed"}
    assert run(judgment.terminal_cause_of("failed", "LaunchFailed")) == {"category": "failed", "reason": "LaunchFailed"}
    with pytest.raises(ValueError, match="outside"):
        run(judgment.terminal_cause_of("exploded", None))
    completed = {"category": "completed"}
    assert run(judgment.ended_status_of({"phase": "Running"}, {"ok": True}, completed, ())) == {
        "phase": "Ended",
        "result": {"ok": True, "cause": completed},
    }
    assert run(judgment.ended_status_of({"phase": "Running"}, None, completed, ())) == {"phase": "Ended", "result": {"cause": completed}}
    assert run(judgment.ended_status_of({"phase": "Running"}, "text", completed, ()))["result"] == {"value": "text", "cause": completed}
    with pytest.raises(ValueError, match="Ended without a contract cause"):
        run(judgment.ended_status_of({"phase": "Running"}, None, {"category": "exploded"}, ()))
    with pytest.raises(ValueError, match="Ended without a contract cause"):
        run(judgment.ended_status_of({"phase": "Running"}, None, {}, ()))
    assert run(judgment.command_cause_of(())) == {"category": "completed"}
    assert run(judgment.command_cause_of(({"type": "VerifyCommandLost", "status": "True", "reason": "x"},))) == {
        "category": "failed",
        "reason": "VerifyCommandLost",
    }
    limit = {"type": "ProviderLimit", "status": "True", "reason": "rate-limited", "model": "m", "message": "limit"}
    done = JobOutcome(ended=True, result={"ok": True}, cause={"category": "completed"}, conditions=())
    assert run(judgment.outcome_with_limit(done, None)) == done
    assert run(judgment.outcome_with_limit(done, limit)).cause == {"category": "failed", "reason": "ProviderLimit"}
    assert run(judgment.outcome_with_limit(done, limit)).result == {"ok": True}
    cancelled = JobOutcome(ended=True, result=None, cause={"category": "cancelled", "stage": "graceful", "reason": "operator"}, conditions=())
    assert run(judgment.outcome_with_limit(cancelled, limit)) == cancelled
