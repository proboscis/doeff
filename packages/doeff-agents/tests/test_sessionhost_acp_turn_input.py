"""送信待ちの列(kind conversation-input)— agentd が運搬郵便の入力の行を運ぶか・取った / 読まれた印を書くか。

設計 herdr-hud docs/design-checks/direct-chat-2026-09-24/design.md 段 2〜3・実装仕様の決定 E1〜E8
(card acp:kanban-issue:ki-0bb4104cd8c2)。operator が送った文は入力の行(ci-…)と、それを refs に持つ運搬郵便の 2 つ。
agentd は本文を読む拍に入力の行へ CAS で taken を書き(取り消し・編集・「今すぐ送る」との競合を 1 つに決める)、
器へ渡せた拍に read を書く。
"""

from __future__ import annotations

import hy  # noqa: F401  # registers the .hy importer
from doeff_agents.sessionhost.acp import turn_input
from doeff_agents.sessionhost.acp.effects import (
    AGORA_KINDS_NAMESPACE,
    JSON,
    MESSAGE_KIND,
    AcpRow,
    JSONObject,
)
from doeff_agents.sessionhost.acp.input_source import CarryInput, NoInputRow, SkipInput
from test_sessionhost_acp import (
    PHASE_ENDED,
    HeadlessWorld,
    World,
    _place_interrupt,
    bound_job,
    message,
    row,
)

from doeff import run

AT = 1789446082000
CI = "ci-01ARZ3NDEKTSV4RRFFQ69G5FAV"
CI_KEY = f"{AGORA_KINDS_NAMESPACE}:conversation-input:{CI}"
REVISIONS: list[JSON] = [{"rev": 0, "text": "元の文", "at": AT}, {"rev": 1, "text": "直した文", "at": AT + 5, "editId": "e-1"}]


def input_row(
    state: str, *, carrier: JSONObject | None = None, revisions: list[JSON] | None = None, rev: int | None = None
) -> AcpRow:
    status: JSONObject = {"state": state}
    if carrier is not None:
        status["carrier"] = carrier
    if rev is not None:
        status["rev"] = rev
    spec: JSONObject = {
        "id": CI,
        "conversation": "c-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "source": {"kind": "operator-chat"},
        "requestId": "rq-1",
        "at": AT,
        "revisions": REVISIONS if revisions is None else revisions,
    }
    return row(AGORA_KINDS_NAMESPACE, "conversation-input", CI, spec, status)


def carrier_mail(message_id: str, body: str = "元の文", refs: list[JSON] | None = None) -> AcpRow:
    return row(
        AGORA_KINDS_NAMESPACE,
        MESSAGE_KIND,
        message_id,
        {"id": message_id, "at": AT, "kind": "note", "from": "operator", "body": body,
         "refs": [CI] if refs is None else refs},
        {"state": "inbox"},
    )


def verdict(input_: AcpRow | None, mail_id: str = "lt-op") -> object:
    return run(turn_input.input_carry_verdict_of(input_, mail_id))


def ci_status(world: World) -> JSONObject:
    status = world.acp.rows[CI_KEY].status
    assert status is not None
    return status


def ci_writes(world: World) -> list[JSONObject]:
    return [status for key, status in world.acp.writes if key == CI_KEY]


def job_status(world: World, job_id: str = "j-1") -> JSONObject:
    status = world.job(job_id).status
    assert status is not None
    return status


def strings(value: JSON) -> list[str]:
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, str)]


def objects(value: JSON) -> list[JSONObject]:
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def cause_reason(status: JSONObject) -> JSON:
    result = status["result"]
    assert isinstance(result, dict)
    cause = result["cause"]
    assert isinstance(cause, dict)
    return cause["reason"]


def launched_prompt(world: World) -> str:
    prompt = world.sessions.launches[-1]["prompt"]
    assert isinstance(prompt, str)
    return prompt


# ---------------------------------------------------------------- 純関数


def test_the_carried_input_id_is_the_single_ci_ref() -> None:
    assert run(turn_input.carried_input_id_of({"refs": ["lt-A", CI]})) == CI
    assert run(turn_input.carried_input_id_of({"refs": ["lt-A"]})) is None
    assert run(turn_input.carried_input_id_of({})) is None
    # 2 つ以上は形の違反 — 運ぶ入力を決められないので旧い経路
    other = "ci-01ARZ3NDEKTSV4RRFFQ69G5FAW"
    assert run(turn_input.carried_input_id_of({"refs": [CI, other]})) is None
    # 綴りの外(小文字・I L O U を含む)は ci id ではない
    assert run(turn_input.carried_input_id_of({"refs": ["ci-01arz3ndektsv4rrffq69g5fav"]})) is None
    assert run(turn_input.carried_input_id_of({"refs": ["ci-01ARZ3NDEKTSV4RRFFQ69G5FAI"]})) is None


def test_the_carry_verdict_table() -> None:
    assert verdict(None) == NoInputRow()
    assert verdict(input_row("pending")) == CarryInput("直した文", 1)
    assert verdict(input_row("taken", carrier={"job": "j-0", "mail": "lt-op"})) == CarryInput("直した文", 1)
    assert verdict(input_row("read", carrier={"job": "j-0", "mail": "lt-op"})) == CarryInput("直した文", 1)
    assert verdict(input_row("taken", carrier={"job": "j-0", "mail": "lt-other"})) == SkipInput("carried-by-another-mail")
    assert verdict(input_row("read", carrier={"job": "j-0", "mail": "lt-other"})) == SkipInput("carried-by-another-mail")
    assert verdict(input_row("withdrawn")) == SkipInput("withdrawn")
    assert verdict(input_row("answered")) == SkipInput("answered")
    assert verdict(input_row("failed")) == SkipInput("failed")
    # 版の読めない行・語彙の外の状態は本文をそのまま運ぶ(発明しない)
    assert verdict(input_row("pending", revisions=[])) == NoInputRow()
    assert verdict(input_row("mystery")) == NoInputRow()
    # 最新の版 = rev の最も大きい項(並びの順ではない)
    shuffled = [REVISIONS[1], REVISIONS[0]]
    assert verdict(input_row("pending", revisions=shuffled)) == CarryInput("直した文", 1)


def test_the_taken_and_read_images_keep_other_fields_and_are_idempotent() -> None:
    taken = run(turn_input.taken_status_of({"state": "pending", "note": "x"}, "j-1", "lt-op", 1, 5_000))
    assert taken == {"state": "taken", "note": "x", "carrier": {"job": "j-1", "mail": "lt-op"}, "rev": 1, "takenAt": 5_000}
    # 同じ job・同じ郵便が既に取っている(借りのやり直し)= 書かない
    assert run(turn_input.taken_status_of(taken, "j-1", "lt-op", 1, 9_000)) is None
    # 同じ郵便を別の job が運び直した = carrier.job を書き換え、takenAt は最初の刻
    moved = run(turn_input.taken_status_of(taken, "j-2", "lt-op", 1, 9_000))
    assert moved == {**taken, "carrier": {"job": "j-2", "mail": "lt-op"}}
    # read は taken へ戻さない
    read = run(turn_input.read_status_of(taken, "lt-op", "handed-to-turn", 6_000))
    assert read == {**taken, "state": "read", "readAt": 6_000, "readEvidence": "handed-to-turn"}
    assert run(turn_input.taken_status_of(read, "j-2", "lt-op", 1, 9_000)) is None
    # read を書くのは、この郵便が取った行だけ
    assert run(turn_input.read_status_of(read, "lt-op", "interjected", 7_000)) is None
    assert run(turn_input.read_status_of(taken, "lt-other", "interjected", 7_000)) is None
    assert run(turn_input.read_status_of({"state": "withdrawn"}, "lt-op", "interjected", 7_000)) is None


# ---------------------------------------------------------------- 手番の始まり(start-claimed)


def test_a_pending_input_is_taken_carried_as_its_latest_revision_and_read() -> None:
    """(a) pending の入力: 1 手番目の prompt は最新の版の本文(編集が効く)・見出しの参照に ci id を出さない・
    入力の行は taken → read{handed-to-turn}(carrier = この job とこの郵便)。"""
    world = HeadlessWorld()
    world.acp.put_row(input_row("pending"))
    world.acp.put_row(carrier_mail("lt-op"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    prompt = launched_prompt(world)
    assert isinstance(prompt, str)
    assert "直した文" in prompt
    assert "元の文" not in prompt
    assert CI not in prompt
    writes = ci_writes(world)
    assert [w["state"] for w in writes] == ["taken", "read"]
    assert writes[0]["carrier"] == {"job": "j-1", "mail": "lt-op"}
    assert writes[0]["rev"] == 1
    status = ci_status(world)
    assert status["state"] == "read"
    assert status["readEvidence"] == "handed-to-turn"
    assert status["carrier"] == {"job": "j-1", "mail": "lt-op"}
    js = job_status(world)
    assert js["inputsDelivered"] == ["lt-op"]


def test_a_withdrawn_input_ends_the_job_without_launching_and_reports_the_mail_handled() -> None:
    """(b) 取り消された入力しか無い手番: CLI を起こさず札も借りず、InputWithdrawn で閉じ、郵便は扱い済みとして報告する
    (Messaging が運び直さない)。"""
    world = HeadlessWorld()
    world.acp.put_row(input_row("withdrawn"))
    world.acp.put_row(carrier_mail("lt-op"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    assert world.sessions.launches == []
    assert world.sessions.sends == []
    assert world.custody.borrowed == []
    assert world.state.jobs == ()
    js = job_status(world)
    assert js["phase"] == PHASE_ENDED
    assert cause_reason(js) == "InputWithdrawn"
    assert js["inputsDelivered"] == ["lt-op"]
    assert ci_writes(world) == []


def test_an_input_taken_by_another_mail_is_skipped_but_the_other_mail_rides() -> None:
    """(c) 別の郵便(「今すぐ送る」の 2 通目)が取った入力は運ばない。同じ手番の他の郵便は今どおり運び、外した郵便も
    扱い済みとして報告する。"""
    world = HeadlessWorld()
    world.acp.put_row(input_row("taken", carrier={"job": "j-0", "mail": "lt-force"}, rev=1))
    world.acp.put_row(carrier_mail("lt-op"))
    world.acp.put_row(message("lt-cx", "進み具合は?"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op", "lt-cx"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    prompt = launched_prompt(world)
    assert "進み具合は?" in prompt
    assert "直した文" not in prompt
    assert "元の文" not in prompt
    js = job_status(world)
    assert sorted(strings(js["inputsDelivered"])) == ["lt-cx", "lt-op"]
    # InputUnavailable(見つからない郵便)に外した郵便を混ぜない
    assert all(c.get("type") != "InputUnavailable" for c in objects(js.get("conditions", [])))
    assert ci_writes(world) == []


def test_a_mail_whose_input_row_is_missing_rides_as_before() -> None:
    """(d) 行の無い入力(旧い経路・行の作成が遅れた窓): 郵便の本文をそのまま運ぶ。"""
    world = HeadlessWorld()
    world.acp.put_row(carrier_mail("lt-op", body="そのままの文"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    assert "そのままの文" in launched_prompt(world)
    js = job_status(world)
    assert js["inputsDelivered"] == ["lt-op"]


def test_a_conflicting_take_rereads_and_wins() -> None:
    """(f) taken の CAS が 1 度負けたら読み直して撃ち直す。"""
    world = HeadlessWorld()
    world.acp.put_row(input_row("pending"))
    world.acp.put_row(carrier_mail("lt-op"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.acp.conflict_once[CI_KEY] = 2
    world.tick()
    assert "直した文" in launched_prompt(world)
    assert ci_status(world)["state"] == "read"


def test_a_take_that_keeps_losing_carries_the_latest_text_without_marking() -> None:
    """(f) 3 回とも負けたら取った印を書かずに最新の版を運ぶ(行の無い入力と同じ競合の窓 — 本文は落とさない)。
    取っていないので read も書かない。"""
    world = HeadlessWorld()
    world.acp.put_row(input_row("pending"))
    world.acp.put_row(carrier_mail("lt-op"))
    world.acp.put_row(bound_job("j-1", inputs=["lt-op"], created_at_ms=world.local.now_ms - 400))
    world.acp.conflict_times[CI_KEY] = (2, 3)
    world.tick()
    assert "直した文" in launched_prompt(world)
    assert ci_status(world) == {"state": "pending"}
    assert any("could not take input" in line for line in world.local.logs)


# ---------------------------------------------------------------- 割り込み(deliver-interrupts-of)


def _running(world: World) -> str:
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"]))
    world.tick()
    return world.sid("j-1")


def test_an_interrupt_with_a_pending_input_is_injected_as_its_latest_revision() -> None:
    """(e) 「今すぐ送る」: 割り込みの運搬郵便の入力が pending なら taken を書いてから最新の版を注入し、read{interjected}。"""
    world = World()
    sid = _running(world)
    world.acp.put_row(input_row("pending"))
    world.acp.put_row(carrier_mail("lt-i1"))
    _place_interrupt(world, "j-1", ["lt-i1"])
    world.tick(advance_ms=1_000)
    assert len(world.sessions.interjections) == 1
    injected_sid, text = world.sessions.interjections[0]
    assert injected_sid == sid
    assert "直した文" in text
    assert "元の文" not in text
    assert [w["state"] for w in ci_writes(world)] == ["taken", "read"]
    status = ci_status(world)
    assert status["readEvidence"] == "interjected"
    assert status["carrier"] == {"job": "j-1", "mail": "lt-i1"}
    js = job_status(world)
    assert js["interruptsDelivered"] == ["lt-i1"]


def test_an_interrupt_whose_input_was_already_taken_is_not_injected_but_recorded_handed() -> None:
    """(e) 入力を別の郵便が既に取った割り込みは注入しない。渡した印(interruptsDelivered)には載せる — 載せないと
    Messaging が終わった手番の割り込みを積み直す。"""
    world = World()
    _running(world)
    world.acp.put_row(input_row("taken", carrier={"job": "j-0", "mail": "lt-op"}, rev=1))
    world.acp.put_row(carrier_mail("lt-i2"))
    _place_interrupt(world, "j-1", ["lt-i2"])
    world.tick(advance_ms=1_000)
    assert world.sessions.interjections == []
    js = job_status(world)
    assert js["interruptsDelivered"] == ["lt-i2"]
    assert js["interrupts"] == []
    assert ci_writes(world) == []


def test_the_operator_chat_heading_does_not_list_the_carried_input_as_a_reference() -> None:
    """見出しの「参照」に入力の行の id(ci-…)を出さない — 他の参照(依頼の id 等)は今どおり出す。"""
    from doeff_agents.sessionhost.acp import reply_channel

    item = run(reply_channel.turn_input_text_of("lt-1", {"kind": "note", "from": "operator", "refs": [CI, "lt-B"]}, "本文"))
    assert CI not in item.text
    assert "・参照 lt-B)" in item.text
    only = run(reply_channel.turn_input_text_of("lt-1", {"kind": "note", "from": "operator", "refs": [CI]}, "本文"))
    assert "参照" not in only.text
