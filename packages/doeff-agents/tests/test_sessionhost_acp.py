"""agentd(sessionhost の ACP の腕・段 2 agora-redesign #19 / #20)の焦点の検。

fake の handler(doeff_agents.sessionhost.acp.fake)で同じ program(agentd.hy)を一周させる
e2e の形 1 本: 参加(node の lease)→ Bound の job を置く → Running + sessionHandle →
TurnDelta を 1 本押す → 手番の終わりに Ended と turn-record。加えて判断の純関数と弁。
HTTP も socket も tmux も無い。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import get_args

import hy  # noqa: F401  # registers the .hy importer
import pytest
from doeff_agents.sessionhost.acp import handlers, judgment
from doeff_agents.sessionhost.acp.effects import (
    AGENTD_PRINCIPAL,
    AGENT_JOB_KIND,
    AGENT_JOB_NAMESPACE,
    AGORA_KINDS_NAMESPACE,
    AcpRow,
    AgentdSettings,
    CLAUDE_OAUTH_TOKEN_ENV,
    CONDITION_CREDENTIAL_PLACE_MISMATCH,
    CONDITION_CREDENTIAL_SOURCE_MISSING,
    CustodyLeaseBorrow,
    EntryKind,
    InFlightJob,
    JSON,
    JSONObject,
    LeaseGrant,
    LeaseRefused,
    MESSAGE_KIND,
    NODE_KIND,
    PHASE_BOUND,
    PHASE_ENDED,
    PHASE_RUNNING,
    PROFILE_KIND,
    SessionRefused,
    SessionView,
    TURN_RECORD_ENTRIES_BYTE_BUDGET,
    TURN_RECORD_KIND,
)
from doeff_agents.sessionhost.acp.fake import Birth, FakeAcp, FakeCustody, FakeLocal, FakeSessions
from doeff_agents.sessionhost.acp.runtime import initial_state, run_close_for_stop, run_tick
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
    effort: str | None = None,
    work_dir: str = "/work",
    agent_type: str = "claude",
    escalation_seconds: int | None = None,
) -> AcpRow:
    binding: JSONObject = {"node": NODE, "profile": "personal"}
    if account is not None:
        binding["account"] = account
    charter: JSONObject = {
        # charter の id は agentd が読まない(session の id は agentd が鋳造する — 2026-09-12 追補 2)。
        # 読んだら検が割れるよう、job の id とも鋳造の綴り(sid-<n>)とも違う綴りにする。
        "session_id": f"charter-{job_id}",
        "session_name": f"charter-{job_id}",
        "agent_type": agent_type,
        "work_dir": work_dir,
        "prompt": "start",
        "model": "claude-opus-5",
    }
    if lifecycle is not None:
        charter["lifecycle"] = lifecycle
    if effort is not None:
        charter["effort"] = effort
    if escalation_seconds is not None:
        # 段 10 lane 10n: 期限は charter の値ちょうど(Messaging が方策の行の値を会話の宣言で重ねて写す)。
        charter["interruptEscalationSeconds"] = escalation_seconds
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
        # 段 10 lane 10d(R28): 宣言の capacity は種の node の行と同じ値 — 揃えの書きを検体の拍に混ぜない。
        self.settings = AgentdSettings(node_name=NODE, homes_root=HOMES, node_capacity=1)
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
    assert node.status["observations"] == {
        "streamCapability": "frames",
        "sessions": [],
        "transcripts": [],
    }
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
        "sessionId": "sid-1",
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


def test_custody_declared_node_does_not_launch_a_job_without_an_account() -> None:
    """段 10c(agora-redesign #80・R23): 預かり所を宣言した node は account の無い job を起こさず(claim も書かず)、
    条件 CredentialSourceMissing で閉じる — 黙って charter の家(機体の profile の家)へ落ちない。"""
    world = World()
    world.settings = replace(world.settings, custody_declared=True)
    world.acp.put_row(bound_job("s-4", inputs=[], account=None))
    world.tick()
    assert world.sessions.launches == []
    assert world.custody.borrowed == []
    job = world.job("s-4")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert "sessionHandle" not in job.status
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict)
    assert last["type"] == CONDITION_CREDENTIAL_SOURCE_MISSING
    assert "custody" in str(last["reason"])
    assert world.state.jobs == ()


def test_a_job_bound_to_another_places_account_is_not_launched() -> None:
    """段 10 lane 10d 便 2(agora-redesign #85・不変条件 I5): 自分の置き場と違う置き場の口座の job は起こさない —
    借りもせず(封じた資格はその置き場の worker にしか無い)、条件 CredentialPlaceMismatch で閉じる。"""
    world = World()
    world.settings = replace(world.settings, custody_declared=True, place="personal")
    world.acp.put_row(row("default", "profile", "personal", {"boundary": "company"}, {}))
    world.acp.put_row(bound_job("s-place", inputs=[]))
    world.tick()
    assert world.custody.borrowed == [], "違う置き場の口座を借りに行った"
    assert world.sessions.launches == []
    job = world.job("s-place")
    assert job.status is not None and job.status["phase"] == PHASE_ENDED
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict) and last["type"] == CONDITION_CREDENTIAL_PLACE_MISMATCH
    assert "place" in str(last["reason"])


def test_a_job_whose_profile_does_not_name_a_place_is_launched() -> None:
    """置き場を名乗らない profile の行(旧い行)は食い違いと読まない — 最後の門は預かり所の redeem(判らないもので止めない)。"""
    world = World()
    world.settings = replace(world.settings, custody_declared=True, place="personal")
    world.acp.put_row(bound_job("s-noplace", inputs=[]))
    world.tick()
    assert world.custody.borrowed == [("claude", "acct", "agent-job s-noplace")]


def test_custody_declared_node_borrows_the_account_and_launches_in_the_borrowed_home() -> None:
    """段 10c(R23): 預かり所を宣言した node でも account の在る job は預かり所から借り、借りた家で起こす。"""
    world = World()
    world.settings = replace(world.settings, custody_declared=True)
    world.acp.put_row(bound_job("s-5", inputs=[]))
    world.tick()
    assert world.custody.borrowed == [("claude", "acct", "agent-job s-5")]
    launch = world.sessions.launches[0]
    env = launch["session_env"]
    assert isinstance(env, dict)
    assert env[CLAUDE_OAUTH_TOKEN_ENV] == TOKEN
    assert launch["binding"] == {"kind": "claude-code", "config_dir": f"{HOMES}/claude/acct"}
    job = world.job("s-5")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING


def test_undeclared_node_keeps_the_charter_home_for_a_job_without_an_account() -> None:
    """段 10c(R23): 預かり所を宣言していない node(移行前の機体)は今日どおり charter で起こす(借りない)。"""
    world = World()
    assert world.settings.custody_declared is False
    world.acp.put_row(bound_job("s-6", inputs=[], account=None))
    world.tick()
    assert world.custody.borrowed == []
    assert len(world.sessions.launches) == 1
    assert "binding" not in world.sessions.launches[0]


@pytest.mark.parametrize(
    ("account", "declared", "verdict"),
    [("acct", True, "lease"), ("acct", False, "lease"), (None, True, "missing"), (None, False, "home")],
)
def test_credential_source_is_one_judgment(account: str | None, declared: bool, verdict: str) -> None:
    plan = run(judgment.launch_plan_of(bound_job("x", inputs=[], account=account)))
    assert run(judgment.credential_source_of(plan, declared)) == verdict


def test_missing_node_row_is_registered_from_the_declaration_and_joined_on_the_next_heartbeat() -> None:
    """R28(段 10 lane 10d・agora-redesign #85): 行の無い node は agentd が機体の宣言から自分の行を作る
    (name・capacity = 宣言・streamCapability = backend の語・labels は空)— 人が撃つ register-node の段は無い。"""
    world = World()
    key = f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"
    del world.acp.rows[key]
    world.tick()
    assert world.acp.rows[key].spec == {"name": NODE, "labels": {}, "capacity": 1, "streamCapability": "frames"}
    assert [line for line in world.local.logs if "node row" in line] == [
        f"agentd: registered node row {NODE!r} from the declaration (capacity 1, streamCapability frames)"
    ]
    assert world.state.node_missing_logged is False
    world.tick(advance_ms=30_000)
    status = world.acp.rows[key].status
    assert isinstance(status, dict)
    lease = status.get("lease")
    assert isinstance(lease, dict)
    assert lease["owner"] == world.settings.principal


def test_node_row_writes_carry_the_fingerprint_of_the_declaration_file_the_agentd_read() -> None:
    """反例(段 10 lane 10y・agora-redesign #110 の実測 2026-09-15 02:39): ACP の kind node の capacity は declaredByFile
    (ACP 段 10 lane 10t 便 1b)で、誕生を含む書きは header x-declaration-sha256 が要る。指紋を運ばない agentd は
    "node row 'Proboscis-MBP' is not in ACP and could not be registered (403: … needs the file's fingerprint …)" で新しい節が
    1 つも参加できなかった。agentd は読んだ宣言 file の指紋(settings.declaration_sha256)を node の行の誕生と spec の揃えの
    両方に運ぶ。lease(status の書き)は declaredByFile の欄を変えないので運ばない。"""
    from dataclasses import replace

    fingerprint = "ab" * 32
    world = World()
    world.settings = replace(world.settings, declaration_sha256=fingerprint)
    key = f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"
    del world.acp.rows[key]
    world.tick()
    assert key in world.acp.rows
    assert world.acp.fingerprints == [(key, fingerprint)]
    # 宣言の capacity を変えた agentd の揃えの書きも同じ指紋を運ぶ
    world.settings = replace(world.settings, node_capacity=3)
    world.tick(advance_ms=30_000)
    assert world.acp.fingerprints == [(key, fingerprint), (key, fingerprint)]
    assert world.acp.rows[key].spec["capacity"] == 3


def test_node_registration_refused_is_logged_once_and_retried_each_heartbeat() -> None:
    """R28: 作れない拍(契約の書き手の登録し直しの前など)は 1 度だけ log し、heartbeat ごとに撃ち直す。"""
    from doeff_agents.sessionhost.acp.effects import Refused

    world = World()
    key = f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"
    del world.acp.rows[key]
    refusal = "principal agentd is not a writer of node create"
    world.acp.create_refusals[key] = [Refused(403, refusal), Refused(403, refusal)]
    world.tick()
    world.tick(advance_ms=30_000)
    assert world.acp.creates[key] == 2
    assert key not in world.acp.rows
    assert [line for line in world.local.logs if "node row" in line] == [
        f"agentd: node row {NODE!r} is not in ACP and could not be registered (403: {refusal}); re-trying each heartbeat"
    ]
    world.tick(advance_ms=30_000)
    assert key in world.acp.rows
    assert world.state.node_missing_logged is False


def test_node_spec_is_aligned_to_the_declaration_keeping_labels_and_the_lease_is_written_in_the_same_tick() -> None:
    """R28: 手で登記された行(capacity 0・labels boundary / pool)は宣言へ揃う — labels は触らず、同じ拍に lease も書く。"""
    from dataclasses import replace

    world = World()
    world.settings = replace(world.settings, node_capacity=2)
    key = f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"
    labels = {"boundary": "personal", "pool": "agentd-pool"}
    seeded = world.acp.rows[key]
    world.acp.put_row(
        replace(seeded, spec={"name": NODE, "labels": labels, "capacity": 0, "streamCapability": "frames"})
    )
    world.tick()
    aligned = {"name": NODE, "labels": labels, "capacity": 2, "streamCapability": "frames"}
    assert world.acp.spec_writes == [(key, aligned)]
    now_row = world.acp.rows[key]
    assert now_row.spec == aligned
    assert isinstance(now_row.status, dict)
    assert now_row.status["state"] == "joined"
    lease = now_row.status.get("lease")
    assert isinstance(lease, dict)
    assert lease["owner"] == world.settings.principal
    assert [line for line in world.local.logs if "node row" in line] == [
        f"agentd: node row {NODE!r} spec aligned to the declaration (capacity 0 -> 2)"
    ]
    world.tick(advance_ms=30_000)
    assert len(world.acp.spec_writes) == 1


def test_node_spec_alignment_refused_still_writes_the_lease_and_logs_once() -> None:
    """R28: 揃えられない拍(書き手の断り)も lease は書く — 参加の生存を spec の書きの成否に結ばない。log は 1 度。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp.effects import Refused

    world = World()
    world.settings = replace(world.settings, node_capacity=2)
    key = f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"
    refusal = "principal agentd is not a writer of node update"
    world.acp.spec_refusals[key] = [Refused(403, refusal), Refused(403, refusal)]
    world.tick()
    world.tick(advance_ms=30_000)
    assert world.acp.spec_writes == []
    now_row = world.acp.rows[key]
    assert now_row.spec["capacity"] == 1
    assert isinstance(now_row.status, dict)
    lease = now_row.status.get("lease")
    assert isinstance(lease, dict)
    assert lease["heartbeatAt"] == world.local.now_ms
    assert [line for line in world.local.logs if "node row" in line] == [
        f"agentd: node row {NODE!r} spec differs from the declaration and could not be aligned (403: {refusal}); "
        "the lease is still written"
    ]
    assert world.state.node_spec_refusal_logged is True


def test_node_spec_of_and_node_spec_declared_are_one_judgment() -> None:
    """R28: 作る時の spec(labels 空)と、揃える時の spec(labels は行のまま・欠落は空)は judgment の 2 点。"""
    settings = AgentdSettings(node_name="pool-1", node_capacity=2, stream_capability="events")
    assert run(judgment.node_spec_of(settings)) == {
        "name": "pool-1",
        "labels": {},
        "capacity": 2,
        "streamCapability": "events",
    }
    # 段 10 lane 10d 便 4(依頼者の裁定 2026-09-15 問 3): 宣言した置き場は **型つきの欄 spec.place**
    # に名乗る(配車の絞りが読む 1 点)。labels.place は読み手が残る間の写し(deprecated)で、
    # 同じ値を書き続ける — 面を落とさない。
    placed = replace(settings, place="personal")
    named = run(judgment.node_spec_of(placed))
    assert isinstance(named, dict)
    assert named["place"] == "personal", "置き場を型つきの欄で名乗っていない(配車が絞れない)"
    assert named["labels"] == {"place": "personal"}, "deprecated の写しを落とした(読み手が残っている)"
    # 置き場を宣言しない断面(検体の既定)では欄ごと足さない — 嘘の名乗りを書かない
    assert "place" not in run(judgment.node_spec_of(settings))
    kept = run(
        judgment.node_spec_declared(
            {"name": "pool-1", "labels": {"boundary": "personal", "pool": "agentd-pool"}, "capacity": 0, "streamCapability": "events"},
            placed,
        )
    )
    assert kept["labels"] == {"boundary": "personal", "pool": "agentd-pool", "place": "personal"}, (
        "宣言の外の名乗り(boundary 等)を触るか、置き場を名乗れていない"
    )
    assert kept["place"] == "personal", "揃える時に型つきの欄を名乗っていない(便 4)"
    hand = {"name": "pool-1", "labels": {"boundary": "company"}, "capacity": 0, "streamCapability": "events"}
    assert run(judgment.node_spec_declared(hand, settings)) == {**hand, "capacity": 2}
    assert run(judgment.node_spec_declared({**hand, "capacity": 2}, settings)) == {**hand, "capacity": 2}
    bare = {"name": "pool-1", "capacity": 2, "streamCapability": "frames"}
    assert run(judgment.node_spec_declared(bare, settings)) == {
        "name": "pool-1",
        "labels": {},
        "capacity": 2,
        "streamCapability": "events",
    }


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
    assert [entry.kind for entry in batch.entries] == ["tool_use", "text", "tool_result"]
    assert [body["kind"] for body in batch.bodies] == ["tool_use", "text", "tool_result"]
    assert batch.usage == {
        "input": 1,
        "output": 2,
        "cacheWrite": 0,
        "cacheRead": 0,
        "model": "claude-opus-5",
    }
    assert batch.next_seq == 14
    # 段 10 lane 10j(agora-redesign #87 の裁定 問 7): 実況の道具の呼び出しは入力の object をそのまま運ぶ(契約 turn-delta.json)
    assert batch.frames[1]["payload"] == {
        "toolUseId": "t1",
        "name": "Bash",
        "summary": '{"command": "ls"}',
        "input": {"command": "ls"},
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


def test_warm_send_carries_the_token_this_turn_borrowed() -> None:
    """段 10 lane 10d 便 2 の追補 2(実弾 #92 = 預かり所が口座を更新した拍に、温かい session の再開の
    手番が誕生時の access token を使い回して 401 revoked)。温かい手番の送りは **その手番で借りた札**
    を env で運ぶ — 器が降りた process を `--resume` で起こし直す時に使う値で、行には残らない。"""
    world = World()
    _run_first_turn(world)
    warm = world.sid("j-1")
    assert world.sessions.send_envs[-1] == (warm, {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN})
    # 預かり所が口座を更新して札が回った(誕生の札は revoke されている)
    world.custody.tokens["acct"] = "sk-ant-oat01-rotated"
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert world.sessions.sends[-1] == (warm, "second", True)
    assert world.sessions.send_envs[-1] == (warm, {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-rotated"})


def test_warm_send_of_a_codex_conversation_carries_no_env() -> None:
    """codex の札は家の中の auth file が運ぶ(env には出さない)— 手番の送りが運ぶ env は空。"""
    world = World()
    charter_job = bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400)
    charter = charter_job.spec["charter"]
    assert isinstance(charter, dict)
    charter["agent_type"] = "codex"
    charter["binding"] = {"kind": "codex", "codex_home": "/bundle"}
    world.custody = FakeCustody(auth_jsons={"acct": '{"tokens": {}}'})
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(charter_job)
    world.tick()
    assert world.sessions.send_envs[-1] == (world.sid("j-1"), {})


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
    # 段 9f lane 9f-4: 行の entry は見出し(本文の欄は無い)— 2 手番目の本文 1 block が 1 見出し。
    assert [e["kind"] for e in entries if isinstance(e, dict)] == ["text"]
    assert all(isinstance(e, dict) and "text" not in e for e in entries)
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
    # 片付いた後の次の手番は同じ機体 ∧ 同じ家なので片付いた session から cold の --resume(cache を保つ・
    # operator 決定 #54)— 新しく鋳造した id(片付いた行と衝突しない)。
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert [resumed["session_id"] for resumed in world.sessions.resumes] == [world.sid("j-1")]
    assert world.sid("j-2") != world.sid("j-1")
    assert world.sessions.resumes[-1]["new_session_id"] == world.sid("j-2")
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
        {
            "conversationId": CONVERSATION,
            "sessionId": world.sid("j-1"),
            "state": "idle",
            "account": "acct",
        }
    ]


def test_node_status_names_the_capability_table() -> None:
    """段 10 lane 10e(agora-redesign #53): node の status.capabilities = agent の種類ごとの {settings, restartOn}
    (契約 kinds.node.status.capabilities・書き手 agentd・lease と同じ拍)。restartOn = session-affinity-key-of の鍵の欄
    (model・profile)ちょうどで、effort と workDir は受けるが session を作り直さない。"""
    from doeff_agents.sessionhost.acp.effects import (
        AGENT_ATTACHMENT_CAPABILITY,
        AGENT_CAPABILITIES,
        AGENT_INTERRUPT_CAPABILITY,
        AGENT_SETTINGS,
    )

    world = World()
    world.tick()
    node = world.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
    assert node.status is not None
    table = node.status["capabilities"]
    assert isinstance(table, dict)
    assert table == run(judgment.capabilities_of())
    assert set(table) == {"claude", "codex"}
    for kind, entry in table.items():
        assert isinstance(entry, dict)
        # 段 10 lane 10n: 割り込みの能力(steer-then-stop = 注入 → 期限で停止の合図 / stop = 即座に止めて渡す)
        # 段 10 lane 10o(agora-redesign #96): 受ける添付の種類の語の列(欠落 = 何も受けない)
        assert entry == {
            "settings": list(AGENT_CAPABILITIES[kind]["settings"]),
            "restartOn": list(AGENT_CAPABILITIES[kind]["restartOn"]),
            "interrupt": AGENT_INTERRUPT_CAPABILITY[kind],
            "attachments": list(AGENT_ATTACHMENT_CAPABILITY[kind]),
        }
        assert entry["attachments"] == ["image"]
        settings_of_kind = entry["settings"]
        restart_of_kind = entry["restartOn"]
        assert isinstance(settings_of_kind, list) and isinstance(restart_of_kind, list)
        assert settings_of_kind == list(AGENT_SETTINGS)
        assert set(restart_of_kind) <= set(settings_of_kind)
        assert restart_of_kind == ["model", "profile"]
    claude_entry = table["claude"]
    codex_entry = table["codex"]
    assert isinstance(claude_entry, dict)
    assert isinstance(codex_entry, dict)
    assert claude_entry["interrupt"] == "steer-then-stop"
    assert codex_entry["interrupt"] == "stop"
    # 鍵の欄 ⇔ restartOn: 鍵は account(profile の家)・binding(profile の家)・model の 3 欄で、effort / workDir は無い。
    plan = run(judgment.launch_plan_of(bound_job("a", inputs=[], effort="xhigh", work_dir="/elsewhere")))
    key = run(judgment.session_affinity_key_of(plan))
    assert set(key) == {"account", "binding", "model"}
    assert "effort" not in key and "workDir" not in key and "work_dir" not in key


def test_ignored_settings_of_is_the_one_decision() -> None:
    """段 10 lane 10e: 効かない会話の宣言の欄は条件 AgentSettingIgnored(1 欄 1 行)— 判断は ignored-settings-of の 1 点。
    claude が全部受ける手番は空 / 能力の表に無い種類は宣言した欄が全部 / 温かい session への send で work_dir が
    session の cwd と違う時は workDir(session は作り直さない・次に起こす時に効く)。"""
    plan = run(judgment.launch_plan_of(bound_job("a", inputs=[], effort="xhigh")))
    warm = _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=10)
    assert run(judgment.ignored_settings_of(plan, None, "launch")) == ()
    assert run(judgment.ignored_settings_of(plan, warm, "send")) == ()
    elsewhere = run(judgment.launch_plan_of(bound_job("b", inputs=[], work_dir="/elsewhere")))
    assert run(judgment.ignored_settings_of(elsewhere, None, "launch")) == ()
    ignored = run(judgment.ignored_settings_of(elsewhere, warm, "send"))
    assert isinstance(ignored, tuple) and len(ignored) == 1
    assert ignored[0]["type"] == "AgentSettingIgnored"
    assert ignored[0]["status"] == "True"
    assert ignored[0]["reason"].startswith("workDir=/elsewhere: the warm session keeps its cwd /work")
    unknown = run(judgment.launch_plan_of(bound_job("c", inputs=[], effort="low", agent_type="gemini")))
    reasons = [c["reason"] for c in run(judgment.ignored_settings_of(unknown, None, "launch"))]
    assert reasons == [
        "model=claude-opus-5: agent kind 'gemini' names no capability table",
        "effort=low: agent kind 'gemini' names no capability table",
        "workDir=/work: agent kind 'gemini' names no capability table",
    ]


def test_second_turn_with_another_effort_resumes_the_same_session_with_the_new_flag() -> None:
    """段 10 lane 10e: effort は process の旗なので温かい process には届かない — 同じ家で effort だけ違う次の手番は、
    温かい session を片付けて同じ session を新しい effort で --resume する(session は作り直さない・cache は保つ)。
    同じ effort の手番は今までどおり send。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], effort="high", created_at_ms=world.local.now_ms - 400))
    world.tick()
    first = world.sid("j-1")
    assert world.sessions.launches[-1]["effort"] == "high"
    attribution = world.sessions.views[first].launch_attribution
    assert isinstance(attribution, dict)
    mine = attribution["agentd"]
    assert isinstance(mine, dict)
    assert mine["effort"] == "high"
    path = f"{HOMES}/claude/acct/projects/-work/{first}.jsonl"
    world.local.transcripts[path] = transcript_line("assistant", [{"type": "text", "text": "one"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(first, world.local.now_ms + 500)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # 同じ effort → send(片付けない・resume しない)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], effort="high", created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert world.sessions.cleanups == []
    assert world.sessions.resumes == []
    assert world.sessions.sends[-1] == (first, "second", True)
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "two"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(first, world.local.now_ms + 200)
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # 違う effort → 片付けて同じ session を新しい旗で resume
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"], effort="xhigh", created_at_ms=world.local.now_ms + 700))
    world.tick(advance_ms=1_000)
    assert world.sessions.cleanups == [first]
    assert len(world.sessions.launches) == 1
    assert len(world.sessions.resumes) == 1
    resumed = world.sessions.resumes[-1]
    assert resumed["session_id"] == first
    assert resumed["effort"] == "xhigh"
    third = world.sid("j-3")
    assert third != first
    assert resumed["new_session_id"] == third
    stamped = world.sessions.views[third].launch_attribution
    assert isinstance(stamped, dict)
    stamped_mine = stamped["agentd"]
    assert isinstance(stamped_mine, dict)
    assert stamped_mine["effort"] == "xhigh"
    assert stamped_mine["arm"] == "resume"
    job = world.job("j-3")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    handle = job.status["sessionHandle"]
    assert isinstance(handle, dict)
    assert handle["sessionId"] == third
    assert job.status.get("conditions", []) == []


def test_warm_send_with_another_work_dir_records_agent_setting_ignored() -> None:
    """段 10 lane 10e: 温かい session への send で charter の work_dir が違う手番は、送りはするが条件
    AgentSettingIgnored{workDir} を手番の終わりに刻む(黙って落とさない)。"""
    world = World()
    path = _run_first_turn(world)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(
        bound_job("j-2", inputs=["m-2"], work_dir="/elsewhere", created_at_ms=world.local.now_ms + 700)
    )
    world.tick(advance_ms=1_000)
    warm = world.sid("j-1")
    assert world.sessions.sends[-1] == (warm, "second", True)
    assert world.sessions.cleanups == []
    world.local.transcripts[path] += transcript_line("assistant", [{"type": "text", "text": "two"}])
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(warm, world.local.now_ms + 200)
    world.tick(advance_ms=1_000)
    ended = world.job("j-2")
    assert ended.status is not None
    assert ended.status["phase"] == PHASE_ENDED
    conditions = ended.status["conditions"]
    assert isinstance(conditions, list)
    ignored = [c for c in conditions if isinstance(c, dict) and c.get("type") == "AgentSettingIgnored"]
    assert len(ignored) == 1
    assert ignored[0]["status"] == "True"
    assert str(ignored[0]["reason"]).startswith("workDir=/elsewhere: the warm session keeps its cwd /work")
    assert any("ignores an agent setting" in line for line in world.local.logs)


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


# ---------------------------------------------------------------- 段 8 lane 4x: 割り込みの本文(agora-redesign #56)


def _place_interrupt(world: World, job_id: str, message_ids: list[str]) -> None:
    """Messaging の代わりに走っている job の行へ interrupts を載せる(他の欄は写す・generation + 1)。"""
    running = world.job(job_id)
    assert running.status is not None
    placed: JSONObject = dict(running.status)
    existing = placed.get("interrupts")
    placed["interrupts"] = (list(existing) if isinstance(existing, list) else []) + list(
        message_ids
    )
    world.acp.put_row(
        row(
            AGENT_JOB_NAMESPACE,
            AGENT_JOB_KIND,
            job_id,
            running.spec,
            placed,
            created_at_ms=running.created_at_ms,
        )
    )


def test_interrupt_on_a_running_job_is_handed_to_the_session_and_recorded_on_the_row() -> None:
    """走っている自分の job の行に載った割り込み(status.interrupts)は、本文を鍵で読んで session.send の
    mode = interrupt で器へ即座に渡し、同じ 1 回の CAS で interrupts から消して interruptsDelivered へ
    足す。次の拍に同じ id を二度渡さない。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    sid = world.sid("j-1")
    world.acp.put_row(message("m-i1", "stop and answer"))
    world.acp.put_row(message("m-i2", "then continue"))
    _place_interrupt(world, "j-1", ["m-i1", "m-i2"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections == [(sid, "stop and answer"), (sid, "then continue")]
    # 通常の send(次の手番)は撃たれていない・手番は 1 つのまま
    assert [text for _sid, text, _awaiting in world.sessions.sends] == ["first"]
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert job.status["interrupts"] == []
    assert job.status["interruptsDelivered"] == ["m-i1", "m-i2"]
    assert job.status["sessionHandle"] == {
        "sessionId": sid,
        "stream": {"owner": "agentd", "name": sid},
    }
    assert [j.interrupts_sent for j in world.state.jobs] == [("m-i1", "m-i2")]
    # 次の拍: 行にも memory にも渡した印が在るので二度渡さない
    world.tick(advance_ms=1_000)
    assert len(world.sessions.interjections) == 2
    # もう 1 通: 行の並びは delivered の末尾に足される
    world.acp.put_row(message("m-i3", "one more"))
    _place_interrupt(world, "j-1", ["m-i3"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections[-1] == (sid, "one more")
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interruptsDelivered"] == ["m-i1", "m-i2", "m-i3"]
    assert job.status["interrupts"] == []


def test_interrupt_refused_by_the_session_stays_on_the_row_and_is_not_recorded_as_delivered() -> (
    None
):
    """器が断った(走っている手番が無い)割り込みは行に残す(渡していない印 = Messaging が終端の
    行から queued へ積み直す材料)。本文の無い id は渡せない(log)が、行には残す。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    world.acp.put_row(message("m-i1", "too late"))
    _place_interrupt(world, "j-1", ["m-i1", "m-missing"])
    world.sessions.refuse_interject = SessionRefused("no turn in flight", None)
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections == []
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interrupts"] == ["m-i1", "m-missing"]
    assert "interruptsDelivered" not in job.status
    # 器が受けるようになれば同じ id を渡す(level-triggered — 断りは memory に残らない)
    world.sessions.refuse_interject = None
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections == [(world.sid("j-1"), "too late")]
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interrupts"] == ["m-missing"]
    assert job.status["interruptsDelivered"] == ["m-i1"]
    # 本文の無い id は memory に写して撃ち直さない(行には残る)
    world.tick(advance_ms=1_000)
    assert len(world.sessions.interjections) == 1
    assert [j.interrupts_sent for j in world.state.jobs] == [("m-i1", "m-missing")]


def _claude_running_turn(session_id: str) -> str:
    """claude の走っている手番の行(init・道具の呼び出しまで — result はまだ)。"""
    return "".join(
        [
            _stream_line({"type": "system", "subtype": "init", "session_id": session_id}),
            _stream_line(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_1",
                        "role": "assistant",
                        "model": "claude-opus-5",
                        "content": [
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 90"}}
                        ],
                        "usage": {"input_tokens": 3, "output_tokens": 7},
                    },
                }
            ),
        ]
    )


def _condition_types(status: JSONObject) -> list[str]:
    """status.conditions の type の列(InterruptEscalationUndeclared の在否の読み口)。"""
    conditions = status["conditions"]
    assert isinstance(conditions, list)
    return [
        str(c["type"]) for c in conditions
        if isinstance(c, dict) and c.get("type") == "InterruptEscalationUndeclared"
    ]


def _entry_seqs_of_kind(world: World, job_id: str, kind: str) -> list[int]:
    record = world.turn_record(job_id)
    assert record is not None
    assert record.status is not None
    entries = record.status["entries"]
    assert isinstance(entries, list)
    out: list[int] = []
    for entry in entries:
        assert isinstance(entry, dict)
        if entry["kind"] == kind:
            seq = entry["seq"]
            assert isinstance(seq, int)
            out.append(seq)
    return out


def _entry_kinds(world: World, job_id: str) -> list[str]:
    record = world.turn_record(job_id)
    assert record is not None
    assert record.status is not None
    entries = record.status["entries"]
    assert isinstance(entries, list)
    return [str(entry["kind"]) for entry in entries if isinstance(entry, dict)]


def test_interrupt_is_injected_with_the_message_id_as_its_name_and_read_at_the_boundary_is_recorded() -> None:
    """段 10 lane 10n(agora-redesign #93): 注入の行の名 = Message の id(session.send の ref)。材料の
    command_lifecycle started(model が読む拍 — 実測 2026-09-14)を kind system の entry にし、その seq を
    行の status.interruptsRead に写す。期限の前に読めたので停止の合図は出ない。"""
    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(
        bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400, escalation_seconds=20)
    )
    world.tick()
    sid = world.sid("j-1")
    events = f"/events/{sid}.events.jsonl"
    world.local.transcripts[events] = _claude_running_turn(sid)
    world.tick(advance_ms=1_000)
    world.acp.put_row(message("m-i1", "stop and answer"))
    _place_interrupt(world, "j-1", ["m-i1"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections == [(sid, "stop and answer")]
    assert world.sessions.interjection_refs == [(sid, "m-i1")]
    assert [j.interrupts_injected for j in world.state.jobs] == [(("m-i1", world.local.now_ms),)]
    assert [j.interrupt_escalation_seconds for j in world.state.jobs] == [20]
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interruptsDelivered"] == ["m-i1"]
    assert "interruptsRead" not in job.status
    assert _condition_types(job.status) == []
    # 道具の境界で畳まれた: CLI が started を名乗る(queued の行は証拠ではない)
    world.local.transcripts[events] += _stream_line(
        {"type": "command_lifecycle", "command_uuid": "m-i1", "state": "queued", "session_id": sid}
    )
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    assert "interruptsRead" not in job.status
    world.local.transcripts[events] += _stream_line(
        {"type": "command_lifecycle", "command_uuid": "m-i1", "state": "started", "session_id": sid}
    )
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    read = job.status["interruptsRead"]
    assert isinstance(read, dict)
    seqs = _entry_seqs_of_kind(world, "j-1", "system")
    assert read == {"m-i1": seqs[-1]}
    assert world.sessions.escalations == []
    assert [j.interrupt_marks_dirty for j in world.state.jobs] == [False]
    # 期限を越えても読んだ id には合図を出さない
    world.tick(advance_ms=30_000)
    assert world.sessions.escalations == []
    job = world.job("j-1")
    assert job.status is not None
    assert "interruptsEscalated" not in job.status


def test_interrupt_unread_past_the_deadline_escalates_once_and_the_next_turn_carries_it() -> None:
    """段 10 lane 10n: 注入から charter.interruptEscalationSeconds の間に読んだ証拠が無ければ session.escalate を
    1 度出し、未読の id に止めた時刻を status.interruptsEscalated へ写す。止めた段の材料(control_response の
    still_queued・is_error の result・started)は kind system の entry(誤りではない)で、started が読んだ印。"""
    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(
        bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400, escalation_seconds=20)
    )
    world.tick()
    sid = world.sid("j-1")
    events = f"/events/{sid}.events.jsonl"
    world.local.transcripts[events] = _claude_running_turn(sid)
    world.tick(advance_ms=1_000)
    world.acp.put_row(message("m-i1", "stop and answer"))
    world.acp.put_row(message("m-i2", "and this"))
    _place_interrupt(world, "j-1", ["m-i1", "m-i2"])
    world.tick(advance_ms=1_000)
    injected_ms = world.local.now_ms
    world.local.transcripts[events] += _stream_line(
        {"type": "command_lifecycle", "command_uuid": "m-i1", "state": "queued", "session_id": sid}
    ) + _stream_line(
        {"type": "command_lifecycle", "command_uuid": "m-i2", "state": "queued", "session_id": sid}
    )
    # 期限の手前では出さない
    world.tick(advance_ms=19_000)
    assert world.sessions.escalations == []
    # 期限: 合図は session に 1 つ・未読の id 全部に止めた印
    world.tick(advance_ms=1_500)
    assert world.sessions.escalations == [sid]
    escalated_ms = world.local.now_ms
    assert escalated_ms - injected_ms >= 20_000
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interruptsEscalated"] == {"m-i1": escalated_ms, "m-i2": escalated_ms}
    assert "interruptsRead" not in job.status
    assert job.status["phase"] == PHASE_RUNNING
    # 次の拍に二度出さない
    world.tick(advance_ms=1_000)
    assert world.sessions.escalations == [sid]
    # 止めた段の材料 → 注入の行が次の手番として走る(host から見た手番は続く — 器の Dialogue が result を飲む)
    world.local.transcripts[events] += "".join(
        [
            _stream_line(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": "r-1",
                        "response": {"still_queued": ["m-i1", "m-i2"]},
                    },
                }
            ),
            _stream_line(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": "",
                    "errors": ["[ede_diagnostic] stop_reason=tool_use"],
                }
            ),
            _stream_line(
                {"type": "command_lifecycle", "command_uuid": "m-i1", "state": "started", "session_id": sid}
            ),
            _stream_line(
                {"type": "command_lifecycle", "command_uuid": "m-i2", "state": "started", "session_id": sid}
            ),
            _stream_line({"type": "system", "subtype": "init", "session_id": sid}),
        ]
    )
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    read = job.status["interruptsRead"]
    assert isinstance(read, dict)
    assert set(read) == {"m-i1", "m-i2"}
    first_seq = read["m-i1"]
    second_seq = read["m-i2"]
    assert isinstance(first_seq, int)
    assert isinstance(second_seq, int)
    assert first_seq < second_seq
    kinds = _entry_kinds(world, "j-1")
    assert "error" not in kinds, kinds
    # 止めた印 → 止めた段の終わり(system)→ 読んだ証拠 2 つ → 次の手番の始まり
    assert kinds[-5:] == ["system", "system", "system", "system", "system"]
    assert job.status["interruptsEscalated"] == {"m-i1": escalated_ms, "m-i2": escalated_ms}
    assert job.status["phase"] == PHASE_RUNNING


def test_interrupt_without_a_declared_deadline_is_injected_only_and_names_the_condition() -> None:
    """段 10 lane 10n(依頼者の追補 2026-09-14): charter に interruptEscalationSeconds が無い job は注入だけ —
    停止の合図は出さず、条件 InterruptEscalationUndeclared を渡した印と同じ書きで行に足す(code に既定の期限は無い)。"""
    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    world.local.transcripts[f"/events/{sid}.events.jsonl"] = _claude_running_turn(sid)
    world.tick(advance_ms=1_000)
    world.acp.put_row(message("m-i1", "stop"))
    _place_interrupt(world, "j-1", ["m-i1"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjection_refs == [(sid, "m-i1")]
    assert [j.interrupt_escalation_seconds for j in world.state.jobs] == [None]
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["interruptsDelivered"] == ["m-i1"]
    assert _condition_types(job.status) == ["InterruptEscalationUndeclared"]
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    reasons = [str(c["reason"]) for c in conditions if isinstance(c, dict)]
    assert any("interruptEscalationSeconds" in reason for reason in reasons)
    world.tick(advance_ms=120_000)
    assert world.sessions.escalations == []
    job = world.job("j-1")
    assert job.status is not None
    assert "interruptsEscalated" not in job.status
    # 条件は 1 つのまま(二度足さない)
    world.acp.put_row(message("m-i2", "again"))
    _place_interrupt(world, "j-1", ["m-i2"])
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    assert _condition_types(job.status) == ["InterruptEscalationUndeclared"]


def test_codex_interrupt_is_read_at_the_turn_started_after_the_stop() -> None:
    """段 10 lane 10n・確定 4: codex に注入の段は無い(inject = turn/interrupt → 同じ thread へ turn/start)。
    止めた後の turn/started が「積んであった注入を model が読む拍」— 名は無いので未読の id 全部に同じ seq。
    interrupted の turn/completed は kind system(誤りではない)。期限を越えても codex には合図を出さない
    (読んだ印が先に付く)。"""
    world = HeadlessWorld(agent_type="codex")
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(
        bound_job(
            "j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400, agent_type="codex",
            escalation_seconds=20,
        )
    )
    world.tick()
    sid = world.sid("j-1")
    events = f"/events/{sid}.events.jsonl"
    world.local.transcripts[events] = "".join(
        [
            _stream_line({"id": "thread/start#2", "result": {"thread": {"id": "thr-1"}}}),
            _stream_line({"method": "turn/started", "params": {"threadId": "thr-1", "turn": {"id": "t1"}}}),
        ]
    )
    world.tick(advance_ms=1_000)
    world.acp.put_row(message("m-i1", "stop and answer"))
    _place_interrupt(world, "j-1", ["m-i1"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjection_refs == [(sid, "m-i1")]
    world.local.transcripts[events] += "".join(
        [
            _stream_line(
                {
                    "method": "turn/completed",
                    "params": {"threadId": "thr-1", "turn": {"id": "t1", "status": "interrupted"}},
                }
            ),
            _stream_line({"method": "turn/started", "params": {"threadId": "thr-1", "turn": {"id": "t2"}}}),
        ]
    )
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    read = job.status["interruptsRead"]
    assert isinstance(read, dict)
    assert read == {"m-i1": _entry_seqs_of_kind(world, "j-1", "system")[-1]}
    assert "error" not in _entry_kinds(world, "j-1")
    world.tick(advance_ms=60_000)
    assert world.sessions.escalations == []


def _in_flight_job(job_id: str, session_id: str) -> InFlightJob:
    """判断の純関数の検の材料(memory の 1 job — 欄は始まりの値)。"""
    return InFlightJob(
        job_key=f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:{job_id}",
        job_namespace=AGENT_JOB_NAMESPACE,
        job_id=job_id,
        subject=CONVERSATION,
        session_id=session_id,
        agent_type="claude",
        node=NODE,
        profile="personal",
        model="claude-opus-5",
        started_ms=0,
        turn_floor_ms=0,
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


def test_interrupt_judgments_are_pure() -> None:
    """判断の純関数(段 10 lane 10n): 期限は charter の整数だけ(bool・負・欠落は None)/ 読んだ証拠は名の在る証拠が
    その id・名の無い証拠が未読の全部(同じ id は最初の証拠)/ 期限の判断 = 注入 + 期限 ≤ 今 ∧ 未読 ∧ 未合図 /
    印の写しは append-only の map / 拾い直しは渡した - 読んだ - 止めた を今から数える。"""
    from doeff_agents.sessionhost.acp.effects import InterruptRead

    assert run(judgment.escalation_seconds_of_charter({"interruptEscalationSeconds": 20})) == 20
    assert run(judgment.escalation_seconds_of_charter({"interruptEscalationSeconds": 0})) == 0
    assert run(judgment.escalation_seconds_of_charter({"interruptEscalationSeconds": True})) is None
    assert run(judgment.escalation_seconds_of_charter({"interruptEscalationSeconds": -1})) is None
    assert run(judgment.escalation_seconds_of_charter({"interruptEscalationSeconds": "20"})) is None
    assert run(judgment.escalation_seconds_of_charter({})) is None
    base = _in_flight_job("j-1", "sid-1")
    job = run(judgment.with_injected_interrupts(base, ("a", "b"), 1_000))
    assert job.interrupts_injected == (("a", 1_000), ("b", 1_000))
    assert run(judgment.with_injected_interrupts(job, ("a", "c"), 2_000)).interrupts_injected == (
        ("a", 1_000), ("b", 1_000), ("c", 2_000),
    )
    assert run(judgment.interrupt_reads_of(job, (InterruptRead("b", 7), InterruptRead("x", 8)))) == (("b", 7),)
    assert run(judgment.interrupt_reads_of(job, (InterruptRead(None, 9),))) == (("a", 9), ("b", 9))
    assert run(judgment.interrupt_reads_of(job, (InterruptRead("a", 5), InterruptRead("a", 6)))) == (("a", 5),)
    read_a = replace(job, interrupts_read=(("a", 5),))
    assert run(judgment.interrupt_reads_of(read_a, (InterruptRead(None, 9),))) == (("b", 9),)
    timed = replace(job, interrupt_escalation_seconds=20)
    assert run(judgment.interrupts_due_for_escalation(timed, 20_999)) == ()
    assert run(judgment.interrupts_due_for_escalation(timed, 21_000)) == ("a", "b")
    assert run(judgment.interrupts_due_for_escalation(replace(timed, interrupts_read=(("a", 5),)), 21_000)) == ("b",)
    assert run(judgment.interrupts_due_for_escalation(replace(timed, interrupts_escalated=(("b", 21_000),)), 30_000)) == ("a",)
    assert run(judgment.interrupts_due_for_escalation(job, 99_000)) == ()  # 宣言なし → 出さない
    status: JSONObject = {"phase": "Running", "interruptsRead": {"a": 5}}
    marked = run(judgment.interrupt_marks_status_of(status, (("a", 99), ("b", 7)), (("b", 21_000),)))
    assert marked == {"phase": "Running", "interruptsRead": {"a": 5, "b": 7}, "interruptsEscalated": {"b": 21_000}}
    assert run(judgment.interrupt_marks_status_of({"phase": "Running"}, (), ())) == {"phase": "Running"}
    recovered_row = row(
        AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, "j-1", {},
        {"phase": "Running", "interruptsDelivered": ["a", "b", "c"], "interruptsRead": {"a": 5},
         "interruptsEscalated": {"b": 21_000}},
    )
    recovered = run(judgment.recovered_interrupts_of(base, recovered_row, 50_000))
    assert recovered.interrupts_injected == (("c", 50_000),)
    assert recovered.interrupts_read == (("a", 5),)
    assert recovered.interrupts_escalated == (("b", 21_000),)


def test_interrupts_are_only_delivered_to_jobs_this_agentd_runs() -> None:
    """他の node の Running の行に載った割り込みは触らない(自分の job = memory の InFlightJob)。"""
    world = World()
    world.acp.put_row(message("m-i1", "for someone else"))
    other = bound_job("j-x", inputs=["m-0"])
    assert other.status is not None
    other_status: JSONObject = dict(other.status)
    other_status["phase"] = PHASE_RUNNING
    other_status["binding"] = {"node": "other-node", "profile": "personal"}
    other_status["sessionHandle"] = {
        "sessionId": "sid-x",
        "stream": {"owner": "agentd", "name": "sid-x"},
    }
    other_status["interrupts"] = ["m-i1"]
    world.acp.put_row(row(AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, "j-x", other.spec, other_status))
    world.tick()
    assert world.sessions.interjections == []
    job = world.job("j-x")
    assert job.status is not None
    assert job.status["interrupts"] == ["m-i1"]


def test_pending_interrupts_of_and_the_delivered_status_are_pure() -> None:
    """判断の純関数: 渡していない id = interrupts − interruptsDelivered − memory(順は載せた順・重複なし)。
    渡した後の status = interrupts から消し delivered の末尾へ(既に在れば足さない)、他の欄は写す。"""
    base = bound_job("j-p", inputs=["m-0"])
    assert base.status is not None
    status: JSONObject = dict(base.status)
    status["interrupts"] = ["a", "b", "c", "b"]
    status["interruptsDelivered"] = ["a"]
    placed = row(AGENT_JOB_NAMESPACE, AGENT_JOB_KIND, "j-p", base.spec, status)
    assert run(judgment.pending_interrupts_of(placed, ())) == ("b", "c")
    assert run(judgment.pending_interrupts_of(placed, ("c",))) == ("b",)
    assert run(judgment.pending_interrupts_of(base, ())) == ()
    delivered = run(judgment.interrupts_delivered_status_of(status, ("b", "c")))
    assert delivered["interrupts"] == []
    assert delivered["interruptsDelivered"] == ["a", "b", "c"]
    assert delivered["phase"] == PHASE_BOUND
    assert delivered["binding"] == status["binding"]
    again = run(judgment.interrupts_delivered_status_of(delivered, ("c",)))
    assert again["interruptsDelivered"] == ["a", "b", "c"]


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


def test_dead_backend_of_a_running_job_ends_it_with_session_lost_and_the_next_turn_resumes() -> None:
    """段 10 lane 10h(agora-redesign #84・実弾 2026-09-14 14:35): agentd の再起動で手番の子 process が
    道連れになり、行は running のまま(turn_ended_at 無し)。復帰(recover-job)は status の語ではなく host の
    観測 backend_alive で判断し、turn-record を ended・job を SessionLost(理由に session・pid・時刻)で Ended に
    する。次の Pending(同じ会話の Bound)は defer せず、候補を片付けて同じ session を --resume する。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    sid = world.sid("j-1")
    assert len(world.state.jobs) == 1
    # 再起動: memory を捨てる・子 process は死んだ(観測)・行は running のまま
    world.state = initial_state()
    world.sessions.kill_backend(sid)
    assert world.sessions.views[sid].status == "running"
    assert world.sessions.views[sid].turn_ended_at_ms is None
    world.tick(advance_ms=1_000)
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert "result" not in job.status
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict)
    assert last["type"] == "SessionLost"
    reason = last["reason"]
    assert isinstance(reason, str)
    assert sid in reason
    assert "pid none" in reason  # fake の backend_ref には pid が無い(発明しない)
    assert "1970-01-01T00:00:02Z" in reason  # 観測の時刻(fake の時計 2_000 ms)
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    assert world.state.jobs == ()
    assert any("lost its session" in line for line in world.local.logs)
    # session は agentd が片付けない(host の monitor が終端に倒す — cause は host の観測の方が詳しい)
    assert world.sessions.cleanups == []
    assert world.sessions.launches[-1]["session_id"] == sid
    # 次の Pending: 行は running のまま(host の monitor がまだ倒していない拍)でも defer しない —
    # backend が死んでいるので候補を片付けて同じ家で --resume(cache は保つ)
    world.acp.put_row(message("m-2", "second"))
    world.acp.put_row(bound_job("j-2", inputs=["m-2"], created_at_ms=world.local.now_ms))
    world.tick(advance_ms=1_000)
    assert not any("deferred" in line for line in world.local.logs)
    assert world.sessions.cleanups == [sid]
    assert len(world.sessions.resumes) == 1
    assert world.sessions.resumes[0]["session_id"] == sid
    next_job = world.job("j-2")
    assert next_job.status is not None
    assert next_job.status["phase"] == PHASE_RUNNING
    assert world.sid("j-2") != sid


def test_live_backend_of_a_recovered_job_is_observed_not_lost() -> None:
    """段 10 lane 10h の対照: 再起動後も backend が生きていれば(host の観測)recover-job は observe のまま —
    SessionLost を書かない。観測の無い眺め(backend_alive = None)も死とは読まない(観測断 ≠ 死亡)。"""
    world = World()
    world.acp.put_row(bound_job("j-live", inputs=[]))
    world.tick()
    sid = world.sid("j-live")
    world.state = initial_state()
    world.tick(advance_ms=1_000)
    assert [job.job_id for job in world.state.jobs] == ["j-live"]
    job = world.job("j-live")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    conditions = job.status.get("conditions")
    assert isinstance(conditions, list)
    assert not any(isinstance(c, dict) and c.get("type") == "SessionLost" for c in conditions)
    # 観測の無い眺め: 生きていると読む
    world.sessions.views[sid] = replace(world.sessions.views[sid], backend_alive=None)
    world.tick(advance_ms=1_000)
    assert [job.job_id for job in world.state.jobs] == ["j-live"]
    still = world.job("j-live")
    assert still.status is not None
    assert still.status["phase"] == PHASE_RUNNING


def test_backend_liveness_is_read_from_the_observation_not_the_status_word() -> None:
    """段 10 lane 10h: job-step-of と next-arm-for-job は backend の生死を眺めの backend_alive(host の観測)で
    読む。running の語のまま backend が死んだ行 → session-lost / 手番の途中で死んだ候補 → 片付けて resume(同じ家)
    か rehydrate(違う家)/ idle の温かい候補は process が降りていても send(host の send が --resume で起こし直す)。"""
    from doeff_agents.sessionhost.acp.effects import ArmChoice

    plan = run(judgment.launch_plan_of(bound_job("a", inputs=[])))
    home = run(judgment.session_affinity_key_of(plan))
    other = {**home, "account": "other"}
    stamp: JSONObject = {
        "agentd": {
            "conversationId": CONVERSATION,
            "agentJobId": "a-0",
            "account": "acct",
            "home": home,
            "arm": "launch",
        }
    }
    busy_alive = replace(
        _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=None),
        launch_attribution=stamp, backend_alive=True,
    )
    busy_dead = replace(busy_alive, backend_alive=False)
    busy_unobserved = replace(busy_alive, backend_alive=None)
    idle_dead = replace(busy_dead, turn_ended_at_ms=10)
    assert run(judgment.backend_alive(busy_alive)) is True
    assert run(judgment.backend_alive(busy_dead)) is False
    assert run(judgment.backend_alive(busy_unobserved)) is True
    assert run(judgment.backend_alive(None)) is False
    assert run(judgment.job_step_of(busy_alive, 0, True)) == "observe"
    assert run(judgment.job_step_of(busy_unobserved, 0, True)) == "observe"
    assert run(judgment.job_step_of(busy_dead, 0, True)) == "session-lost"
    # 終端の語・手番の終わりは backend の観測より先に読む(死んだ後に host が倒した行は record-end)
    assert run(judgment.job_step_of(replace(busy_dead, status="exited"), 0, True)) == "record-end"
    assert run(judgment.job_step_of(idle_dead, 0, True)) == "turn-end"
    assert run(judgment.job_step_of(idle_dead, 20, True)) == "session-lost"
    assert run(judgment.next_arm_for_job("p", busy_alive, home, None, False)) == ArmChoice("defer", "p", None)
    assert run(judgment.next_arm_for_job("p", busy_unobserved, home, None, False)) == ArmChoice("defer", "p", None)
    assert run(judgment.next_arm_for_job("p", busy_dead, home, None, False)) == ArmChoice("resume", "p", "p")
    assert run(judgment.next_arm_for_job("p", busy_dead, other, None, False)) == ArmChoice("rehydrate", None, "p")
    assert run(judgment.next_arm_for_job("p", idle_dead, home, None, False)) == ArmChoice("send", "p", None)
    condition = run(judgment.session_lost_condition_of(replace(busy_dead, backend_kind="headless", backend_ref={"pid": 22663}), 1_789_365_000_000))
    assert condition["type"] == "SessionLost"
    assert "pid 22663" in condition["reason"]
    assert "headless" in condition["reason"]
    assert "2026-09-14T05:50:00Z" in condition["reason"]


def test_stop_closes_running_jobs_with_agentd_restart_and_leaves_the_session_to_the_host() -> None:
    """段 10 lane 10h 便 2(agora-redesign #84): agentd の停止(TERM)の前に、走っている job は記録の腕
    (残りの材料・turn-record ended)と条件 AgentdRestart(node・理由・session・時刻)で Ended になり、status frame
    ended・札の返却まで済む。session は片付けない(host が降ろす)。job の無い停止は何も書かない。"""
    world = World()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    sid = world.sid("j-1")
    path = f"{HOMES}/claude/acct/projects/-work/{sid}.jsonl"
    world.local.transcripts[path] = transcript_line("assistant", [{"type": "text", "text": "partial"}])
    assert len(world.state.jobs) == 1
    world.state = run_close_for_stop(
        world.settings,
        world.state,
        [world.acp.dispatch, world.custody.dispatch, world.sessions.dispatch, world.local.dispatch],
        "SIGTERM",
    )
    assert world.state.jobs == ()
    job = world.job("j-1")
    assert job.status is not None
    assert job.status["phase"] == PHASE_ENDED
    assert "result" not in job.status
    conditions = job.status["conditions"]
    assert isinstance(conditions, list)
    last = conditions[-1]
    assert isinstance(last, dict)
    assert last["type"] == "AgentdRestart"
    reason = last["reason"]
    assert isinstance(reason, str)
    assert f"agentd on node {NODE} stopped (SIGTERM)" in reason
    assert sid in reason
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    entries = record.status["entries"]
    assert isinstance(entries, list)
    assert [e["kind"] for e in entries if isinstance(e, dict)] == ["text"]  # 停止の前に読んだ材料は残る
    assert world.pushed_kinds()[-1] == "status"
    assert world.custody.revoked == ["lease-1"]
    assert world.sessions.cleanups == []
    assert world.sessions.views[sid].status == "running"
    assert any("closed for the stop of agentd" in line for line in world.local.logs)
    assert world.local.metrics[-1]["metric"] == "agent-job-turn"
    assert world.local.metrics[-1]["step"] == "agentd-stop"
    # 再起動後の最初の拍: Ended の行は拾わない(running-on-me でない)— 二度閉じない
    world.state = initial_state()
    world.tick(advance_ms=1_000)
    assert world.state.jobs == ()
    # job の無い停止は書かない
    writes_before = len(world.acp.writes)
    world.state = run_close_for_stop(
        world.settings,
        world.state,
        [world.acp.dispatch, world.custody.dispatch, world.sessions.dispatch, world.local.dispatch],
        "SIGTERM",
    )
    assert len(world.acp.writes) == writes_before


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
    """起こし方の判定は judgment.next-arm-for-job の 1 点(R10 / R20)。cache(温かい send / --resume)を保つのは
    同じ機体 ∧ 同じ家の時だけ(operator 決定 #54): 家が違えば温かい session を片付けて履歴からの再開、器に無い候補・
    家の分からない(帰属の無い)session も履歴からの再開。resume が断られたら履歴からの再開(fallback-arm-of)。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp.effects import ArmChoice

    plan = run(judgment.launch_plan_of(bound_job("a", inputs=[])))
    home = run(judgment.session_affinity_key_of(plan))
    other_plan = run(judgment.launch_plan_of(bound_job("b", inputs=[], account="other")))
    other = run(judgment.session_affinity_key_of(other_plan))
    assert home == {"account": "acct", "binding": None, "model": "claude-opus-5"}
    assert other != home
    # 段 9o lane 9o-3: model だけが違う手番も違う家(温かい session に送らない・片付いた session を --resume しない)。
    other_model = {**home, "model": "claude-sonnet-5"}
    stamp: JSONObject = {
        "agentd": {
            "conversationId": CONVERSATION,
            "agentJobId": "a-0",
            "account": "acct",
            "home": {"account": "acct", "binding": None, "model": "claude-opus-5"},
            "arm": "launch",
        }
    }
    warm = replace(
        _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=10), launch_attribution=stamp
    )
    busy = replace(
        _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=None),
        launch_attribution=stamp,
    )
    dead = replace(
        _view("p", "exited", lifecycle="multi_turn", turn_ended_at_ms=10), launch_attribution=stamp
    )
    bare_warm = _view("p", "running", lifecycle="multi_turn", turn_ended_at_ms=10)
    bare_dead = _view("p", "exited", lifecycle="multi_turn", turn_ended_at_ms=10)

    def arm(
        candidate: str | None,
        view: SessionView | None,
        at: JSONObject,
        effort: str | None = None,
        compact: bool = False,
    ) -> ArmChoice:
        choice = run(judgment.next_arm_for_job(candidate, view, at, effort, compact))
        assert isinstance(choice, ArmChoice)
        return choice

    assert arm(None, None, home) == ArmChoice("launch", None, None)
    # 段 10 lane 10e: effort は鍵に入れない — 同じ家で effort だけ違う温かい session は片付けて同じ session を
    # 新しい旗で --resume(cache は保つ)。帰属に effort の欄が無い(前の agentd が起こした)session は「既定で起きた」と読む。
    stamped_agentd = stamp["agentd"]
    assert isinstance(stamped_agentd, dict)
    stamped_high = replace(warm, launch_attribution={"agentd": {**stamped_agentd, "effort": "high"}})
    assert arm("p", stamped_high, home, "high") == ArmChoice("send", "p", None)
    assert arm("p", stamped_high, home, "xhigh") == ArmChoice("resume", "p", "p")
    assert arm("p", stamped_high, home, None) == ArmChoice("resume", "p", "p")
    assert arm("p", warm, home, "high") == ArmChoice("resume", "p", "p")
    assert arm("p", busy, home, "xhigh") == ArmChoice("defer", "p", None)
    assert arm("p", dead, home, "xhigh") == ArmChoice("resume", "p", None)
    assert arm("p", stamped_high, other, "xhigh") == ArmChoice("rehydrate", None, "p")
    assert arm("p", warm, home) == ArmChoice("send", "p", None)
    assert arm("p", warm, other) == ArmChoice("rehydrate", None, "p")
    assert arm("p", bare_warm, home) == ArmChoice("rehydrate", None, "p")
    assert arm("p", busy, home) == ArmChoice("defer", "p", None)
    assert arm("p", busy, other) == ArmChoice("defer", "p", None)
    assert arm("p", dead, home) == ArmChoice("resume", "p", None)
    assert arm("p", dead, other) == ArmChoice("rehydrate", None, None)
    assert arm("p", warm, other_model) == ArmChoice("rehydrate", None, "p")
    assert arm("p", busy, other_model) == ArmChoice("defer", "p", None)
    assert arm("p", dead, other_model) == ArmChoice("rehydrate", None, None)
    assert arm("p", bare_dead, home) == ArmChoice("rehydrate", None, None)
    assert arm("p", None, home) == ArmChoice("rehydrate", None, None)
    # 段 10f 便 2(agora-redesign #82): 圧縮の手番 — 同じ家の温かい session でも送らず片付けて履歴から再開(compacts)。
    # 終端の候補は片付ける物が無い。手番の途中は圧縮より defer が先(走っている手番に本文を積まない)。候補が無ければ launch。
    assert arm("p", warm, home, None, True) == ArmChoice("rehydrate", None, "p", True)
    assert arm("p", stamped_high, home, "high", True) == ArmChoice("rehydrate", None, "p", True)
    assert arm("p", dead, home, None, True) == ArmChoice("rehydrate", None, None, True)
    assert arm("p", busy, home, None, True) == ArmChoice("defer", "p", None)
    assert arm(None, None, home, None, True) == ArmChoice("launch", None, None)
    assert run(judgment.fallback_arm_of(ArmChoice("resume", "p", None))) == ArmChoice(
        "rehydrate", None, None
    )
    assert run(judgment.fallback_arm_of(ArmChoice("launch", None, None))) is None
    assert run(judgment.fallback_arm_of(ArmChoice("rehydrate", None, None))) is None
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
        from dataclasses import replace

        self.settings = AgentdSettings(
            node_name=NODE,
            homes_root=HOMES,
            backend_kind="headless",
            stream_capability="events",
            node_capacity=1,
        )
        # 段 10 lane 10d(R28): 種の node の行も events の器の宣言と同じ spec にする(揃えの書きを混ぜない)。
        seeded = self.acp.rows[f"{AGORA_KINDS_NAMESPACE}:{NODE_KIND}:{NODE}"]
        self.acp.put_row(replace(seeded, spec={**seeded.spec, "streamCapability": "events"}))
        self.sessions = FakeSessions(
            agent_type=agent_type, backend_kind="headless", events_root="/events"
        )


def _assert_claude_headless_entries(entries: list[JSON]) -> None:
    """段 8 lane 4u → 段 9f lane 9f-4: 出来事の列の欄は見出し(system・text・道具の呼び出しと結果の toolUseId — 本文も
    model も summary も無く、bytes / sha256 が本文の同一性を運ぶ)。"""
    system_entry = entries[0]
    assert isinstance(system_entry, dict)
    assert system_entry["kind"] == "system"
    assert set(system_entry) == {"seq", "at", "kind", "bytes", "sha256"}
    first_entry = entries[1]
    assert isinstance(first_entry, dict)
    assert first_entry["kind"] == "text"
    assert set(first_entry) == {"seq", "at", "kind", "bytes", "sha256"}
    assert first_entry["bytes"] == len(b'{"text":"hello world"}')
    use_entry = entries[2]
    assert isinstance(use_entry, dict)
    assert use_entry == {
        "seq": use_entry["seq"],
        "at": use_entry["at"],
        "kind": "tool_use",
        "toolName": "Bash",
        "toolUseId": "t1",
        "bytes": use_entry["bytes"],
        "sha256": use_entry["sha256"],
    }
    result_entry = entries[3]
    assert isinstance(result_entry, dict)
    assert result_entry["toolUseId"] == "t1"
    assert set(result_entry) == {"seq", "at", "kind", "toolUseId", "bytes", "sha256"}
    assert "isError" not in result_entry


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
    # 段 8 lane 4u: 出来事は手番の**途中**で turn-record に耐久化されている(終わりを待たない)。
    running = world.turn_record("j-1")
    assert running is not None
    assert running.status is not None
    assert running.status["state"] == "running"
    mid_turn = running.status["entries"]
    assert isinstance(mid_turn, list)
    assert [entry["kind"] for entry in mid_turn if isinstance(entry, dict)] == [
        "system",
        "text",
        "tool_use",
        "tool_result",
    ]
    assert "usage" not in running.status
    # 手番の終わり(host が result の行で刻む)→ turn-record の entries と usage
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    record = world.turn_record("j-1")
    assert record is not None
    assert record.status is not None
    assert record.status["state"] == "ended"
    entries = record.status["entries"]
    assert isinstance(entries, list)
    # 終わりの書きは途中の出来事を落とさず(追記)、同じ出来事を二度積まない。
    assert [entry["kind"] for entry in entries if isinstance(entry, dict)] == [
        "system",
        "text",
        "tool_use",
        "tool_result",
    ]
    assert entries == mid_turn
    _assert_claude_headless_entries(entries)
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
    # 段 10 lane 10n: codex の turn/started は kind system の entry("turn started" — 止めた後の手番なら
    # 積んであった割り込みを model が読む拍の証拠)。
    assert [entry["kind"] for entry in entries if isinstance(entry, dict)] == [
        "system",
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

    # 本文の行き先(段 9f lane 9f-6): 宛先の無い env は settings_from_env が参加を断るので、束に宛先を持たせる。
    bare = {"DOEFF_AGENTD_NODE_NAME": NODE, "RECORD_SERVICE_URL": "http://record:8874", "DOEFF_AGENTD_CAPACITY": "1", "DOEFF_AGENTD_PLACE": "personal"}
    env = {**bare, "DOEFF_SESSIONHOST_BACKEND": "headless"}
    assert settings_from_env(env, ()).stream_capability == "events"
    assert settings_from_env(env, ()).backend_kind == "headless"
    assert settings_from_env(bare, ()).backend_kind == "tmux"
    assert settings_from_env(bare, ("--backend", "herdr")).backend_kind == "herdr"
    assert settings_from_env(bare, ("--backend", "headless")).stream_capability == "events"
    assert settings_from_env(bare, ()).stream_capability == "frames"
    assert settings_from_env(bare, ("--backend", "herdr")).stream_capability == "frames"


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


def test_after_the_idle_ttl_the_next_job_resumes_with_a_fresh_id_and_within_the_ttl_it_is_sent() -> (
    None
):
    """実弾 2026-09-12: 温かい session が idle TTL で片付いた後、次の job が charter の固定の id で
    `session is already registered` に落ちて LaunchFailed で Ended した。鋳造した id は片付いた
    行(host に登記のまま残る)と衝突しない。TTL 内の同じ会話は send、TTL の後は同じ家の片付いた
    session から --resume(段 8q・operator 決定 #54 — cache を保つのは同じ機体 ∧ 同じ家)。"""
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
    # 次の job: 片付いた session から新しい id で --resume に成功(charter の id は同じ固定の綴りのまま)
    world.acp.put_row(message("m-3", "third"))
    world.acp.put_row(bound_job("j-3", inputs=["m-3"]))
    world.tick(advance_ms=1_000)
    assert len(world.sessions.launches) == 1
    assert [resumed["session_id"] for resumed in world.sessions.resumes] == [first]
    third = world.sid("j-3")
    assert third != first
    job = world.job("j-3")
    assert job.status is not None
    assert job.status["phase"] == PHASE_RUNNING
    assert world.sessions.sends[-1] == (third, "third", True)
    conditions = job.status["conditions"]
    assert conditions == []


# ---------------------------------------------------------------- 段 6 lane 6f: 1 命令の参加(join)と所有の等級


def _join_spec(argv: list[str], declaration: dict[str, object] | None = None) -> object:
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinArgv, JoinDeclaration

    return run(
        join.join_spec_of(
            JoinArgv(items=tuple(argv)), JoinDeclaration(tables=declaration or {}), "/state"
        )
    )


def test_join_spec_is_flags_over_declaration_over_defaults() -> None:
    """`doeff-sessionhost join` の宣言は flag > toml > 既定の 1 点(join.join-spec-of)で組む。
    既定: node の名は無し(機体の名)・置き場は state の根の下・backend は headless・
    hooks は inherit・custody は無し(既定の URL は handler)・所有は名乗らない。"""
    from doeff_agents.sessionhost.acp.effects import JoinSpec, Ownership

    bare = _join_spec(["--server", "http://acp:8868", "--token-file", "/t/agentd.token", "--capacity", "1", "--place", "personal"])
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
        capacity=1,
        place="personal",
    )
    declaration: dict[str, object] = {
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
            "capacity": "2",
            "place": "personal",
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
        capacity=2,
        place="personal",
    )
    flagged = _join_spec(
        [
            "--server",
            "http://flag:2",
            "--node-name",
            "mac-9",
            "--ownership",
            "personal",
            "--ownership-proof",
            "declared",
            "--backend",
            "headless",
            "--capacity",
            "3",
            "--place",
            "personal",
        ],
        declaration,
    )
    assert isinstance(flagged, JoinSpec)
    assert flagged.server == "http://flag:2"
    assert flagged.token_file == "/toml/token"
    assert flagged.node_name == "mac-9"
    assert flagged.backend == "headless"
    assert flagged.ownership == Ownership(grade="personal", proof="declared")
    assert flagged.capacity == 3  # flag > toml(段 10 lane 10d・R28)


def test_join_spec_carries_the_service_account_token_file_to_the_custody_env() -> None:
    """段 10 lane 10y(agora-redesign #110・依頼者の裁定 問い 3 案 A): k8s の pod の agentd は預かり所へ ServiceAccount の
    token で名乗る。宣言 file の [custody].service_account_token_file(flag --service-account-token-file が優先)が
    JoinSpec を通って env AGORA_CUSTODY_SA_TOKEN_PATH に写る。名乗らない宣言(欄なし・空文字)は env に現れない。"""
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinSpec

    base = ["--server", "http://acp:8868", "--token-file", "/t/agentd.token", "--capacity", "2", "--place", "personal"]
    sa_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    declaration: dict[str, object] = {
        "schema": "doeff.agentd-join.v1",
        "custody": {"url": "http://custodian.agora-custody.svc.cluster.local:8320", "service_account_token_file": sa_path},
    }
    from_toml = _join_spec(base, declaration)
    assert isinstance(from_toml, JoinSpec)
    assert from_toml.service_account_token_file == sa_path
    assert from_toml.borrower_key_file is None
    env = dict(run(join.join_plan_of(from_toml)).env)
    assert env["AGORA_CUSTODY_SA_TOKEN_PATH"] == sa_path
    assert "AGORA_BORROWER_KEY_PATH" not in env
    flagged = _join_spec([*base, "--service-account-token-file", "/flag/token"], declaration)
    assert isinstance(flagged, JoinSpec)
    assert flagged.service_account_token_file == "/flag/token"
    absents: list[dict[str, object]] = [
        {"schema": "doeff.agentd-join.v1"},
        {"schema": "doeff.agentd-join.v1", "custody": {"service_account_token_file": ""}},
    ]
    for absent in absents:
        spec = _join_spec(base, absent)
        assert isinstance(spec, JoinSpec)
        assert spec.service_account_token_file is None
        assert "AGORA_CUSTODY_SA_TOKEN_PATH" not in dict(run(join.join_plan_of(spec)).env)


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
            [
                "--server",
                "http://a",
                "--token-file",
                "/t",
                "--ownership",
                "company",
                "--ownership-proof",
                "trust-me",
            ]
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
    # 段 10 lane 10d(R28): node の capacity は宣言の 1 点 — 無い・読めない agentd は参加しない。
    with pytest.raises(ValueError, match="capacity"):
        _join_spec(["--server", "http://a", "--token-file", "/t"])
    with pytest.raises(ValueError, match="capacity"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--capacity", "two"])
    with pytest.raises(ValueError, match="capacity"):
        _join_spec(["--server", "http://a", "--token-file", "/t", "--capacity", "-1"])
    assert isinstance(_join_spec(["--server", "http://a", "--token-file", "/t", "--capacity", "0", "--place", "personal"]), JoinSpec)
    # 空文字は「名乗らない」(宣言 file で欄を空にして外せる — runtime の env の読みと同じ)。
    blank = _join_spec(
        ["--server", "http://a", "--token-file", "/t", "--ownership", "", "--ownership-proof", "", "--capacity", "1", "--place", "personal"]
    )
    assert isinstance(blank, JoinSpec)
    assert blank.ownership is None


def test_join_config_path_is_read_from_the_flag() -> None:
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinArgv

    assert run(
        join.config_path_of(JoinArgv(items=("--config", "/etc/doeff/agentd.toml", "--server", "x")))
    ) == ("/etc/doeff/agentd.toml")
    assert run(join.config_path_of(JoinArgv(items=("--server", "x")))) is None
    with pytest.raises(ValueError, match="--config requires a value"):
        run(join.config_path_of(JoinArgv(items=("--server", "x", "--config"))))


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
        capacity=2,
        place="personal",
    )
    plan = run(join.join_plan_of(spec))
    assert plan == JoinPlan(
        host_argv=(
            "--db",
            "/var/lib/doeff/agentd/agentd.sqlite",
            "--socket",
            "/var/lib/doeff/agentd/agentd.sock",
            "--max-running",
            "none",
            "--backend",
            "headless",
            "serve",
        ),
        env=(
            ("DOEFF_AGENTD_ACP", "on"),
            ("ACP_DAEMON_URL", "http://acp:8868"),
            ("ACP_AGENTD_TOKEN_FILE", "/t/agentd.token"),
            ("DOEFF_AGENTD_NODE_NAME", "gcp-0"),
            ("DOEFF_AGENTD_CAPACITY", "2"),
            ("DOEFF_AGENTD_PLACE", "personal"),
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
                capacity=0,
                place="personal",
            )
        )
    )
    assert isinstance(bare, JoinPlan)
    names = [name for name, _value in bare.env]
    assert "DOEFF_AGENTD_NODE_NAME" not in names
    assert "AGORA_CUSTODY_URL" not in names
    assert "DOEFF_AGENTD_OWNERSHIP" not in names


def test_join_reads_the_fingerprint_of_the_declaration_file_bytes_into_the_env_and_settings(tmp_path: Path) -> None:
    """段 10 lane 10y(agora-redesign #110・依頼者の裁定 2026-09-15): 指紋 = agentd が読んだ宣言 file(描いた写し)の bytes の
    sha256。composition root(runtime.read_join_declaration)が境界で読み、JoinSpec → env DOEFF_AGENTD_DECLARATION_SHA256 →
    AgentdSettings.declaration_sha256。宣言 file の無い参加(flag だけ)は指紋を持たない。形の違う指紋の env は参加を断る。"""
    import hashlib

    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JoinSpec
    from doeff_agents.sessionhost.acp.runtime import join_plan, read_join_declaration, settings_from_env

    text = (
        'schema = "doeff.agentd-join.v1"\n\n[agentd]\nserver = "http://acp:8868"\ntoken_file = "/t/agentd.token"\n'
        'capacity = "2"\nplace = "personal"\n\n[record]\nurl = "http://record:8874"\n'
    )
    path = tmp_path / "agentd.toml"
    path.write_bytes(text.encode("utf-8"))
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    declaration = read_join_declaration(str(path))
    assert declaration.sha256 == expected
    plan = join_plan(["--config", str(path)], {"HOME": str(tmp_path)})
    env = dict(plan.env)
    assert env["DOEFF_AGENTD_DECLARATION_SHA256"] == expected
    assert settings_from_env(env, plan.host_argv).declaration_sha256 == expected
    # 宣言 file なし(flag だけ)= 指紋なし
    bare = _join_spec(["--server", "http://acp:8868", "--token-file", "/t", "--capacity", "1", "--place", "personal"])
    assert isinstance(bare, JoinSpec)
    assert bare.declaration_sha256 is None
    assert "DOEFF_AGENTD_DECLARATION_SHA256" not in dict(run(join.join_plan_of(bare)).env)
    recorded = {"DOEFF_AGENTD_NODE_NAME": NODE, "RECORD_SERVICE_URL": "http://record:8874", "DOEFF_AGENTD_CAPACITY": "1", "DOEFF_AGENTD_PLACE": "personal"}
    assert settings_from_env(recorded, ()).declaration_sha256 is None
    with pytest.raises(ValueError, match="DOEFF_AGENTD_DECLARATION_SHA256"):
        settings_from_env({**recorded, "DOEFF_AGENTD_DECLARATION_SHA256": "AB" * 32}, ())
    with pytest.raises(ValueError, match="DOEFF_AGENTD_DECLARATION_SHA256"):
        settings_from_env({**recorded, "DOEFF_AGENTD_DECLARATION_SHA256": "ab" * 31}, ())


def test_acp_writes_put_the_fingerprint_header_only_on_the_writes_that_carry_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """handlers.AcpHttp: 指紋は書きごとの header x-declaration-sha256(誕生・spec の書きが運ぶ時だけ)。運ばない書きと status の
    書きの header は札だけ。"""
    from doeff_agents.sessionhost.acp.effects import AcpCreate, AcpPutSpec, AcpPutStatus

    seen: list[dict[str, str]] = []

    def fake_http(
        method: str, url: str, headers: Mapping[str, str], body: JSON, timeout: float
    ) -> handlers.HttpReply:
        seen.append(dict(headers))
        return handlers.HttpReply(200, {"eventId": "ev-1"})

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    acp = handlers.AcpHttp("http://acp.test", "tok")
    fingerprint = "cd" * 32
    acp._write(AcpCreate(namespace="default", kind="node", resource_id="n-1", spec={"capacity": 2}, declaration_sha256=fingerprint))
    row = AcpRow(
        namespace="default", key="default:node:n-1", kind="node", resource_id="n-1", version="v1", generation=1,
        created_at_ms=0, labels={}, payload={}, spec={"capacity": 2}, status=None,
    )
    acp._write(AcpPutSpec(row=row, spec={"capacity": 3}, declaration_sha256=fingerprint))
    acp._write(AcpPutSpec(row=row, spec={"capacity": 3}))
    acp._write(AcpPutStatus(row=row, status={"state": "joined"}))
    assert [headers.get("x-declaration-sha256") for headers in seen] == [fingerprint, fingerprint, None, None]
    assert all(headers.get("Authorization") == "Bearer tok" for headers in seen)


def test_settings_from_env_reads_the_ownership_and_the_valve_and_runtime_agree_on_the_bundle() -> (
    None
):
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
                capacity=2,
                place="personal",
                record_url="http://record:8874",
            )
        )
    )
    assert isinstance(plan, JoinPlan)
    env = dict(plan.env)
    settings = settings_from_env(env, plan.host_argv)
    assert settings.node_name == "gcp-0"
    assert settings.node_capacity == 2
    assert settings.backend_kind == "headless"
    assert settings.stream_capability == "events"
    assert settings.ownership == Ownership(grade="company", proof="gce-project:cyberagent-050")
    assert settings.record_enabled is True
    # 段 10c(R23): 預かり所の宣言の有無は CUSTODY_URL_ENV の在否 1 点(この束は custody_url を名乗らない)
    assert settings.custody_declared is False
    assert acp_valve(list(plan.host_argv), env).enabled is True
    recorded = {"DOEFF_AGENTD_NODE_NAME": NODE, "RECORD_SERVICE_URL": "http://record:8874", "DOEFF_AGENTD_CAPACITY": "1", "DOEFF_AGENTD_PLACE": "personal"}
    assert settings_from_env(recorded, ()).ownership is None
    assert settings_from_env({**recorded, "AGORA_CUSTODY_URL": "http://custody:8320"}, ()).custody_declared is True
    assert settings_from_env({**recorded, "AGORA_CUSTODY_URL": "  "}, ()).custody_declared is False
    with pytest.raises(ValueError, match="DOEFF_AGENTD_OWNERSHIP"):
        settings_from_env({**recorded, "DOEFF_AGENTD_OWNERSHIP": "corp"}, ())
    # 段 10 lane 10d(R28): capacity の無い env は参加を断る(join.capacity-of の 1 点)。
    without_capacity = {name: value for name, value in recorded.items() if name != "DOEFF_AGENTD_CAPACITY"}
    with pytest.raises(ValueError, match="capacity"):
        settings_from_env(without_capacity, ())
    # 段 10 lane 10d 便 2: 置き場も宣言ちょうど — 無い env と語彙の外は参加を断る(join.place-of の 1 点)
    assert settings_from_env(recorded, ()).place == "personal"
    without_place = {name: value for name, value in recorded.items() if name != "DOEFF_AGENTD_PLACE"}
    with pytest.raises(ValueError, match="place"):
        settings_from_env(without_place, ())
    with pytest.raises(ValueError, match="company | personal"):
        settings_from_env({**recorded, "DOEFF_AGENTD_PLACE": "k3s:cluster"}, ())


def test_acp_url_has_no_localhost_default_and_the_join_bundle_carries_it(tmp_path: Path) -> None:
    """段 10 lane 10h 便 2(agora-redesign #84): 実況の push を含む ACP の全部の宛先は宣言(join の --server /
    [agentd].server → ACP_DAEMON_URL)ちょうどで、127.0.0.1:8868 の既定値は無い — 宣言の無い agentd は参加を断る。"""
    from doeff_agents.sessionhost.acp import handlers, join
    from doeff_agents.sessionhost.acp.effects import (
        ACP_TOKEN_FILE_ENV,
        ACP_URL_ENV,
        JoinPlan,
        JoinSpec,
    )
    from doeff_agents.sessionhost.acp.runtime import AgentdPreflightError, real_dispatchers

    assert not hasattr(handlers, "ACP_URL_DEFAULT")
    token = tmp_path / "agentd.token"
    token.write_text("sk-roster-token\n", encoding="utf-8")
    env = {ACP_TOKEN_FILE_ENV: str(token), "RECORD_SERVICE_URL": "http://record:8874", "DOEFF_AGENTD_CAPACITY": "1", "DOEFF_AGENTD_PLACE": "personal"}
    with pytest.raises(AgentdPreflightError, match=ACP_URL_ENV):
        real_dispatchers(env, str(tmp_path / "agentd.sock"))
    with pytest.raises(AgentdPreflightError, match=ACP_URL_ENV):
        real_dispatchers({**env, ACP_URL_ENV: "  "}, str(tmp_path / "agentd.sock"))
    plan = run(
        join.join_plan_of(
            JoinSpec(
                server="http://acp-control.example.ts.net:8868",
                token_file=str(token),
                node_name="mac-1",
                state_dir=str(tmp_path),
                backend="headless",
                session_hooks="inherit",
                custody_url=None,
                borrower_key_file=None,
                ownership=None,
                capacity=1,
                place="personal",
                record_url="http://record:8874",
            )
        )
    )
    assert isinstance(plan, JoinPlan)
    assert dict(plan.env)[ACP_URL_ENV] == "http://acp-control.example.ts.net:8868"


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
    assert node.status["observations"] == {
        "streamCapability": "frames",
        "sessions": [],
        "transcripts": [],
    }

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
        "transcripts": [],
        "ownership": {"grade": "company", "proof": "gce-project:cyberagent-050"},
    }


# ------------------------------------------------------------------ 契約 agora-kinds.json の写しの自己整合(段 9f lane 9f-8)
#
# 正本は proboscis/agent-control-plane の docs/contracts/agora-kinds.json。写しと正本の byte 一致は正本側の
# scripts/check_cross_repo_contracts.hy が撃つ(消費側は自分の checkout しか持たない)。ここが撃つのは写しの中で閉じる 3 点:
# docs/contracts/reads.json の読む欄が写しに実在する(dot 区切り・* は辿らない)/ 写しが版と互換の規則を名乗り宣言 file が
# 正本の在処を名乗る / effects.py が写しとして持つ値(principal・kind 名・entries の kind の語・行の上限・書き手の軸)が写しと一致する。

#: packages/doeff-agents/tests/<this> → parents[3] = repo の root。
_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "contracts"
_AGORA_KINDS_COPY = _CONTRACTS_DIR / "agora-kinds.json"
_CONTRACT_READS = _CONTRACTS_DIR / "reads.json"
_CONTRACT_DECLARATION = _CONTRACTS_DIR / "README.md"
_AGORA_KINDS_CANON_MARKER = (
    "acp-contract-canon: proboscis/agent-control-plane:docs/contracts/agora-kinds.json"
)
#: agentd が書き手として名乗る status の軸(kind → 軸)。
_AGENTD_STATUS_AXES: tuple[tuple[str, str], ...] = (
    (TURN_RECORD_KIND, "state"),
    (TURN_RECORD_KIND, "usage"),
    (TURN_RECORD_KIND, "entries"),
    (NODE_KIND, "lease"),
    (NODE_KIND, "observations"),
    (NODE_KIND, "capabilities"),
    (PROFILE_KIND, "observed"),
)


class _Absent:
    """path が写しに無い印(JSON の null と区別する)。"""


_ABSENT = _Absent()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(document: object, path: str) -> object:
    """dot 区切りの path を object の鍵で辿る。途中で鍵が無い・object でない = _ABSENT。"""
    node = document
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def _string_list(value: object) -> list[str]:
    assert isinstance(value, list), repr(value)
    items: list[str] = []
    for item in value:
        assert isinstance(item, str), repr(item)
        items.append(item)
    return items


def test_agora_kinds_reads_exist_in_the_copy() -> None:
    reads = _load_json(_CONTRACT_READS)
    assert _lookup(reads, "schema") == "acp.contract-reads.v1"
    paths = _string_list(_lookup(reads, "reads.agora-kinds"))
    assert paths, "agora-kinds の読む欄が 1 つも宣言されていない"
    assert not [path for path in paths if "*" in path], "容器を開く * はこの検が辿らない"
    copy = _load_json(_AGORA_KINDS_COPY)
    missing = [path for path in paths if _lookup(copy, path) is _ABSENT]
    assert missing == [], f"読む欄が写しに無い: {missing}"


def test_agora_kinds_copy_names_its_version_and_the_canon() -> None:
    copy = _load_json(_AGORA_KINDS_COPY)
    version = _lookup(copy, "version")
    assert isinstance(version, int), repr(version)
    assert not isinstance(version, bool), repr(version)
    assert version >= 1, repr(version)
    assert _lookup(copy, "compatibility.rule") == "additive-only"
    _string_list(_lookup(copy, "compatibility.deprecated"))
    assert _AGORA_KINDS_CANON_MARKER in _CONTRACT_DECLARATION.read_text(encoding="utf-8")


def test_agentd_values_copied_from_agora_kinds_match_the_copy() -> None:
    copy = _load_json(_AGORA_KINDS_COPY)
    assert AGENTD_PRINCIPAL in _string_list(_lookup(copy, "principals"))
    for kind in (TURN_RECORD_KIND, NODE_KIND, PROFILE_KIND, MESSAGE_KIND):
        assert _lookup(copy, f"kinds.{kind}.schema") is not _ABSENT, kind
    entry_kinds = set(
        _string_list(
            _lookup(
                copy,
                "kinds.turn-record.schema.properties.status.properties.entries.items.properties.kind.enum",
            )
        )
    )
    written = set(get_args(EntryKind))
    assert written <= entry_kinds, (
        f"agentd が書く entries の kind が契約の語彙の外: {sorted(written - entry_kinds)}"
    )
    budget = _lookup(copy, "conventions.turnRecordEntries.byteBudget")
    assert budget == TURN_RECORD_ENTRIES_BYTE_BUDGET, repr(budget)
    for kind, axis in _AGENTD_STATUS_AXES:
        writers = _string_list(_lookup(copy, f"kinds.{kind}.declaration.writers.status.{axis}"))
        assert AGENTD_PRINCIPAL in writers, (
            f"{kind}.status.{axis} の書き手に agentd が居ない: {writers}"
        )
    # 段 10 lane 10e: 能力の表の語彙(settings / restartOn の語)は契約の settings の閉語彙と同じ綴り、
    # effort の語は契約の efforts の閉語彙(claude の --effort / codex の model_reasoning_effort に渡す語)。
    from doeff_agents.sessionhost.acp.effects import AGENT_CAPABILITIES, AGENT_SETTINGS

    settings_words = _string_list(_lookup(copy, "conventions.agentSettings.settings"))
    assert list(AGENT_SETTINGS) == settings_words, (AGENT_SETTINGS, settings_words)
    table_schema = _lookup(copy, "kinds.node.schema.properties.status.properties.capabilities.additionalProperties.properties")
    for axis in ("settings", "restartOn"):
        assert _string_list(_lookup(table_schema, f"{axis}.items.enum")) == settings_words
    for kind, entry in AGENT_CAPABILITIES.items():
        assert set(entry["restartOn"]) <= set(entry["settings"]), kind
        assert set(entry["settings"]) <= set(settings_words), kind
    efforts = _string_list(_lookup(copy, "conventions.agentSettings.efforts"))
    assert efforts == ["low", "medium", "high", "xhigh"]


# ---------------------------------------------------------------------------
# 借りは 2 段(段 10 lane 10d・預かり所の契約 v2): master の貸与 = 引換券、札は口座の worker で受ける
# ---------------------------------------------------------------------------


def _lease_answer() -> JSONObject:
    return {
        "ok": True,
        "leaseId": "lease-9",
        "renewed": False,
        "voucher": "vch-0123456789ABCDEFGHJKMNPQRS",
        "workerUrl": "https://worker.test/",
        "holdExpiresAt": "2026-09-14T12:00:00.000Z",
        "note": "引換券を workerUrl の POST /redeem へ",
    }


def test_borrow_takes_the_voucher_to_the_worker_and_the_token_never_touches_the_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """貸与の答えは引換券と worker の基点で、札はその worker の redeem で受ける(札は master を通らない)。"""
    seen: list[tuple[str, str, JSON]] = []

    def fake_http(
        method: str, url: str, headers: Mapping[str, str], body: JSON, timeout: float
    ) -> handlers.HttpReply:
        seen.append((method, url, body))
        if url.endswith("/lease/claude"):
            return handlers.HttpReply(200, _lease_answer())
        return handlers.HttpReply(200, {"ok": True, "leaseId": "lease-9", "accessToken": "tok-1"})

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    custody = handlers.CustodyHttp("http://master.test", "borrower-key")
    grant = custody._borrow(CustodyLeaseBorrow(kind="claude", account="acct", purpose="agent-job s-1"))

    assert [(method, url) for method, url, _ in seen] == [
        ("POST", "http://master.test/lease/claude"),
        ("POST", "https://worker.test/redeem"),
    ], "貸与 → 引換券の redeem の 2 呼びでない"
    assert seen[1][2] == {"voucher": "vch-0123456789ABCDEFGHJKMNPQRS"}
    assert isinstance(grant, LeaseGrant)
    assert grant.lease_id == "lease-9" and grant.access_token == "tok-1"
    assert grant.hold_expires_at_ms == _epoch_ms("2026-09-14T12:00:00.000Z")
    assert "tok-1" not in json.dumps(seen[0][2], ensure_ascii=False), "札が master への要求に混ざっている"


def test_borrow_refuses_when_the_voucher_cannot_be_redeemed(monkeypatch: pytest.MonkeyPatch) -> None:
    """引換券を札に換えられない拍(worker 不達・期限切れ・別の借り手)は断り — 貸与の hold は master の答えから運ぶ。"""

    def fake_http(
        method: str, url: str, headers: Mapping[str, str], body: JSON, timeout: float
    ) -> handlers.HttpReply:
        if url.endswith("/lease/claude"):
            return handlers.HttpReply(200, _lease_answer())
        return handlers.HttpReply(410, {"ok": False, "code": "voucher-expired", "error": "期限が過ぎた"})

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    custody = handlers.CustodyHttp("http://master.test", "borrower-key")
    refused = custody._borrow(CustodyLeaseBorrow(kind="claude", account="acct", purpose="p"))
    assert isinstance(refused, LeaseRefused)
    assert refused.status == 410 and refused.hold_expires_at_ms == _epoch_ms("2026-09-14T12:00:00.000Z")


def test_borrow_names_the_pod_by_its_service_account_token_to_master_and_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """反例(段 10 lane 10y・agora-redesign #110 の実測 2026-09-15 02:09): k3s の pool の agentd は借り手札を持たず、
    X-Borrower-Key だけを送る CustodyHttp は預かり所に『身元が要る(SA token か借り手札)』の 401 で断られ、配車が届いた
    手番が全部 CredentialUnavailable で終わった。SA token の file を宣言した agentd は、貸与(master)・引換券の redeem
    (worker)・返却のどれにも Authorization: Bearer で名乗る。file は要求ごとに読む(kubelet が回した後の値で名乗る)。"""
    seen: list[tuple[str, dict[str, str]]] = []

    def fake_http(
        method: str, url: str, headers: Mapping[str, str], body: JSON, timeout: float
    ) -> handlers.HttpReply:
        seen.append((url, dict(headers)))
        if url.endswith("/lease/claude"):
            return handlers.HttpReply(200, _lease_answer())
        return handlers.HttpReply(200, {"ok": True, "leaseId": "lease-9", "accessToken": "tok-1"})

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    token_file = tmp_path / "token"
    token_file.write_text("sa-token-1\n", encoding="utf-8")
    custody = handlers.CustodyHttp("http://master.test", None, str(token_file))
    grant = custody._borrow(CustodyLeaseBorrow(kind="claude", account="acct", purpose="agent-job s-1"))
    assert isinstance(grant, LeaseGrant)
    assert [(url, headers) for url, headers in seen] == [
        ("http://master.test/lease/claude", {"Authorization": "Bearer sa-token-1"}),
        ("https://worker.test/redeem", {"Authorization": "Bearer sa-token-1"}),
    ]
    token_file.write_text("sa-token-2", encoding="utf-8")
    seen.clear()
    assert custody._revoke("lease-9") is True
    assert seen == [("http://master.test/lease/lease-9/revoke", {"Authorization": "Bearer sa-token-2"})]


def test_borrow_with_a_declared_but_unreadable_service_account_token_refuses_without_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SA token の file を宣言したのに無い・空の拍は、名乗らずに撃って 401 で知るのではなく、宣言の置き場を名指して断る。
    借り手札だけの agentd(会社 Mac)は今日どおり X-Borrower-Key だけを送る。"""

    def refuse_http(*args: object, **kw: object) -> handlers.HttpReply:  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("身元の無い要求を預かり所へ撃った")

    monkeypatch.setattr(handlers, "_http_json", refuse_http)
    refused = handlers.CustodyHttp("http://master.test", None, str(tmp_path / "absent"))._borrow(
        CustodyLeaseBorrow(kind="claude", account="acct", purpose="p")
    )
    assert isinstance(refused, LeaseRefused) and refused.status == 503
    assert "AGORA_CUSTODY_SA_TOKEN_PATH" in refused.error

    seen: list[dict[str, str]] = []

    def fake_http(
        method: str, url: str, headers: Mapping[str, str], body: JSON, timeout: float
    ) -> handlers.HttpReply:
        seen.append(dict(headers))
        return handlers.HttpReply(409, {"ok": False, "error": "held"})

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    handlers.CustodyHttp("http://master.test", "borrower-key")._borrow(
        CustodyLeaseBorrow(kind="claude", account="acct", purpose="p")
    )
    assert seen == [{"X-Borrower-Key": "borrower-key"}]


def test_borrow_without_a_declared_custody_url_refuses_without_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """預かり所の宣言が無い機体は借りない(既定の宿を発明しない — 段 10 lane 10d 便 2)。"""

    def fake_http(*args: object, **kw: object) -> handlers.HttpReply:  # pragma: no cover - 呼ばれたら失敗
        raise AssertionError("宣言が無いのに預かり所を叩いた")

    monkeypatch.setattr(handlers, "_http_json", fake_http)
    refused = handlers.CustodyHttp("", None)._borrow(
        CustodyLeaseBorrow(kind="claude", account="acct", purpose="p")
    )
    assert isinstance(refused, LeaseRefused) and refused.status == 503
    assert "AGORA_CUSTODY_URL" in refused.error


def _epoch_ms(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
