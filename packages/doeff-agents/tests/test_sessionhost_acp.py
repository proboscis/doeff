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
)
from doeff_agents.sessionhost.acp.fake import Birth, FakeAcp, FakeCustody, FakeLocal, FakeSessions
from doeff_agents.sessionhost.acp.runtime import initial_state, run_tick
from doeff_agents.sessionhost.acp.valve import ACP_VALVE_ENV, acp_valve

from doeff import run

NODE = "mac-1"
HOMES = "/homes"
TOKEN = "sk-ant-oat01-secret-token"


def row(
    namespace: str, kind: str, resource_id: str, spec: JSONObject, status: JSONObject | None
) -> AcpRow:
    return AcpRow(
        namespace=namespace,
        key=f"{namespace}:{kind}:{resource_id}",
        kind=kind,
        resource_id=resource_id,
        version="v1",
        generation=1,
        created_at_ms=500,
        labels={},
        payload={},
        spec=spec,
        status=status,
    )


def bound_job(job_id: str, *, inputs: list[str], account: str | None = "acct") -> AcpRow:
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
    spec: JSONObject = {
        "subject": "c-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "inputs": list(inputs),
        "charter": charter,
    }
    status: JSONObject = {"phase": PHASE_BOUND, "binding": binding, "conditions": []}
    return row(AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, job_id, spec, status)


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
    assert node.status["observations"] == {"streamCapability": "frames", "sessions": 0}
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
    assert world.sessions.sends == [("s-1", "hello agent")]
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
