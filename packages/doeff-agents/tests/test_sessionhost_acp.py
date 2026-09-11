"""agentd(sessionhost の ACP の腕・段 2 agora-redesign #19 / #20)の焦点の検。

fake の handler(doeff_agents.sessionhost.acp.fake)で同じ program(agentd.hy)を一周させる
e2e の形 1 本: 参加(node の lease)→ Bound の job を置く → Running + sessionHandle →
TurnDelta を 1 本押す → 手番の終わりに Ended と turn-record。加えて判断の純関数と弁。
HTTP も socket も tmux も無い。
"""

from __future__ import annotations

import json

import hy  # noqa: F401  # registers the .hy importer
import pytest
from doeff_agents.sessionhost.acp import judgment
from doeff_agents.sessionhost.acp.effects import (
    AGENT_JOB_KIND,
    AGENT_JOB_NAMESPACE,
    AGORA_KINDS_NAMESPACE,
    CLAUDE_OAUTH_TOKEN_ENV,
    JSON,
    MESSAGE_KIND,
    NODE_KIND,
    PHASE_BOUND,
    PHASE_ENDED,
    PHASE_RUNNING,
    TURN_RECORD_KIND,
    AcpRow,
    AgentdSettings,
    JSONObject,
    LeaseRefused,
    SessionView,
)
from doeff_agents.sessionhost.acp.fake import Birth, FakeAcp, FakeCustody, FakeLocal, FakeSessions
from doeff_agents.sessionhost.acp.runtime import initial_state, run_tick
from doeff_agents.sessionhost.acp.valve import ACP_VALVE_ENV, acp_valve

from doeff import run

NODE = "mac-1"
HOMES = "/homes"
TOKEN = "sk-ant-oat01-secret-token"


def row(
    namespace: str,
    kind: str,
    resource_id: str,
    spec: JSONObject,
    status: JSONObject | None,
    *,
    created_at_ms: int = 500,
) -> AcpRow:
    return AcpRow(
        namespace=namespace,
        key=f"{namespace}:{kind}:{resource_id}",
        kind=kind,
        resource_id=resource_id,
        version="v1",
        generation=1,
        created_at_ms=created_at_ms,
        labels={},
        payload={},
        spec=spec,
        status=status,
    )


CONVERSATION = "c-01ARZ3NDEKTSV4RRFFQ69G5FAV"


def bound_job(
    job_id: str,
    *,
    inputs: list[str],
    account: str | None = "acct",
    subject: str = CONVERSATION,
    predecessor: str | None = None,
    lifecycle: str | None = None,
    created_at_ms: int = 500,
) -> AcpRow:
    binding: JSONObject = {"node": NODE, "profile": "personal"}
    if account is not None:
        binding["account"] = account
    charter: JSONObject = {
        "session_id": job_id,
        "session_name": job_id,
        "agent_type": "claude",
        "work_dir": "/work",
        "prompt": "start",
        "model": "claude-opus-5",
    }
    if lifecycle is not None:
        charter["lifecycle"] = lifecycle
    spec: JSONObject = {
        "subject": subject,
        "inputs": list(inputs),
        "charter": charter,
    }
    if predecessor is not None:
        spec["affinity"] = {"predecessor": predecessor}
    status: JSONObject = {"phase": PHASE_BOUND, "binding": binding, "conditions": []}
    return row(
        AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, job_id, spec, status, created_at_ms=created_at_ms
    )


class World:
    """fake の 4 handler と値の宣言(test の 1 つの世界)。"""

    def __init__(self) -> None:
        self.settings = AgentdSettings(node_name=NODE, homes_root=HOMES)
        self.acp = FakeAcp(births={TURN_RECORD_KIND: Birth("state", "running")})
        self.custody = FakeCustody(tokens={"acct": TOKEN})
        self.sessions = FakeSessions()
        self.local = FakeLocal(now_ms=1_000)
        self.state = initial_state()
        self.acp.put_row(
            row(
                AGORA_KINDS_NAMESPACE,
                NODE_KIND,
                NODE,
                {"name": NODE, "labels": {}, "capacity": 1, "streamCapability": "frames"},
                {"state": "joined"},
            )
        )

    def tick(self, advance_ms: int = 0) -> None:
        self.local.now_ms += advance_ms
        self.state = run_tick(
            self.settings,
            self.state,
            [self.acp.dispatch, self.custody.dispatch, self.sessions.dispatch, self.local.dispatch],
        )

    def job(self, job_id: str) -> AcpRow:
        return self.acp.rows[f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:{job_id}"]

    def turn_record(self, job_id: str) -> AcpRow | None:
        return self.acp.rows.get(f"{AGORA_KINDS_NAMESPACE}:{TURN_RECORD_KIND}:{job_id}")

    def pushed_kinds(self) -> list[str]:
        return [str(frame["kind"]) for _owner, _name, frames in self.acp.pushes for frame in frames]


def transcript_line(kind: str, content: list[JSONObject], usage: JSONObject | None = None) -> str:
    blocks: list[JSON] = list(content)
    message: JSONObject = {
        "role": "assistant" if kind == "assistant" else "user",
        "content": blocks,
    }
    if kind == "assistant":
        message["id"] = "msg_1"
        message["model"] = "claude-opus-5"
        if usage is not None:
            message["usage"] = usage
    return json.dumps({"type": kind, "message": message}) + "\n"


# ---------------------------------------------------------------- e2e(fake): join → Bound → Running → delta → Ended


def _assert_joined_and_claimed(world: World) -> None:
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    lease = node.status["lease"]
    assert isinstance(lease, dict)
    assert lease["owner"] == "agentd"
    assert lease["expiresAt"] == 1_000 + 90 * 1_000
    assert node.status["observations"] == {"streamCapability": "frames", "sessions": []}
    assert node.status["state"] == "joined"

    job = world.job("s-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert job.status["sessionHandle"] == {
        "sessionId": "s-1",
        "stream": {"owner": "agentd", "name": "s-1"},
    }
    assert job.status["binding"] == {"node": NODE, "profile": "personal", "account": "acct"}


def _assert_launched_with_borrowed_token(world: World) -> None:
    assert world.custody.borrowed == [("claude", "acct", "agent-job s-1")]
    launch = world.sessions.launches[0]
    env = launch["session_env"]
    assert isinstance(env, dict)
    assert env[CLAUDE_OAUTH_TOKEN_ENV] == TOKEN
    assert launch["binding"] == {"kind": "claude-code", "config_dir": f"{HOMES}/claude/acct"}
    assert launch["prompt"] == "start"
    assert world.sessions.sends == [("s-1", "hello agent", True)]
    assert world.local.metrics[0]["metric"] == "agent-job-to-send"
    assert world.local.metrics[0]["ms"] == 1_000 - 500

    record = world.turn_record("s-1")
    assert record is not None
    assert record.spec == {
        "conversationId": "c-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "agentJobId": "s-1",
        "node": NODE,
        "profile": "personal",
        "model": "claude-opus-5",
    }
    assert record.status == {"state": "running"}
    assert world.pushed_kinds() == ["status"]
    assert world.sessions.captures == []
    assert len(world.state.jobs) == 1


def _assert_ended(world: World) -> None:
    record = world.turn_record("s-1")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    entries = record.status["entries"]
    assert isinstance(entries, list)
    assert [entry["kind"] for entry in entries if isinstance(entry, dict)] == ["text"]
    assert record.status["usage"] == {
        "input": 3,
        "output": 7,
        "cacheWrite": 1,
        "cacheRead": 2,
        "model": "claude-opus-5",
    }
    job = world.job("s-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert job.status["result"] == {"ok": True}
    assert job.status["binding"] == {"node": NODE, "profile": "personal", "account": "acct"}
    assert world.custody.revoked == ["lease-1"]
    assert world.pushed_kinds()[-1] == "status"
    assert world.state.jobs == ()
    assert world.local.metrics[-1]["metric"] == "agent-job-turn"


def test_agentd_round_trip_join_bound_running_delta_ended() -> None:
    world = World()
    world.acp.put_row(
        row(
            AGORA_KINDS_NAMESPACE,
            MESSAGE_KIND,
            "lt-1",
            {"id": "lt-1", "body": "hello agent"},
            {"state": "inbox"},
        )
    )
    world.acp.put_row(bound_job("s-1", inputs=["lt-1"]))

    # tick 1: 参加(lease)と受け(Running + sessionHandle・借用・launch・inputs の send・記録の行)
    world.tick()
    _assert_joined_and_claimed(world)
    _assert_launched_with_borrowed_token(world)

    # tick 2: transcript の追記 → TurnDelta(text + usage)。購読 0 のまま = capture は呼ばれない
    path = f"{HOMES}/claude/acct/projects/-work/s-1.jsonl"
    world.local.transcripts[path] = transcript_line(
        "assistant",
        [{"type": "text", "text": "working on it"}],
        {
            "input_tokens": 3,
            "output_tokens": 7,
            "cache_creation_input_tokens": 1,
            "cache_read_input_tokens": 2,
        },
    )
    world.tick(advance_ms=1_000)
    kinds = world.pushed_kinds()
    assert "text" in kinds
    assert "usage" in kinds
    assert world.sessions.captures == []
    assert world.state.jobs[0].capturing is False

    # tick 3〜4: 購読者が現れると status frame の読み直しで capture が始まる(2〜5 Hz)
    world.acp.subscribers["s-1"] = 1
    world.tick(advance_ms=5_000)
    assert world.state.jobs[0].capturing is True
    world.tick(advance_ms=500)
    assert world.sessions.captures == [("s-1", 60)]
    assert world.pushed_kinds()[-1] == "frame"

    # tick 5: 手番の終わり → turn-record ended(entries + usage)・agent-job Ended(result)・札の返却
    world.sessions.finish("s-1", "done", {"ok": True})
    world.tick(advance_ms=500)
    _assert_ended(world)


def test_agentd_does_not_take_jobs_bound_to_another_node_or_not_bound() -> None:
    world = World()
    other = bound_job("s-other", inputs=[])
    assert other.status is not None
    other.status["binding"] = {"node": "someone-else"}
    world.acp.put_row(other)
    pending = bound_job("s-pending", inputs=[])
    assert pending.status is not None
    pending.status["phase"] = "Pending"
    world.acp.put_row(pending)
    world.tick()
    assert world.sessions.launches == []
    assert world.job("s-other").status == other.status
    assert world.job("s-pending").status == pending.status


def test_failed_session_ends_the_job_with_a_condition_and_no_result() -> None:
    world = World()
    world.acp.put_row(bound_job("s-2", inputs=[], account=None))
    world.tick()
    assert world.custody.borrowed == []
    world.sessions.finish("s-2", "failed")
    world.tick(advance_ms=1_000)
    job = world.job("s-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert "result" not in job.status
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    assert conditions[-1] == {
        "type": "SessionFailed",
        "status": "True",
        "reason": "session failed: run_failed (failed)",
    }


def test_custody_refusal_ends_the_job_without_launching() -> None:
    world = World()
    world.custody.refuse_with = LeaseRefused(409, "1 認証 1 宿", None)
    world.acp.put_row(bound_job("s-3", inputs=[]))
    world.tick()
    assert world.sessions.launches == []
    job = world.job("s-3")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict)
    assert last["type"] == "CredentialUnavailable"


def test_missing_node_row_is_logged_once_and_re_read() -> None:
    world = World()
    del world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    world.tick()
    world.tick(advance_ms=30_000)
    assert [line for line in world.local.logs if "node row" in line] == [
        f"agentd: node row {NODE!r} is not in ACP yet (created by acp-scheduling / register-node); re-reading each heartbeat"
    ]
    assert world.state.node_missing_logged is True


# ---------------------------------------------------------------- 判断の純関数


def test_bound_to_me_is_phase_bound_and_binding_node() -> None:
    mine = bound_job("a", inputs=[])
    assert run(judgment.bound_to_me(mine, NODE)) is True
    assert run(judgment.bound_to_me(mine, "other")) is False
    running = bound_job("b", inputs=[])
    assert running.status is not None
    running.status["phase"] = PHASE_RUNNING
    assert run(judgment.bound_to_me(running, NODE)) is False
    unbound = row(
        AGENT_JOB_NAMESPACE,
        AGENT_JOB_KIND,
        "c",
        {"subject": "c", "inputs": []},
        {"phase": PHASE_BOUND},
    )
    assert run(judgment.bound_to_me(unbound, NODE)) is False


@pytest.mark.parametrize(
    ("subscribers", "verdict"),
    [(0, "stop"), (1, "continue"), (3, "continue"), (None, "stop")],
)
def test_capture_verdict_follows_subscribers(subscribers: int | None, verdict: str) -> None:
    assert run(judgment.capture_verdict(subscribers)) == verdict


def test_charter_with_grant_claude_rides_env_not_disk() -> None:
    charter: JSONObject = {"session_id": "s", "agent_type": "claude", "session_env": {"X": "1"}}
    rebuilt, auth_file = run(
        judgment.charter_with_grant(charter, "claude", "me@x", TOKEN, None, HOMES)
    )
    assert auth_file is None
    assert rebuilt["session_env"] == {"X": "1", CLAUDE_OAUTH_TOKEN_ENV: TOKEN}
    assert rebuilt["binding"] == {"kind": "claude-code", "config_dir": f"{HOMES}/claude/me_x"}
    assert charter == {"session_id": "s", "agent_type": "claude", "session_env": {"X": "1"}}


def test_charter_with_grant_codex_places_auth_json_inside_the_home() -> None:
    charter: JSONObject = {
        "session_id": "s",
        "agent_type": "codex",
        "binding": {"kind": "codex", "codex_home": "/bundle"},
    }
    rebuilt, auth_file = run(
        judgment.charter_with_grant(charter, "codex", "acct", None, '{"tokens": {}}', HOMES)
    )
    assert auth_file == f"{HOMES}/codex/acct/auth.json"
    assert rebuilt["binding"] == {"kind": "codex", "auth_file": auth_file, "profile_dir": "/bundle"}


def test_deltas_of_claude_folds_blocks_and_counts_usage_once_per_message() -> None:
    text = (
        transcript_line(
            "assistant",
            [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}],
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        + transcript_line(
            "assistant",
            [{"type": "text", "text": "done"}],
            {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        + transcript_line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "a\nb"}])
    )
    batch = run(judgment.deltas_of("claude", text, "job", 10, 777))
    assert [frame["kind"] for frame in batch.frames] == ["usage", "tool_use", "text", "tool_result"]
    assert [entry["kind"] for entry in batch.entries] == ["tool_use", "text", "tool_result"]
    assert batch.usage == {
        "input": 1,
        "output": 2,
        "cacheWrite": 0,
        "cacheRead": 0,
        "model": "claude-opus-5",
    }
    assert batch.next_seq == 14
    assert batch.frames[1]["payload"] == {
        "toolUseId": "t1",
        "name": "Bash",
        "summary": '{"command": "ls"}',
    }
    assert batch.frames[3]["payload"] == {
        "toolUseId": "t1",
        "summary": "a\nb",
        "bytes": 6,
        "isError": False,
    }


# ---------------------------------------------------------------- 弁


def test_valve_defaults_off_and_strips_the_flag() -> None:
    off = acp_valve(["serve", "--socket", "/tmp/x.sock"], {})
    assert off.enabled is False
    assert off.host_argv == ("serve", "--socket", "/tmp/x.sock")
    flagged = acp_valve(["serve", "--acp"], {})
    assert flagged.enabled is True
    assert flagged.host_argv == ("serve",)
    assert acp_valve(["serve"], {ACP_VALVE_ENV: "on"}).enabled is True
    assert acp_valve(["serve"], {ACP_VALVE_ENV: "off"}).enabled is False
    with pytest.raises(ValueError, match=r"on\|off"):
        acp_valve(["serve"], {ACP_VALVE_ENV: "yes"})


# ---------------------------------------------------------------- 段 2 の直し(agora-redesign #19 の実弾 002 / 003)


def running_job(job_id: str, *, owner: str = "agentd", node: str = NODE) -> AcpRow:
    """agentd が claim した後の行(phase Running + sessionHandle)— 再起動後に list で映る形。"""
    base = bound_job(job_id, inputs=[], account=None)
    assert base.status is not None
    status: JSONObject = dict(base.status)
    binding = status["binding"]
    assert isinstance(binding, dict)
    binding["node"] = node
    status["phase"] = PHASE_RUNNING
    status["sessionHandle"] = {"sessionId": job_id, "stream": {"owner": owner, "name": job_id}}
    return row(AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, job_id, base.spec, status)


def turn_record_row(job_id: str) -> AcpRow:
    return row(
        AGORA_KINDS_NAMESPACE,
        TURN_RECORD_KIND,
        job_id,
        {
            "conversationId": "c-01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "agentJobId": job_id,
            "node": NODE,
            "profile": "personal",
            "model": "claude-opus-5",
        },
        {"state": "running"},
    )


def _start_capturing(world: World, job_id: str) -> None:
    world.acp.put_row(bound_job(job_id, inputs=[], account=None))
    world.tick()
    world.acp.subscribers[job_id] = 1
    world.tick(advance_ms=5_000)
    assert world.state.jobs[0].capturing is True


def test_capture_gone_is_the_end_of_the_stream_not_an_error() -> None:
    """(a) 片付いた session の capture(pane も server も無い)は例外ではなく実況の終わり:
    capture を止め、器が終端になった拍に Ended と turn-record ended を書く。"""
    world = World()
    _start_capturing(world, "s-g")
    world.sessions.capture_gone = "tmux capture-pane failed: no server running"
    world.tick(advance_ms=500)
    assert world.sessions.captures == [("s-g", 60)]
    assert world.state.jobs[0].capturing is False
    assert world.state.jobs[0].stream_gone is True
    assert [line for line in world.local.logs if "tick failed" in line] == []
    assert any("stream of job s-g is gone" in line for line in world.local.logs)
    # gone の後は capture も購読の読み直しも撃たない(pane が無い)
    world.tick(advance_ms=5_000)
    assert world.sessions.captures == [("s-g", 60)]
    world.sessions.finish("s-g", "done", {"ok": True})
    world.tick(advance_ms=500)
    job = world.job("s-g")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert job.status["result"] == {"ok": True}
    record = world.turn_record("s-g")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    assert world.state.jobs == ()


def test_capture_gone_with_a_terminal_session_ends_in_the_same_tick() -> None:
    """(a') session.get の後に pane が消えて器も終端に倒れた(race)— gone の拍で器を読み直し、
    同じ拍で記録の腕を撃つ。"""
    world = World()
    _start_capturing(world, "s-gr")
    world.sessions.capture_gone = "tmux capture-pane failed: can't find pane"
    world.sessions.finish_on_capture = ("done", {"ok": 1})
    world.tick(advance_ms=500)
    assert world.sessions.captures == [("s-gr", 60)]
    job = world.job("s-gr")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert job.status["result"] == {"ok": 1}
    record = world.turn_record("s-gr")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    assert world.state.jobs == ()


def test_terminal_session_is_recorded_without_a_capture() -> None:
    """器が終端の拍は実況(capture)を撃たずに記録の腕へ進む(実弾 003 の直接の機序)。"""
    world = World()
    _start_capturing(world, "s-t")
    world.sessions.finish("s-t", "done", {"ok": True})
    world.sessions.capture_gone = "tmux capture-pane failed: no server running"
    world.tick(advance_ms=500)
    assert world.sessions.captures == []
    job = world.job("s-t")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED


def test_running_job_of_mine_is_recovered_on_the_first_tick_after_restart() -> None:
    """(b) 再起動(memory を捨てる)後の最初の tick で、自分の Running を行から拾い、器が
    終端なら記録の腕だけを撃って閉じる(launch も send もし直さない)。"""
    world = World()
    world.acp.put_row(bound_job("s-r", inputs=[], account=None))
    world.tick()
    assert len(world.sessions.launches) == 1
    world.state = initial_state()
    world.sessions.finish("s-r", "done", {"ok": True})
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    job = world.job("s-r")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert job.status["result"] == {"ok": True}
    record = world.turn_record("s-r")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    assert world.state.jobs == ()
    assert world.local.metrics[-1]["metric"] == "agent-job-turn"


def test_running_job_without_a_session_is_ended_with_session_failed() -> None:
    """(b') Running の行が在るのに器に session が無い(実弾 002 の孤児)— 記録が在れば ended
    にし、job は SessionFailed で Ended。"""
    world = World()
    world.acp.put_row(running_job("s-orphan"))
    world.acp.put_row(turn_record_row("s-orphan"))
    world.tick()
    job = world.job("s-orphan")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert "result" not in job.status
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict)
    assert last["type"] == "SessionFailed"
    record = world.turn_record("s-orphan")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    assert world.sessions.launches == []
    assert world.state.jobs == ()


def test_running_job_with_a_live_session_is_adopted_and_observed() -> None:
    """(b'') 再起動後に器がまだ走っていれば、行から InFlightJob を組み直し(札は借り直す)、
    観測を続けて終端で閉じる。"""
    world = World()
    world.acp.put_row(bound_job("s-live", inputs=[]))
    world.tick()
    assert world.custody.borrowed == [("claude", "acct", "agent-job s-live")]
    world.state = initial_state()
    world.tick(advance_ms=1_000)
    assert len(world.state.jobs) == 1
    assert world.state.jobs[0].job_id == "s-live"
    assert len(world.sessions.launches) == 1
    assert world.custody.borrowed[-1] == ("claude", "acct", "agent-job s-live (recovered)")
    job = world.job("s-live")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    world.sessions.finish("s-live", "done", {"ok": True})
    world.tick(advance_ms=1_000)
    ended = world.job("s-live")
    assert ended.status is not None
    assert ended.status["phase"] == PHASE_ENDED
    # 前の札の id は行に無い(memory だけが知っていた)ので返せない — 預かり所の錠の期限で戻る。
    # 借り直した札は手番の終わりに返す。
    assert world.custody.revoked == ["lease-2"]
    assert world.state.jobs == ()


def test_running_jobs_of_other_owners_or_nodes_are_left_alone() -> None:
    world = World()
    world.acp.put_row(running_job("s-other-node", node="someone-else"))
    world.acp.put_row(running_job("s-other-owner", owner="not-agentd"))
    world.tick()
    for job_id in ("s-other-node", "s-other-owner"):
        job = world.job(job_id)
        assert job.status is not None
        assert job.status["phase"] == PHASE_RUNNING
    assert world.state.jobs == ()


def test_one_job_failure_does_not_stop_the_heartbeat_or_other_jobs() -> None:
    """(c) 1 job の handler の例外(器の RPC が落ちた)は他の job と参加の heartbeat を止めない —
    その job は次の拍へ持ち越す。"""
    world = World()
    world.acp.put_row(bound_job("s-a", inputs=[], account=None))
    world.acp.put_row(bound_job("s-b", inputs=[], account=None))
    world.tick()
    assert len(world.state.jobs) == 2
    world.sessions.failures["s-a"] = RuntimeError("socket reset")
    world.sessions.finish("s-b", "done", {"ok": True})
    world.tick(advance_ms=30_000)
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    lease = node.status["lease"]
    assert isinstance(lease, dict)
    assert lease["heartbeatAt"] == 31_000
    ended = world.job("s-b")
    assert ended.status is not None
    assert ended.status["phase"] == PHASE_ENDED
    still = world.job("s-a")
    assert still.status is not None
    assert still.status["phase"] == PHASE_RUNNING
    assert [job.job_id for job in world.state.jobs] == ["s-a"]
    assert any(
        line == "agentd: job s-a tick failed: RuntimeError: socket reset"
        for line in world.local.logs
    )
    del world.sessions.failures["s-a"]
    world.sessions.finish("s-a", "done", {"ok": True})
    world.tick(advance_ms=1_000)
    recovered = world.job("s-a")
    assert recovered.status is not None
    assert recovered.status["phase"] == PHASE_ENDED
    assert world.state.jobs == ()


def test_acp_list_failure_does_not_stop_the_observation_of_running_jobs() -> None:
    """(c') 受け(list)が落ちても(実弾 002 の Connection reset)、走っている job の観測は続く。"""
    world = World()
    world.acp.put_row(bound_job("s-c", inputs=[], account=None))
    world.tick()
    world.acp.list_failures[AGENT_JOB_KIND] = RuntimeError("Connection reset by peer")
    world.sessions.finish("s-c", "done", {"ok": True})
    world.tick(advance_ms=30_000)
    ended = world.job("s-c")
    assert ended.status is not None
    assert ended.status["phase"] == PHASE_ENDED
    assert any(
        line == "agentd: receive failed: RuntimeError: Connection reset by peer"
        for line in world.local.logs
    )


def test_job_step_of_is_the_one_decision_for_a_running_row() -> None:
    assert run(judgment.job_step_of(None, 0, True)) == "fail-missing"
    view = _view("s", "running")
    assert run(judgment.job_step_of(view, 0, True)) == "observe"
    for status in ("done", "failed", "exited", "stopped", "cancelled"):
        assert run(judgment.job_step_of(_view("s", status), 0, True)) == "record-end"
    # 温かい session(multi_turn)の手番の終わり: 器は生きたまま turn_ended_at が手番の始まり
    # (floor)より後に付き、記録が進んでいる(自分の本文が届いた証拠)時だけ turn-end。
    warm = _view("s", "running", lifecycle="multi_turn", turn_ended_at_ms=5_000)
    assert run(judgment.job_step_of(warm, 4_000, True)) == "turn-end"
    assert run(judgment.job_step_of(warm, 6_000, True)) == "observe"
    assert run(judgment.job_step_of(warm, 4_000, False)) == "observe"
    busy = _view("s", "running", lifecycle="multi_turn", turn_ended_at_ms=None)
    assert run(judgment.job_step_of(busy, 0, True)) == "observe"
    # run_to_completion の器は turn_ended_at が付いても turn-end にはならない(終端で record-end)。
    cold = _view("s", "running", turn_ended_at_ms=5_000)
    assert run(judgment.job_step_of(cold, 0, True)) == "observe"


def _view(
    session_id: str,
    status: str,
    *,
    lifecycle: str = "run_to_completion",
    turn_ended_at_ms: int | None = None,
) -> SessionView:
    return SessionView(
        session_id=session_id,
        agent_type="claude",
        status=status,
        work_dir="/work",
        lifecycle=lifecycle,
        conversation={"session_id": session_id},
        effective_identity={"CLAUDE_CONFIG_DIR": "/homes/claude/acct"},
        result_payload=None,
        terminal_cause=None,
        turn_ended_at_ms=turn_ended_at_ms,
    )


def test_running_on_me_needs_phase_node_and_stream_owner() -> None:
    mine = running_job("a")
    assert run(judgment.running_on_me(mine, NODE, "agentd")) is True
    assert run(judgment.running_on_me(mine, "other", "agentd")) is False
    assert run(judgment.running_on_me(mine, NODE, "someone")) is False
    assert run(judgment.running_on_me(bound_job("b", inputs=[]), NODE, "agentd")) is False


# ---------------------------------------------------------------- 温かい session(段 2・設計 17.4・lane 2b-3)


def message(message_id: str, body: str) -> AcpRow:
    return row(
        AGORA_KINDS_NAMESPACE,
        MESSAGE_KIND,
        message_id,
        {"id": message_id, "body": body},
        {"state": "inbox"},
    )


def _run_first_turn(world: World, job_id: str = "j-1") -> str:
    """手番 1: launch(multi_turn)→ 記録が進む → 手番の終わり(host が turn_ended_at を刻む)
    → job Ended・session は生きたまま。戻り = transcript の path。"""
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job(job_id, inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    assert world.sessions.launches[-1]["lifecycle"] == "multi_turn"
    assert world.sessions.sends[-1] == (job_id, "first", True)
    path = f"{HOMES}/claude/acct/projects/-work/{job_id}.jsonl"
    world.local.transcripts[path] = transcript_line("assistant", [{"type": "text", "text": "one"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(job_id, world.local.now_ms + 500)
    world.tick(advance_ms=1_000)
    job = world.job(job_id)
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert world.sessions.cleanups == []
    assert world.sessions.views[job_id].status == "running"
    assert world.state.jobs == ()
    return path


def test_second_turn_of_the_same_conversation_is_sent_to_the_warm_session() -> None:
    """同じ会話の次の手番は launch せず send: sessionHandle は既存の session を指し、
    turn-record は手番ごとに別の行、計器 agent-job-to-send は温かい path で 2 秒未満。"""
    world = World()
    path = _run_first_turn(world)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert world.sessions.resumes == []
    assert world.sessions.sends[-1] == ("j-1", "second", True)
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert job.status["sessionHandle"] == {
        "sessionId": "j-1",
        "stream": {"owner": "agentd", "name": "j-1"},
    }
    record = world.turn_record("j-2")
    assert record is not None
    assert record.spec["conversationId"] == CONVERSATION
    assert record.spec["agentJobId"] == "j-2"
    to_send = [m for m in world.local.metrics if m["metric"] == "agent-job-to-send"][-1]
    assert to_send["agentJobId"] == "j-2"
    assert to_send["sessionId"] == "j-1"
    assert isinstance(to_send["ms"], int)
    assert to_send["ms"] < 2_000
    # 送った直後: 前の手番の turn_ended_at(送りより前)では手番は終わらない。
    world.tick(advance_ms=500)
    running = world.job("j-2")
    assert running.status is not None
    assert running.status["phase"] == PHASE_RUNNING
    # 記録が進み(自分の本文が届いた証拠)、host が新しい turn_ended_at を刻むと手番の終わり。
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "two"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn("j-1", world.local.now_ms + 200)
    world.tick(advance_ms=1_000)
    ended = world.job("j-2")
    assert ended.status is not None
    assert ended.status["phase"] == PHASE_ENDED
    record = world.turn_record("j-2")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    entries = record.status["entries"]
    assert isinstance(entries, list)
    assert [e["text"] for e in entries if isinstance(e, dict)] == ["two"]
    assert world.sessions.cleanups == []
    assert world.state.jobs == ()


def test_warm_send_stays_under_two_seconds_across_turns() -> None:
    """温かい path の計器: 5 手番の create → send がすべて 2 秒未満(fake の時計で pin)。"""
    world = World()
    path = _run_first_turn(world)
    samples: list[int] = []
    for n in range(2, 7):
        world.acp.put_row(message(f"m-{n}", f"turn {n}"))
        world.acp.put_row(
            bound_job(f"j-{n}", inputs=[f"m-{n}"], created_at_ms=world.local.now_ms + 900)
        )
        world.tick(advance_ms=1_000)
        metric = [m for m in world.local.metrics if m["metric"] == "agent-job-to-send"][-1]
        assert metric["agentJobId"] == f"j-{n}"
        ms = metric["ms"]
        assert isinstance(ms, int)
        samples.append(ms)
        world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "x"}])
        world.tick(advance_ms=1_000)
        world.sessions.finish_turn("j-1", world.local.now_ms + 100)
        world.tick(advance_ms=1_000)
        assert world.state.jobs == ()
    assert len(world.sessions.launches) == 1
    assert max(samples) < 2_000


def test_a_different_conversation_launches_its_own_session() -> None:
    world = World()
    _run_first_turn(world)
    world.acp.put_row(message("m-x", "other"))
    world.acp.put_row(bound_job("j-x", inputs=["m-x"], subject="c-other"))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 2
    assert world.sessions.launches[-1]["session_id"] == "j-x"
    assert world.sessions.sends[-1] == ("j-x", "other", True)
    job = world.job("j-x")
    assert job.status is not None
    handle = job.status["sessionHandle"]
    assert isinstance(handle, dict)
    assert handle["sessionId"] == "j-x"


def test_idle_session_past_the_ttl_is_cleaned_up() -> None:
    """idle が TTL(値の宣言 1 点 AgentdSettings.session_idle_ttl_seconds)を過ぎた session は
    agentd が session.cleanup で片付ける(判断は純関数・時計は effect)。"""
    world = World()
    _run_first_turn(world)
    assert world.settings.session_idle_ttl_seconds == 600
    world.tick(advance_ms=30_000)
    assert world.sessions.cleanups == []
    world.tick(advance_ms=600_000)
    assert world.sessions.cleanups == ["j-1"]
    # 片付いた後の次の手番は cold launch。
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 2
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    observations = node.status["observations"]
    assert isinstance(observations, dict)
    assert observations["sessions"] == []


def test_node_observations_carry_the_conversation_sessions() -> None:
    """node の status.observations.sessions = 行から導いた [{conversationId, sessionId, state}]。"""
    world = World()
    _run_first_turn(world)
    world.tick(advance_ms=30_000)
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    observations = node.status["observations"]
    assert isinstance(observations, dict)
    assert observations["sessions"] == [
        {"conversationId": CONVERSATION, "sessionId": "j-1", "state": "idle"}
    ]


def test_predecessor_alive_is_sent_and_terminal_predecessor_is_resumed() -> None:
    world = World()
    _run_first_turn(world)
    # (a) predecessor が生きて idle → send(温かい resume)。
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], predecessor="j-1"))
    world.tick(advance_ms=1_000)
    assert world.sessions.resumes == []
    assert world.sessions.sends[-1] == ("j-1", "second", True)
    world.sessions.finish_turn("j-1", world.local.now_ms + 100)
    path = f"{HOMES}/claude/acct/projects/-work/j-1.jsonl"
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "y"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn("j-1", world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # (b) predecessor が終端 → session.resume(cold)。
    world.sessions.finish("j-1", "exited")
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"], predecessor="j-1"))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.resumes) == 1
    assert world.sessions.resumes[0]["session_id"] == "j-1"
    assert world.sessions.resumes[0]["new_session_id"] == "j-3"
    assert world.sessions.sends[-1] == ("j-3", "third", True)


def test_withdrawn_job_cleans_up_its_session_and_stops_observing() -> None:
    world = World()
    _run_first_turn(world)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.state.jobs) == 1
    running = world.job("j-2")
    assert running.status is not None
    withdrawn: JSONObject = dict(running.status)
    withdrawn["phase"] = "Withdrawn"
    world.acp.put_row(
        row(
            AGENT_JOB_NAMESPACE,
            AGENT_JOB_KIND,
            "j-2",
            running.spec,
            withdrawn,
            created_at_ms=running.created_at_ms,
        )
    )
    world.tick(advance_ms=1_000)
    assert world.sessions.cleanups == ["j-1"]
    assert world.state.jobs == ()
    record = world.turn_record("j-2")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == "Withdrawn"
    # 次の拍で同じ Withdrawn の行に cleanup を撃ち直さない。
    world.tick(advance_ms=1_000)
    assert world.sessions.cleanups == ["j-1"]


def test_busy_conversation_session_defers_the_claim() -> None:
    """会話の session が手番の途中(turn_ended_at 無し)なら次の手番は受けない(Bound のまま・
    launch も send も無し)— 次の list で読み直す。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert world.sessions.sends == [("j-1", "first", True)]
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_BOUND
    assert any("deferred" in line for line in world.local.logs)


def test_terminal_warm_session_is_cleaned_up_at_record_end() -> None:
    """multi_turn の器が終端(awaiting の期限で failed 等)になった手番は、記録の腕の後に
    agentd が session.cleanup で片付ける(host は multi_turn を掃かない)。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    world.sessions.finish("j-1", "failed")
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert world.sessions.cleanups == ["j-1"]


def test_charter_lifecycle_is_respected_when_declared() -> None:
    """charter が lifecycle を名指せばそれを使う(run_to_completion の charter は今日どおり
    1 手番で片付く)。無ければ agentd の既定 multi_turn。"""
    world = World()
    world.acp.put_row(bound_job("j-rtc", inputs=[], lifecycle="run_to_completion"))
    world.tick()
    assert world.sessions.launches[-1]["lifecycle"] == "run_to_completion"
    world.sessions.finish("j-rtc", "done", {"ok": True})
    world.tick(advance_ms=1_000)
    assert world.sessions.cleanups == []


def test_next_arm_for_job_is_the_one_decision() -> None:
    plan_plain = run(judgment.launch_plan_of(bound_job("a", inputs=[])))
    plan_pred = run(judgment.launch_plan_of(bound_job("b", inputs=[], predecessor="p")))
    warm = _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=10)
    busy = _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=None)
    dead = _view("p", "exited", lifecycle="multi_turn", turn_ended_at_ms=10)
    assert run(judgment.next_arm_for_job(plan_plain, None)) == "launch"
    assert run(judgment.next_arm_for_job(plan_plain, warm)) == "send"
    assert run(judgment.next_arm_for_job(plan_plain, busy)) == "defer"
    assert run(judgment.next_arm_for_job(plan_plain, dead)) == "launch"
    assert run(judgment.next_arm_for_job(plan_pred, None)) == "resume"
    assert run(judgment.next_arm_for_job(plan_pred, warm)) == "send"
    assert run(judgment.next_arm_for_job(plan_pred, busy)) == "defer"
    assert run(judgment.next_arm_for_job(plan_pred, dead)) == "resume"
    assert run(judgment.launch_lifecycle_of({})) == "multi_turn"
    assert run(judgment.launch_lifecycle_of({"lifecycle": "interactive"})) == "interactive"
