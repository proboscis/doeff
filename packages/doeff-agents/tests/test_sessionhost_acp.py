"""agentd(sessionhost の ACP の腕・段 2 agora-redesign #19 / #20)の焦点の検。

fake の handler(doeff_agents.sessionhost.acp.fake)で同じ program(agentd.hy)を一周させる
e2e の形 1 本: 参加(node の lease)→ Bound の job を置く → Running + sessionHandle →
TurnDelta を 1 本押す → 手番の終わりに Ended と turn-record。加えて判断の純関数と弁。
HTTP も socket も tmux も無い。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

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
        # charter の id は agentd が読まない(session の id は agentd が鋳造する — 2026-09-12 追補 2)。
        # 読んだら検が割れるよう、job の id とも鋳造の綴り(sid-<n>)とも違う綴りにする。
        "session_id": f"charter-{job_id}",
        "session_name": f"charter-{job_id}",
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

    def sid(self, job_id: str) -> str:
        """job が使っている session の id(行の sessionHandle — agentd が鋳造した綴り)。"""
        status = self.job(job_id).status
        assert status is not None
        handle = status["sessionHandle"]
        assert isinstance(handle, dict)
        session_id = handle["sessionId"]
        assert isinstance(session_id, str)
        return session_id

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
        "sessionId": "sid-1",
        "stream": {"owner": "agentd", "name": "sid-1"},
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
    assert launch["session_id"] == "sid-1"
    assert launch["session_name"] == "sid-1"
    assert world.sessions.sends == [("sid-1", "hello agent", True)]
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
    path = f"{HOMES}/claude/acct/projects/-work/sid-1.jsonl"
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
    world.acp.subscribers["sid-1"] = 1
    world.tick(advance_ms=5_000)
    assert world.state.jobs[0].capturing is True
    world.tick(advance_ms=500)
    assert world.sessions.captures == [("sid-1", 60)]
    assert world.pushed_kinds()[-1] == "frame"

    # tick 5: 手番の終わり → turn-record ended(entries + usage)・agent-job Ended(result)・札の返却
    world.sessions.finish("sid-1", "done", {"ok": True})
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
    world.sessions.finish(world.sid("s-2"), "failed")
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
    batch = run(judgment.deltas_of("claude", "transcript", text, "job", 10, 777))
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
    world.acp.subscribers[world.sid(job_id)] = 1
    world.tick(advance_ms=5_000)
    assert world.state.jobs[0].capturing is True


def test_capture_gone_is_the_end_of_the_stream_not_an_error() -> None:
    """(a) 片付いた session の capture(pane も server も無い)は例外ではなく実況の終わり:
    capture を止め、器が終端になった拍に Ended と turn-record ended を書く。"""
    world = World()
    _start_capturing(world, "s-g")
    world.sessions.capture_gone = "tmux capture-pane failed: no server running"
    world.tick(advance_ms=500)
    assert world.sessions.captures == [(world.sid("s-g"), 60)]
    assert world.state.jobs[0].capturing is False
    assert world.state.jobs[0].stream_gone is True
    assert [line for line in world.local.logs if "tick failed" in line] == []
    assert any("stream of job s-g is gone" in line for line in world.local.logs)
    # gone の後は capture も購読の読み直しも撃たない(pane が無い)
    world.tick(advance_ms=5_000)
    assert world.sessions.captures == [(world.sid("s-g"), 60)]
    world.sessions.finish(world.sid("s-g"), "done", {"ok": True})
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
    assert world.sessions.captures == [(world.sid("s-gr"), 60)]
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
    world.sessions.finish(world.sid("s-t"), "done", {"ok": True})
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
    world.sessions.finish(world.sid("s-r"), "done", {"ok": True})
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
    world.sessions.finish(world.sid("s-live"), "done", {"ok": True})
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
    world.sessions.failures[world.sid("s-a")] = RuntimeError("socket reset")
    world.sessions.finish(world.sid("s-b"), "done", {"ok": True})
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
    del world.sessions.failures[world.sid("s-a")]
    world.sessions.finish(world.sid("s-a"), "done", {"ok": True})
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
    world.sessions.finish(world.sid("s-c"), "done", {"ok": True})
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
    sid = world.sid(job_id)
    assert sid != job_id
    assert world.sessions.launches[-1]["session_id"] == sid
    assert world.sessions.sends[-1] == (sid, "first", True)
    path = f"{HOMES}/claude/acct/projects/-work/{sid}.jsonl"
    world.local.transcripts[path] = transcript_line("assistant", [{"type": "text", "text": "one"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 500)
    world.tick(advance_ms=1_000)
    job = world.job(job_id)
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert world.sessions.cleanups == []
    assert world.sessions.views[sid].status == "running"
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
    warm = world.sid("j-1")
    assert world.sessions.sends[-1] == (warm, "second", True)
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert job.status["sessionHandle"] == {
        "sessionId": warm,
        "stream": {"owner": "agentd", "name": warm},
    }
    record = world.turn_record("j-2")
    assert record is not None
    assert record.spec["conversationId"] == CONVERSATION
    assert record.spec["agentJobId"] == "j-2"
    to_send = [m for m in world.local.metrics if m["metric"] == "agent-job-to-send"][-1]
    assert to_send["agentJobId"] == "j-2"
    assert to_send["sessionId"] == warm
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
    world.sessions.finish_turn(warm, world.local.now_ms + 200)
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
        world.local.transcripts[path] += transcript_line(
            "assistant", [{"type": "text", "text": "x"}]
        )
        world.tick(advance_ms=1_000)
        world.sessions.finish_turn(world.sid("j-1"), world.local.now_ms + 100)
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
    other = world.sid("j-x")
    assert other != world.sid("j-1")
    assert world.sessions.launches[-1]["session_id"] == other
    assert world.sessions.sends[-1] == (other, "other", True)


def test_idle_session_past_the_ttl_is_cleaned_up() -> None:
    """idle が TTL(値の宣言 1 点 AgentdSettings.session_idle_ttl_seconds)を過ぎた session は
    agentd が session.cleanup で片付ける(判断は純関数・時計は effect)。"""
    world = World()
    _run_first_turn(world)
    assert world.settings.session_idle_ttl_seconds == 600
    world.tick(advance_ms=30_000)
    assert world.sessions.cleanups == []
    world.tick(advance_ms=600_000)
    assert world.sessions.cleanups == [world.sid("j-1")]
    # 片付いた後の次の手番は cold launch — 新しく鋳造した id(片付いた行と衝突しない)。
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 2
    assert world.sid("j-2") != world.sid("j-1")
    assert world.sessions.launches[-1]["session_id"] == world.sid("j-2")
    job2 = world.job("j-2")
    assert job2.status is not None
    assert job2.status["phase"] == PHASE_RUNNING
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
        {"conversationId": CONVERSATION, "sessionId": world.sid("j-1"), "state": "idle"}
    ]


def test_predecessor_alive_is_sent_and_terminal_predecessor_is_resumed() -> None:
    world = World()
    _run_first_turn(world)
    first = world.sid("j-1")
    # (a) predecessor が生きて idle → send(温かい resume)。
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], predecessor=first))
    world.tick(advance_ms=1_000)
    assert world.sessions.resumes == []
    assert world.sessions.sends[-1] == (first, "second", True)
    world.sessions.finish_turn(first, world.local.now_ms + 100)
    path = f"{HOMES}/claude/acct/projects/-work/{first}.jsonl"
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "y"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(first, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # (b) predecessor が終端 → session.resume(cold)— 新しい incarnation の id も鋳造。
    world.sessions.finish(first, "exited")
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"], predecessor=first))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.resumes) == 1
    assert world.sessions.resumes[0]["session_id"] == first
    assert world.sessions.resumes[0]["new_session_id"] == world.sid("j-3")
    assert world.sid("j-3") != first
    assert world.sessions.sends[-1] == (world.sid("j-3"), "third", True)


def test_withdrawn_job_interrupts_the_turn_and_keeps_the_session_warm() -> None:
    """取り下げ(Withdrawn)は中断の合図(agora-redesign #37): 手番の途中なら session.interrupt を
    1 回撃ち、turn-record は ended・agent-job には condition Interrupted(phase は Withdrawn の
    まま)、session は片付けない(温かいまま — 次の手番は send)。"""
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
    assert world.sessions.interrupts == [world.sid("j-1")]
    assert world.sessions.cleanups == []
    assert world.state.jobs == ()
    record = world.turn_record("j-2")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    job = world.job("j-2")
    assert job.status is not None
    assert job.status["phase"] == "Withdrawn"
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    assert [item["type"] for item in conditions if isinstance(item, dict)] == ["Interrupted"]
    assert world.pushed_kinds()[-1] == "status"
    # 次の拍で同じ Withdrawn の行に割り込みを撃ち直さない・session は生きたまま
    world.tick(advance_ms=1_000)
    assert world.sessions.interrupts == [world.sid("j-1")]
    assert world.sessions.views[world.sid("j-1")].status == "running"


def test_withdrawn_job_whose_turn_already_ended_is_not_interrupted() -> None:
    """手番が既に終わっている(turn_ended_at がこの手番の始まりより後)job の取り下げは
    割り込まない(判断は interrupt-arm-for の 1 点)— 記録は ended・条件は Interrupted。"""
    world = World()
    _run_first_turn(world)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(world.sid("j-1"), world.local.now_ms + 100)
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
    assert world.sessions.interrupts == []
    assert world.sessions.cleanups == []
    assert world.state.jobs == ()


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
    assert world.sessions.sends == [(world.sid("j-1"), "first", True)]
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
    world.sessions.finish(world.sid("j-1"), "failed")
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert world.sessions.cleanups == [world.sid("j-1")]


def test_charter_lifecycle_is_respected_when_declared() -> None:
    """charter が lifecycle を名指せばそれを使う(run_to_completion の charter は今日どおり
    1 手番で片付く)。無ければ agentd の既定 multi_turn。"""
    world = World()
    world.acp.put_row(bound_job("j-rtc", inputs=[], lifecycle="run_to_completion"))
    world.tick()
    assert world.sessions.launches[-1]["lifecycle"] == "run_to_completion"
    world.sessions.finish(world.sid("j-rtc"), "done", {"ok": True})
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


# ---------------------------------------------------------------- headless backend(events の実況・agora-redesign #37)


def _stream_line(obj: Mapping[str, object]) -> str:
    return json.dumps(obj) + "\n"


def _claude_events(session_id: str, text: str) -> str:
    """claude の print mode(-p・--output-format stream-json --include-partial-messages)の 1 手番の行。"""
    usage = {
        "input_tokens": 3,
        "output_tokens": 7,
        "cache_creation_input_tokens": 1,
        "cache_read_input_tokens": 2,
    }
    return "".join(
        [
            _stream_line({"type": "system", "subtype": "init", "session_id": session_id}),
            _stream_line(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": text[:3]},
                    },
                }
            ),
            _stream_line(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": text[3:]},
                    },
                }
            ),
            _stream_line(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "role": "assistant",
                        "model": "claude-opus-5",
                        "content": [
                            {"type": "text", "text": text},
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
                        ],
                        "usage": usage,
                    },
                }
            ),
            _stream_line(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
                    },
                }
            ),
            _stream_line(
                {"type": "result", "subtype": "success", "is_error": False, "usage": usage}
            ),
        ]
    )


def _codex_events(thread_id: str, text: str) -> str:
    """codex app-server(JSON-RPC)の 1 手番の行(応答 + 通知)。"""
    return "".join(
        [
            _stream_line({"id": "thread/start#2", "result": {"thread": {"id": thread_id}}}),
            _stream_line(
                {"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": "t1"}}}
            ),
            _stream_line(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": thread_id, "itemId": "i1", "delta": text[:3]},
                }
            ),
            _stream_line(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": thread_id, "itemId": "i1", "delta": text[3:]},
                }
            ),
            _stream_line(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "item": {"id": "i1", "type": "agentMessage", "text": text},
                    },
                }
            ),
            _stream_line(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "item": {
                            "id": "c1",
                            "type": "commandExecution",
                            "command": "echo hi",
                            "aggregatedOutput": "hi\n",
                            "exitCode": 0,
                        },
                    },
                }
            ),
            _stream_line(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": thread_id,
                        "tokenUsage": {
                            "total": {
                                "inputTokens": 11,
                                "cachedInputTokens": 4,
                                "cacheWriteInputTokens": 0,
                                "outputTokens": 5,
                            }
                        },
                    },
                }
            ),
            _stream_line(
                {
                    "method": "turn/completed",
                    "params": {"threadId": thread_id, "turn": {"id": "t1", "status": "completed"}},
                }
            ),
        ]
    )


class HeadlessWorld(World):
    """backend=headless の器(events file が実況の正本・pane は無い)。"""

    def __init__(self, agent_type: str = "claude") -> None:
        super().__init__()
        self.settings = AgentdSettings(
            node_name=NODE, homes_root=HOMES, backend_kind="headless", stream_capability="events"
        )
        self.sessions = FakeSessions(
            agent_type=agent_type, backend_kind="headless", events_root="/events"
        )


def test_headless_claude_turn_streams_text_deltas_and_records_entries() -> None:
    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    # headless の 1 手番目: 郵便は launch の prompt に畳む(send は撃たない — 追補 2026-09-12)
    assert world.sessions.launches[-1]["prompt"] == "start\n\nfirst"
    assert world.sessions.sends == []
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    observations = node.status["observations"]
    assert isinstance(observations, dict)
    assert observations["streamCapability"] == "events"
    # events の追記 → text の delta が 1 行ずつ frame に・完成した本文は entry だけ
    world.local.transcripts[f"/events/{sid}.events.jsonl"] = _claude_events(sid, "hello world")
    world.acp.subscribers[sid] = 1
    world.tick(advance_ms=1_000)
    frames = [frame for _o, _n, batch in world.acp.pushes for frame in batch]
    texts = [frame["payload"] for frame in frames if frame["kind"] == "text"]
    assert texts == [{"text": "hel"}, {"text": "lo world"}]
    assert [
        frame["kind"] for frame in frames if frame["kind"] in {"tool_use", "tool_result", "usage"}
    ] == [
        "usage",
        "tool_use",
        "tool_result",
    ]
    # pane は無い: 購読者が居ても capture は撃たない・frame の種類も出ない
    world.tick(advance_ms=500)
    assert world.sessions.captures == []
    assert "frame" not in world.pushed_kinds()
    # 手番の終わり(host が result の行で刻む)→ turn-record の entries と usage
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    entries = record.status["entries"]
    assert isinstance(entries, list)
    assert [entry["kind"] for entry in entries if isinstance(entry, dict)] == [
        "text",
        "tool_use",
        "tool_result",
    ]
    first_entry = entries[0]
    assert isinstance(first_entry, dict)
    assert first_entry["text"] == "hello world"
    assert record.status["usage"] == {
        "input": 3,
        "output": 7,
        "cacheWrite": 1,
        "cacheRead": 2,
        "model": "claude-opus-5",
    }
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert world.sessions.views[sid].status == "running"


def test_headless_codex_turn_streams_deltas_and_records_command_execution() -> None:
    world = HeadlessWorld(agent_type="codex")
    world.acp.put_row(message("m-1", "first"))
    charter_job = bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400)
    charter = charter_job.spec["charter"]
    assert isinstance(charter, dict)
    charter["agent_type"] = "codex"
    charter["binding"] = {"kind": "codex", "codex_home": "/bundle"}
    world.custody = FakeCustody(auth_jsons={"acct": '{"tokens": {}}'})
    world.acp.put_row(charter_job)
    world.tick()
    sid = world.sid("j-1")
    world.local.transcripts[f"/events/{sid}.events.jsonl"] = _codex_events("thr-1", "hi there")
    world.tick(advance_ms=1_000)
    frames = [frame for _o, _n, batch in world.acp.pushes for frame in batch]
    assert [frame["payload"] for frame in frames if frame["kind"] == "text"] == [
        {"text": "hi "},
        {"text": "there"},
    ]
    tool_use = [frame["payload"] for frame in frames if frame["kind"] == "tool_use"]
    assert tool_use == [{"toolUseId": "c1", "name": "command_execution", "summary": "echo hi"}]
    usage = [frame["payload"] for frame in frames if frame["kind"] == "usage"]
    assert usage == [{"input": 11, "output": 5, "cacheWrite": 0, "cacheRead": 4}]
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status is not None
    entries = record.status["entries"]
    assert isinstance(entries, list)
    assert [entry["kind"] for entry in entries if isinstance(entry, dict)] == [
        "text",
        "tool_use",
        "tool_result",
    ]
    assert record.status["usage"] == {"input": 11, "output": 5, "cacheWrite": 0, "cacheRead": 4}


def test_first_turn_prompt_of_joins_the_charter_and_the_mail_with_blank_lines() -> None:
    """純関数: 1 手番目の本文 = charter の prompt(前置き)+ 空行 + 郵便の本文(inputs の順)。
    郵便が無ければ charter だけ・空の部分は入れない。畳むのは headless の器だけ(判定 1 点)。"""
    assert run(judgment.first_turn_prompt_of("start", ("a", "b"))) == "start\n\na\n\nb"
    assert run(judgment.first_turn_prompt_of("start", ())) == "start"
    assert run(judgment.first_turn_prompt_of("", ("only",))) == "only"
    assert run(judgment.first_turn_prompt_of("start", ("", "  "))) == "start"
    assert run(judgment.first_turn_carries_inputs("headless", "launch")) is True
    assert run(judgment.first_turn_carries_inputs("headless", "resume")) is True
    assert run(judgment.first_turn_carries_inputs("headless", "send")) is False
    assert run(judgment.first_turn_carries_inputs("tmux", "launch")) is False
    assert run(judgment.first_turn_carries_inputs("herdr", "launch")) is False


def test_headless_launch_folds_the_mail_into_the_first_turn_and_does_not_send() -> None:
    """実弾 2026-09-12(agentd-4.log): headless の claude で launch の腕が charter の prompt で
    1 手番目の process を起こした直後に after-start が郵便を session.send し、host が同じ名で
    --resume の process を spawn して `headless session already exists` で落ちた(1 手番 1 process)。
    根 = launch(charter の prompt)と send(郵便)を 2 手番として撃つこと。headless では 1 手番目の
    本文に郵便を畳み、send は撃たない。turn-record・計器・in-flight は同じ。2 手番目は send。"""
    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    assert world.sessions.launches[-1]["prompt"] == "start\n\nfirst"
    assert world.sessions.sends == []
    assert [m["metric"] for m in world.local.metrics] == ["agent-job-to-send"]
    assert world.local.metrics[-1]["arm"] == "launch"
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status == {"state": "running"}
    assert len(world.state.jobs) == 1
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    # 手番の終わり → job Ended・session は温かいまま
    world.local.transcripts[f"/events/{sid}.events.jsonl"] = _claude_events(sid, "hello")
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # 2 手番目は send(郵便の本文だけ・launch は増えない)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert world.sessions.sends == [(sid, "second", True)]
    world.local.transcripts[f"/events/{sid}.events.jsonl"] += _claude_events(sid, "again")
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # predecessor が終端 → cold の resume(--resume)も郵便を prompt に畳み、send は撃たない
    world.sessions.finish(sid, "exited")
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"], predecessor=sid))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.resumes) == 1
    assert world.sessions.resumes[0]["prompt"] == "start\n\nthird"
    assert world.sessions.sends == [(sid, "second", True)]
    assert world.local.metrics[-1]["metric"] == "agent-job-to-send"
    assert world.local.metrics[-1]["arm"] == "resume"


def test_headless_launch_without_mail_or_with_missing_mail_uses_the_charter_alone() -> None:
    world = HeadlessWorld()
    # 郵便が無い job は charter だけ
    world.acp.put_row(bound_job("j-4", inputs=[], subject="c-quiet"))
    world.tick()
    assert world.sessions.launches[-1]["prompt"] == "start"
    assert world.sessions.sends == []
    # 郵便が見つからない id は畳めない — condition InputUnavailable は今日どおり
    world.acp.put_row(bound_job("j-5", inputs=["m-missing"], subject="c-missing"))
    world.tick(advance_ms=1_000)
    assert world.sessions.launches[-1]["prompt"] == "start"
    assert world.sessions.sends == []
    in_flight = [job for job in world.state.jobs if job.job_id == "j-5"]
    assert len(in_flight) == 1
    assert [c["type"] for c in in_flight[0].pending_conditions] == ["InputUnavailable"]


def test_tui_launch_still_sends_the_mail_after_the_launch() -> None:
    """tmux / herdr の器は今日どおり: launch(charter の prompt)の後に郵便を send(pane の paste は
    手番の途中でも積める)。畳むのは headless だけ(judgment.first-turn-carries-inputs の 1 点)。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    sid = world.sid("j-1")
    assert world.sessions.launches[-1]["prompt"] == "start"
    assert world.sessions.sends == [(sid, "first", True)]


def test_stream_capability_is_derived_from_the_host_backend() -> None:
    from doeff_agents.sessionhost.acp.runtime import settings_from_env

    env = {"DOEFF_AGENTD_NODE_NAME": NODE, "DOEFF_SESSIONHOST_BACKEND": "headless"}
    assert settings_from_env(env, ()).stream_capability == "events"
    assert settings_from_env(env, ()).backend_kind == "headless"
    assert settings_from_env({"DOEFF_AGENTD_NODE_NAME": NODE}, ()).backend_kind == "tmux"
    assert (
        settings_from_env({"DOEFF_AGENTD_NODE_NAME": NODE}, ("--backend", "herdr")).backend_kind
        == "herdr"
    )
    assert (
        settings_from_env(
            {"DOEFF_AGENTD_NODE_NAME": NODE}, ("--backend", "headless")
        ).stream_capability
        == "events"
    )
    assert settings_from_env({"DOEFF_AGENTD_NODE_NAME": NODE}, ()).stream_capability == "frames"
    assert (
        settings_from_env(
            {"DOEFF_AGENTD_NODE_NAME": NODE}, ("--backend", "herdr")
        ).stream_capability
        == "frames"
    )


def test_interrupt_arm_for_is_the_one_decision() -> None:
    from doeff_agents.sessionhost.acp.effects import InFlightJob

    def job(floor_ms: int) -> InFlightJob:
        return InFlightJob(
            job_key="k",
            job_namespace="n",
            job_id="j",
            subject="c",
            session_id="s",
            agent_type="claude",
            node=NODE,
            profile="p",
            model="m",
            started_ms=floor_ms,
            turn_floor_ms=floor_ms,
            start_offset=0,
            transcript_offset=0,
            delta_seq=0,
            lease_id=None,
            lease_kind=None,
            lease_account=None,
            lease_hold_ms=None,
            capturing=False,
            stream_gone=False,
            last_frame_ms=0,
            last_probe_ms=0,
            pending_conditions=(),
        )

    def view(status: str, turn_ended_at_ms: int | None) -> SessionView:
        return SessionView(
            session_id="s",
            agent_type="claude",
            status=status,
            work_dir="/w",
            lifecycle="multi_turn",
            conversation=None,
            effective_identity=None,
            result_payload=None,
            terminal_cause=None,
            turn_ended_at_ms=turn_ended_at_ms,
        )

    assert run(judgment.interrupt_arm_for(job(1_000), view("running", None))) == "interrupt"
    assert run(judgment.interrupt_arm_for(job(1_000), view("running", 500))) == "interrupt"
    assert run(judgment.interrupt_arm_for(job(1_000), view("running", 1_500))) == "none"
    assert run(judgment.interrupt_arm_for(job(1_000), view("done", None))) == "none"
    assert run(judgment.interrupt_arm_for(job(1_000), None)) == "none"


# ---------------------------------------------------------------- watch の拍は差分の読み・計器の始点は生まれの着地(2026-09-12 追補)


def test_watch_wake_reads_the_event_window_not_the_full_list() -> None:
    """watch で起きた拍は agent-job の全量 list も郵便の全量 list も撃たず、event-window の
    post-image(変わった行)と鍵での郵便の読みだけで claim → send まで進む。全量 list は最初の拍
    (まだ 1 度も読んでいない)と周期の保険だけ。"""
    world = World()
    world.tick()  # 最初の拍: 全量 list(周期の保険の初回)
    assert world.acp.lists.count(AGENT_JOB_KIND) == 1
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick(advance_ms=1_000)  # watch: changed → window
    assert world.acp.lists.count(AGENT_JOB_KIND) == 1
    assert MESSAGE_KIND not in world.acp.lists
    assert world.sessions.sends[-1] == (world.sid("j-1"), "first", True)
    assert [job.job_id for job in world.state.jobs] == ["j-1"]
    # 窓が読めない(retention の床の下)拍は全量 list に落ちる
    world.acp.window_incomplete = True
    world.acp.put_row(message("m-2", "second"))
    world.tick(advance_ms=1_000)
    assert world.acp.lists.count(AGENT_JOB_KIND) == 2
    # 周期の保険(watch_resync_seconds)でも全量 list
    world.acp.window_incomplete = False
    world.tick(advance_ms=31_000)
    assert world.acp.lists.count(AGENT_JOB_KIND) == 3


def test_agent_job_to_send_starts_from_the_birth_landing_not_the_second_granular_created_at() -> (
    None
):
    """計器 agent-job-to-send の始点 = 生まれの event(generation 1 の image)の landed_at(ns 精度)。
    行の createdAt(秒の粒度)は欄が無い時の値。"""
    world = World()
    world.tick()
    born = bound_job("j-1", inputs=[], created_at_ms=1_000)  # createdAt は秒の粒度
    assert born.status is not None
    pending: JSONObject = dict(born.status)
    pending["phase"] = "Pending"
    pending.pop("binding", None)
    world.acp.put_row(
        AcpRow(
            namespace=born.namespace,
            key=born.key,
            kind=born.kind,
            resource_id=born.resource_id,
            version=born.version,
            generation=1,
            created_at_ms=1_000,
            labels={},
            payload={},
            spec=born.spec,
            status=pending,
            landed_at_ms=1_437,
        )
    )
    bound = AcpRow(
        namespace=born.namespace,
        key=born.key,
        kind=born.kind,
        resource_id=born.resource_id,
        version=born.version,
        generation=2,
        created_at_ms=1_000,
        labels={},
        payload={},
        spec=born.spec,
        status=born.status,
        landed_at_ms=1_900,
    )
    world.acp.put_row(bound)
    world.local.now_ms = 2_500
    world.tick()
    metric = [line for line in world.local.metrics if line["metric"] == "agent-job-to-send"][-1]
    assert metric["createdAtMs"] == 1_437
    assert metric["ms"] == 2_500 - 1_437
    # 欄が無い行(古い journal)は今日の値に落ちる
    assert run(judgment.birth_ms_of(born, ())) == 1_000


# ---------------------------------------------------------------- session の id は agentd が鋳造(2026-09-12 追補 2)


def test_session_id_is_minted_by_agentd_and_the_charter_id_is_ignored() -> None:
    """charter の session_id / session_name(Messaging が組む launch の params)は読まない:
    起こす session の id は MintId の 1 点(fake は sid-<n>)。sessionHandle と stream の name も
    その id。"""
    world = World()
    world.acp.put_row(bound_job("j-1", inputs=[]))
    world.tick()
    launch = world.sessions.launches[-1]
    assert launch["session_id"] == "sid-1"
    assert launch["session_name"] == "sid-1"
    assert "charter-j-1" not in json.dumps(launch)
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["sessionHandle"] == {
        "sessionId": "sid-1",
        "stream": {"owner": "agentd", "name": "sid-1"},
    }
    plan = run(judgment.launch_plan_of(bound_job("j-9", inputs=[])))
    assert "session_id" not in plan.charter
    assert "session_name" not in plan.charter


def test_after_the_idle_ttl_the_next_job_launches_with_a_fresh_id_and_within_the_ttl_it_is_sent() -> (
    None
):
    """実弾 2026-09-12: 温かい session が idle TTL で片付いた後、次の job が charter の固定の id で
    `session is already registered` に落ちて LaunchFailed で Ended した。鋳造した id は片付いた
    行(host に登記のまま残る)と衝突しない。TTL 内の同じ会話は send。"""
    world = World()
    _run_first_turn(world)
    first = world.sid("j-1")
    # TTL 内: 同じ会話の次の手番は send(launch しない)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert world.sessions.sends[-1] == (first, "second", True)
    world.sessions.finish_turn(first, world.local.now_ms + 100)
    path = f"{HOMES}/claude/acct/projects/-work/{first}.jsonl"
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "two"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(first, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # TTL 超過: 片付く(行は host に登記のまま残る = fake も同じ意味論)
    world.tick(advance_ms=601_000)
    assert world.sessions.cleanups == [first]
    assert world.sessions.views[first].status == "stopped"
    # 次の job: 新しい id で launch に成功(charter の id は同じ固定の綴りのまま)
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 2
    third = world.sid("j-3")
    assert third != first
    job = world.job("j-3")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert world.sessions.sends[-1] == (third, "third", True)
    conditions = job.status["conditions"]
    assert conditions == []


# ---------------------------------------------------------------- 段 6 lane 6f: 1 命令の参加(join)と所有の等級


def _join_spec(argv: list[str], declaration: JSONObject | None = None) -> object:
    from doeff_agents.sessionhost.acp import join

    return run(join.join_spec_of(tuple(argv), declaration or {}, "/state"))


def test_join_spec_is_flags_over_declaration_over_defaults() -> None:
    """`doeff-sessionhost join` の宣言は flag > toml > 既定の 1 点(join.join-spec-of)で組む。
    既定: node の名は無し(機体の名)・置き場は state の根の下・backend は headless・
    hooks は inherit・custody は無し(既定の URL は handler)・所有は名乗らない。"""
    from doeff_agents.sessionhost.acp.effects import JoinSpec, Ownership

    bare = _join_spec(["--server", "http://acp:8868", "--token-file", "/t/agentd.token"])
    assert bare == JoinSpec(
        server="http://acp:8868",
        token_file="/t/agentd.token",
        node_name=None,
        state_dir="/state/doeff/acp-agentd",
        backend="headless",
        session_hooks="inherit",
        custody_url=None,
        borrower_key_file=None,
        ownership=None,
    )
    declaration: JSONObject = {
        "schema": "doeff.agentd-join.v1",
        "agentd": {
            "server": "http://toml:1",
            "token_file": "/toml/token",
            "node_name": "gcp-0",
            "state_dir": "/var/lib/doeff/agentd",
            "backend": "tmux",
            "session_hooks": "none",
            "ownership": "company",
            "ownership_proof": "gce-project:cyberagent-050",
        },
        "custody": {"url": "http://custody:8320", "borrower_key_file": "/toml/borrower"},
    }
    from_toml = _join_spec([], declaration)
    assert from_toml == JoinSpec(
        server="http://toml:1",
        token_file="/toml/token",
        node_name="gcp-0",
        state_dir="/var/lib/doeff/agentd",
        backend="tmux",
        session_hooks="none",
        custody_url="http://custody:8320",
        borrower_key_file="/toml/borrower",
        ownership=Ownership(grade="company", proof="gce-project:cyberagent-050"),
    )
    flagged = _join_spec(
        ["--server", "http://flag:2", "--node-name", "mac-9", "--ownership", "personal",
         "--ownership-proof", "declared", "--backend", "headless"],
        declaration,
    )
    assert isinstance(flagged, JoinSpec)
    assert flagged.server == "http://flag:2"
    assert flagged.token_file == "/toml/token"
    assert flagged.node_name == "mac-9"
    assert flagged.backend == "headless"
    assert flagged.ownership == Ownership(grade="personal", proof="declared")


def test_join_spec_refuses_missing_server_or_token_unknown_flags_and_bad_words() -> None:
    from doeff_agents.sessionhost.acp.effects import JoinSpec

    with pytest.raises(ValueError, match="--server"):
        _join_spec(["--token-file", "/t"])
    with pytest.raises(ValueError, match="--token-file"):
        _join_spec(["--server", "http://a"])
    with pytest.raises(ValueError, match="unknown argument: --acp"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--acp"])
    with pytest.raises(ValueError, match="requires a value"):
        _join_spec(["--server"])
    with pytest.raises(ValueError, match="ownership"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--ownership", "corporate"])
    # 等級を名乗るなら検の方法も要る(検なしは declared と明示する)。
    with pytest.raises(ValueError, match="ownership-proof"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--ownership", "company"])
    with pytest.raises(ValueError, match="ownership-proof"):
        _join_spec(
            ["--server", "http://a", "--token-file", "/t", "--ownership", "company",
             "--ownership-proof", "trust-me"]
        )
    with pytest.raises(ValueError, match="backend"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--backend", "docker"])
    with pytest.raises(ValueError, match="schema"):
        _join_spec(["--server", "http://a", "--token-file", "/t"], {"schema": "other.v9"})
    with pytest.raises(ValueError, match=r"\[agentd\]\.node_name"):
        _join_spec(
            ["--server", "http://a", "--token-file", "/t"],
            {"schema": "doeff.agentd-join.v1", "agentd": {"node_name": 7}},
        )
    with pytest.raises(ValueError, match=r"\[agentd\]\.colour"):
        _join_spec(
            ["--server", "http://a", "--token-file", "/t"],
            {"schema": "doeff.agentd-join.v1", "agentd": {"colour": "red"}},
        )
    assert isinstance(_join_spec(["--server", "http://a", "--token-file", "/t"]), JoinSpec)


def test_join_config_path_is_read_from_the_flag() -> None:
    from doeff_agents.sessionhost.acp import join

    assert run(join.config_path_of(("--config", "/etc/doeff/agentd.toml", "--server", "x"))) == (
        "/etc/doeff/agentd.toml"
    )
    assert run(join.config_path_of(("--server", "x"))) is None


def test_join_plan_derives_the_host_argv_and_the_env_bundle_from_the_spec() -> None:
    """env の束(今日の serve --acp の起動が読む名)と host の argv は宣言から導く(join-plan-of の 1 点)。
    弁は on・backend は argv と env の両方(host.hy は env・agentd は argv を読む)。"""
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinPlan, JoinSpec, Ownership

    spec = JoinSpec(
        server="http://acp:8868",
        token_file="/t/agentd.token",
        node_name="gcp-0",
        state_dir="/var/lib/doeff/agentd",
        backend="headless",
        session_hooks="inherit",
        custody_url="http://custody:8320",
        borrower_key_file="/etc/doeff/borrower-key",
        ownership=Ownership(grade="company", proof="gce-project:cyberagent-050"),
    )
    plan = run(join.join_plan_of(spec))
    assert plan == JoinPlan(
        host_argv=(
            "--db", "/var/lib/doeff/agentd/agentd.sqlite",
            "--socket", "/var/lib/doeff/agentd/agentd.sock",
            "--max-running", "none",
            "--backend", "headless",
            "serve",
        ),
        env=(
            ("DOEFF_AGENTD_ACP", "on"),
            ("ACP_DAEMON_URL", "http://acp:8868"),
            ("ACP_AGENTD_TOKEN_FILE", "/t/agentd.token"),
            ("DOEFF_AGENTD_NODE_NAME", "gcp-0"),
            ("DOEFF_SESSIONHOST_BACKEND", "headless"),
            ("DOEFF_SESSIONHOST_HEADLESS_DIR", "/var/lib/doeff/agentd/headless-events"),
            ("DOEFF_AGENTD_SESSION_HOOKS", "inherit"),
            ("AGORA_CUSTODY_URL", "http://custody:8320"),
            ("AGORA_BORROWER_KEY_PATH", "/etc/doeff/borrower-key"),
            ("DOEFF_AGENTD_OWNERSHIP", "company"),
            ("DOEFF_AGENTD_OWNERSHIP_PROOF", "gce-project:cyberagent-050"),
        ),
    )
    # 名乗らない値は env に現れない(handler の既定に任せる)。
    bare = run(
        join.join_plan_of(
            JoinSpec(
                server="http://acp:8868",
                token_file="/t/agentd.token",
                node_name=None,
                state_dir="/s",
                backend="headless",
                session_hooks="inherit",
                custody_url=None,
                borrower_key_file=None,
                ownership=None,
            )
        )
    )
    assert isinstance(bare, JoinPlan)
    names = [name for name, _value in bare.env]
    assert "DOEFF_AGENTD_NODE_NAME" not in names
    assert "AGORA_CUSTODY_URL" not in names
    assert "DOEFF_AGENTD_OWNERSHIP" not in names


def test_settings_from_env_reads_the_ownership_and_the_valve_and_runtime_agree_on_the_bundle() -> None:
    """join の env の束を今日の serve --acp の読み(settings_from_env / acp_valve)がそのまま読める =
    座は 1 つ(join-plan-of)で、読み手は増えない。"""
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinPlan, JoinSpec, Ownership
    from doeff_agents.sessionhost.acp.runtime import settings_from_env

    plan = run(
        join.join_plan_of(
            JoinSpec(
                server="http://acp:8868",
                token_file="/t/agentd.token",
                node_name="gcp-0",
                state_dir="/s",
                backend="headless",
                session_hooks="inherit",
                custody_url=None,
                borrower_key_file=None,
                ownership=Ownership(grade="company", proof="gce-project:cyberagent-050"),
            )
        )
    )
    assert isinstance(plan, JoinPlan)
    env = dict(plan.env)
    settings = settings_from_env(env, plan.host_argv)
    assert settings.node_name == "gcp-0"
    assert settings.backend_kind == "headless"
    assert settings.stream_capability == "events"
    assert settings.ownership == Ownership(grade="company", proof="gce-project:cyberagent-050")
    assert acp_valve(list(plan.host_argv), env).enabled is True
    assert settings_from_env({"DOEFF_AGENTD_NODE_NAME": NODE}, ()).ownership is None
    with pytest.raises(ValueError, match="DOEFF_AGENTD_OWNERSHIP"):
        settings_from_env({"DOEFF_AGENTD_NODE_NAME": NODE, "DOEFF_AGENTD_OWNERSHIP": "corp"}, ())


def test_ownership_verdict_gce_project_must_match_and_declared_is_taken_as_is() -> None:
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import Ownership, ProbeAnswer

    gce = Ownership(grade="company", proof="gce-project:cyberagent-050")
    assert run(join.ownership_verdict(gce, ProbeAnswer(value="cyberagent-050"))) == gce
    with pytest.raises(ValueError, match="cyberagent-050"):
        run(join.ownership_verdict(gce, ProbeAnswer(value="someone-else")))
    with pytest.raises(ValueError, match="metadata"):
        run(join.ownership_verdict(gce, ProbeAnswer(value=None)))
    declared = Ownership(grade="personal", proof="declared")
    assert run(join.ownership_verdict(declared, ProbeAnswer(value=None))) == declared


def test_ownership_preflight_probes_gce_only_for_a_gce_proof_and_refuses_a_mismatch() -> None:
    """検は起動の前(preflight)に 1 回: proof が gce-project なら OwnershipProbe を撃ち、declared なら
    撃たない。不一致は ValueError(runtime が fail-closed に写す — 参加しない)。"""
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import Ownership
    from doeff_agents.sessionhost.acp.runtime import install

    gce = Ownership(grade="company", proof="gce-project:cyberagent-050")
    local = FakeLocal()
    local.probe_answers["gce-project:cyberagent-050"] = "cyberagent-050"
    assert run(install(join.ownership_preflight(gce), [local.dispatch])) == gce
    assert local.probes == ["gce-project:cyberagent-050"]
    local.probe_answers["gce-project:cyberagent-050"] = None
    with pytest.raises(ValueError, match="metadata"):
        run(install(join.ownership_preflight(gce), [local.dispatch]))
    declared = Ownership(grade="company", proof="declared")
    before = list(local.probes)
    assert run(install(join.ownership_preflight(declared), [local.dispatch])) == declared
    assert local.probes == before


def test_node_observations_carry_the_ownership_when_declared() -> None:
    """node の status.observations.ownership = {grade, proof}(宣言が在る時だけ・無ければ欄ごと無い =
    未観測)。書く点は judgment.node-status-with-lease の 1 点。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp.effects import Ownership

    world = World()
    world.tick()
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    assert node.status["observations"] == {"streamCapability": "frames", "sessions": []}

    owned = World()
    owned.settings = replace(
        owned.settings, ownership=Ownership(grade="company", proof="gce-project:cyberagent-050")
    )
    owned.tick()
    node = owned.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    assert node.status["observations"] == {
        "streamCapability": "frames",
        "sessions": [],
        "ownership": {"grade": "company", "proof": "gce-project:cyberagent-050"},
    }
